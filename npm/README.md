# clauditor-eval

Node.js wrapper for [clauditor](https://github.com/wjduenow/clauditor) — an
auditor for Claude Code skills and slash commands. Run skills, validate them
against an eval spec, and assert the result from Jest or Vitest.

## How it works: a subprocess bridge

`clauditor-eval` is a **subprocess bridge**. It does NOT reimplement
clauditor's evaluation layers in JavaScript and it does NOT bundle Python —
it shells out to the Python `clauditor` engine and parses its `--json`
output. The Python engine **must be installed separately**.

### Prerequisite: install the Python engine

Install the engine with any of:

```sh
pipx install clauditor-eval        # recommended — isolated, on PATH
uv tool install clauditor-eval     # if you use uv
pip install clauditor-eval         # into the active environment
```

`clauditor-eval` resolves the **Python engine** in this order:

1. `CLAUDITOR_BIN` — an explicit path to the engine binary (wins).
2. `clauditor` on `PATH` — this is the **Python engine's** console script
   (installed by `pipx install clauditor-eval`), NOT this npm wrapper. The
   wrapper installs its own command as `clauditor-eval`, so the scan can
   never resolve to itself.
3. `python -m clauditor` — best-effort fallback, probing `python3`, then
   `python`, then (on Windows) the `py` launcher.

If none resolve, calls throw `ClauditorNotFoundError` with an install hint.

Note: on Windows the PATH scan only accepts a spawnable `clauditor.exe`;
`.cmd`/`.bat` shims are skipped (they can't be launched without a shell), so
point `CLAUDITOR_BIN` at the real interpreter for those installs.

## Install

```sh
npm install --save-dev clauditor-eval
```

Requires Node.js `>=18`.

## API

```js
const { runSkill, validate, loadSpec } = require("clauditor-eval");
```

### `runSkill(skill, opts)`

Runs `clauditor run <skill> --json` and resolves to the parsed SkillResult:

```js
const result = await runSkill("my-skill", {
  args: '"San Jose, CA"',
  projectDir: ".",
  timeout: 120, // SECONDS
});
// result === {
//   output, exit_code, duration_seconds, error, error_category,
//   warnings, input_tokens, output_tokens, harness, skill, args
// }
```

Options:

- `args` — forwarded as `--args <value>`.
- `projectDir` — forwarded as `--project-dir <value>`.
- `timeout` — engine timeout in **seconds** (also bounds the subprocess).

**v1 limitation:** `runSkill` does NOT return an `entries` field. Per-field
Layer 2 `entries` require an eval spec and the L2 extraction pass — use
`validate()` for per-criterion assertion results (DEC-004).

### `validate(skillPath, opts)`

Runs `clauditor validate <skillPath> --json` and resolves to the eval report:

```js
const report = await validate(".claude/skills/my-skill/SKILL.md", {
  eval: "my-skill.eval.json",
  timeout: 180,
});
// report === {
//   passed, pass_rate,
//   results: [{ name, passed, message, ... }, ...]
// }
```

Options:

- `eval` — forwarded as `--eval <path>`.
- `timeout` — engine timeout in **seconds**.

**A failing eval resolves, it does not throw.** A failing eval is the Python
engine's exit code 1, which `clauditor-eval` treats as DATA: `validate()`
resolves with `{ passed: false, ... }`. Only exit 2 (input validation) and
exit 3 (provider API failure) throw (see Errors below).

### `loadSpec(target)`

Reads and parses an eval spec from disk and resolves to the parsed object:

```js
const spec = await loadSpec(".claude/skills/my-skill/SKILL.md");
const explicit = await loadSpec("my-skill.eval.json");
```

Discovery rules:

- A `target` ending in `.json` is read directly as the eval file.
- `X.md` → looks for sibling `X.eval.json`.
- `.../SKILL.md` → looks for `eval.json`, then `<dirname>.eval.json`, in the
  skill's directory.

**v1 limitation:** `loadSpec` only reads and `JSON.parse`s the file. It does
NOT re-validate the spec through the Python engine's loader (DEC-012).

## Errors

All errors extend `ClauditorError`, so one `catch` can cover the family:

- `ClauditorNotFoundError` — the Python engine could not be resolved.
- `ClauditorInputError` — Python exit code 2 (input validation failure).
- `ClauditorApiError` — Python exit code 3 (provider API failure).

```js
const {
  ClauditorError,
  ClauditorNotFoundError,
  ClauditorInputError,
  ClauditorApiError,
} = require("clauditor-eval");

try {
  await validate(".claude/skills/my-skill/SKILL.md");
} catch (err) {
  if (err instanceof ClauditorNotFoundError) {
    console.error("Install the engine: pipx install clauditor-eval");
  } else if (err instanceof ClauditorInputError) {
    console.error("Bad input:", err.message);
  } else if (err instanceof ClauditorApiError) {
    console.error("Provider API failure — retry later:", err.message);
  } else if (err instanceof ClauditorError) {
    console.error("clauditor failed:", err.message);
  } else {
    throw err;
  }
}
```

## Jest / Vitest matcher

`clauditor-eval/jest-helper` exports a custom `toPassClauditor` matcher that
works under both Jest and Vitest (both await async matchers).

Register it once and assert against a `validate()` result:

```js
const { validate } = require("clauditor-eval");
const { toPassClauditor } = require("clauditor-eval/jest-helper");
expect.extend({ toPassClauditor });

test("skill passes eval", async () => {
  await expect(
    await validate(".claude/skills/my-skill/SKILL.md")
  ).toPassClauditor();
});
```

You can also pass a `runSkill()` result plus the eval path — the matcher
calls `validate(evalPath)` to obtain the verdict:

```js
const { runSkill } = require("clauditor-eval");

test("run then judge", async () => {
  const result = await runSkill("my-skill", { args: '"San Jose, CA"' });
  await expect(result).toPassClauditor(".claude/skills/my-skill/SKILL.md");
});
```

Negate with `.not.toPassClauditor()`. On failure the matcher message lists
the names of the criteria that did not pass.

### Vitest

The matcher is the same; import `expect` from `vitest`:

```js
import { expect, test } from "vitest";
import { validate } from "clauditor-eval";
import { toPassClauditor } from "clauditor-eval/jest-helper";

expect.extend({ toPassClauditor });

test("skill passes eval", async () => {
  await expect(
    await validate(".claude/skills/my-skill/SKILL.md")
  ).toPassClauditor();
});
```

## `CLAUDITOR_BIN`

Set `CLAUDITOR_BIN` to point at a specific engine binary, bypassing PATH
resolution entirely:

```sh
export CLAUDITOR_BIN=/opt/pipx/venvs/clauditor-eval/bin/clauditor
```

This wins over `clauditor` on `PATH` and the `python3 -m clauditor` fallback.

## v1 limitations and roadmap

- **Subprocess bridge today.** The Python engine is a hard prerequisite;
  this package does not bundle Python.
- **Standalone binaries are planned.** Zero-Python-install distribution via
  PyInstaller binaries is a planned follow-up (DEC-013). The npm scope
  `@clauditor-eval/*` is reserved for that per-platform binary epic and is
  intentionally unused in v1 — no `optionalDependencies`, no platform
  packages (DEC-002 / DEC-003 / DEC-013).
- **No `entries` from `runSkill`.** See the `runSkill` note above (DEC-004).
- **`loadSpec` does not re-validate.** See the `loadSpec` note above
  (DEC-012).

## Versioning

The npm package version is **`0.1.0`** — a clean semver chosen for the
initial npm release. The Python engine versions independently
(`pyproject.toml [project].version`); the two are not pinned 1:1. The
unscoped npm name `clauditor` was already taken by an unrelated owner, so
this package uses `clauditor-eval`, matching the PyPI package name (DEC-001).

## License

Apache-2.0. See [LICENSE](./LICENSE).
