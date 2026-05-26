// clauditor-eval — Node.js wrapper for the Python clauditor engine.
//
// This is the public JS API entry point. v1 is a SUBPROCESS BRIDGE: it
// shells out to the `clauditor` Python CLI rather than reimplementing the
// engine in JS. All three async functions build a CLI arg array and route
// it through `lib/exec.execJson`, which resolves the engine binary, spawns
// it with NO shell (DEC-007), and maps the exit code to outcome/error per
// the Python exit-code taxonomy (DEC-008).
//
// Public surface (US-004):
//   - runSkill(skill, opts)     -> `clauditor run <skill> --json` (DEC-004)
//   - validate(skillPath, opts) -> `clauditor validate <skillPath> --json`
//   - loadSpec(target)          -> discover + read sibling eval (DEC-012)
// plus re-exported error classes from lib/errors.

const fs = require("fs");
const path = require("path");

const { execJson } = require("./lib/exec");
const {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
} = require("./lib/errors");

// Grace added to the JS-side exec timeout over the engine's own --timeout
// (both in the same wall-clock window otherwise). The engine catches its
// OWN timeout and reports it as structured data (run --json exit 0 with
// error_category="timeout"; validate --json exit 1 with passed:false). If
// the JS execFile kill fired at the same instant it would instead surface
// as a thrown ClauditorError ("killed (timeout?)"). The grace lets the
// engine's watchdog always win; the JS timeout is only a hard backstop for
// a genuinely hung engine.
const _EXEC_TIMEOUT_GRACE_MS = 5000;

/**
 * Run a skill via `clauditor run <skill> --json` and return the parsed
 * SkillResult-shaped object (DEC-004).
 *
 * NOTE: v1 does NOT return `entries`. The `run --json` payload is the raw
 * skill execution result; per-field L2 `entries` require an eval spec and
 * the L2 extraction pass, which is the `validate()` / `extract` path.
 *
 * @param {string} skill - Skill name or path passed to `clauditor run`.
 * @param {object} [opts]
 * @param {string} [opts.args]       - Forwarded as `--args <value>`.
 * @param {string} [opts.projectDir] - Forwarded as `--project-dir <value>`.
 * @param {number} [opts.timeout]    - Engine timeout in SECONDS. Forwarded as
 *   `--timeout <value>` AND used as the execJson exec timeout (timeout * 1000 ms).
 * @returns {Promise<object>} Parsed `run --json` object:
 *   `{output, exit_code, duration_seconds, error, error_category, warnings,
 *     input_tokens, output_tokens, harness, skill, args}`.
 */
async function runSkill(skill, opts = {}) {
  const options = opts || {};
  const cliArgs = ["run", skill, "--json"];

  if (options.args !== undefined) {
    cliArgs.push("--args", String(options.args));
  }
  if (options.projectDir !== undefined) {
    cliArgs.push("--project-dir", String(options.projectDir));
  }

  const execOpts = {};
  if (typeof options.timeout === "number") {
    // The engine's --timeout is in SECONDS; the execJson timeout is in ms,
    // with a grace margin so the engine's own watchdog wins (see constant).
    cliArgs.push("--timeout", String(options.timeout));
    execOpts.timeout = options.timeout * 1000 + _EXEC_TIMEOUT_GRACE_MS;
  }

  return execJson(cliArgs, execOpts);
}

/**
 * Validate a skill against its eval spec via
 * `clauditor validate <skillPath> --json`.
 *
 * Per DEC-008, a failing eval (Python exit 1) is DATA, not an error:
 * `execJson` returns the parsed object on exit 1, so this resolves with
 * `{passed: false, ...}` rather than throwing. Exit 2 (input validation)
 * and exit 3 (provider API failure) throw via execJson.
 *
 * @param {string} skillPath - Skill path passed to `clauditor validate`.
 * @param {object} [opts]
 * @param {string} [opts.eval]    - Forwarded as `--eval <path>`.
 * @param {number} [opts.timeout] - Engine timeout in SECONDS (also ms exec timeout).
 * @returns {Promise<object>} Parsed `validate --json` object:
 *   `{skill, pass_rate, passed, results: [...]}`.
 */
async function validate(skillPath, opts = {}) {
  const options = opts || {};
  const cliArgs = ["validate", skillPath, "--json"];

  if (options.eval !== undefined) {
    cliArgs.push("--eval", String(options.eval));
  }

  const execOpts = {};
  if (typeof options.timeout === "number") {
    cliArgs.push("--timeout", String(options.timeout));
    execOpts.timeout = options.timeout * 1000 + _EXEC_TIMEOUT_GRACE_MS;
  }

  return execJson(cliArgs, execOpts);
}

/**
 * Discover and read a clauditor eval spec from disk (DEC-012).
 *
 * This is a pure-ish file read: it does NOT shell out to the Python engine
 * and does NOT re-validate the spec through the engine's loader (a
 * documented v1 limitation). It only resolves a path and `JSON.parse`s it.
 *
 * Discovery rules:
 *   - If `target` ends in `.json` (e.g. `foo.eval.json` or any `.json`),
 *     it is treated as an explicit eval file: read + parse it directly.
 *   - Otherwise `target` is treated as a skill file path and the sibling
 *     eval is discovered:
 *       - `X.md`        -> look for `X.eval.json` in the same directory.
 *       - `.../SKILL.md` -> look for, in order:
 *           1. `SKILL.eval.json` (engine-canonical sibling, matches
 *              spec.py's skill_path.with_suffix(".eval.json")), then
 *           2. `eval.json` in the same directory, then
 *           3. `<dirname>.eval.json` in the same directory (where
 *              `<dirname>` is the name of the skill's parent directory).
 *
 * @param {string} target - Explicit `.json` eval path OR a skill file path.
 * @returns {Promise<object>} The parsed eval-spec object.
 * @throws {Error} If no eval file is found, or if reading/parsing fails.
 */
async function loadSpec(target) {
  if (typeof target !== "string" || target === "") {
    throw new Error("loadSpec: target must be a non-empty string");
  }

  // Explicit .json path: read it directly.
  if (target.endsWith(".json")) {
    return _readJson(target);
  }

  // Skill file path: discover the sibling eval.
  const dir = path.dirname(target);
  const base = path.basename(target);

  const candidates = [];
  if (base === "SKILL.md") {
    // Modern layout: <skill-dir>/SKILL.md. Lead with the engine-canonical
    // sibling name (spec.py uses skill_path.with_suffix(".eval.json") ->
    // SKILL.eval.json) so loadSpec agrees with what `validate()` loads;
    // keep eval.json / <dir>.eval.json as convenience fallbacks.
    candidates.push(path.join(dir, "SKILL.eval.json"));
    candidates.push(path.join(dir, "eval.json"));
    candidates.push(path.join(dir, `${path.basename(dir)}.eval.json`));
  } else {
    // Legacy / flat layout: X.md -> X.eval.json
    const stem = base.replace(/\.md$/, "");
    candidates.push(path.join(dir, `${stem}.eval.json`));
  }

  for (const candidate of candidates) {
    if (_existsFile(candidate)) {
      return _readJson(candidate);
    }
  }

  throw new Error(
    `loadSpec: no eval spec found for ${r(target)} — looked for: ` +
      candidates.join(", ")
  );
}

// Format a value for an error message.
function r(value) {
  return JSON.stringify(value);
}

function _existsFile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function _readJson(p) {
  let text;
  try {
    text = fs.readFileSync(p, "utf8");
  } catch (err) {
    throw new Error(`loadSpec: could not read ${r(p)}: ${err.message}`);
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(`loadSpec: invalid JSON in ${r(p)}: ${err.message}`);
  }
}

module.exports = {
  runSkill,
  validate,
  loadSpec,
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
};
