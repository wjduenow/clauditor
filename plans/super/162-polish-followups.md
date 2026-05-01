# 162: Follow-ups from #145 — 4 polish items (C1–C4)

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/162
- **Branch:** `feature/162-polish-followups`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/162-polish-followups`
- **Phase:** devolved
- **PR:** https://github.com/wjduenow/clauditor/pull/163
- **Epic:** TBD (created at devolve)
- **Sessions:** 1 (2026-05-01)
- **Depends on:** #145 (CLOSED — multi-provider OpenAI shipped in #160)
- **Blocks:** none (all items independent of #146/#147)
- **Source beads:** `clauditor-pjm` (C1, P3), `clauditor-edb` (C2, P4), `clauditor-nl7` (C3, P4), `clauditor-m0m` (C4, P4)

## Discovery

### What

Umbrella issue grouping 4 independent polish/parity follow-ups discovered during QG passes for #145 (multi-provider OpenAI support). Each is small in scope; together they close out the parity gaps that QG surfaced but #145 chose not to land in the merge PR:

- **C1** — Provider-aware auth in pytest fixtures (`pytest_plugin.py`)
- **C2** — `clauditor doctor` reports `OPENAI_API_KEY` status (info-level)
- **C3** — `cli/suggest.py` plumbs `provider=` from spec to `propose_edits`
- **C4** — Both providers' retry loops catch base SDK error class as final fallback

### Why

QG passes for #145 surfaced four gaps where the OpenAI provider work is functionally complete but parity/observability is incomplete:

- C1: a user with `grading_provider="openai"` + `OPENAI_API_KEY` (no Anthropic key) hits the pytest fixtures and sees a misleading "ANTHROPIC_API_KEY missing" error.
- C2: OpenAI users have no way to verify their auth posture via `clauditor doctor`.
- C3: `clauditor suggest` always routes through Anthropic regardless of `eval.json`, silently overriding the spec author's `grading_provider` choice.
- C4: a future SDK version's new typed error not yet in the retry ladder would escape uncaught and bypass the exit-3 routing per `.claude/rules/llm-cli-exit-code-taxonomy.md`.

All four are low-risk, scoped to small surfaces, and inherit existing patterns from `#145`'s shipped work.

### Acceptance criteria (from ticket)

1. **C1**: pytest fixtures resolve provider from spec, dispatch through `check_provider_auth(provider, label)` with both `AnthropicAuthMissingError` and `OpenAIAuthMissingError` except branches. `CLAUDITOR_FIXTURE_ALLOW_CLI` semantics preserved for Anthropic; OpenAI always strict key-only.
2. **C2**: `clauditor doctor` emits an info-level (not error) check for `OPENAI_API_KEY` presence regardless of whether key is set.
3. **C3**: `cli/suggest.py` loads `EvalSpec`, resolves `provider = eval_spec.grading_provider or "anthropic"`, calls `check_provider_auth(provider, "suggest")` with dual except branches, passes `provider=` to `propose_edits(...)`.
4. **C4**: Both `_providers/_anthropic.py::call_anthropic` and `_providers/_openai.py::call_openai` retry loops catch base `AnthropicError` / `OpenAIError` as final except branch, wrapping as the corresponding `*HelperError`. Order preserves: subclass branches before base catch-all.

### Codebase findings (from Codebase Scout)

#### C1 — `src/clauditor/pytest_plugin.py`

Three fixtures requiring identical refactoring (lines 220–372):

- **`clauditor_grader`** lines 220–260 — auth check at lines 248–251 (between `_fixture_allow_cli()` branch on `check_any_auth_available` vs `check_api_key_only`); spec loaded at line 252.
- **`clauditor_blind_compare`** lines 292–341 — auth at 329–332; spec at 333.
- **`clauditor_triggers`** lines 345–372 — auth at 363–366; spec at 367.

Fix: reorder to load spec first (so `eval_spec.grading_provider` is reachable), then dispatch via `check_provider_auth(provider, fixture_label)` with both auth-missing except branches. `CLAUDITOR_FIXTURE_ALLOW_CLI` env var becomes Anthropic-only since OpenAI has no CLI transport.

Test surface: `tests/test_pytest_plugin.py` (no existing OpenAI-routed fixture tests; new tests needed).

#### C2 — `src/clauditor/cli/doctor.py`

Lines 50–141. Current shape:

- Lines 105–121 — `ANTHROPIC_API_KEY` `[ok]`/`[info]` check via `(label, status, message)` tuple appended to `checks` list.
- Lines 123–140 — `claude` CLI check mirrors same pattern.

Add: after line 121, append `("openai-key-available", "ok"/"info", "...")` tuple. Probe by env presence only (no SDK call). `[info]` regardless of presence per ticket — OpenAI is opt-in.

Test surface: `tests/test_doctor.py` — three existing tests (`ok_when_set`, `info_when_unset`, `info_when_whitespace`) for Anthropic; mirror for OpenAI.

#### C3 — `src/clauditor/cli/suggest.py`

`_cmd_suggest_impl` at lines 199–239:

- Line 205: `check_any_auth_available("suggest")` — Anthropic-only guard today.
- Line 235: `propose_edits(suggest_input, model=..., transport=...)` — no `provider=` kwarg.

`propose_edits` signature at `src/clauditor/suggest.py` lines 931–938 already accepts `provider="anthropic"` default. The plumbing target exists; only the CLI seam needs work.

Pattern to mirror: `cli/triggers.py` lines 114–127 — load `SkillSpec.from_file(skill_path)`, resolve provider, dispatch through `check_provider_auth`, pass `provider=` through.

Source of skill path: `args.skill` at line 110 — already in scope.

Test surface: `tests/test_suggest.py` — no provider-routed tests today; new test for OpenAI-spec routing.

#### C4 — `_providers/_anthropic.py` + `_providers/_openai.py`

**Anthropic** (`call_anthropic` retry loop, lines 513–603):
- Imports at 473–480: `RateLimitError, APIStatusError, AuthenticationError, PermissionDeniedError, APIConnectionError`. **`AnthropicError` not yet imported** — must add.
- Try block at 514–518; except ladder at 519–598 ends with `TypeError`.
- Add: `except anthropic.AnthropicError as exc: raise AnthropicHelperError(...) from exc` AFTER the TypeError branch (line 598).

**OpenAI** (`call_openai` retry loop, lines 370–447):
- Imports at 315–322: includes `OpenAIError` (already imported).
- Try block at 371–375; except ladder at 376–447 ends with `TypeError`.
- Add: `except OpenAIError as exc: raise OpenAIHelperError(...) from exc` AFTER the TypeError branch.

**Order critical**: bare-class catch-all MUST land after all subclass branches (`RateLimitError`, `APIStatusError`, `AuthenticationError`, `PermissionDeniedError`, `APIConnectionError`, `TypeError`) so subclass matching takes precedence.

Test surface:
- `tests/test_providers_anthropic.py` — typed-subclass tests exist; new test class for bare base-class wrap.
- `tests/test_providers_openai.py` — typed-subclass tests from #145 US-003/US-004; new test class for bare base-class wrap.

### Convention constraints (from Convention Checker)

| Rule | Item(s) | Constraint |
|------|---------|-----------|
| `multi-provider-dispatch.md` | C1, C3 | Resolve provider from spec at the seam; call `check_provider_auth(provider, cmd_name)` with distinct except branches per provider. |
| `precall-env-validation.md` | C1, C2, C3 | Pure auth helpers, raise distinct per-provider exception classes; CLI/fixture wrapper owns stderr + exit code. |
| `llm-cli-exit-code-taxonomy.md` | C3, C4 | Exit 2 for auth-missing, exit 3 for `*HelperError`. C4 catch-all preserves exit-3 routing for unknown SDK errors. |
| `centralized-sdk-call.md` | C3, C4 | All `call_model` calls dispatch through the centralized seam; retry policy + error categorization owned there. |
| `pure-compute-vs-io-split.md` | C2, C3 | Pure helpers (env-read, parse, validate); CLI wrapper owns I/O. |
| `mock-side-effect-for-distinct-calls.md` | C1 | Tests with mocks called >1x per test must use `side_effect=[...]`, not `return_value`. |
| `back-compat-shim-discipline.md` | n/a | No new exception classes introduced (only re-using existing); class-identity invariant unaffected. |
| `rule-refresh-vs-delete.md` | post-merge | `multi-provider-dispatch.md` "When this rule does NOT apply" §("Pytest fixtures... routing through the Anthropic helpers... per-provider fixture dispatch is forward-compat work") becomes stale once C1 lands — refresh in Patterns & Memory story. |

Rules NOT applicable: `monotonic-time-indirection`, `non-mutating-scrub`, `path-validation`, `skill-identity-from-frontmatter`, `bundled-skill-docs-sync`, `json-schema-version`, `constant-with-type-info`, `pre-llm-contract-hard-validate` (anchor validator already in place — no new contract added).

## Phase 1 scoping (answered)

- **Q1 = A** — Single PR with 4 implementation stories + Quality Gate + Patterns & Memory. Matches the umbrella issue's intent and lets the reviewer see all parity gaps closed in one delta.
- **Q2 = A** — `SkillSpec.from_file(args.skill)` then read `skill_spec.eval_spec.grading_provider`. Mirrors `cli/triggers.py:114-127` exactly. No new resolution path invented.
- **Q3 = A** — `f"API request failed: {exc}"`. Uniform across both providers. `str(exc)` on Anthropic's typed errors already calls into the SDK's reasonable `__str__`.
- **Q4 = A** — `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is silently ignored when provider resolves to `"openai"`. The env var is documented today as gating CLI transport (Anthropic-only); OpenAI has no CLI transport, so the variable is a no-op for that branch. No warning needed — the user sees no behavior change.
- **Q5 = B** — Per-provider `TestBaseErrorCatchAll` class with two tests each: (1) bare base-class instance wraps to `*HelperError("API request failed: ...")`, (2) ordering-regression — `RateLimitError` (subclass) still caught by its specific branch, not the new catch-all.

## Phase 2 architecture review

| Area | Rating | Notes |
|------|--------|-------|
| **Security — auth routing** | pass | (1) `SkillSpec.from_file` is pure file-read+parse+validate (no side effects pre-auth). (2) Missing-spec edge case: existing `_load_spec_or_report` in `cli/triggers.py` returns `None` on read failure → exit 2 before any auth check. (3) `check_provider_auth` routes structurally by exact string match — no fallback when provider="openai". (4) `check_openai_auth` is unconditionally strict (no CLI fallback). (6) No env values logged — only fixed templates and presence indicators. |
| **Security — C4 catch-all message content** | concern | `f"API request failed: {exc}"` (Q3=A) calls `str(exc)` on the SDK exception. Today's typed branches already include body excerpts (truncated) for `APIStatusError`, but the catch-all is for *future* SDK error types we haven't seen. Risk: a hypothetical future SDK error whose `__str__` echoes prompt/response body would surface in stderr. See refinement Q-R1. |
| **Performance** | pass | No new queries, no new loops, no new I/O. C2 reads env (O(1)). C3 adds one `SkillSpec.from_file` (already on the path for triggers/grade). C4 is a single new except branch. |
| **Data Model** | pass | No schema changes, no new sidecars, no migrations. |
| **API Design** | pass | `propose_edits` already accepts `provider=` kwarg (default "anthropic"); C3 only plumbs it. C4 wraps as existing `*HelperError` types (no new exception classes). C2 extends doctor's existing `(label, status, message)` tuple format. |
| **Observability** | pass | C2 surfaces OpenAI auth posture in `clauditor doctor` (info-level — no false-error noise for Anthropic-only users). C4 ensures unknown SDK errors land in the existing exit-3 routing instead of escaping as raw tracebacks. |
| **Testing strategy** | concern | (1) C1 falls under `pytester-inprocess-coverage-hazard.md` — new tests must use `__wrapped__` direct calls, NOT `runpytest_inprocess + mock.patch` on already-coverage-instrumented modules. (2) C3 vacuous-test risk — tests must construct an EvalSpec with a *distinct* `grading_provider="openai"` value (not the default) and assert the value flows to `call_model`'s `provider=` kwarg. (3) C4 catch-all dead-code risk — first test MUST use a bare `Exception` instance NOT in any typed branch's class hierarchy. Both concerns are scoping refinements, not blockers — embedded in story acceptance criteria. |

### Refinement decisions (DEC log)

- **DEC-001** — PR shape. Single PR with 4 implementation stories + Quality Gate + Patterns & Memory. Rationale: matches the umbrella-issue intent, lets reviewers see all parity gaps closed in one delta, no cross-story dependencies require ordering. (Q1=A)
- **DEC-002** — C3 EvalSpec source. `cli/suggest.py` loads via `SkillSpec.from_file(args.skill)` and reads `skill_spec.eval_spec.grading_provider`. Mirrors `cli/triggers.py:114-127`; no new resolution path invented. (Q2=A)
- **DEC-003** — C4 message format. `f"API request failed: {type(exc).__name__}: {str(exc)[:500]}"` for both providers. Class name is always safe and useful; 500-char cap on `str(exc)` bounds any prompt/body leakage well below typical sizes while preserving diagnostic info ("invalid_request_error: ..."). Supersedes Q3=A after security review concern. (Q-R1=C)
- **DEC-004** — `CLAUDITOR_FIXTURE_ALLOW_CLI=1` UX with `provider="openai"`. Silently no-op. The env var is documented today as gating CLI transport (Anthropic-only); OpenAI has no CLI transport, so the variable has nothing to gate. No warning, no error. (Q4=A)
- **DEC-005** — C4 test coverage shape. Per-provider `TestBaseErrorCatchAll` class with two tests each: (1) bare base-class wraps to `*HelperError("API request failed: ...")`, (2) ordering regression — `RateLimitError` (subclass) still routes to its specific branch. (Q5=B)
- **DEC-006** — C1 test discipline (pytester-coverage-hazard guardrail). New tests for the OpenAI fixture path use `__wrapped__` direct calls, NOT `runpytest_inprocess + mock.patch` on already-coverage-instrumented modules. Per `.claude/rules/pytester-inprocess-coverage-hazard.md`. (Architecture review concern.)
- **DEC-007** — C3 vacuous-test guardrail. Tests construct an EvalSpec with a *distinct* `grading_provider="openai"` value (NOT the default-anthropic path) and assert `call_mock.await_args.kwargs["provider"] == "openai"` — proves the field is actually consulted, not just plumbed inertly. (Architecture review concern.)
- **DEC-008** — C4 dead-code guardrail. The first catch-all test MUST construct an `AnthropicError`/`OpenAIError` instance that is NOT a subclass of any specifically-caught typed exception (`RateLimitError`, `APIStatusError`, `AuthenticationError`, `PermissionDeniedError`, `APIConnectionError`). Otherwise the test passes via the typed branch and the catch-all is unexercised. (Architecture review concern.)
- **DEC-009** — Post-merge rule refresh. `.claude/rules/multi-provider-dispatch.md` (and `precall-env-validation.md`) currently document "Pytest fixtures... routing through the Anthropic helpers... per-provider fixture dispatch is forward-compat work". Once US-001 lands, that's stale. Refresh in-place per `.claude/rules/rule-refresh-vs-delete.md`; preserve historical validation notes byte-verbatim.

## Phase 4 — Detailed Breakdown

Six stories total. Stories US-001 through US-004 are independent (no cross-dependencies); US-005 (Quality Gate) depends on all four; US-006 (Patterns & Memory) depends on US-005. Order below matches the ticket's C1→C4 enumeration; Ralph may execute US-001 through US-004 in parallel.

### US-001 — C1: Provider-aware auth in pytest fixtures

**Description.** Reorder the three `pytest_plugin.py` fixtures (`clauditor_grader`, `clauditor_blind_compare`, `clauditor_triggers`) to load the `SkillSpec` *before* the auth check, then resolve `provider = spec.eval_spec.grading_provider or "anthropic"` and dispatch through `check_provider_auth(provider, fixture_label)` with both `AnthropicAuthMissingError` and `OpenAIAuthMissingError` except branches. `CLAUDITOR_FIXTURE_ALLOW_CLI=1` becomes Anthropic-only (silently no-op when provider resolves to `"openai"`).

**Traces to.** DEC-001, DEC-004, DEC-006.

**Acceptance criteria.**
- All three fixtures resolve `provider` from `spec.eval_spec.grading_provider` with `"anthropic"` fallback when None or absent.
- Dispatch routes through `check_provider_auth(provider, fixture_label)`; both auth-missing exceptions caught explicitly per `.claude/rules/multi-provider-dispatch.md`.
- `CLAUDITOR_FIXTURE_ALLOW_CLI=1` toggle is read only inside the Anthropic branch; silently no-op when provider resolves to `"openai"` (DEC-004).
- New tests in `tests/test_pytest_plugin.py::TestClauditorFixturesAuthGuardOpenAI` covering: provider="openai" + missing OPENAI_API_KEY raises `OpenAIAuthMissingError` (no Anthropic fallback even when `ANTHROPIC_API_KEY` is set); provider="openai" + key set passes; provider="openai" + `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is no-op.
- New tests use `__wrapped__` direct calls per `.claude/rules/pytester-inprocess-coverage-hazard.md` (DEC-006).
- Existing `TestClauditorFixturesAuthGuard` tests still pass (Anthropic path unchanged).
- `uv run ruff check src/ tests/` passes; `uv run pytest --cov=clauditor --cov-report=term-missing` passes; coverage ≥80%.

**Done when.** Three fixtures dispatch through `check_provider_auth`; OpenAI branch is unit-tested via `__wrapped__`; existing Anthropic tests green; quality gates clean.

**Files.**
- `src/clauditor/pytest_plugin.py` — three fixtures' auth-check + spec-load reorder (~lines 220-372).
- `tests/test_pytest_plugin.py` — new `TestClauditorFixturesAuthGuardOpenAI` class.

**Depends on.** none.

**TDD.**
- T1: provider="openai" + OPENAI_API_KEY unset + ANTHROPIC_API_KEY set → `OpenAIAuthMissingError` raised at factory invocation (no Anthropic fallback).
- T2: provider="openai" + OPENAI_API_KEY set → fixture factory returns successfully.
- T3: provider="openai" + `CLAUDITOR_FIXTURE_ALLOW_CLI=1` set → strict OpenAI key check still applied (env var is no-op).
- T4: provider unset (None in spec) + ANTHROPIC_API_KEY set → existing Anthropic happy path.
- T5: provider="anthropic" explicit + `CLAUDITOR_FIXTURE_ALLOW_CLI=1` → existing relaxed-guard behavior preserved.

### US-002 — C2: clauditor doctor checks OPENAI_API_KEY

**Description.** Add an info-level `OPENAI_API_KEY` presence check to `cli/doctor.py` after the existing `ANTHROPIC_API_KEY` check. Probe by env presence only; status is `[ok]` when set + non-whitespace, `[info]` when unset or whitespace-only (never `[fail]` — OpenAI is opt-in via `grading_provider="openai"`).

**Traces to.** DEC-001.

**Acceptance criteria.**
- `cli/doctor.py` appends `("openai-api-key-available", "ok"|"info", message)` tuple after the `ANTHROPIC_API_KEY` check (~line 121).
- Status logic mirrors Anthropic's: `[ok]` if key set + non-whitespace; `[info]` if unset or whitespace-only.
- Three new tests in `tests/test_doctor.py::TestDoctorOpenAIKeyCheck` mirroring the three Anthropic tests (`ok_when_set`, `info_when_unset`, `info_when_whitespace`).
- Doctor output never echoes the key value itself — only presence status.
- Existing Anthropic and `claude` CLI doctor tests still pass.
- `uv run ruff check src/ tests/` passes; coverage ≥80%.

**Done when.** Running `clauditor doctor` with `OPENAI_API_KEY` set shows the new `[ok]` line; unset shows `[info]`. Three new tests green.

**Files.**
- `src/clauditor/cli/doctor.py` — add OpenAI check tuple after line 121.
- `tests/test_doctor.py` — new `TestDoctorOpenAIKeyCheck` class with 3 tests.

**Depends on.** none.

**TDD.**
- T1: `OPENAI_API_KEY="sk-test-1"` → `[ok]` line present in output, `OPENAI_API_KEY` substring present.
- T2: `OPENAI_API_KEY` unset → `[info]` line present (NOT `[fail]`).
- T3: `OPENAI_API_KEY="   "` (whitespace-only) → `[info]` (matches Anthropic's whitespace handling).

### US-003 — C3: cli/suggest.py provider plumbing

**Description.** Extend `_cmd_suggest_impl` in `src/clauditor/cli/suggest.py` to load `SkillSpec.from_file(args.skill)` (mirror `cli/triggers.py:114-127` per DEC-002), resolve `provider = spec.eval_spec.grading_provider or "anthropic"`, dispatch through `check_provider_auth(provider, "suggest")` with both auth-missing except branches (exit 2), and pass `provider=provider` to `propose_edits(...)`. Replace the existing single `check_any_auth_available("suggest")` call.

**Traces to.** DEC-001, DEC-002, DEC-007.

**Acceptance criteria.**
- `_cmd_suggest_impl` loads `SkillSpec` AFTER arg validation + dry-run early return, BEFORE the auth check (per `.claude/rules/precall-env-validation.md`).
- Provider resolves from `spec.eval_spec.grading_provider` (None → `"anthropic"` fallback).
- `check_provider_auth(provider, "suggest")` replaces `check_any_auth_available("suggest")`; both `AnthropicAuthMissingError` and `OpenAIAuthMissingError` caught with distinct exit-2 branches per `.claude/rules/llm-cli-exit-code-taxonomy.md`.
- `propose_edits(suggest_input, model=..., transport=..., provider=provider)` — new kwarg plumbed through.
- Spec-load failure (`FileNotFoundError`, `ValueError` from EvalSpec validation) exits with crisp ERROR message before any auth check (mirrors `_load_spec_or_report` shape from `cli/triggers.py`).
- New tests in `tests/test_suggest.py::TestCmdSuggestProviderPlumbing` use a *distinct* `grading_provider="openai"` value (NOT default), patch `clauditor._providers.call_model`, run `cmd_suggest(args)`, assert `call_mock.await_args.kwargs["provider"] == "openai"` (DEC-007).
- `uv run ruff check src/ tests/` passes; coverage ≥80%.

**Done when.** `clauditor suggest` on a skill with `grading_provider="openai"` routes through OpenAI; existing Anthropic flow unchanged; tests green.

**Files.**
- `src/clauditor/cli/suggest.py` — add SkillSpec load + provider resolution + dual-except auth dispatch + provider kwarg plumbing (~lines 199-239).
- `tests/test_suggest.py` — new `TestCmdSuggestProviderPlumbing` class.

**Depends on.** none.

**TDD.**
- T1: skill with `grading_provider="openai"` + OPENAI_API_KEY set → `call_model` receives `provider="openai"` (DEC-007 distinct-value assertion).
- T2: skill with no `grading_provider` field → `call_model` receives `provider="anthropic"` (default path).
- T3: skill with `grading_provider="openai"` + OPENAI_API_KEY unset → exits 2, stderr contains `OpenAIAuthMissingError` template, no `call_model` invocation.
- T4: skill with `grading_provider="openai"` + ANTHROPIC_API_KEY set + OPENAI_API_KEY unset → still exits 2 (no Anthropic fallback even when Anthropic auth is available).
- T5: missing skill file → exits 1 (or 2 per existing convention) with crisp error before any auth check.

### US-004 — C4: base helper-error catch-all in retry loops

**Description.** Add a final `except` branch in both `_providers/_anthropic.py::call_anthropic` and `_providers/_openai.py::call_openai` retry loops that catches the SDK base exception class (`anthropic.AnthropicError` / `openai.OpenAIError`) and wraps as the corresponding `*HelperError` with message format `f"API request failed: {type(exc).__name__}: {str(exc)[:500]}"` (DEC-003). The catch-all lands AFTER all typed branches so subclass matching takes precedence per Python's `except` first-match semantics.

**Traces to.** DEC-001, DEC-003, DEC-005, DEC-008.

**Acceptance criteria.**
- `src/clauditor/_providers/_anthropic.py`: import `AnthropicError` from anthropic SDK; add `except AnthropicError as exc:` AFTER the `TypeError` branch in `call_anthropic`'s retry-loop ladder. Wraps as `AnthropicHelperError(f"API request failed: {type(exc).__name__}: {str(exc)[:500]}") from exc`.
- `src/clauditor/_providers/_openai.py`: `OpenAIError` is already imported; add `except OpenAIError as exc:` AFTER the `TypeError` branch in `call_openai`'s retry-loop ladder. Same wrap shape.
- Order verified: bare base catch-all is LAST in each ladder; subclass branches (`RateLimitError`, `APIStatusError`, `AuthenticationError`, `PermissionDeniedError`, `APIConnectionError`, `TypeError`) still match first.
- New `tests/test_providers_anthropic.py::TestCallAnthropicBaseErrorCatchAll` class with two tests:
  - `test_bare_anthropic_error_wraps_to_helper_error`: construct an `AnthropicError` instance NOT in any typed branch's hierarchy (DEC-008), mock client raises it, assert `AnthropicHelperError` raised with `"API request failed:"` prefix and class name in message; `__cause__` preserves original.
  - `test_rate_limit_subclass_routes_to_specific_branch_not_catch_all`: ordering regression — exhaust `RateLimitError` retries, assert message indicates rate-limit-specific handling (NOT generic catch-all phrasing); `mock_client.messages.create.await_count == 4`.
- Mirror `tests/test_providers_openai.py::TestCallOpenAIBaseErrorCatchAll` with the same two-test shape.
- Existing typed-branch tests in both files still pass (subclass branches unchanged).
- `uv run ruff check src/ tests/` passes; coverage ≥80%.

**Done when.** Both retry loops have catch-all branches; four new tests (two per provider) green; existing tests green.

**Files.**
- `src/clauditor/_providers/_anthropic.py` — add `AnthropicError` import + new except branch in `call_anthropic`'s retry ladder.
- `src/clauditor/_providers/_openai.py` — add new except branch in `call_openai`'s retry ladder (`OpenAIError` already imported).
- `tests/test_providers_anthropic.py` — new `TestCallAnthropicBaseErrorCatchAll` class with 2 tests.
- `tests/test_providers_openai.py` — new `TestCallOpenAIBaseErrorCatchAll` class with 2 tests.

**Depends on.** none.

**TDD.**
- T1 (Anthropic catch-all): `AnthropicError("simulated unknown failure")` instance (DEC-008 — not a subclass of any typed branch) → `AnthropicHelperError`, message contains `"API request failed:"` AND `"AnthropicError"` AND truncated payload.
- T2 (Anthropic ordering): `RateLimitError` exhausted → message indicates rate-limit (subclass branch wins); `await_count` proves retry policy applied.
- T3 (OpenAI catch-all): `OpenAIError("simulated unknown failure")` → `OpenAIHelperError`, message contains `"API request failed:"` AND `"OpenAIError"` AND truncated payload.
- T4 (OpenAI ordering): `RateLimitError` exhausted → rate-limit-specific message; ordering preserved.

### US-005 — Quality Gate

**Description.** Run code-reviewer agent 4 times across the full changeset, fixing all real findings each pass. Run CodeRabbit (or pr-reviewer agent) once on the staged diff. Project validation must pass after all fixes.

**Traces to.** DEC-001.

**Acceptance criteria.**
- `uv run ruff check src/ tests/` passes (no warnings).
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes; coverage ≥80%.
- 4 passes of code-reviewer agent across the full diff vs `dev` branch; each pass either fixes all real findings OR documents false positives in the plan's Session Notes.
- 1 pass of CodeRabbit review (or pr-reviewer agent) on the staged diff; same fix-or-document policy.
- All US-001 through US-004 new tests still pass; pre-existing test suite still green.
- No new TODO comments, no skipped tests added, no `--no-verify` git operations.

**Done when.** All quality gates green; reviewer findings resolved or documented; final `pytest` + `ruff` clean.

**Files.** Any files touched by US-001 through US-004 (review surface).

**Depends on.** US-001, US-002, US-003, US-004.

### US-006 — Patterns & Memory

**Description.** Refresh `.claude/rules/*.md` files whose canonical-implementation sections describe surfaces modified in this batch. Per `.claude/rules/rule-refresh-vs-delete.md`: refresh in-place, preserve historical validation notes byte-verbatim. No new rules introduced.

**Traces to.** DEC-001, DEC-009.

**Acceptance criteria.**
- `.claude/rules/multi-provider-dispatch.md` — refresh "When this rule does NOT apply" section to reflect that pytest fixtures NOW route through `check_provider_auth` (the "forward-compat work for a future ticket" sentence becomes stale once US-001 lands).
- `.claude/rules/precall-env-validation.md` — spot-check the "Pytest fixtures (three, all routing through the Anthropic helpers — per-provider fixture dispatch is forward-compat work)" mention in the Canonical implementation section; refresh to match the post-#162 reality.
- `.claude/rules/llm-cli-exit-code-taxonomy.md` — Canonical implementation section: add `cli/suggest.py` to the list of single-call commands routing through `check_provider_auth` with the dual-except shape.
- `.claude/rules/centralized-sdk-call.md` — Canonical implementation section: add a one-paragraph note about the C4 base-class catch-all in both providers' retry loops (defense-in-depth against unknown future SDK error types).
- All four refreshed rules pass any rule-self-test if present (e.g. `tests/test_rules.py` if it exists; otherwise N/A).
- No new memory files added (the patterns are captured in the rules above).

**Done when.** Four rule files refreshed; all post-#162 documentation reflects the new shape; preserved historical validation notes unchanged.

**Files.**
- `.claude/rules/multi-provider-dispatch.md`
- `.claude/rules/precall-env-validation.md`
- `.claude/rules/llm-cli-exit-code-taxonomy.md`
- `.claude/rules/centralized-sdk-call.md`

**Depends on.** US-005.

## Beads Manifest

- **Epic:** `clauditor-3dy` — `#162: Follow-ups from #145 — 4 polish items (C1–C4)` (P3)
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/162-polish-followups`
- **Branch:** `feature/162-polish-followups`
- **PR:** https://github.com/wjduenow/clauditor/pull/163

### Tasks (all parented under `clauditor-3dy`)

| Story | Bead | Title | Priority | Depends on |
|-------|------|-------|----------|------------|
| US-001 | `clauditor-pjm` | C1: pytest fixtures provider-aware auth | P3 | none |
| US-002 | `clauditor-edb` | C2: clauditor doctor checks `OPENAI_API_KEY` | P4 | none |
| US-003 | `clauditor-nl7` | C3: cli/suggest.py provider plumbing | P4 | none |
| US-004 | `clauditor-m0m` | C4: base helper-error catch-all | P4 | none |
| US-005 | `clauditor-3dy.1` | Quality Gate — code review x4 + CodeRabbit | P3 | clauditor-pjm, clauditor-edb, clauditor-nl7, clauditor-m0m |
| US-006 | `clauditor-3dy.2` | Patterns & Memory — refresh 4 `.claude/rules/*.md` | P3 | clauditor-3dy.1 |

US-001 through US-004 (the four `C*` source beads) are independent and Ralph-parallelizable — `bd ready` shows them all as unblocked.

Each implementation bead carries an appended `notes` block pointing to its US-### in this plan and the DEC-### entries it traces to (so a Ralph worker reading the bead in isolation has the full context: plan path, story id, decisions, test discipline guardrails).
