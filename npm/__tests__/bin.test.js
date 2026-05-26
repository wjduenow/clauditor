// bin/clauditor.js launcher tests (jest runner). Drives ``run`` with
// injectable fake spawn / resolve / stderr / exit so no real process is
// launched. The pure arg-assembly helper is also mirrored under
// test/bin.test.js for vitest.
const { EventEmitter } = require("events");

const { run, buildChildArgs } = require("../bin/clauditor");
const { ClauditorNotFoundError } = require("../lib/errors");

// A fake child the test can drive by emitting ``exit`` / ``error``.
function makeFakeChild() {
  return new EventEmitter();
}

// Build a deps bundle capturing spawn args + exit code + stderr writes.
function makeDeps(resolution, { throwNotFound = false } = {}) {
  const captured = { spawnArgs: null, exitCode: undefined, stderr: "" };
  const child = makeFakeChild();
  const deps = {
    spawn: (command, args, opts) => {
      captured.spawnArgs = { command, args, opts };
      return child;
    },
    resolve: () => {
      if (throwNotFound) {
        throw new ClauditorNotFoundError("install hint: pipx install clauditor-eval");
      }
      return resolution;
    },
    stderr: { write: (s) => { captured.stderr += s; } },
    exit: (code) => { captured.exitCode = code; },
  };
  return { deps, child, captured };
}

describe("buildChildArgs", () => {
  test("prepends empty prefix verbatim", () => {
    expect(buildChildArgs({ command: "clauditor", argsPrefix: [] }, ["validate", "foo.md"]))
      .toEqual(["validate", "foo.md"]);
  });

  test("prepends python module fallback prefix", () => {
    expect(
      buildChildArgs(
        { command: "python3", argsPrefix: ["-m", "clauditor"] },
        ["run", "foo.md", "--json"],
      ),
    ).toEqual(["-m", "clauditor", "run", "foo.md", "--json"]);
  });

  test("preserves args with spaces verbatim", () => {
    expect(buildChildArgs({ command: "clauditor", argsPrefix: [] }, ["run", "a b c"]))
      .toEqual(["run", "a b c"]);
  });
});

describe("run argv forwarding", () => {
  test("forwards argv to resolved binary as an array, no shell", () => {
    const { deps, captured } = makeDeps({ command: "clauditor", argsPrefix: [] });
    run(["validate", "foo.md", "--json"], deps);

    expect(captured.spawnArgs.command).toBe("clauditor");
    expect(captured.spawnArgs.args).toEqual(["validate", "foo.md", "--json"]);
    // No shell: true — argument array only (DEC-007).
    expect(captured.spawnArgs.opts.shell).toBeUndefined();
    expect(captured.spawnArgs.opts.stdio).toBe("inherit");
    expect(captured.spawnArgs.opts.env).toBe(process.env);
  });

  test("prepends argsPrefix for the python -m fallback", () => {
    const { deps, captured } = makeDeps({
      command: "python3",
      argsPrefix: ["-m", "clauditor"],
    });
    run(["run", "foo.md", "--json"], deps);

    expect(captured.spawnArgs.command).toBe("python3");
    expect(captured.spawnArgs.args).toEqual(["-m", "clauditor", "run", "foo.md", "--json"]);
  });

  test("preserves an arg containing spaces", () => {
    const { deps, captured } = makeDeps({ command: "clauditor", argsPrefix: [] });
    run(["run", "skill with spaces.md"], deps);
    expect(captured.spawnArgs.args).toEqual(["run", "skill with spaces.md"]);
  });
});

describe("run exit-code propagation", () => {
  test.each([0, 1, 2, 3])("child exit %i → process exit %i", (code) => {
    const { deps, child, captured } = makeDeps({ command: "clauditor", argsPrefix: [] });
    run([], deps);
    child.emit("exit", code, null);
    expect(captured.exitCode).toBe(code);
  });

  test("signal-kill (code null) → nonzero exit + stderr note", () => {
    const { deps, child, captured } = makeDeps({ command: "clauditor", argsPrefix: [] });
    run([], deps);
    child.emit("exit", null, "SIGKILL");
    expect(captured.exitCode).toBe(1);
    expect(captured.stderr).toContain("SIGKILL");
  });

  test("spawn error → exit 1 + stderr note", () => {
    const { deps, child, captured } = makeDeps({ command: "clauditor", argsPrefix: [] });
    run([], deps);
    child.emit("error", new Error("ENOENT"));
    expect(captured.exitCode).toBe(1);
    expect(captured.stderr).toContain("ENOENT");
  });
});

describe("run missing-engine handling", () => {
  test("ClauditorNotFoundError → install hint to stderr + exit 2", () => {
    const { deps, captured } = makeDeps(null, { throwNotFound: true });
    run(["validate", "foo.md"], deps);
    expect(captured.exitCode).toBe(2);
    expect(captured.stderr).toContain("install hint");
    // Did NOT spawn.
    expect(captured.spawnArgs).toBeNull();
  });

  test("non-NotFound resolve error propagates", () => {
    const deps = {
      resolve: () => { throw new TypeError("boom"); },
      spawn: () => { throw new Error("should not spawn"); },
      stderr: { write: () => {} },
      exit: () => {},
    };
    expect(() => run([], deps)).toThrow(TypeError);
  });
});
