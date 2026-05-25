// Run the Python clauditor engine and map its result for JS callers.
//
// `execJson(args, opts)` resolves the engine binary, spawns it with an
// ARGUMENT ARRAY (never a shell string), collects stdout/stderr, and routes
// the exit code through the pure `mapExit` helper per DEC-008 of
// plans/super/4-npm-wrapper.md (mirroring the Python exit-code taxonomy in
// .claude/rules/llm-cli-exit-code-taxonomy.md):
//
//   exit 0 → resolve with parsed JSON (success).
//   exit 1 → resolve with parsed JSON (a failing eval is DATA, not an error;
//            the caller inspects `passed`).
//   exit 2 → throw ClauditorInputError  (input validation failure).
//   exit 3 → throw ClauditorApiError    (provider API failure).
//   other  → throw ClauditorError.
//
// Security (DEC-007): the child is spawned via `execFile` with an arg array
// and NO shell, so skill names / args cannot inject shell metacharacters.
// `process.env` is inherited so ANTHROPIC_API_KEY / OPENAI_API_KEY flow to
// the child — secrets travel via env, never argv, and argv is never logged.

const { execFile } = require("child_process");
const { promisify } = require("util");

const { resolveBinary } = require("./resolve-binary");
const {
  ClauditorError,
  ClauditorInputError,
  ClauditorApiError,
} = require("./errors");

const _execFileAsync = promisify(execFile);

// Defensively parse `stdout` as JSON. Throws ClauditorError with a clear
// message (including a bounded snippet) when stdout is not valid JSON.
function _parseJson(stdout) {
  try {
    return JSON.parse(stdout);
  } catch (err) {
    const snippet = String(stdout).slice(0, 500);
    throw new ClauditorError(
      "clauditor engine returned non-JSON output on stdout: " +
        `${err.message}; output snippet: ${JSON.stringify(snippet)}`
    );
  }
}

// Pure exit-code → outcome/error mapper (DEC-008). Separated from the I/O
// spawn per .claude/rules/pure-compute-vs-io-split.md so it is unit-testable
// without spawning a subprocess.
//
//   - code 0 / 1: parse stdout as JSON and RETURN the parsed object. Exit 1
//     is a failing eval (data), not an exception; the caller reads `passed`.
//   - code 2: throw ClauditorInputError (stderr text included).
//   - code 3: throw ClauditorApiError (stderr text included).
//   - any other code: throw ClauditorError.
//
// A non-JSON stdout on exit 0/1 surfaces as ClauditorError via _parseJson.
function mapExit(code, stdout, stderr) {
  if (code === 0 || code === 1) {
    return _parseJson(stdout);
  }
  const detail = String(stderr || "").trim();
  if (code === 2) {
    throw new ClauditorInputError(
      detail !== "" ? detail : "clauditor input validation failed (exit 2)"
    );
  }
  if (code === 3) {
    throw new ClauditorApiError(
      detail !== "" ? detail : "clauditor provider API call failed (exit 3)"
    );
  }
  throw new ClauditorError(
    `clauditor exited with unexpected code ${code}` +
      (detail !== "" ? `: ${detail}` : "")
  );
}

// Run the engine with `args` (an array) and return the parsed JSON result.
//
// opts:
//   - timeout: milliseconds; forwarded to execFile's `timeout`.
//
// Resolves with the parsed JSON object on exit 0/1; rejects with a
// Clauditor* error on exit 2/3/other or on a non-JSON exit-0/1 payload.
async function execJson(args, opts) {
  const options = opts || {};
  const { command, argsPrefix } = resolveBinary();
  const fullArgs = [...argsPrefix, ...args];

  // execFile options: inherit env (so API keys flow to the child), large
  // buffer for transcript-heavy JSON, optional timeout. NO `shell` — the
  // arg array is passed literally so special characters cannot inject.
  const execOptions = {
    env: process.env,
    maxBuffer: 64 * 1024 * 1024,
  };
  if (typeof options.timeout === "number") {
    execOptions.timeout = options.timeout;
  }

  let stdout;
  let stderr;
  let code = 0;
  try {
    const result = await _execFileAsync(command, fullArgs, execOptions);
    stdout = result.stdout;
    stderr = result.stderr;
  } catch (err) {
    // A non-zero exit rejects with an Error carrying `code`, `stdout`,
    // `stderr`. A killed-by-timeout / spawn failure carries `killed` /
    // `code === null`. Route the exit code through mapExit; surface
    // spawn/timeout failures as ClauditorError.
    if (typeof err.code === "number") {
      code = err.code;
      stdout = err.stdout != null ? err.stdout : "";
      stderr = err.stderr != null ? err.stderr : "";
    } else {
      // No numeric exit code: spawn failure (ENOENT), timeout kill, etc.
      const detail = err.killed
        ? `clauditor process was killed (timeout?): ${err.message}`
        : `failed to run clauditor engine: ${err.message}`;
      throw new ClauditorError(detail);
    }
  }

  return mapExit(code, stdout, stderr);
}

module.exports = { execJson, mapExit };
