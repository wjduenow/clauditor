// execJson integration test (jest only — avoids duplicating temp-file
// fixtures across runners). Drives a FAKE clauditor stub via CLAUDITOR_BIN:
// a tiny node script that echoes canned JSON + a chosen exit code, and
// records the argv it received so we can prove no shell was used.
const { execJson } = require("../lib/exec");
const {
  ClauditorInputError,
  ClauditorApiError,
} = require("../lib/errors");

const fs = require("fs");
const os = require("os");
const path = require("path");

let tmpDir;
let argvDumpPath;

// Write a stub script that:
//   - dumps its argv (everything after `node stub.js`) to ARGV_DUMP as JSON,
//   - prints CANNED_JSON to stdout,
//   - prints CANNED_STDERR to stderr,
//   - exits with EXIT_CODE.
// All three are read from env so each test configures behavior without
// rewriting the file.
function writeStub() {
  const script = `
const fs = require("fs");
const argv = process.argv.slice(2);
if (process.env.ARGV_DUMP) {
  fs.writeFileSync(process.env.ARGV_DUMP, JSON.stringify(argv));
}
if (process.env.CANNED_JSON) {
  process.stdout.write(process.env.CANNED_JSON);
}
if (process.env.CANNED_STDERR) {
  process.stderr.write(process.env.CANNED_STDERR);
}
process.exit(Number(process.env.EXIT_CODE || "0"));
`;
  // CLAUDITOR_BIN must be a single executable command, and resolveBinary
  // returns argsPrefix=[] for the override path — so the stub must BE the
  // command. Make it self-executing with a node shebang + chmod so execFile
  // can run it directly.
  const exeStub = path.join(tmpDir, "clauditor-stub");
  fs.writeFileSync(exeStub, `#!${process.execPath}\n${script}`);
  fs.chmodSync(exeStub, 0o755);
  return exeStub;
}

// Env keys these tests mutate. Save originals in beforeEach and restore in
// afterEach (rather than blanket-delete) so a pre-existing value in the
// developer's shell — notably CLAUDITOR_BIN — survives the test run.
const _MUTATED_ENV = [
  "CLAUDITOR_BIN",
  "ARGV_DUMP",
  "CANNED_JSON",
  "CANNED_STDERR",
  "EXIT_CODE",
];
let _savedEnv;

beforeEach(() => {
  _savedEnv = {};
  for (const key of _MUTATED_ENV) {
    _savedEnv[key] = process.env[key];
  }
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-exec-"));
  argvDumpPath = path.join(tmpDir, "argv.json");
  process.env.CLAUDITOR_BIN = writeStub();
  process.env.ARGV_DUMP = argvDumpPath;
});

afterEach(() => {
  for (const key of _MUTATED_ENV) {
    if (_savedEnv[key] === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = _savedEnv[key];
    }
  }
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe("execJson", () => {
  test("exit 0 resolves with parsed JSON", async () => {
    process.env.CANNED_JSON = '{"passed": true, "pass_rate": 1.0}';
    process.env.EXIT_CODE = "0";
    const result = await execJson(["run", "--json", "my-skill"]);
    expect(result).toEqual({ passed: true, pass_rate: 1.0 });
  });

  test("exit 1 resolves with parsed JSON (failing eval is data)", async () => {
    process.env.CANNED_JSON = '{"passed": false, "pass_rate": 0.0}';
    process.env.EXIT_CODE = "1";
    const result = await execJson(["run", "--json", "my-skill"]);
    expect(result).toEqual({ passed: false, pass_rate: 0.0 });
  });

  test("exit 2 throws ClauditorInputError", async () => {
    process.env.EXIT_CODE = "2";
    process.env.CANNED_STDERR = "ERROR: missing skill file";
    await expect(execJson(["run", "missing"])).rejects.toThrow(
      ClauditorInputError
    );
  });

  test("exit 3 throws ClauditorApiError", async () => {
    process.env.EXIT_CODE = "3";
    process.env.CANNED_STDERR = "ERROR: rate limited";
    await expect(execJson(["grade", "my-skill"])).rejects.toThrow(
      ClauditorApiError
    );
  });

  test("args with spaces/special chars pass literally (no shell)", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    const args = ["run", "--user-prompt", "hello $(rm -rf /); world", "a b c"];
    await execJson(args);
    const received = JSON.parse(fs.readFileSync(argvDumpPath, "utf8"));
    // The stub receives EXACTLY the args we passed — no shell expansion,
    // no word-splitting on the spaces, no $() evaluation.
    expect(received).toEqual(args);
  });

  test("timeout option is forwarded (no crash on small timeout)", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    // A generous timeout that the fast stub finishes well within.
    const result = await execJson(["run"], { timeout: 30000 });
    expect(result).toEqual({});
  });
});
