# Rule: Pytest fixtures mirror the CLI's operator > author > default precedence

When clauditor exposes a multi-axis precedence resolver at the CLI
seam (`harness`, `grading_provider`, `grading_model`, …) via the
shape codified in `.claude/rules/spec-cli-precedence.md`, the
matching pytest fixtures **MUST** mirror the same precedence
direction in fixture-land. The fixture seam is a **factory-fixture
mirror**: each fixture exposes a `_factory(...)` callable whose
kwargs are the operator-intent layer at the call site, and the
factory closes over the pytest CLI options + env vars + spec fields
to compose the same operator > author > default direction the CLI
honors. A fixture that resolves axes differently from the CLI
silently produces test results that do not reflect what
`clauditor <cmd>` would do for the same operator under the same
spec.

The mirror is intentionally **asymmetric** across fixtures — three
distinct precedence shapes ship today, each tuned to the seam's
available context. The asymmetry is load-bearing, not an oversight.

## The three shapes

### Shape A — operator-only (no spec available)

`clauditor_runner` does not load an `EvalSpec`, so only the
operator-intent and default layers apply. Four layers:

1. Factory `harness=` kwarg — operator intent at call site (wins).
2. `--clauditor-harness` pytest option — operator intent at session start.
3. `CLAUDITOR_HARNESS` env var — operator intent in the shell.
4. Default `"auto"` — `resolve_harness(..., spec_value=None)` falls
   through to PATH lookup.

The author layer (`EvalSpec.harness`) is **structurally absent**, NOT
silently skipped. A test that wants spec-author intent honored uses
`clauditor_spec` (Shape B) instead. The fixture has no spec to read;
faking one would require a sentinel and a defensive code path with
no production analog.

### Shape B — author-only (spec-load seam)

`clauditor_spec` honors `EvalSpec.harness` (when set to a concrete
value, not `"auto"`) and threads it as `harness_name_override` to
`SkillSpec.run`. The fixture itself takes no operator-intent
kwargs, no pytest option, no env var — but **per-call operator
intent IS supported via `spec.run(harness_name_override="codex")`**
on the wrapped run method (DEC-005: per-call kwarg wins over the
spec field, mirroring the four-layer CLI precedence at the
call-site granularity). Session-level operator intent
(`--clauditor-harness`, `CLAUDITOR_HARNESS`) lives on
`clauditor_runner`'s factory instead; consulting it here would let
a session flag silently override a skill author's explicit
`EvalSpec.harness` even for tests that deliberately probe author
intent.

### Shape C — full five-layer mirror (spec-load + grader call)

`clauditor_grader` / `clauditor_blind_compare` / `clauditor_triggers`
wrap both a spec-load AND a grader call, so they compose author intent
(in the loaded spec) AND operator intent (factory kwargs + pytest
options + env). Per axis:

1. Factory `provider=` / `model=` kwarg — operator intent at call site (wins).
2. `--clauditor-grading-provider` / `--clauditor-model` pytest option.
3. `CLAUDITOR_GRADING_PROVIDER` env var (provider only — no
   `CLAUDITOR_MODEL` env var per DEC-010).
4. `EvalSpec.grading_provider` / `EvalSpec.grading_model` — author intent.
5. Default — `"auto"` with auto-inference, or per-provider model default.

Both axes flow through the same pure resolver chain
(`resolve_grading_provider` + `resolve_grading_model`) the CLI uses,
so a fixture caller and an operator running `clauditor grade` get
the same provider+model resolution from the same `EvalSpec`.

## Why each piece matters

- **Factory > pytest option > env > spec > default direction is the
  same shape the CLI honors.** A test that pins `provider="openai"`
  at the factory kwarg is expressing the same operator-at-call-site
  intent as `clauditor grade --grading-provider openai`. Reversing
  the direction in fixture-land would mean tests grade with one
  provider while documented user behavior grades with another —
  silently false positives in CI.
- **Shape A's missing author layer is structural, not a bug.** The
  runner-factory fixture has no spec to read; pretending otherwise
  by routing through `EvalSpec` would require a fake spec object
  and would invite the "factory secretly knows about author intent"
  hazard. The fixture's audience opted out of the spec layer by
  picking the runner factory.
- **Shape B's missing session-level operator layer is structural,
  not a bug.** Reading `--clauditor-harness` at `clauditor_spec`
  would let a session-level flag silently mask spec-author tests.
  A test that asserts "this skill author's harness preference is
  honored" must NOT be overridable by the test runner's session
  flags. Per-call operator intent on `spec.run()` is fine — it is
  scoped to one test's one run, not the whole session, and DEC-005
  pins it as the winning layer over the spec field.
- **Auth dispatch fires per-provider via
  `_dispatch_fixture_auth_guard`.** Mirrors the CLI's
  `check_provider_auth` ladder with distinct exception classes
  (`AnthropicAuthMissingError` / `OpenAIAuthMissingError`) so
  fixture callers route on a structural `except` ladder per
  `.claude/rules/multi-provider-dispatch.md`. The Anthropic branch
  retains the `CLAUDITOR_FIXTURE_ALLOW_CLI` opt-in toggle (relaxed
  `check_any_auth_available` vs strict `check_api_key_only`) per
  #86 DEC-009; the OpenAI branch is always strict per #145 DEC-002.
- **Eager `check_codex_auth` at TWO seams, not one (DEC-005).**
  Both `clauditor_runner` (resolved harness `"codex"`) AND
  `clauditor_spec` (`eval_spec.harness == "codex"`) fire the codex
  auth guard eagerly. The two seams are independent: each fires its
  own guard on its own resolved value. A test using both fixtures
  sees both guards fire when applicable; a test using only one sees
  only that seam's guard. Mirrors the CLI's
  `cli/grade.py::cmd_grade` shape (`_resolve_harness` resolves;
  `check_codex_auth` dispatches).
- **Hard break, not back-compat shim.** Per DEC-002 / DEC-009 of
  #155, the pre-#155 value-fixture form
  (`clauditor_runner.run("foo")`) is a hard break — callers must
  now invoke `clauditor_runner()` to get the runner. The
  value-fixture shape conflicts structurally with the per-call
  `harness=` kwarg the factory needs; a back-compat shim would
  defeat the conversion. Companion discipline:
  `.claude/rules/back-compat-shim-discipline.md` — break loudly
  rather than degrade silently.

## What NOT to do

- Do NOT route `clauditor_runner` through a fake `EvalSpec` to
  "preserve symmetry" with `clauditor_spec`. The missing author
  layer is intentional; faking it removes the cross-axis isolation
  Shape A buys.
- Do NOT consult `--clauditor-harness` inside `clauditor_spec`.
  Operator-intent layers live on Shape A's factory only; mixing
  them at the spec-load seam defeats the author-intent isolation
  Shape B buys.
- Do NOT pre-compute `env_without_api_key()` outside the per-call
  closure inside `clauditor_spec`'s factory. The session-scoped
  `--clauditor-no-api-key` option tempts this, but pre-computing
  defaults the scrub to claude-code semantics (strips
  `OPENAI_API_KEY`), which silently breaks codex callers when the
  resolved harness is codex. **Defer the
  `env_without_api_key(harness_name=...)` call until inside the
  per-call wrapper**, after the effective harness is resolved (per
  DEC-006 / US-007 of #155). This is the inverse failure mode of
  `.claude/rules/test-infra-shutil-which-coupling.md` —
  pre-computed scrub couples unrelated harness backends.
- Do NOT swallow `ValueError` in `_resolve_fixture_provider` (or
  any future pure resolver helper). Fail-fast on misconfigured
  spec / CLI override at test setup — a swallowed exception
  silently degrades to "fixture returned None" and downstream tests
  pass spuriously. CodeRabbit finding on PR #164.

## Canonical implementation

Factory fixtures (one per shape):

- **Shape A**:
  `src/clauditor/pytest_plugin.py::clauditor_runner` — closes over
  `--clauditor-harness`, `CLAUDITOR_HARNESS` env, and the factory
  `harness=` kwarg. Calls `resolve_harness(cli, env, None)` then
  `check_codex_auth("runner")` when name == `"codex"`. Materializes
  a fresh `SkillRunner` via `construct_harness(name)` for
  non-default harnesses; preserves `--clauditor-claude-bin`
  plumbing for the default `"claude-code"` path.
- **Shape B**:
  `src/clauditor/pytest_plugin.py::clauditor_spec` — reads
  `spec.eval_spec.harness` (concrete values only) and threads as
  `harness_name_override=` to `SkillSpec.run`. Eager
  `check_codex_auth("spec")` fires when spec author declared
  `harness="codex"`. Same-harness skip layer avoids reconstructing
  a runner with the same harness class but losing the fixture's
  `--clauditor-claude-bin` configuration.
- **Shape C**:
  `src/clauditor/pytest_plugin.py::clauditor_grader`,
  `clauditor_blind_compare`, `clauditor_triggers` — each closes
  over `--clauditor-grading-provider` / `--clauditor-model`,
  accepts `provider=` / `model=` factory kwargs (kwarg wins at call
  site), loads the spec, dispatches `_dispatch_fixture_auth_guard`
  and `_resolve_fixture_provider`, invokes the grader orchestrator.

Pure helpers (mirror the CLI seam):

- `src/clauditor/pytest_plugin.py::_resolve_fixture_provider` —
  threads `provider_override` as `cli_override` to
  `clauditor._providers.resolve_grading_provider`, honoring the
  same env > spec > auto chain. Does NOT swallow `ValueError`.
- `src/clauditor/pytest_plugin.py::_dispatch_fixture_auth_guard` —
  routes by resolved provider (anthropic strict/relaxed via
  `CLAUDITOR_FIXTURE_ALLOW_CLI`; openai always strict). Distinct
  exception classes preserve structural routing per
  `.claude/rules/multi-provider-dispatch.md`.

Test-infra coupling: the autouse
`_force_api_transport_in_tests` fixture in `tests/conftest.py`
pairs its `shutil.which → None` patch with
`monkeypatch.setenv("CLAUDITOR_HARNESS", "claude-code")` so the
harness resolver short-circuits at the env layer rather than
landing on the `which → None` path. Full pattern + rationale in
`.claude/rules/test-infra-shutil-which-coupling.md`; #155 DEC-011
inherits the recipe.

Traces to DEC-001 through DEC-012 of
`plans/super/155-pytest-fixtures-parametrize.md`. Companion rules:
`.claude/rules/spec-cli-precedence.md` (the CLI-side precedence
shape this rule mirrors),
`.claude/rules/multi-provider-dispatch.md` (the structural-routing
invariant the distinct fixture-side exception classes preserve),
`.claude/rules/precall-env-validation.md` (the eager auth-guard
pattern at the fixture seam),
`.claude/rules/back-compat-shim-discipline.md` (the value→factory
hard break),
`.claude/rules/test-infra-shutil-which-coupling.md` (the
autouse-PATH-pin coupling at fixture collection).

## When this rule applies

Any future multi-axis precedence resolver added to the CLI per
`.claude/rules/spec-cli-precedence.md` — the matching pytest
fixtures need to mirror the new axis in whichever of the three
shapes fits the fixture's available context. The mechanical recipe
when adding a new axis to an existing fixture:

1. Identify which shape the fixture belongs to (A / B / C).
2. For Shape A, expose the new axis as a factory kwarg, a pytest
   option, and an env var. Skip the spec layer.
3. For Shape B, read the new axis from `EvalSpec.<field>` only.
   Skip operator-intent layers.
4. For Shape C, expose the new axis at all four operator-intent
   layers + the spec field + the default.
5. Route the resolved value through the same pure resolver the CLI
   uses (do NOT re-implement precedence in `pytest_plugin.py`).
6. Mirror any per-axis eager auth-guard the CLI fires.

## When this rule does NOT apply

- Knobs that have no fixture-facing surface (library-internal
  constants, construction-time config). Fixtures don't need to
  mirror what users can't set anyway.
- Diagnostic / one-off scripts in `scripts/` that hit clauditor
  directly. They are not fixtures; they can resolve axes inline.
- Tests that mock the resolver entirely (`patch("clauditor._providers.resolve_*")`).
  The mock substitutes for the real resolver; no fixture-side
  mirroring is needed.
