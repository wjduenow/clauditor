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

// Pure fs PATH scan: return true if `name` is found as a file on PATH.
// Avoids spawning `which`/`where` (which would be a subprocess per resolve).
function _isOnPath(name) {
  const pathEnv = process.env.PATH || "";
  if (pathEnv === "") {
    return false;
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
          return true;
        }
      } catch {
        // Not present / not readable in this dir — keep scanning.
        continue;
      }
    }
  }
  return false;
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

  // 2. `clauditor` on PATH.
  if (_isOnPath("clauditor")) {
    return { command: "clauditor", argsPrefix: [] };
  }

  // 3. `python3 -m clauditor` best-effort fallback.
  if (_isOnPath("python3")) {
    return { command: "python3", argsPrefix: ["-m", "clauditor"] };
  }

  // 4. Nothing resolved.
  throw new ClauditorNotFoundError(_NOT_FOUND_HINT);
}

module.exports = { resolveBinary };
