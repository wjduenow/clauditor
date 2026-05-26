// clauditor-eval — custom matcher `toPassClauditor` for Jest and Vitest.
//
// Register it with either runner's `expect.extend`:
//
//   const { toPassClauditor } = require("clauditor-eval/jest-helper");
//   expect.extend({ toPassClauditor });
//
// Then assert (async — always `await`):
//
//   await expect(validateResult).toPassClauditor();
//   await expect(runSkillResult).toPassClauditor("path/to/SKILL.md");
//   await expect(failingResult).not.toPassClauditor();
//
// Both Jest and Vitest support async matchers (a matcher returning a
// Promise is awaited), so a single implementation works under both via
// `expect.extend({ toPassClauditor })`. CommonJS to match the package's
// `main`/`bin`/`lib` style.

const { validate } = require("./index");

/**
 * Detect whether `received` is already a `validate()` eval result.
 *
 * Detection rule: an eval result is a non-null object that carries the
 * `results` array (the per-criterion list) OR the `pass_rate` field —
 * both are produced by `validate --json` and absent from a `runSkill`
 * (`run --json`) payload, which instead carries `output` / `exit_code`.
 * If neither eval field is present we treat `received` as a runSkill
 * result and require an eval path to derive the verdict.
 */
function isValidateResult(received) {
  return (
    received !== null &&
    typeof received === "object" &&
    (Array.isArray(received.results) || typeof received.pass_rate === "number")
  );
}

/**
 * Custom matcher: assert a clauditor eval passed.
 *
 * `received` is EITHER a `validate()` result (used directly) OR a
 * `runSkill()` result (in which case `evalPath` is REQUIRED and the
 * matcher calls `validate(evalPath)` to obtain the eval verdict).
 *
 * Returns a Promise (async matcher) resolving to the standard
 * `{ pass, message }` matcher result. On failure, `message()` enumerates
 * the names of the criteria that did not pass so the developer sees
 * exactly what failed. `.not` is honored via `this.isNot`.
 *
 * @param {object} received - validate() result OR runSkill() result.
 * @param {string} [evalPath] - skill/eval path; REQUIRED when `received`
 *   is a runSkill() result.
 * @returns {Promise<{pass: boolean, message: () => string}>}
 */
async function toPassClauditor(received, evalPath) {
  const isNot = this && this.isNot;

  let evalResult;
  if (isValidateResult(received)) {
    // Already an eval result — judge it directly.
    evalResult = received;
  } else {
    // A runSkill() result (or anything without eval fields): we need a
    // path to validate against.
    if (typeof evalPath !== "string" || evalPath === "") {
      return {
        pass: false,
        message: () =>
          "toPassClauditor: received a value without `results`/`pass_rate` " +
          "(looks like a runSkill() result), so an eval path argument is " +
          "REQUIRED — call `expect(result).toPassClauditor(evalPath)`.",
      };
    }
    evalResult = await validate(evalPath);
  }

  const pass = evalResult.passed === true;

  // Names of failing criteria for the failure message.
  const failingNames = Array.isArray(evalResult.results)
    ? evalResult.results
        .filter((r) => r && typeof r === "object" && r.passed === false)
        .map((r) => (typeof r.name === "string" ? r.name : "(unnamed)"))
    : [];

  const message = () => {
    if (isNot) {
      // `.not.toPassClauditor()` — this message shows only when the
      // negated assertion fails (i.e. the eval DID pass).
      return "Expected clauditor eval NOT to pass, but it passed.";
    }
    if (failingNames.length > 0) {
      return (
        "Expected clauditor eval to pass, but it failed. " +
        `Failing criteria: ${failingNames.join(", ")}.`
      );
    }
    return "Expected clauditor eval to pass, but it failed.";
  };

  return { pass, message };
}

module.exports = { toPassClauditor };
