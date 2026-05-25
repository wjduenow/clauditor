// Public JS API tests (vitest). Mirrors __tests__/api.test.js so the same
// contract runs under both runners. Uses a FAKE clauditor stub via
// CLAUDITOR_BIN for runSkill/validate and real tmp-dir fixtures for loadSpec.
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { runSkill, validate, loadSpec } = require("../index");
const {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
} = require("../index");

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
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-api-v-"));
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

describe("re-exported error classes (vitest)", () => {
  it("are the same classes lib/errors exports", () => {
    const libErrors = require("../lib/errors");
    expect(ClauditorError).toBe(libErrors.ClauditorError);
    expect(ClauditorNotFoundError).toBe(libErrors.ClauditorNotFoundError);
    expect(ClauditorInputError).toBe(libErrors.ClauditorInputError);
    expect(ClauditorApiError).toBe(libErrors.ClauditorApiError);
  });
});

describe("runSkill (vitest)", () => {
  it("maps args / projectDir / timeout to flags and returns parsed shape", async () => {
    process.env.CANNED_JSON = JSON.stringify({ output: "ok", exit_code: 0 });
    process.env.EXIT_CODE = "0";
    const result = await runSkill("my-skill", {
      args: "a b",
      projectDir: "/p",
      timeout: 12,
    });
    expect(receivedArgv()).toEqual([
      "run",
      "my-skill",
      "--json",
      "--args",
      "a b",
      "--project-dir",
      "/p",
      "--timeout",
      "12",
    ]);
    expect(result).toEqual({ output: "ok", exit_code: 0 });
  });

  it("exit 2 throws ClauditorInputError", async () => {
    process.env.EXIT_CODE = "2";
    process.env.CANNED_STDERR = "bad";
    await expect(runSkill("nope")).rejects.toThrow(ClauditorInputError);
  });
});

describe("validate (vitest)", () => {
  it("maps to validate --json and resolves {passed:false} on exit 1", async () => {
    process.env.CANNED_JSON = JSON.stringify({
      skill: "s",
      pass_rate: 0.0,
      passed: false,
      results: [],
    });
    process.env.EXIT_CODE = "1";
    const result = await validate("SKILL.md");
    expect(receivedArgv()).toEqual(["validate", "SKILL.md", "--json"]);
    expect(result.passed).toBe(false);
  });

  it("exit 3 throws ClauditorApiError", async () => {
    process.env.EXIT_CODE = "3";
    process.env.CANNED_STDERR = "api down";
    await expect(validate("SKILL.md")).rejects.toThrow(ClauditorApiError);
  });
});

describe("loadSpec (vitest)", () => {
  let specDir;

  beforeEach(() => {
    specDir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-spec-v-"));
  });

  afterEach(() => {
    fs.rmSync(specDir, { recursive: true, force: true });
  });

  it("reads an explicit .eval.json path", async () => {
    const evalPath = path.join(specDir, "my.eval.json");
    fs.writeFileSync(evalPath, JSON.stringify({ skill_name: "my" }));
    expect(await loadSpec(evalPath)).toEqual({ skill_name: "my" });
  });

  it("discovers sibling <stem>.eval.json for an X.md skill", async () => {
    fs.writeFileSync(path.join(specDir, "greeter.md"), "# skill");
    fs.writeFileSync(
      path.join(specDir, "greeter.eval.json"),
      JSON.stringify({ skill_name: "greeter" })
    );
    expect(await loadSpec(path.join(specDir, "greeter.md"))).toEqual({
      skill_name: "greeter",
    });
  });

  it("discovers eval.json for a SKILL.md skill", async () => {
    fs.writeFileSync(path.join(specDir, "SKILL.md"), "# skill");
    fs.writeFileSync(path.join(specDir, "eval.json"), JSON.stringify({ via: "eval.json" }));
    expect(await loadSpec(path.join(specDir, "SKILL.md"))).toEqual({
      via: "eval.json",
    });
  });

  it("throws when no sibling eval file is found", async () => {
    fs.writeFileSync(path.join(specDir, "lonely.md"), "# skill");
    await expect(loadSpec(path.join(specDir, "lonely.md"))).rejects.toThrow(
      /no eval spec found/
    );
  });
});
