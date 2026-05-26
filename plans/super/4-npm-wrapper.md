# Super Plan: #4 — npm package: Node.js wrapper with Jest/Vitest helpers

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/4
- **Branch:** `feature/4-npm-wrapper`
- **Worktree:** in-place (`/home/wesd/Projects/clauditor`)
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/199
- **Beads epic:** `clauditor-ovml` (tasks `.1`–`.9`)
- **Follow-up epic (deferred binary distribution):** https://github.com/wjduenow/clauditor/issues/200
- **Sessions:** 1
- **Last session:** 2026-05-25

> **Scope note:** This plan implements a **v1 subprocess-bridge** npm
> wrapper (JS API + jest/vitest helpers) plus the one Python-side
> change needed to back it (`run --json`). PyInstaller binary
> distribution (the issue's "esbuild pattern") is **explicitly
> deferred** to a sequenced follow-up epic — see DEC-002 / DEC-003.

---

## Discovery

### Ticket Summary

**What:** Ship an npm-installable wrapper so Node.js developers can
use clauditor in their Jest/Vitest test suites — a JS API
(`runSkill`, `validate`, `loadSpec`), a `toPassClauditor` custom
matcher, and a CLI launcher (`npx`).

**Why:** The clauditor engine is Python (`pip install
clauditor-eval`). Node.js teams testing Claude Code skills today have
no first-class entry point; they'd shell out by hand and parse output
ad hoc.

**Who benefits:** Node.js / TypeScript teams with Jest or Vitest
suites that want to assert on skill output and eval-spec pass/fail.

**Done when:** `npm install clauditor-eval` exposes a working JS API
and jest matcher that drive the Python CLI via subprocess, with a
clear actionable error when the Python engine is not installed; README
documents Node usage; an npm-publish CI workflow exists.

### Critical findings that correct the issue's assumptions

The issue was written early and carries **stale assumptions** that
this plan corrects:

| Issue assumes | Reality (verified) | Consequence |
|---------------|--------------------|-------------|
| npm name `clauditor` | **Taken** — npm `clauditor@1.0.0`, unrelated owner | Must rename → `clauditor-eval` (DEC-001) |
| PyPI `clauditor` @ `0.1.0` | PyPI is **`clauditor-eval`** @ `0.1.3.dev0` | Naming + version references updated |
| All CLI commands support `--json` | `validate`, `extract`, `triggers`, `audit`, `lint`, `grade`, `suggest`, `propose_eval` do; **`run` and `capture` do NOT** | Add `run --json` (DEC-004) |
| `runSkill()` returns `{output, entries}` | No single command returns both; `entries` need an eval spec + L2 extraction | `runSkill` returns SkillResult shape (no entries) in v1; entries documented via `validate`/`extract` (DEC-004) |
| `python -m clauditor` fallback works | No `src/clauditor/__main__.py` exists | Resolver can't rely on `-m`; see DEC-005 |
| Binary wrapper is the recommended approach | clauditor **requires** API keys + network for grading, so "no Python runtime needed" buys far less than for ruff/esbuild; bundling `anthropic`+`openai` yields large/fragile binaries | Subprocess bridge is v1 (DEC-002); binary deferred |

### Codebase Findings

**Engine entry points (Python):**
- Console script: `clauditor = "clauditor.cli:main"` (`pyproject.toml`).
  No `__main__.py`, so `python -m clauditor` is unavailable today.
- Modular CLI under `src/clauditor/cli/*.py`. Commands: `validate`,
  `run`, `capture`, `grade`, `extract`, `triggers`, `compare`,
  `audit`, `trend`, `badge`, `lint`, `init`, `setup`, `suggest`,
  `propose-eval`, `doctor`.
- Exit-code taxonomy (`.claude/rules/llm-cli-exit-code-taxonomy.md`):
  **0** success · **1** load/parse failure · **2** input-validation /
  post-call invariant · **3** Anthropic/OpenAI API failure.

**Existing `--json` shapes (the JS wrapper parses these verbatim):**
- `validate --json` → `{skill, pass_rate, passed, results:[{name,
  passed, message, evidence?, raw_data?}]}`. Returns exit 0 when
  passed, 1 when failed. (`cli/validate.py:358`)
- `extract --json` → `{skill, model, pass_rate, passed, results:[…]}`
  (`cli/extract.py:247`).
- These stdout payloads carry **no `schema_version`** — established
  convention for stdout `--json` (distinct from persisted sidecars
  which MUST carry it per `.claude/rules/json-schema-version.md`).
  See DEC-009.

**`run` today (`cli/run.py`):** resolves harness, runs the skill, and
prints `result.output` (or a rendered error). No `--json`. The
backing `SkillResult` (`runner.py:54`) carries: `output`, `exit_code`,
`skill_name`, `args`, `duration_seconds`, `error`, `error_category`,
`harness`, `input_tokens`, `output_tokens`, `warnings`,
`api_key_source` — the field set for the new `run --json` contract.

**npm / binary state:** greenfield. No `package.json`, no `npm/`
subtree, no PyInstaller config. CI has `ci.yml` (lint + validate-skill
+ pytest matrix) and `publish.yml` (PyPI trusted-publish on GitHub
release). The npm-publish workflow will be **new and separate** — it
must not touch the PyPI path.

**Name availability (verified 2026-05-25):**
- npm `clauditor` → taken (1.0.0). npm `clauditor-eval` → **free**.
  npm scope `@clauditor` / `@clauditor-eval` → **free**.

### Applicable `.claude/rules/`

Most rules are Python-engine-specific and don't bind the `npm/`
subtree, but these shape the design:

1. **`llm-cli-exit-code-taxonomy.md`** — the JS exec layer MUST mirror
   the 0/1/2/3 mapping: 0=pass, 1=fail (returned as data, not thrown),
   2=input error (throw `ClauditorInputError`), 3=API error (throw
   `ClauditorApiError`). DEC-008.
2. **`json-schema-version.md`** — stdout `--json` (run/validate/extract)
   stays *unversioned* to match existing convention; only persisted
   sidecars carry `schema_version`. `run --json` follows the stdout
   convention. DEC-009.
3. **`pure-compute-vs-io-split.md`** — `run --json` reuses the existing
   `SkillResult`→dict composition; the CLI command stays a thin
   serialize-and-print wrapper. The JS side keeps a pure
   exit-code→error mapping helper separate from the I/O `execFile`.
4. **`stream-json-schema.md`** (spirit) — the JS parser treats the
   Python stdout JSON as a tolerated external contract: parse
   defensively, surface a crisp error if stdout isn't valid JSON
   rather than throwing a raw `SyntaxError`.
5. **`precall-env-validation.md`** — when the Python engine exits 2/3
   on missing `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, the JS layer
   surfaces that engine message verbatim (it's already actionable);
   the JS layer does NOT re-implement the env check.

---

## Architecture Review

| Area | Rating | Finding |
|------|--------|---------|
| Security | **concern** | Subprocess spawn must use `execFile`/`spawn` with an **argument array, never a shell string** — skill names/args could otherwise inject. Skill-name safety is already enforced Python-side (`SKILL_NAME_RE`). `CLAUDITOR_BIN` env override runs an arbitrary path: acceptable (user's own env) but documented. Secrets (API keys) flow via **inherited env**, never argv — don't log argv. |
| Performance | **pass** | Skill runs are seconds-to-minutes; one subprocess spawn per call is negligible. API must be **async** (non-blocking) so a multi-minute run doesn't freeze the Node event loop. |
| Data model | **concern** | The `run --json` stdout shape becomes a **cross-language contract**. Document it; JS parses defensively. No `schema_version` (stdout convention). |
| API design | **concern** | JS API is **async** (promisified `execFile`), not `execFileSync` as the issue sketched. `runSkill`/`validate` return data; throw only on input(2)/API(3) errors. Honor a `timeout` option. |
| Observability | **pass** | JS surfaces engine `stderr` + `warnings[]` on errors; no secret logging. |
| Testing | **concern** | Node tests must NOT call the real API. Use a **fake `clauditor` stub** on PATH (a tiny script emitting canned JSON / exit codes) so jest/vitest exercise resolve+exec+mapping deterministically. Python `run --json` covered by `tests/test_cli.py`. |

**Blockers:** none. The two largest forks (naming, distribution) were
resolved in Discovery scoping questions.

---

## Refinement Log (Decisions)

- **DEC-001 — npm name `clauditor-eval`.** Matches the PyPI name
  exactly; `clauditor` is taken on npm. Platform-package scope
  `@clauditor-eval/*` is reserved for the deferred binary epic
  (DEC-013) but unused in v1.
- **DEC-002 — Distribution = subprocess bridge.** The npm package
  resolves and shells out to the Python `clauditor` engine. Chosen
  over PyInstaller because clauditor requires API keys + network to do
  anything useful, so the "no runtime needed" benefit that justifies
  ruff/esbuild's binary pattern is largely absent here, while the
  cross-compile CI + large fragile binaries are the bulk of the cost.
- **DEC-003 — v1 scope = JS API + jest/vitest helpers.** No
  PyInstaller, no platform packages, no `optionalDependencies` in v1.
  The full binary-distribution path is a sequenced follow-up epic
  (file at devolve).
- **DEC-004 — Add `run --json`; `runSkill` returns SkillResult shape.**
  `clauditor run --json` emits `{output, exit_code, duration_seconds,
  error, error_category, warnings, input_tokens, output_tokens,
  harness, skill, args}`. `runSkill()` is a thin parser over it and
  does **not** return `entries` in v1 (entries need an eval spec +
  L2). The issue's `result.entries` is documented as the
  `validate()`/`extract` path. Benefits Python users too.
- **DEC-005 — Binary resolution order.** `CLAUDITOR_BIN` env (explicit
  override) → `clauditor` on `PATH` → (best-effort) `python3 -m
  clauditor` *only if* a `__main__.py` is added → else throw
  `ClauditorNotFoundError` with an actionable hint (`pipx install
  clauditor-eval` / `uv tool install clauditor-eval`). A tiny
  `src/clauditor/__main__.py` is added so `python -m clauditor` works
  as a real fallback (cheap, also useful standalone).
- **DEC-006 — Async JS API.** Uses `child_process.execFile` promisified
  (or `spawn` with collected stdout) — never the `*Sync` variants, so
  long runs don't block the event loop. Each call honors a `timeout`
  option (ms), defaulting to the Python engine's own default.
- **DEC-007 — `execFile` with arg array, no shell, no secrets in
  argv.** Prevents command injection; API keys ride the inherited
  env. argv is never logged.
- **DEC-008 — JS exit-code mapping mirrors the Python taxonomy.** 0 →
  resolve with `{passed:true,…}`; 1 → resolve with `{passed:false,…}`
  (a *failing* eval is data, not an exception); 2 → throw
  `ClauditorInputError`; 3 → throw `ClauditorApiError`; any other code
  / non-JSON stdout → throw `ClauditorError`. Pure mapping helper
  separated from the I/O exec per the pure/IO-split rule.
- **DEC-009 — `run --json` stdout is unversioned.** Matches the
  existing `validate`/`extract` stdout `--json` convention; persisted
  sidecars (not produced by `run`) keep `schema_version` per the rule.
- **DEC-010 — `npm/` subtree at repo root; separate publish workflow.**
  All Node code lives under `npm/`. A new `.github/workflows/
  npm-publish.yml` publishes on a dedicated tag/release and is fully
  independent of `publish.yml` (PyPI).
- **DEC-011 — Node engines `>=18`.** LTS baseline with stable
  `fs/promises`, `util.promisify`, native `fetch` (unused here but
  future-proof). `package.json` declares `"engines": {"node": ">=18"}`.
- **DEC-012 — `loadSpec` = discover + read.** Resolves an explicit eval
  path or the sibling `<skill>.eval.json`, reads + JSON-parses it, and
  returns the object. v1 does **not** re-validate through the Python
  loader (documented limitation); a future `--echo-spec` command could
  add validation.
- **DEC-013 — `@clauditor-eval/*` platform scope reserved.** No
  platform packages ship in v1; the scope is documented as reserved so
  the binary follow-up epic can claim it without renaming.

---

## Detailed Breakdown (Stories)

> Validation command (Python stories): `uv run ruff check src/ tests/`
> + `uv run pytest --cov=clauditor --cov-report=term-missing` (80%
> gate). Validation command (Node stories): `cd npm && npm test` (jest)
> + `npm run test:vitest` + `npm run lint`.

### US-001 — Python: `run --json` structured output + `python -m clauditor`
- **Description:** Add a `--json` flag to the `run` command that emits
  the `SkillResult` as a stable JSON object, and add
  `src/clauditor/__main__.py` so `python -m clauditor` dispatches to
  `cli:main` (fallback path for the JS resolver).
- **Traces to:** DEC-004, DEC-005, DEC-009.
- **Files:**
  - `src/clauditor/cli/run.py` — add `--json` arg; when set, print
    `json.dumps({output, exit_code, duration_seconds, error,
    error_category, warnings, input_tokens, output_tokens, harness,
    skill, args}, indent=2)` instead of the rendered output. Preserve
    the existing exit-code behavior.
    - **Note:** harness key already populated on `SkillResult`. Do NOT
      include secrets.
  - `src/clauditor/__main__.py` — `from clauditor.cli import main;
    raise SystemExit(main())`.
- **TDD:**
  - `tests/test_cli.py::TestCmdRun` — `run --json` emits valid JSON
    with the documented keys; `output`/`exit_code` correct on a mocked
    `SkillRunner.run`.
  - `--json` payload omits/empties optional fields cleanly (no crash
    when `error is None`, `warnings == []`).
  - `python -m clauditor --help` exits 0 (smoke via `subprocess` or
    `runpy`).
- **Acceptance:** `clauditor run <skill> --json` prints parseable JSON
  with all documented keys; `python -m clauditor` works; ruff + pytest
  pass (80% gate).
- **Done when:** new tests green; coverage gate holds.
- **Depends on:** none.

### US-002 — Node: `npm/` package skeleton + tooling
- **Description:** Scaffold the `npm/` subtree: `package.json` (name
  `clauditor-eval`, version mirroring PyPI, `engines.node >=18`, `bin`,
  `main`, `files`), `.npmignore`, jest + vitest config, an ESLint/lint
  script, and a placeholder `index.js`/`bin/clauditor.js` so the
  package is installable and `npm test` runs (empty-green).
- **Traces to:** DEC-001, DEC-003, DEC-010, DEC-011, DEC-013.
- **Files:** `npm/package.json`, `npm/.npmignore`, `npm/jest.config.js`,
  `npm/vitest.config.js`, `npm/.eslintrc.json` (or flat config),
  `npm/index.js` (stub), `npm/bin/clauditor.js` (stub, executable),
  `npm/README.md` (stub).
- **Acceptance:** `cd npm && npm install && npm test && npm run lint`
  succeed; `package.json` declares `clauditor-eval`, `engines`,
  `bin.clauditor`, `main`, `files` (whitelist), and documents
  `@clauditor-eval/*` as reserved.
- **Done when:** package installs and the empty test suite passes.
- **Depends on:** none.

### US-003 — Node: lib core (resolve-binary + exec-json + errors)
- **Description:** Implement the engine resolver and the exec/parse/map
  core. `resolveBinary()` follows DEC-005 order and throws
  `ClauditorNotFoundError` with an install hint when nothing is found.
  `execJson(args, opts)` runs the engine via `execFile` (array args, no
  shell, inherited env, honored `timeout`), parses stdout JSON
  defensively, and maps the exit code to outcome/error per DEC-008.
- **Traces to:** DEC-005, DEC-006, DEC-007, DEC-008.
- **Files:** `npm/lib/resolve-binary.js`, `npm/lib/exec.js`,
  `npm/lib/errors.js` (`ClauditorError`, `ClauditorNotFoundError`,
  `ClauditorInputError`, `ClauditorApiError`).
- **TDD:**
  - `resolveBinary`: honors `CLAUDITOR_BIN`; falls back to PATH; throws
    `ClauditorNotFoundError` with hint when absent (mock PATH lookup).
  - exit-code map (pure helper): 0→pass, 1→fail-data, 2→InputError,
    3→ApiError, 7→generic ClauditorError, non-JSON stdout→ClauditorError.
  - `execJson` runs a **fake clauditor stub** script (fixture emitting
    canned JSON + chosen exit code); asserts parsed result + error
    classes; never invokes a shell (assert arg-array path).
  - timeout option propagated to `execFile`.
- **Acceptance:** all lib tests green under jest AND vitest; no shell
  string used; secrets never appear in any logged output.
- **Done when:** resolve/exec/error modules covered and green.
- **Depends on:** US-002.

### US-004 — Node: JS API (`index.js`) — runSkill / validate / loadSpec
- **Description:** Public async API. `runSkill(skill, opts)` → `run
  --json` parsed (DEC-004 shape). `validate(skillPath, opts)` →
  `validate --json` (`{passed, pass_rate, results}`). `loadSpec(path |
  skillPath)` → discover sibling `<skill>.eval.json`, read + parse
  (DEC-012). Re-export error classes.
- **Traces to:** DEC-004, DEC-006, DEC-008, DEC-012.
- **Files:** `npm/index.js` (+ `npm/index.d.ts` TypeScript types).
- **TDD:**
  - `runSkill` maps options (`args`, `projectDir`, `timeout`) to the
    correct CLI flags; returns the parsed SkillResult shape.
  - `validate` returns `{passed:true}` on exit 0, `{passed:false}` on
    exit 1, throws on 2/3 — driven by the fake stub.
  - `loadSpec` discovers the sibling eval file and returns parsed JSON;
    throws a clear error when missing.
- **Acceptance:** API tests green (jest + vitest); `index.d.ts` types
  compile (`tsc --noEmit` smoke).
- **Done when:** all three functions covered and green.
- **Depends on:** US-003, US-001 (needs `run --json`).

### US-005 — Node: `bin/clauditor.js` CLI launcher
- **Description:** `npx clauditor-eval <args…>` resolves the engine and
  forwards argv with inherited stdio, propagating the child's exit
  code. On `ClauditorNotFoundError`, print the install hint to stderr
  and exit 2.
- **Traces to:** DEC-005, DEC-007, DEC-008.
- **Files:** `npm/bin/clauditor.js` (shebang, executable).
- **TDD:**
  - forwards argv verbatim to the resolved binary (assert via fake
    stub that echoes argv); exit code propagated.
  - missing engine → stderr hint + exit 2.
- **Acceptance:** launcher tests green; `bin` wired in `package.json`;
  stdio is inherited (interactive passthrough).
- **Done when:** launcher covered and green.
- **Depends on:** US-003.

### US-006 — Node: `jest-helper.js` `toPassClauditor` + Vitest compat
- **Description:** Custom matcher `toPassClauditor(received, evalPath?)`.
  Accepts either a `runSkill` result (then runs `validate` on the
  resolved eval) or a `validate` result directly; returns the
  `{pass, message}` matcher shape. Verify it works under both Jest
  `expect.extend` and Vitest `expect.extend` (same contract).
- **Traces to:** DEC-004, DEC-008.
- **Files:** `npm/jest-helper.js` (+ types), tests under
  `npm/__tests__/` for jest and `npm/test/` (or shared) for vitest.
- **TDD:**
  - matcher passes when eval passes, fails with a readable message
    listing failing assertion names when it doesn't (fake stub).
  - identical behavior asserted under jest and vitest harnesses.
- **Acceptance:** matcher green under both runners; failure message
  enumerates failing `results[].name`.
- **Done when:** jest + vitest matcher suites green.
- **Depends on:** US-004.

### US-007 — Node: README usage + npm-publish CI workflow
- **Description:** Write `npm/README.md` (install incl. the Python
  engine prerequisite, `runSkill`/`validate`/`loadSpec` examples,
  jest + vitest matcher examples, `CLAUDITOR_BIN` override, error
  classes, v1-limitations note re: entries + binary distribution). Add
  `.github/workflows/npm-publish.yml` publishing `npm/` on a dedicated
  tag (e.g. `npm-v*`) via `NODE_AUTH_TOKEN`; independent of the PyPI
  workflow. Cross-link from the root `README.md` (a short teaser per
  `.claude/rules/readme-promotion-recipe.md`).
- **Traces to:** DEC-002, DEC-003, DEC-010, DEC-013.
- **Files:** `npm/README.md`, `.github/workflows/npm-publish.yml`,
  root `README.md` (teaser pointing to `npm/README.md`).
- **Acceptance:** README renders the documented examples; workflow
  lints (actionlint or manual review) and uses a separate tag trigger;
  does not modify `publish.yml`.
- **Done when:** docs + workflow committed; root README cross-links.
- **Depends on:** US-005, US-006.

### US-008 — Quality Gate — code review x4 + CodeRabbit
- **Description:** Run the code reviewer 4× over the full changeset
  (Python `run --json` + entire `npm/` subtree + CI workflow), fixing
  every real bug each pass. Run CodeRabbit if available. Re-run both
  validation commands (Python: ruff + pytest 80% gate; Node: `npm
  test` + `npm run test:vitest` + `npm run lint`) until green.
- **Traces to:** all DECs.
- **Acceptance:** 4 review passes complete; all real findings fixed;
  both validation suites pass.
- **Done when:** clean review + green gates.
- **Depends on:** US-001 … US-007.

### US-009 — Patterns & Memory (priority 99)
- **Description:** Capture new patterns: the cross-language stdout-JSON
  contract + defensive JS parse, the JS exit-code mirror of the Python
  taxonomy, the subprocess-bridge distribution decision, and a pointer
  to the deferred binary epic. Update `.claude/rules/` and/or
  `docs/`, and `MEMORY.md` as appropriate. File the **PyInstaller
  binary-distribution follow-up epic** (deferred per DEC-003).
- **Traces to:** DEC-002, DEC-003, DEC-008, DEC-009.
- **Acceptance:** rules/docs updated; follow-up epic filed and linked
  in this plan's Meta.
- **Done when:** patterns recorded; follow-up issue created.
- **Depends on:** US-008.

---

## Deferred to follow-up epic (NOT in this plan)
> Filed as https://github.com/wjduenow/clauditor/issues/200.

- PyInstaller standalone-binary builds for linux-x64, darwin-arm64,
  darwin-x64, win32-x64 (DEC-002 / DEC-003).
- `@clauditor-eval/<platform>` scoped platform packages +
  `optionalDependencies` wiring (DEC-013).
- Binary-build CI matrix + per-platform npm publish.
- Platform-detection swap-in inside `lib/resolve-binary.js` (the
  resolver seam is designed v1 to accept this without an API change).

---

## Beads Manifest
- **Epic:** `clauditor-ovml`
- **Tasks:**
  - `clauditor-ovml.1` — US-001 Python `run --json` + `__main__.py` *(ready)*
  - `clauditor-ovml.2` — US-002 npm/ skeleton + tooling *(ready)*
  - `clauditor-ovml.3` — US-003 lib core (resolve+exec+errors) — needs .2
  - `clauditor-ovml.4` — US-004 index.js API — needs .3, .1
  - `clauditor-ovml.5` — US-005 bin launcher — needs .3
  - `clauditor-ovml.6` — US-006 jest/vitest matcher — needs .4
  - `clauditor-ovml.7` — US-007 README + npm-publish CI — needs .5, .6
  - `clauditor-ovml.8` — US-008 Quality Gate — needs .1–.7
  - `clauditor-ovml.9` — US-009 Patterns & Memory — needs .8
- **Dependency graph:** `.1`→`.4`; `.2`→`.3`→{`.4`,`.5`}; `.4`→`.6`;
  {`.5`,`.6`}→`.7`; `.1`–`.7`→`.8`→`.9`.
