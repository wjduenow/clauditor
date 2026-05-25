// clauditor-eval — Node.js wrapper for the Python clauditor engine.
//
// This is the public JS API entry point. v1 is a SUBPROCESS BRIDGE: it
// shells out to the `clauditor` Python CLI rather than reimplementing the
// engine in JS.
//
// US-004 fills this stub with the public async API:
//   - runSkill(skill, opts)        -> run --json, parsed SkillResult
//   - validate(skillPath, opts)    -> validate --json {passed, pass_rate, results}
//   - loadSpec(path | skillPath)   -> discover + read sibling <skill>.eval.json
// plus re-exported error classes (from lib/, landed in US-003).
//
// Skeleton story (US-002): export an empty object so the package requires
// cleanly and `npm test` is green.
module.exports = {};
