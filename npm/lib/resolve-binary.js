// Resolve the Python `clauditor` engine binary for the subprocess bridge.
//
// Per DEC-005 of plans/super/4-npm-wrapper.md, resolution order is:
//   1. process.env.CLAUDITOR_BIN — explicit operator override (wins).
//   2. `clauditor` on PATH — a pure fs scan of each PATH entry (no spawn).
//   3. `python3 -m clauditor` — best-effort fallback when python3 is on PATH.
//   4. throw ClauditorNotFoundError with an actionable install hint.
//
// The return shape is normalized so callers can uniformly prepend a prefix
// to the engine's CLI args:
//
//   { command: string, argsPrefix: string[] }
//
// Callers spawn `command` with `[...argsPrefix, ...cliArgs]`. For the binary
// path (cases 1 and 2) argsPrefix is empty; for the python module fallback
// (case 3) argsPrefix is `["-m", "clauditor"]`.

const fs = require("fs");
const path = require("path");

const { ClauditorNotFoundError } = require("./errors");

const _NOT_FOUND_HINT =
  "clauditor engine not found. Install it with: " +
  "pipx install clauditor-eval  (or: uv tool install clauditor-eval), " +
  "or set CLAUDITOR_BIN to the binary path.";

// Windows: only consider extensions we can actually spawn WITHOUT a shell.
// `.exe` is spawnable via execFile; `.cmd`/`.bat` are NOT (Node refuses them
// without `shell: true` since CVE-2024-27980), so we deliberately do NOT
// return them — a `.cmd`/`.bat`-only install must point CLAUDITOR_BIN at the
// real interpreter. The bare name is kept as a last resort.
function _candidateNames(base) {
  if (process.platform === "win32") {
    return [base + ".exe", base];
  }
  return [base];
}

// Realpath of this wrapper's OWN launcher (npm/bin/clauditor.js). Used to
// skip self-matches during the PATH scan: belt-and-suspenders against the
// infinite-recursion trap where the wrapper resolves its own installed bin
// shim instead of the Python engine. (The primary defense is naming the
// installed command `clauditor-eval` in package.json, so a scan for the
// engine's `clauditor` cannot match it; this guard covers symlink/rename
// edge cases too.) Computed once; null if it can't be resolved.
const _SELF_BIN = (() => {
  try {
    return fs.realpathSync(path.join(__dirname, "..", "bin", "clauditor.js"));
  } catch {
    return null;
  }
})();

// True when `full` is executable. On POSIX we require the execute bit so a
// non-exec data file named `clauditor` earlier on PATH doesn't shadow the
// real binary and then fail at spawn. On Windows, file presence is the
// signal (the exec bit has no POSIX meaning there).
function _isExecutable(full) {
  if (process.platform === "win32") {
    return true;
  }
  try {
    fs.accessSync(full, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Pure fs PATH scan: return the FULL resolved path (including any platform
// extension) of `name` on PATH, or null if not found. Avoids spawning
// `which`/`where`. Returning the full path — rather than the bare name —
// matters on Windows: `execFile`/`spawn` targets the exact file. Skips
// non-executable files (POSIX) and any path that resolves to this wrapper's
// own launcher (recursion guard).
function _findOnPath(name) {
  const pathEnv = process.env.PATH || "";
  if (pathEnv === "") {
    return null;
  }
  const entries = pathEnv.split(path.delimiter);
  for (const dir of entries) {
    if (dir === "") {
      continue;
    }
    for (const candidate of _candidateNames(name)) {
      const full = path.join(dir, candidate);
      try {
        const stat = fs.statSync(full);
        if (!stat.isFile() || !_isExecutable(full)) {
          continue;
        }
        // Skip a match that is really this wrapper's own launcher.
        if (_SELF_BIN !== null) {
          let real;
          try {
            real = fs.realpathSync(full);
          } catch {
            real = full;
          }
          if (real === _SELF_BIN) {
            continue;
          }
        }
        return full;
      } catch {
        // Not present / not readable in this dir — keep scanning.
        continue;
      }
    }
  }
  return null;
}

// Interpreter names to probe for the `python -m clauditor` fallback, in
// order. Windows installs almost always expose `python` (not `python3`),
// and the `py` launcher as a last resort.
function _pythonCandidates() {
  if (process.platform === "win32") {
    return ["python", "python3", "py"];
  }
  return ["python3", "python"];
}

// Resolve the engine binary per the DEC-005 precedence order.
//
// Returns { command, argsPrefix }. Throws ClauditorNotFoundError when no
// resolution path succeeds.
function resolveBinary() {
  // 1. Explicit override.
  const override = process.env.CLAUDITOR_BIN;
  if (typeof override === "string" && override.trim() !== "") {
    return { command: override, argsPrefix: [] };
  }

  // 2. `clauditor` on PATH — return the full resolved path.
  const clauditorPath = _findOnPath("clauditor");
  if (clauditorPath !== null) {
    return { command: clauditorPath, argsPrefix: [] };
  }

  // 3. `python -m clauditor` best-effort fallback (probe python3/python/py).
  for (const interpreter of _pythonCandidates()) {
    const interpreterPath = _findOnPath(interpreter);
    if (interpreterPath !== null) {
      return { command: interpreterPath, argsPrefix: ["-m", "clauditor"] };
    }
  }

  // 4. Nothing resolved.
  throw new ClauditorNotFoundError(_NOT_FOUND_HINT);
}

module.exports = { resolveBinary };
