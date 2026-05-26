// Pure-helper + resolveBinary tests (vitest runner). Mirrors
// __tests__/lib-core.test.js so the same contract runs under both runners.
import { describe, it, expect, afterEach } from "vitest";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { mapExit } = require("../lib/exec");
const { resolveBinary } = require("../lib/resolve-binary");
const {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
} = require("../lib/errors");

describe("error class hierarchy (vitest)", () => {
  it("every error extends ClauditorError and sets .name", () => {
    for (const Cls of [
      ClauditorNotFoundError,
      ClauditorInputError,
      ClauditorApiError,
    ]) {
      const e = new Cls("boom");
      expect(e).toBeInstanceOf(ClauditorError);
      expect(e).toBeInstanceOf(Error);
      expect(e.name).toBe(Cls.name);
    }
    expect(new ClauditorError("x").name).toBe("ClauditorError");
  });
});

describe("mapExit (vitest)", () => {
  it("exit 0 returns parsed JSON", () => {
    expect(mapExit(0, '{"passed": true}', "")).toEqual({ passed: true });
  });

  it("exit 1 returns parsed JSON (failing eval is data)", () => {
    expect(mapExit(1, '{"passed": false}', "")).toEqual({ passed: false });
  });

  it("exit 2 throws ClauditorInputError", () => {
    expect(() => mapExit(2, "", "bad input")).toThrow(ClauditorInputError);
  });

  it("exit 3 throws ClauditorApiError", () => {
    expect(() => mapExit(3, "", "api down")).toThrow(ClauditorApiError);
  });

  it("exit 7 throws ClauditorError", () => {
    expect(() => mapExit(7, "", "weird")).toThrow(ClauditorError);
  });

  it("non-JSON stdout on exit 0 throws ClauditorError", () => {
    expect(() => mapExit(0, "nope", "")).toThrow(ClauditorError);
  });
});

describe("resolveBinary (vitest)", () => {
  const ORIG_BIN = process.env.CLAUDITOR_BIN;
  const ORIG_PATH = process.env.PATH;

  afterEach(() => {
    if (ORIG_BIN === undefined) {
      delete process.env.CLAUDITOR_BIN;
    } else {
      process.env.CLAUDITOR_BIN = ORIG_BIN;
    }
    process.env.PATH = ORIG_PATH;
  });

  it("honors CLAUDITOR_BIN override", () => {
    process.env.CLAUDITOR_BIN = "/opt/custom/clauditor";
    expect(resolveBinary()).toEqual({
      command: "/opt/custom/clauditor",
      argsPrefix: [],
    });
  });

  it("throws ClauditorNotFoundError with install hint when nothing resolves", () => {
    delete process.env.CLAUDITOR_BIN;
    process.env.PATH = "";
    expect(() => resolveBinary()).toThrow(ClauditorNotFoundError);
  });

  // H2: PATH resolution returns the FULL resolved path, not the bare name.
  it("PATH resolution returns the full resolved path, not the bare name", () => {
    delete process.env.CLAUDITOR_BIN;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-path-"));
    const name = process.platform === "win32" ? "clauditor.exe" : "clauditor";
    const full = path.join(dir, name);
    fs.writeFileSync(full, "#!/bin/sh\n", { mode: 0o755 });
    process.env.PATH = dir;
    try {
      const res = resolveBinary();
      expect(res.command).toBe(full);
      expect(res.argsPrefix).toEqual([]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  // H3: python fallback resolves `python` (not only python3) with -m prefix.
  it("python fallback resolves `python` with the -m clauditor prefix", () => {
    delete process.env.CLAUDITOR_BIN;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-py-"));
    const name = process.platform === "win32" ? "python.exe" : "python";
    const full = path.join(dir, name);
    fs.writeFileSync(full, "#!/bin/sh\n", { mode: 0o755 });
    process.env.PATH = dir;
    try {
      const res = resolveBinary();
      expect(res.command).toBe(full);
      expect(res.argsPrefix).toEqual(["-m", "clauditor"]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
