# 155: Comparability — pytest fixtures parametrize harness/provider

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/155
- **Branch:** `feature/155-pytest-fixtures-parametrize`
- **Worktree:** `worktrees/clauditor/155-pytest-fixtures-parametrize` (relative)
- **PR:** https://github.com/wjduenow/clauditor/pull/172
- **Phase:** devolved
- **Epic:** clauditor-6fy
- **Sessions:** 1 (2026-05-09)
- **Total decisions:** 12 (DEC-001 through DEC-012)
- **Stories:** 7 implementation + Quality Gate + Patterns & Memory = 9
- **Depends on:** #147 (CLOSED — provider sidecar field), #151 (CLOSED — `EvalSpec.harness` four-layer precedence), #152 (CLOSED — harness sidecar field)
- **Blocks:** _(none)_
- **Parent epic:** #143 (multi-provider / multi-harness)

---

## Discovery

### Ticket summary

**What:** Extend the pytest plugin (`src/clauditor/pytest_plugin.py`)
so test suites can parametrize across `{harness × provider}` combinations.

Adds:
1. `clauditor_runner` accepts a `harness` kwarg / pytest option,
   instantiating the matching `Harness`.
2. `clauditor_grader` / `clauditor_blind_compare` / `clauditor_triggers`
   accept `provider=` and `model=` kwargs on the factory call (operator
   intent), threaded into the underlying orchestrator calls.
3. New pytest CLI options:
   - `--clauditor-harness {claude-code,codex,auto}`
   - `--clauditor-grading-provider {anthropic,openai,auto}`
   - (`--clauditor-grading-model` already exists as `--clauditor-model`.)
4. Per-harness/provider auth-guard env vars: ~~`CLAUDITOR_FIXTURE_ALLOW_OPENAI=1`
   was considered (mirroring existing `CLAUDITOR_FIXTURE_ALLOW_CLI=1`)
   but **rejected by DEC-001** — OpenAI has no CLI-fallback /
   subscription analog (per #145 DEC-002), so there is no
   relaxed-mode to opt into. The asymmetry with the Anthropic side is
   deliberate.~~
5. Fixture-level **hard error** when required auth missing for
   selected provider/harness, per
   `.claude/rules/precall-env-validation.md` — never `pytest.skip`.
6. `docs/pytest-plugin.md` extended with parametrization examples.

**Why:** Epic #143 multi-harness. #146 wired the four-layer
`grading_provider` precedence into the CLI; #151 wired the same
shape for `harness`. The pytest plugin was given partial automation
(eval_spec.harness/grading_provider are honored by fixtures already)
but lacks the **operator-intent** layers (factory kwargs and pytest
CLI options) that the CLI commands all expose. Without those layers,
a CI matrix like `pytest --clauditor-harness=codex
--clauditor-grading-provider=openai` is impossible from fixture-land.

**Done when (per ticket acceptance):**
1. `clauditor_grader(spec_path, output, provider="openai")` runs
   against OpenAI.
2. `pytest --clauditor-harness=codex` runs all fixtures under Codex.
3. Auth-missing surfaces a hard error (not `pytest.skip`).
4. Tests cover the new fixture branches.

**Out of scope (per ticket):**
- Built-in `pytest.mark.parametrize` matrix helpers (users compose
  their own).

### Codebase findings

#### Existing fixture surface (pytest_plugin.py — 607 lines)

- `clauditor_runner` (line 117): bare `SkillRunner`, no harness selection.
- `clauditor_spec` (line 145): wraps `SkillSpec.from_file`; honors
  `eval_spec.harness` via `harness_name_override` (per #151 US-007).
- `clauditor_grader` (line 397): factory; takes `(skill_path,
  eval_path, output)`. Calls `_dispatch_fixture_auth_guard` +
  `_resolve_fixture_provider`. Threads provider/model into
  `grade_quality(...)`.
- `clauditor_blind_compare` (line 471): factory; takes `(skill_path,
  output_a, output_b, eval_path, *, model)`. **Already has `model=`
  kwarg**, but no `provider=` kwarg.
- `clauditor_triggers` (line 530): factory; no `provider`/`model`
  kwargs on the call signature.
- `clauditor_capture` (line 451): unrelated to this work.

Helpers already in place:
- `_resolve_fixture_provider(eval_spec, model_override)` — pure;
  consults env + spec via `resolve_grading_provider`.
- `_dispatch_fixture_auth_guard(eval_spec, fixture_name, model_override)`
  — routes per provider, raises `AnthropicAuthMissingError` /
  `OpenAIAuthMissingError`.

#### Existing pytest CLI options (lines 53–95)

- `--clauditor-project-dir`
- `--clauditor-timeout`
- `--clauditor-no-api-key`
- `--clauditor-claude-bin`
- `--clauditor-grade`
- `--clauditor-model` (this is the `--clauditor-grading-model` from
  the ticket, already shipped)

**Missing:**
- `--clauditor-harness {claude-code,codex,auto}`
- `--clauditor-grading-provider {anthropic,openai,auto}`

#### Existing env-var seam

- `CLAUDITOR_FIXTURE_ALLOW_CLI=1` (lines 31–48): opts into
  Anthropic CLI transport (relaxed auth). Strict `check_api_key_only`
  is the default; relaxed `check_any_auth_available` fires when env
  is set. **Anthropic-only by design** — OpenAI has no
  CLI-fallback / subscription concept (#145 DEC-002).
- `CLAUDITOR_GRADING_PROVIDER` (used in `_dispatch_fixture_auth_guard`):
  already honored by the env-precedence layer of
  `resolve_grading_provider`.
- `CLAUDITOR_HARNESS`: honored by `resolve_harness` for the CLI
  commands. **Not yet read by fixtures** — `clauditor_runner` does
  not call `resolve_harness`.

#### Available pure resolvers (no I/O at the precedence layer)

- `clauditor._providers.resolve_grading_provider(cli, env, spec, model)`
  — returns concrete `"anthropic"` / `"openai"`.
- `clauditor._providers.resolve_grading_model(eval_spec, provider)`
  — provider-aware default-picker.
- `clauditor._providers.resolve_harness(cli, env, spec)` — returns
  `(name, auto_codex_flag)` tuple; reads PATH via `shutil.which`.
- `clauditor._harnesses.construct_harness(name)` — materializes a
  `ClaudeCodeHarness` / `CodexHarness` instance from a literal name.

These are the same helpers the CLI commands already use; the pytest
plugin will adopt them so a single change to precedence semantics
propagates to fixtures and CLI together.

#### Auth machinery

- `clauditor._providers.check_provider_auth(provider, cmd_name)` —
  multi-provider dispatcher (raises `AnthropicAuthMissingError` or
  `OpenAIAuthMissingError`).
- `clauditor._providers.check_codex_auth(cmd_name)` — harness-axis
  guard (raises `CodexAuthMissingError` when neither
  `CODEX_API_KEY` nor `OPENAI_API_KEY` is set).
- `clauditor._providers.check_api_key_only(cmd_name)` — strict
  Anthropic guard (default in fixture-land).
- `clauditor._providers.check_any_auth_available(cmd_name)` —
  relaxed Anthropic guard (opt-in via `CLAUDITOR_FIXTURE_ALLOW_CLI=1`).

The harness-axis guard is **not yet wired** in fixture-land. Today,
when `eval_spec.harness == "codex"` (set via spec), fixtures
auto-construct `CodexHarness` but never call `check_codex_auth` —
the failure surfaces as the harness's SDK error rather than the
crisp `CodexAuthMissingError` the CLI emits.

### Convention Checker findings (`.claude/rules/`)

Rules that constrain this plan (full list):

- **`precall-env-validation.md`** — fixture guards must raise distinct
  exception classes per provider/harness; never `pytest.skip` on
  auth-missing. The three grading fixtures already comply for
  Anthropic/OpenAI; add `CodexAuthMissingError` branch when fixture
  resolves harness to `"codex"`.
- **`spec-cli-precedence.md`** — operator > author > library default.
  Factory kwargs (operator) win over pytest CLI options (operator),
  win over env vars (operator), win over spec fields (author), win
  over default. The four operator-intent layers stack:
  factory-kwarg > pytest-CLI > env > spec > default.
- **`multi-provider-dispatch.md`** — sibling exception classes per
  provider (no shared parent); structural `except` ladder. The new
  `clauditor_runner` harness-aware path adds a third sibling branch
  (`CodexAuthMissingError`) per the post-#162 dispatcher pattern.
- **`pure-compute-vs-io-split.md`** — fixture-land helpers stay pure
  (no stderr, no `sys.exit`); pytest fixtures themselves own raising
  the exception which pytest then surfaces as a setup error.
- **`test-infra-shutil-which-coupling.md`** — `tests/conftest.py`
  autouse fixture pins `CLAUDITOR_HARNESS=claude-code`. The new
  fixture path that calls `resolve_harness` will short-circuit at
  the env layer under that pin (no `shutil.which` collision). New
  tests that *want* to exercise the auto-PATH branch must
  `monkeypatch.delenv("CLAUDITOR_HARNESS")` and pin `shutil.which`
  inline.
- **`back-compat-shim-discipline.md`** — Pattern 3 (deferred-import
  for test patches): the new fixture-internal `resolve_harness` call
  must target the canonical seam
  (`clauditor._providers.resolve_harness`), not a re-export.
- **`json-schema-version.md`** — N/A (no sidecar emission in
  fixture-land).
- **`pytester-inprocess-coverage-hazard.md`** — N/A (no
  `pytester.runpytest_inprocess` involved).

No rule forbids the work. The fixture seam needs to mirror the CLI
seam structurally; that mirror is exactly what
`spec-cli-precedence.md` and `multi-provider-dispatch.md` codify.

### Phase status

`devolved` — discovery, architecture review, decisions, and
implementation are complete. All 12 decisions and 9 user stories
shipped. The summary above is preserved as the original Discovery
ticket-summary; refer to the Decisions section below and the
implemented code (`src/clauditor/pytest_plugin.py`) for the final
state. DEC-001 explicitly rejected `CLAUDITOR_FIXTURE_ALLOW_OPENAI=1`.

---

## Architecture Review

| Area | Rating | Summary |
|---|---|---|
| Security (auth-guard) | concern | Eager `check_codex_auth` in `clauditor_runner` factory is correct, but `clauditor_spec` also constructs runners (when `eval_spec.harness=="codex"`); needs the same guard for consistency. |
| API Design (factory signature) | concern | `clauditor_runner` value→factory conversion is breaking. Two pyetster-internal call sites today (low blast radius), but doc + 1 README example need update. Open: should the factory accept ALL of `harness=`, `timeout=`, `claude_bin=` for symmetry, or only `harness=` per ticket scope? |
| API Design (precedence stacking) | pass | Operator > author > library default (per `spec-cli-precedence.md`). Four operator-intent layers stack as: factory-kwarg > pytest-CLI-option > env-var > spec-field > default. Mirrors the CLI seam exactly (no new precedence shape). |
| Testing Strategy | concern | `test_pytest_plugin.py:102` asserts `addoption call_count == 6` — bumps to 8 with two new options. New branches need coverage: factory `harness=` override, pytest CLI option, env var, auto-resolution under PATH-pinned conftest. |
| Test-infra coupling | pass | `tests/conftest.py:754` already pins `CLAUDITOR_HARNESS="claude-code"` per `test-infra-shutil-which-coupling.md`. New tests that exercise the auto-PATH branch must `monkeypatch.delenv("CLAUDITOR_HARNESS")` + pin `shutil.which` inline. Symmetric `CLAUDITOR_GRADING_PROVIDER` env-pin is **not** needed (no autouse `shutil.which` collision on that axis). |
| Documentation | concern | `docs/pytest-plugin.md` is the canonical surface (~53 lines today). Per `readme-promotion-recipe.md`, adding parametrization examples likely lands in `docs/pytest-plugin.md` (already a docs/ file with breadcrumb); `README.md` teaser may need a one-sentence extension only if the workflow shape changes. |
| Backward compatibility | concern | `clauditor_runner` becoming a factory is a breaking change for any user calling `def test_x(clauditor_runner):` and treating it as a value. Pre-1.0 project + `CHANGELOG.md` is the canonical break-with-note seam. |

### Findings detail

**Eager `check_codex_auth` from two seams.** The `clauditor_runner`
factory (per Q4 answer) calls `check_codex_auth` when resolved
harness is `"codex"`. But `clauditor_spec` also constructs codex
runners (via `harness_name_override` in `SkillSpec.run`). The
guard should fire from **both seams**, otherwise a test using
only `clauditor_spec` (no `clauditor_runner`) bypasses the guard
when `eval_spec.harness == "codex"`. Two options:
(a) duplicate the guard at both seams, (b) call `check_codex_auth`
inside `SkillSpec.run` itself when materializing a fresh codex
runner — the latter centralizes the guard but moves it out of
fixture-land. The CLI seam guards eagerly (per #151 DEC-012) so
duplicating in `clauditor_spec` is idiomatic.

**Factory signature scope.** The ticket scope mentions `harness`
kwarg only on `clauditor_runner`. But the existing fixture wiring
already passes `timeout` and `claude_bin` from pytest options;
the factory could either:
(a) accept just `harness=` as additive kwarg over today's pytest
options, or (b) become the full constructor surface (factory
takes `harness=`, `timeout=`, `claude_bin=`, falling back to
pytest options when `None`). Option (a) keeps scope minimal;
option (b) is forward-compat for future runner kwargs.

**Test brittleness.** `test_pytest_plugin.py:102` and the addoption
counter assertions are fragile to option-set changes. Same shape
fired on every prior option add (#86 added `--clauditor-no-api-key`,
#151 did not need a fixture option since it's harness-axis). Bump
the count and add per-option assertions.

---

## Refinement Log

### Decisions

**DEC-001: Drop `CLAUDITOR_FIXTURE_ALLOW_OPENAI`.**
*Decision:* The ticket suggests mirroring `CLAUDITOR_FIXTURE_ALLOW_CLI=1`
for OpenAI. Reject the mirror; do not introduce the env var.
*Rationale:* `CLAUDITOR_FIXTURE_ALLOW_CLI=1` opts into a *relaxed*
Anthropic guard (accepts subscription auth via the `claude` CLI on
PATH). OpenAI has no CLI-fallback / subscription analog (#145
DEC-002). There is no relaxed-mode to opt into; a no-op env var
would be misleading. Document the asymmetry in
`docs/pytest-plugin.md` so users understand why one provider has
the env-var and the other does not.

**DEC-002: Convert `clauditor_runner` to a factory fixture.**
*Decision:* `clauditor_runner` becomes `def clauditor_runner(...)
→ Callable[[harness?: str], SkillRunner]`. Today's value-shape
(`def test(clauditor_runner): runner = clauditor_runner`) becomes
`def test(clauditor_runner): runner = clauditor_runner()`.
*Rationale:* Pytest fixtures cannot accept call-site kwargs; only
factory fixtures can. Per the audit, only 2 in-repo tests use
`clauditor_runner` as a value (both pytester-internal); user-facing
test code is unaffected. Pre-1.0 project precedent is a hard break
with `CHANGELOG.md` note + docs update (per the
`data-vs-asserter-split.md` migration that broke
`result.assert_*`).

**DEC-003: `clauditor_runner` factory accepts only `harness=`
kwarg (minimal scope).** *Decision:* Signature is
`clauditor_runner(harness: str | None = None) → SkillRunner`.
`timeout` and `claude_bin` continue to come from pytest options
exclusively. *Rationale:* Ticket-aligned. Forward-compat is fine
to widen in a follow-up; the wider signature has no current call
sites today and would be solving a problem we don't have.

**DEC-004: Auto-resolution via `resolve_harness` (PATH lookup
mirroring the CLI).** *Decision:* When `--clauditor-harness=auto`
(default) and no `harness=` factory kwarg, the fixture calls
`clauditor._providers.resolve_harness(cli_override=harness_kwarg,
env_override=os.environ.get("CLAUDITOR_HARNESS"),
spec_value=None)` and uses the returned name.
*Rationale:* Mirror the CLI seam exactly. `tests/conftest.py:754`
already pins `CLAUDITOR_HARNESS=claude-code` per
`test-infra-shutil-which-coupling.md`, so the in-repo suite
short-circuits before PATH lookup. User test suites get the
auto-resolution behavior.

**DEC-005: Eager `check_codex_auth` from BOTH `clauditor_runner`
factory AND `clauditor_spec` factory.** *Decision:* When the
resolved harness is `"codex"`, both factories call
`check_codex_auth("runner")` / `check_codex_auth("spec")` before
returning the runner. Distinct exception class
`CodexAuthMissingError` per `precall-env-validation.md` and
`multi-provider-dispatch.md`. *Rationale:* CLI commands all
guard eagerly per #151 DEC-012; fixtures must match. Two seams
because tests can use either fixture in isolation; missing the
guard at `clauditor_spec` would let a `clauditor_spec`-only test
bypass it. Push-down into `SkillSpec.run` was rejected: it
moves the guard out of fixture-land and changes CLI behavior
(wider blast radius).

**DEC-006: Cross-axis isolation — no `provider=`/`model=` on
`clauditor_runner`.** *Decision:* Only the three grading
fixtures (`clauditor_grader`, `clauditor_blind_compare`,
`clauditor_triggers`) accept `provider=` and `model=` factory
kwargs. `clauditor_runner` accepts only `harness=`.
*Rationale:* The harness vs grading-provider distinction is
structural per `multi-provider-dispatch.md` (harness=skill
runtime, provider=grader runtime). They are independent axes;
the runner has no grading concern. Conflating them would
re-introduce the "harness ≠ provider" bug DEC-010 of #151
explicitly avoided.

**DEC-007: Factory-kwarg precedence: kwarg > pytest CLI option >
env > spec > default.** *Decision:* The new factory kwargs
(`harness=` on runner; `provider=`/`model=` on graders) sit at
the top of the operator-intent stack. Each layer falls through to
the next when `None` (or `"auto"`, for the auto-resolved fields).
*Rationale:* Mirrors the CLI seam exactly per
`spec-cli-precedence.md`. The four operator-intent layers stack
above the existing `eval_spec.<field>` (author intent) and the
library default. No new precedence shape; this rule's pytest
fixture anchor is the goal.

**DEC-008: Sibling `CodexAuthMissingError` as a third except
branch on `clauditor_runner`.** *Decision:* The `clauditor_runner`
factory `except` ladder has three branches:
`AnthropicAuthMissingError` (when transport-related auth bridges
fail), `OpenAIAuthMissingError` (defensive — provider auth is
graders, not runners; included for ladder shape stability), and
`CodexAuthMissingError`. Per `multi-provider-dispatch.md`, no
shared parent class. *Rationale:* Structural routing, not
substring matching. Each class is a sibling of `Exception`; the
exit-code-equivalent here is "pytest setup failure with this
specific error class" — assertable in tests via
`pytest.raises(CodexAuthMissingError)`.

**DEC-009: CHANGELOG + docs update; no back-compat shim.**
*Decision:* Document the `clauditor_runner` value→factory break
in `CHANGELOG.md` with a one-line migration guide. Update
`docs/pytest-plugin.md` and any README example. *Rationale:*
Pre-1.0 project. Established precedent: #1 (data-vs-asserter
split broke `result.assert_*` with no shim). Two fixtures for
one concept (`clauditor_runner` + `clauditor_runner_factory`)
would carry deprecation surface for years; the hard break is
cleaner.

**DEC-010: `--clauditor-model` is the canonical grading-model
option name; do NOT rename to `--clauditor-grading-model`.**
*Decision:* The ticket's mention of `--clauditor-grading-model
STR` is interpreted as "ensure a grading-model knob exists"
(it does, named `--clauditor-model`). No rename, no alias.
*Rationale:* Renaming a public CLI option pre-1.0 is still a
breaking change with no upside; readers can see from
`docs/pytest-plugin.md` and the option's `help=` string that it
governs grading. The cleanup belongs to a future dedicated
naming-pass ticket if at all.

**DEC-011: Test-infra: existing `tests/conftest.py:754` pin
covers harness axis; no new pin needed for grading-provider.**
*Decision:* Per the audit, no `shutil.which` collision exists on
the grading-provider axis (provider resolution reads only env +
spec, no PATH). New tests that exercise the auto-PATH harness
branch must `monkeypatch.delenv("CLAUDITOR_HARNESS")` AND pin
`shutil.which` inline (per `test-infra-shutil-which-coupling.md`).
*Rationale:* Don't add coupling we don't need. The defensive pin
is for the autouse-shutil-which-collision class of bug; that
class doesn't apply on the grading-provider axis.

**DEC-012: Documentation lands in `docs/pytest-plugin.md`; no
README teaser change.** *Decision:* Add parametrization examples
+ env-var docs to `docs/pytest-plugin.md`. The README's "Pytest
Integration" teaser is unchanged (workflow shape stays the
same; the README points at the doc). *Rationale:* Per
`readme-promotion-recipe.md`, the workflow's *shape* did not
change — only its *capability* widened. README teaser bumps are
reserved for shape changes (per the rule's update criteria).
`CHANGELOG.md` carries the migration note for the
factory break.

### Session notes

Three of four scoping questions and four of four architecture-
review questions chose the recommended option (the safe path).
The fourth scoping question (auto-resolution mode) also chose
recommended. Decisions feel well-anchored; no surprising
trade-offs surfaced.

---

## Detailed Breakdown

Architecture order: **pytest-options → factory conversion → grader
fixtures → spec auth-guard → tests → docs → quality gate → patterns**.

### US-001: Add `--clauditor-harness` and `--clauditor-grading-provider` pytest options

**Description.** Register two new pytest CLI options in
`pytest_addoption` with closed-set choice validation. No fixture
behavior change yet — this is the option-parsing seam only.

**Traces to:** DEC-008 (option naming), DEC-010 (no rename).

**Acceptance Criteria:**
- `pytest --clauditor-harness {claude-code,codex,auto}` accepts
  the three values; rejects others with argparse error.
- `pytest --clauditor-grading-provider {anthropic,openai,auto}`
  same shape.
- `pytest_addoption` registers both options under the existing
  `clauditor` group; help strings explain the operator-intent
  precedence.
- `tests/test_pytest_plugin.py:102` `addoption call_count == 6`
  assertion bumps to `== 8`.
- `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor
  --cov-report=term-missing` passes (80% coverage gate).

**Done when:** Both options parseable from `pytest --help`; existing
test suite green with the count bump.

**Files:**
- `src/clauditor/pytest_plugin.py` (add options to
  `pytest_addoption`).
- `tests/test_pytest_plugin.py` (bump count assertion + add
  per-option presence assertions).

**Depends on:** none.

**TDD:** Write failing assertions for `--clauditor-harness=codex`
and `--clauditor-grading-provider=openai` parsing (via
`pytester` or `_argparse` introspection); add option-presence
checks. Implement in `pytest_addoption` until green.

---

### US-002: Convert `clauditor_runner` to factory; harness resolution + Codex auth guard

**Description.** Replace the value fixture with a factory that
accepts an optional `harness=` kwarg. Resolve the harness via
`resolve_harness(cli_override, env_override, spec_value=None)`,
where `cli_override` is the factory kwarg (when set) **or** the
`--clauditor-harness` pytest option (when set). When the
resolved harness is `"codex"`, eagerly call
`check_codex_auth("runner")`. Materialize via
`construct_harness(name)`.

**Traces to:** DEC-002, DEC-003, DEC-004, DEC-005 (codex guard
at runner seam), DEC-007 (precedence stacking), DEC-008 (sibling
exception class).

**Acceptance Criteria:**
- `def test(clauditor_runner): runner = clauditor_runner()` works
  with default harness resolution.
- `runner = clauditor_runner(harness="codex")` overrides to Codex
  even when `--clauditor-harness=claude-code` is set (factory
  kwarg wins).
- When the resolved harness is `"codex"` and neither
  `CODEX_API_KEY` nor `OPENAI_API_KEY` is set,
  `clauditor_runner()` raises `CodexAuthMissingError` (NOT
  `pytest.skip`).
- The two existing in-repo callers
  (`tests/test_pytest_plugin.py:65-66, 74-75`) updated to call
  `clauditor_runner()`.
- `runner.harness.name == "codex"` after the codex path.
- `CodexAuthMissingError` is a sibling of `Exception` (not a
  subclass of `AnthropicAuthMissingError`) — verified by an
  identity test.
- `uv run ruff check && uv run pytest` passes.

**Done when:** Factory invocation produces correct runner per
precedence layer; auth guard fires before any subprocess; all
in-repo tests pass.

**Files:**
- `src/clauditor/pytest_plugin.py::clauditor_runner` (rewrite).
- `tests/test_pytest_plugin.py` (update 2 in-repo callers; add
  factory branch tests).

**Depends on:** US-001.

**TDD:**
- `test_factory_kwarg_wins_over_pytest_option` — set option to
  `claude-code`, call `clauditor_runner(harness="codex")`,
  assert `runner.harness.name == "codex"`.
- `test_pytest_option_used_when_no_kwarg` — set option to
  `codex`, call `clauditor_runner()`, assert codex.
- `test_env_var_used_when_no_kwarg_no_option` —
  `monkeypatch.setenv("CLAUDITOR_HARNESS", "codex")`, assert
  codex.
- `test_codex_auth_missing_raises` —
  `monkeypatch.delenv("CODEX_API_KEY", raising=False)`,
  `monkeypatch.delenv("OPENAI_API_KEY", raising=False)`, expect
  `CodexAuthMissingError`.
- `test_class_identity_codex_auth_missing` — `assert
  CodexAuthMissingError is not AnthropicAuthMissingError`.
- `test_auto_resolution_path_lookup` —
  `monkeypatch.delenv("CLAUDITOR_HARNESS")`,
  `monkeypatch.setattr(shutil, "which", lambda n: "/x/codex" if
  n == "codex" else None)`, expect codex (per
  `test-infra-shutil-which-coupling.md`).

---

### US-003: `clauditor_grader` accepts `provider=` and `model=` factory kwargs

**Description.** Extend `clauditor_grader` factory signature with
`provider: str | None = None` and `model: str | None = None`.
Thread `provider` through `_dispatch_fixture_auth_guard` and
`_resolve_fixture_provider` as the highest-precedence layer
(operator intent kwarg > pytest CLI option > env > spec). Thread
`model` similarly. Pass both into `grade_quality(provider=...,
model=...)`.

**Traces to:** DEC-007 (precedence), DEC-006 (axis isolation).

**Acceptance Criteria:**
- `clauditor_grader(skill, output, provider="openai")` routes
  through OpenAI auth + SDK regardless of spec's
  `grading_provider`.
- `clauditor_grader(skill, output, model="gpt-5.4")` overrides
  pytest `--clauditor-model`.
- Without kwargs, today's resolution path (pytest CLI option →
  env → spec) is unchanged.
- `_dispatch_fixture_auth_guard` and `_resolve_fixture_provider`
  accept the new operator-intent layer (likely a new
  `provider_override` param) and pass it as
  `cli_override` to `resolve_grading_provider`.
- `pytest --clauditor-grading-provider=openai` (without factory
  kwarg) routes to OpenAI auth + SDK on a default spec.

**Done when:** All four operator-intent layers honored;
`grade_quality` receives the resolved provider; existing tests
pass; new branch tests cover each layer.

**Files:**
- `src/clauditor/pytest_plugin.py::clauditor_grader`,
  `_dispatch_fixture_auth_guard`, `_resolve_fixture_provider`.
- `tests/test_pytest_plugin.py` (new branch tests).

**Depends on:** US-001.

**TDD:**
- `test_grader_factory_kwarg_provider_wins` — spec has
  `grading_provider="anthropic"`; pass
  `provider="openai"` kwarg; assert OpenAI auth dispatched.
- `test_grader_pytest_option_provider_used_when_no_kwarg` —
  `pytest --clauditor-grading-provider=openai` honored.
- `test_grader_env_used_when_no_kwarg_no_option` —
  `CLAUDITOR_GRADING_PROVIDER=openai`.
- `test_grader_factory_kwarg_model_wins` — assert override
  threads to `grade_quality`.

---

### US-004: `clauditor_blind_compare` accepts `provider=` factory kwarg

**Description.** Same shape as US-003. `clauditor_blind_compare`
already has `model=` kwarg; add `provider=`. Thread through
`_dispatch_fixture_auth_guard` + `_resolve_fixture_provider` and
into `blind_compare_from_spec(provider=...)`.

**Traces to:** DEC-007.

**Acceptance Criteria:**
- `clauditor_blind_compare(skill, a, b, provider="openai")`
  dispatches OpenAI auth.
- All four operator-intent layers honored.
- `BlindReport.provider_source` reflects the resolved provider.

**Done when:** Branch tests pass; existing blind-compare tests
pass.

**Files:**
- `src/clauditor/pytest_plugin.py::clauditor_blind_compare`.
- `tests/test_pytest_plugin.py`.

**Depends on:** US-003 (shares helper plumbing).

**TDD:** mirror US-003's TDD list for the blind-compare seam.

---

### US-005: `clauditor_triggers` accepts `provider=` and `model=` factory kwargs

**Description.** Same shape as US-003. `clauditor_triggers`
factory signature gains `provider: str | None = None`,
`model: str | None = None`. Thread through helpers and into
`run_triggers(...)`.

**Traces to:** DEC-007.

**Acceptance Criteria:** mirror US-003.

**Done when:** Branch tests pass.

**Files:**
- `src/clauditor/pytest_plugin.py::clauditor_triggers`.
- `tests/test_pytest_plugin.py`.

**Depends on:** US-003 (shared helpers).

**TDD:** mirror US-003's list for triggers.

---

### US-006: Eager `check_codex_auth` in `clauditor_spec` factory

**Description.** When `clauditor_spec` is instantiated and the
loaded spec has `eval_spec.harness == "codex"` (or the spec
override resolves to codex), eagerly call
`check_codex_auth("spec")` before returning the wrapped
`SkillSpec`. Mirrors the runner-side guard in US-002 so any
test using `clauditor_spec` alone (no `clauditor_runner`) gets
the same crisp `CodexAuthMissingError` instead of a deep
subprocess error.

**Traces to:** DEC-005.

**Acceptance Criteria:**
- A test with `eval_spec.harness="codex"` and missing
  `CODEX_API_KEY`/`OPENAI_API_KEY` raises
  `CodexAuthMissingError` from the `clauditor_spec(...)` call.
- Specs that don't resolve to codex are unaffected.

**Done when:** Branch test passes.

**Files:**
- `src/clauditor/pytest_plugin.py::clauditor_spec`.
- `tests/test_pytest_plugin.py`.

**Depends on:** US-002 (introduces `CodexAuthMissingError` in
fixture-land).

**TDD:**
- `test_spec_factory_codex_auth_missing` — load spec with
  `harness="codex"`, no env keys, expect
  `CodexAuthMissingError`.
- `test_spec_factory_claude_code_no_guard` — load spec with
  `harness="claude-code"`, missing OpenAI key, no raise.

---

### US-007: Update `docs/pytest-plugin.md` + `CHANGELOG.md`

**Description.** Add a new section to `docs/pytest-plugin.md`
covering parametrization across `{harness × provider}`. Include
a worked example using `pytest.mark.parametrize` over
`{("claude-code", "anthropic"), ("codex", "openai")}`. Document
the `clauditor_runner` factory shape change and call out the
intentional `CLAUDITOR_FIXTURE_ALLOW_OPENAI` non-mirror.
`CHANGELOG.md` gets a new entry under `## Unreleased` with the
factory-conversion migration line.

**Traces to:** DEC-001 (asymmetry doc), DEC-002 (break-comms),
DEC-009 (no shim), DEC-012 (docs landing site).

**Acceptance Criteria:**
- `docs/pytest-plugin.md` has a `## Parametrizing harness × provider`
  H2 with at least one runnable code block.
- The asymmetry note explains why `_ALLOW_OPENAI=1` does not
  exist (no relaxed mode for OpenAI auth).
- `CHANGELOG.md` documents:
  ```
  - `clauditor_runner` is now a factory fixture. Migration:
    `runner = clauditor_runner` → `runner = clauditor_runner()`.
  ```
- README's "Pytest Integration" teaser unchanged (DEC-012).

**Done when:** Docs render cleanly on GitHub; the CHANGELOG entry
is one line and accurate.

**Files:**
- `docs/pytest-plugin.md`.
- `CHANGELOG.md`.

**Depends on:** US-002, US-003, US-004, US-005, US-006.

---

### US-008: Quality Gate

**Description.** Run code-reviewer agent 4× across the full
changeset, fixing every real bug each pass. Run CodeRabbit (if
available). Run `uv run ruff check src/ tests/` and
`uv run pytest --cov=clauditor --cov-report=term-missing` —
80% coverage gate must pass.

**Traces to:** Project validation gate.

**Acceptance Criteria:**
- 4 passes of code-reviewer agent; all real findings addressed.
- CodeRabbit pass clean (or all findings explicitly resolved).
- `ruff check` clean.
- `pytest` green; coverage ≥ 80%.

**Done when:** All quality signals green; PR ready to flip to
non-draft.

**Files:** any file the reviewers point at.

**Depends on:** US-001 through US-007.

---

### US-009: Patterns & Memory

**Description.** Update `.claude/rules/spec-cli-precedence.md`
to add the pytest fixture seam as another four-layer-precedence
canonical-implementation anchor. Specifically: factory-kwarg >
pytest-CLI-option > env > spec > default. The CLI seams
(`grade`, `extract`, etc.) and pytest fixture seams now mirror
each other; the rule should call this out.

If session surfaced any non-obvious pattern (e.g. the codex
auth guard living at TWO fixture seams), add a memory entry per
the auto-memory `feedback`/`project` schema.

**Traces to:** All decisions; rule-anchor maintenance.

**Acceptance Criteria:**
- `spec-cli-precedence.md` has a new subsection (or extended
  existing one) documenting the pytest fixture mirror.
- Memory updated only if a non-obvious pattern surfaced (avoid
  noise per memory hygiene).

**Done when:** Rule file updated; final commit lands.

**Files:**
- `.claude/rules/spec-cli-precedence.md`.
- (optional) `~/.claude/projects/.../memory/MEMORY.md` + entry.

**Depends on:** US-008.

---

## Beads Manifest

- **Epic:** `clauditor-6fy`
- **Worktree:** `worktrees/clauditor/155-pytest-fixtures-parametrize`
- **Branch:** `feature/155-pytest-fixtures-parametrize`
- **PR:** https://github.com/wjduenow/clauditor/pull/172

| Story | Bead | Depends on |
|---|---|---|
| US-001 — pytest options | `clauditor-6fy.1` | (ready) |
| US-002 — runner factory + codex guard | `clauditor-6fy.2` | US-001 |
| US-003 — grader provider/model kwargs | `clauditor-6fy.3` | US-001 |
| US-004 — blind_compare provider kwarg | `clauditor-6fy.4` | US-003 |
| US-005 — triggers provider/model kwargs | `clauditor-6fy.5` | US-003 |
| US-006 — spec-side codex guard | `clauditor-6fy.6` | US-002 |
| US-007 — docs + CHANGELOG | `clauditor-6fy.7` | US-002, US-003, US-004, US-005, US-006 |
| US-008 — Quality Gate | `clauditor-6fy.8` | US-007 |
| US-009 — Patterns & Memory | `clauditor-6fy.9` | US-008 |

Ready set on devolve: `clauditor-6fy.1` (US-001).

---

## Session Notes

### 2026-05-09 — Discovery

- Created worktree, plan doc, ran parallel research.
- Key finding: ~70% of the work is already done by #146/#151's
  fixture wiring (`_resolve_fixture_provider`,
  `_dispatch_fixture_auth_guard`, `eval_spec.harness` honoring in
  `clauditor_spec`). #155 is mostly the **operator-intent** layers:
  factory kwargs + pytest CLI options + harness-axis auth guard.
- Three open scoping questions surfaced; presenting before
  architecture review.
