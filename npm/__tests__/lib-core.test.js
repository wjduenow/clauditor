// Pure-helper + resolveBinary tests (jest runner). Mirrored under
// test/lib-core.test.js for vitest. No subprocess spawned here.
const fs = require("fs");
const os = require("os");
const path = require("path");
const { mapExit } = require("../lib/exec");
const { resolveBinary } = require("../lib/resolve-binary");
const {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
} = require("../lib/errors");

describe("error class hierarchy", () => {
  test("every error extends ClauditorError and sets .name", () => {
    for (const Cls of [
      ClauditorNotFoundError,
      ClauditorInputError,
      ClauditorApiError,
    ]) {
      const e = new Cls("boom");
      expect(e).toBeInstanceOf(ClauditorError);
      expect(e).toBeInstanceOf(Error);
      expect(e.name).toBe(Cls.name);
      expect(e.message).toBe("boom");
    }
    expect(new ClauditorError("x").name).toBe("ClauditorError");
  });
});

describe("mapExit (pure exit-code mapper)", () => {
  test("exit 0 returns parsed JSON", () => {
    const out = mapExit(0, '{"passed": true, "pass_rate": 1.0}', "");
    expect(out).toEqual({ passed: true, pass_rate: 1.0 });
  });

  test("exit 1 returns parsed JSON (failing eval is data, not error)", () => {
    const out = mapExit(1, '{"passed": false, "pass_rate": 0.5}', "");
    expect(out).toEqual({ passed: false, pass_rate: 0.5 });
  });

  test("exit 2 throws ClauditorInputError with stderr text", () => {
    expect(() => mapExit(2, "", "bad spec: missing id")).toThrow(
      ClauditorInputError
    );
    try {
      mapExit(2, "", "bad spec: missing id");
    } catch (e) {
      expect(e.message).toContain("bad spec: missing id");
    }
  });

  test("exit 3 throws ClauditorApiError with stderr text", () => {
    expect(() => mapExit(3, "", "rate limited")).toThrow(ClauditorApiError);
    try {
      mapExit(3, "", "rate limited");
    } catch (e) {
      expect(e.message).toContain("rate limited");
    }
  });

  test("exit 7 (unexpected) throws ClauditorError", () => {
    expect(() => mapExit(7, "", "weird")).toThrow(ClauditorError);
    try {
      mapExit(7, "", "weird");
    } catch (e) {
      expect(e).not.toBeInstanceOf(ClauditorInputError);
      expect(e).not.toBeInstanceOf(ClauditorApiError);
      expect(e.message).toContain("7");
    }
  });

  test("non-JSON stdout on exit 0 throws ClauditorError with snippet", () => {
    expect(() => mapExit(0, "not json at all", "")).toThrow(ClauditorError);
    try {
      mapExit(0, "not json at all", "");
    } catch (e) {
      expect(e.message).toContain("non-JSON");
      expect(e.message).toContain("not json at all");
    }
  });
});

describe("resolveBinary", () => {
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

  test("honors CLAUDITOR_BIN override", () => {
    process.env.CLAUDITOR_BIN = "/opt/custom/clauditor";
    expect(resolveBinary()).toEqual({
      command: "/opt/custom/clauditor",
      argsPrefix: [],
    });
  });

  test("throws ClauditorNotFoundError with install hint when nothing resolves", () => {
    delete process.env.CLAUDITOR_BIN;
    process.env.PATH = "";
    expect(() => resolveBinary()).toThrow(ClauditorNotFoundError);
    try {
      resolveBinary();
    } catch (e) {
      expect(e.message).toContain("pipx install clauditor-eval");
      expect(e.message).toContain("CLAUDITOR_BIN");
    }
  });

  // H2: a `clauditor` on PATH resolves to the FULL path (incl. extension),
  // not the bare name — Windows execFile/spawn must target the exact file.
  test("PATH resolution returns the full resolved path, not the bare name", () => {
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

  // H3: the python fallback fires for `python` (not only `python3`), and
  // returns the full interpreter path + the `-m clauditor` prefix.
  test("python fallback resolves `python` with the -m clauditor prefix", () => {
    delete process.env.CLAUDITOR_BIN;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-py-"));
    const name = process.platform === "win32" ? "python.exe" : "python";
    const full = path.join(dir, name);
    fs.writeFileSync(full, "#!/bin/sh\n", { mode: 0o755 });
    process.env.PATH = dir; // no `clauditor` here — forces the fallback
    try {
      const res = resolveBinary();
      expect(res.command).toBe(full);
      expect(res.argsPrefix).toEqual(["-m", "clauditor"]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  // POSIX: a non-executable file named `clauditor` on PATH must NOT be
  // selected (it would fail at spawn). Skipped on Windows (no exec bit).
  const itPosix = process.platform === "win32" ? test.skip : test;
  itPosix("skips a non-executable PATH match (POSIX)", () => {
    delete process.env.CLAUDITOR_BIN;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-noexec-"));
    fs.writeFileSync(path.join(dir, "clauditor"), "not a binary\n", {
      mode: 0o644,
    });
    process.env.PATH = dir;
    try {
      // No executable `clauditor` and no python here → nothing resolves.
      expect(() => resolveBinary()).toThrow(ClauditorNotFoundError);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  // Recursion guard: a PATH `clauditor` that is really this wrapper's own
  // launcher (npm/bin/clauditor.js) must be skipped, not spawned as the
  // engine. Simulated via a symlink whose realpath is the wrapper bin.
  itPosix("skips a PATH match that resolves to the wrapper's own bin", () => {
    delete process.env.CLAUDITOR_BIN;
    const selfBin = path.join(__dirname, "..", "bin", "clauditor.js");
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clauditor-self-"));
    fs.symlinkSync(selfBin, path.join(dir, "clauditor"));
    process.env.PATH = dir;
    try {
      // The only `clauditor` on PATH is the wrapper itself → must NOT
      // resolve to it (would infinite-loop); nothing else here resolves.
      expect(() => resolveBinary()).toThrow(ClauditorNotFoundError);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
