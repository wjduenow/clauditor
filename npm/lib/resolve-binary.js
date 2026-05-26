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

// On Windows, PATHEXT-style executables need the extension. We keep this
// minimal: try the bare name and `.exe`. (Cross-platform without spawning.)
function _candidateNames(base) {
  if (process.platform === "win32") {
    return [base + ".exe", base + ".cmd", base + ".bat", base];
  }
  return [base];
}

// Pure fs PATH scan: return the FULL resolved path (including any platform
// extension) of `name` on PATH, or null if not found. Avoids spawning
// `which`/`where`. Returning the full path — rather than the bare name —
// matters on Windows: `child_process.execFile`/`spawn` targets the exact
// file, and the `.exe` candidate is tried before `.cmd`/`.bat` so the
// spawnable executable wins. (Node refuses to spawn `.cmd`/`.bat` without
// `shell: true` since CVE-2024-27980; for those rare shim-only installs the
// operator should point CLAUDITOR_BIN at the real interpreter.)
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
        if (stat.isFile()) {
          return full;
        }
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
