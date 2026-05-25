// toPassClauditor matcher tests (jest). Mirrored under test/jest-helper.test.js
// for vitest so the SAME matcher proves identical behavior under both runners.
//
// validate-result branches feed the matcher a plain eval-shaped object so no
// subprocess is needed. The runSkill-result branch drives a FAKE clauditor
// stub via CLAUDITOR_BIN so `validate(evalPath)` returns a canned payload.
const { toPassClauditor } = require("../jest-helper");

const fs = require("fs");
const os = require("os");
const path = require("path");

expect.extend({ toPassClauditor });

function passingValidateResult() {
  return {
    skill: "greeter",
    pass_rate: 1.0,
    passed: true,
    results: [
      { name: "contains-hi", passed: true },
      { name: "min-length", passed: true },
    ],
  };
}

function failingValidateResult() {
  return {
    skill: "greeter",
    pass_rate: 0.5,
    passed: false,
    results: [
      { name: "contains-hi", passed: true },
      { name: "min-length", passed: false },
      { name: "no-todo", passed: false },
    ],
  };
}

describe("toPassClauditor with a validate() result", () => {
  test("passes when passed:true", async () => {
    await expect(passingValidateResult()).toPassClauditor();
  });

  test("fails when passed:false and message names failing criteria", async () => {
    const result = await toPassClauditor.call(
      { isNot: false },
      failingValidateResult()
    );
    expect(result.pass).toBe(false);
    const msg = result.message();
    expect(msg).toContain("min-length");
    expect(msg).toContain("no-todo");
    // A passing criterion must NOT be listed.
    expect(msg).not.toContain("contains-hi");
  });

  test(".not passes for a failing result", async () => {
    await expect(failingValidateResult()).not.toPassClauditor();
  });

  test(".not fails (with a message) for a passing result", async () => {
    const result = await toPassClauditor.call(
      { isNot: true },
      passingValidateResult()
    );
    expect(result.pass).toBe(true); // isNot inverts the outcome
    expect(result.message()).toContain("NOT to pass");
  });
});

describe("toPassClauditor with a runSkill() result", () => {
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
process.exit(Number(process.env.EXIT_CODE || "0"));
`;
    const exeStub = path.join(tmpDir, "clauditor-stub");
    fs.writeFileSync(exeStub, `#!${process.execPath}\n${script}`);
    fs.chmodSync(exeStub, 0o755);
    return exeStub;
  }

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-jh-"));
    argvDumpPath = path.join(tmpDir, "argv.json");
    process.env.CLAUDITOR_BIN = writeStub();
    process.env.ARGV_DUMP = argvDumpPath;
  });

  afterEach(() => {
    delete process.env.CLAUDITOR_BIN;
    delete process.env.ARGV_DUMP;
    delete process.env.CANNED_JSON;
    delete process.env.EXIT_CODE;
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test("requires an eval path argument", async () => {
    const runResult = { output: "hi", exit_code: 0, skill: "greeter" };
    const result = await toPassClauditor.call({ isNot: false }, runResult);
    expect(result.pass).toBe(false);
    expect(result.message()).toContain("eval path argument is REQUIRED");
  });

  test("calls validate(evalPath) and passes on a passing payload", async () => {
    process.env.CANNED_JSON = JSON.stringify(passingValidateResult());
    process.env.EXIT_CODE = "0";
    const runResult = { output: "hi", exit_code: 0, skill: "greeter" };
    await expect(runResult).toPassClauditor("SKILL.md");
    // Confirm validate routed `validate <path> --json`.
    const argv = JSON.parse(fs.readFileSync(argvDumpPath, "utf8"));
    expect(argv).toEqual(["validate", "SKILL.md", "--json"]);
  });

  test("calls validate(evalPath) and fails (naming criteria) on a failing payload", async () => {
    process.env.CANNED_JSON = JSON.stringify(failingValidateResult());
    process.env.EXIT_CODE = "1"; // failing eval is data, not an error
    const runResult = { output: "hi", exit_code: 0, skill: "greeter" };
    const result = await toPassClauditor.call(
      { isNot: false },
      runResult,
      "SKILL.md"
    );
    expect(result.pass).toBe(false);
    expect(result.message()).toContain("min-length");
  });
});
