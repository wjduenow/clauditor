// Public JS API tests (jest). Drives runSkill/validate through a FAKE
// clauditor stub via CLAUDITOR_BIN — a node script that dumps the argv it
// received and echoes a canned JSON payload + exit code — so we can assert
// both the option->flag mapping and the parsed return shape. loadSpec is
// tested against real tmp-dir fixtures (no subprocess).
const { runSkill, validate, loadSpec } = require("../index");
const {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
} = require("../index");

const fs = require("fs");
const os = require("os");
const path = require("path");

let tmpDir;
let argvDumpPath;

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
  const exeStub = path.join(tmpDir, "clauditor-stub");
  fs.writeFileSync(exeStub, `#!${process.execPath}\n${script}`);
  fs.chmodSync(exeStub, 0o755);
  return exeStub;
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-api-"));
  argvDumpPath = path.join(tmpDir, "argv.json");
  process.env.CLAUDITOR_BIN = writeStub();
  process.env.ARGV_DUMP = argvDumpPath;
});

afterEach(() => {
  delete process.env.CLAUDITOR_BIN;
  delete process.env.ARGV_DUMP;
  delete process.env.CANNED_JSON;
  delete process.env.CANNED_STDERR;
  delete process.env.EXIT_CODE;
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function receivedArgv() {
  return JSON.parse(fs.readFileSync(argvDumpPath, "utf8"));
}

describe("re-exported error classes", () => {
  test("are the same classes lib/errors exports", () => {
    const libErrors = require("../lib/errors");
    expect(ClauditorError).toBe(libErrors.ClauditorError);
    expect(ClauditorNotFoundError).toBe(libErrors.ClauditorNotFoundError);
    expect(ClauditorInputError).toBe(libErrors.ClauditorInputError);
    expect(ClauditorApiError).toBe(libErrors.ClauditorApiError);
  });
});

describe("runSkill", () => {
  test("maps to `run <skill> --json` with no options", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    await runSkill("my-skill");
    expect(receivedArgv()).toEqual(["run", "my-skill", "--json"]);
  });

  test("maps args / projectDir / timeout to flags", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    await runSkill("my-skill", {
      args: "find me thai food",
      projectDir: "/tmp/proj",
      timeout: 45,
    });
    expect(receivedArgv()).toEqual([
      "run",
      "my-skill",
      "--json",
      "--args",
      "find me thai food",
      "--project-dir",
      "/tmp/proj",
      "--timeout",
      "45",
    ]);
  });

  test("returns the parsed run --json shape", async () => {
    const payload = {
      output: "hello",
      exit_code: 0,
      duration_seconds: 1.5,
      error: null,
      error_category: null,
      warnings: [],
      input_tokens: 10,
      output_tokens: 20,
      harness: "claude-code",
      skill: "my-skill",
      args: "",
    };
    process.env.CANNED_JSON = JSON.stringify(payload);
    process.env.EXIT_CODE = "0";
    const result = await runSkill("my-skill");
    expect(result).toEqual(payload);
  });

  test("does not push --args when args is omitted", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    await runSkill("my-skill", { projectDir: "/x" });
    const argv = receivedArgv();
    expect(argv).not.toContain("--args");
    expect(argv).toContain("--project-dir");
  });

  test("exit 2 throws ClauditorInputError", async () => {
    process.env.EXIT_CODE = "2";
    process.env.CANNED_STDERR = "ERROR: bad skill";
    await expect(runSkill("nope")).rejects.toThrow(ClauditorInputError);
  });

  test("exit 3 throws ClauditorApiError", async () => {
    process.env.EXIT_CODE = "3";
    process.env.CANNED_STDERR = "ERROR: rate limited";
    await expect(runSkill("my-skill")).rejects.toThrow(ClauditorApiError);
  });
});

describe("validate", () => {
  test("maps to `validate <skillPath> --json`", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    await validate("skills/my-skill/SKILL.md");
    expect(receivedArgv()).toEqual([
      "validate",
      "skills/my-skill/SKILL.md",
      "--json",
    ]);
  });

  test("maps eval / timeout options to flags", async () => {
    process.env.CANNED_JSON = "{}";
    process.env.EXIT_CODE = "0";
    await validate("SKILL.md", { eval: "my.eval.json", timeout: 30 });
    expect(receivedArgv()).toEqual([
      "validate",
      "SKILL.md",
      "--json",
      "--eval",
      "my.eval.json",
      "--timeout",
      "30",
    ]);
  });

  test("resolves {passed: true} on exit 0", async () => {
    process.env.CANNED_JSON = JSON.stringify({
      skill: "s",
      pass_rate: 1.0,
      passed: true,
      results: [{ name: "a", passed: true }],
    });
    process.env.EXIT_CODE = "0";
    const result = await validate("SKILL.md");
    expect(result.passed).toBe(true);
    expect(result.pass_rate).toBe(1.0);
  });

  test("resolves {passed: false} on exit 1 (failing eval is data, not thrown)", async () => {
    process.env.CANNED_JSON = JSON.stringify({
      skill: "s",
      pass_rate: 0.5,
      passed: false,
      results: [{ name: "a", passed: false }],
    });
    process.env.EXIT_CODE = "1";
    const result = await validate("SKILL.md");
    expect(result.passed).toBe(false);
    expect(result.pass_rate).toBe(0.5);
  });

  test("exit 2 throws ClauditorInputError", async () => {
    process.env.EXIT_CODE = "2";
    process.env.CANNED_STDERR = "ERROR: malformed spec";
    await expect(validate("SKILL.md")).rejects.toThrow(ClauditorInputError);
  });

  test("exit 3 throws ClauditorApiError", async () => {
    process.env.EXIT_CODE = "3";
    process.env.CANNED_STDERR = "ERROR: api down";
    await expect(validate("SKILL.md")).rejects.toThrow(ClauditorApiError);
  });
});

describe("loadSpec", () => {
  let specDir;

  beforeEach(() => {
    specDir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-spec-"));
  });

  afterEach(() => {
    fs.rmSync(specDir, { recursive: true, force: true });
  });

  test("reads an explicit .eval.json path", async () => {
    const evalPath = path.join(specDir, "my.eval.json");
    fs.writeFileSync(evalPath, JSON.stringify({ skill_name: "my", assertions: [] }));
    const spec = await loadSpec(evalPath);
    expect(spec).toEqual({ skill_name: "my", assertions: [] });
  });

  test("reads an explicit plain .json path", async () => {
    const evalPath = path.join(specDir, "config.json");
    fs.writeFileSync(evalPath, JSON.stringify({ k: 1 }));
    expect(await loadSpec(evalPath)).toEqual({ k: 1 });
  });

  test("discovers sibling <stem>.eval.json for an X.md skill", async () => {
    fs.writeFileSync(path.join(specDir, "greeter.md"), "# skill");
    fs.writeFileSync(
      path.join(specDir, "greeter.eval.json"),
      JSON.stringify({ skill_name: "greeter" })
    );
    const spec = await loadSpec(path.join(specDir, "greeter.md"));
    expect(spec).toEqual({ skill_name: "greeter" });
  });

  test("prefers SKILL.eval.json (engine-canonical) for a SKILL.md skill", async () => {
    // The Python engine loads skill_path.with_suffix(".eval.json") ->
    // SKILL.eval.json; loadSpec must agree, even when eval.json also exists.
    fs.writeFileSync(path.join(specDir, "SKILL.md"), "# skill");
    fs.writeFileSync(
      path.join(specDir, "SKILL.eval.json"),
      JSON.stringify({ via: "SKILL.eval.json" })
    );
    fs.writeFileSync(
      path.join(specDir, "eval.json"),
      JSON.stringify({ via: "eval.json" })
    );
    const spec = await loadSpec(path.join(specDir, "SKILL.md"));
    expect(spec).toEqual({ via: "SKILL.eval.json" });
  });

  test("discovers eval.json for a SKILL.md skill", async () => {
    fs.writeFileSync(path.join(specDir, "SKILL.md"), "# skill");
    fs.writeFileSync(
      path.join(specDir, "eval.json"),
      JSON.stringify({ via: "eval.json" })
    );
    const spec = await loadSpec(path.join(specDir, "SKILL.md"));
    expect(spec).toEqual({ via: "eval.json" });
  });

  test("falls back to <dir>.eval.json for a SKILL.md skill", async () => {
    const skillDir = path.join(specDir, "my-skill");
    fs.mkdirSync(skillDir);
    fs.writeFileSync(path.join(skillDir, "SKILL.md"), "# skill");
    fs.writeFileSync(
      path.join(skillDir, "my-skill.eval.json"),
      JSON.stringify({ via: "dir.eval.json" })
    );
    const spec = await loadSpec(path.join(skillDir, "SKILL.md"));
    expect(spec).toEqual({ via: "dir.eval.json" });
  });

  test("throws when no sibling eval file is found", async () => {
    fs.writeFileSync(path.join(specDir, "lonely.md"), "# skill");
    await expect(loadSpec(path.join(specDir, "lonely.md"))).rejects.toThrow(
      /no eval spec found/
    );
  });

  test("throws on a non-string / empty target", async () => {
    await expect(loadSpec("")).rejects.toThrow(/non-empty string/);
  });

  test("throws on invalid JSON in the eval file", async () => {
    const evalPath = path.join(specDir, "broken.eval.json");
    fs.writeFileSync(evalPath, "{not json");
    await expect(loadSpec(evalPath)).rejects.toThrow(/invalid JSON/);
  });
});
