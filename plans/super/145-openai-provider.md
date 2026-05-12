# 145: Multi-provider — add OpenAI provider via Responses API

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/145
- **Branch:** `feature/145-openai-provider`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/145-openai-provider`
- **Phase:** devolved
- **PR:** https://github.com/wjduenow/clauditor/pull/160
- **Epic:** clauditor-2cq
- **Sessions:** 1 (2026-04-29)
- **Depends on:** #144 (CLOSED — `call_model` dispatcher + `ModelResult` envelope shipped)
- **Blocks:** #146 (`grading_provider` precedence), #147 (sidecar v3 with `provider` field)

## Discovery

### What

Add `src/clauditor/_providers/_openai.py` as a sibling to `_anthropic.py`. Implement `call_openai(...)` against the OpenAI **Responses API** with retry/error parity to `call_anthropic`. Wire into the existing `call_model` dispatcher (today raises `NotImplementedError("openai provider lands in #145")` at `_providers/__init__.py:168-169`). Add `OpenAIAuthMissingError` + `check_openai_auth(cmd_name)` mirroring the Anthropic guard pattern.

### Why

Epic A, ticket 2 of 4 in the multi-provider initiative (#143). #144 shipped the dispatcher and `ModelResult` envelope; this ticket fills the OpenAI stub. Responses API was chosen over Chat Completions because its `usage.input_tokens` / `usage.output_tokens` field names match Anthropic's, keeping `ModelResult` uniform with no translation layer (confirmed by domain research — see Refinement). Unblocks #146 (the four-layer precedence resolver).

### Acceptance criteria (from ticket)

1. Unit tests parallel to `test_providers_anthropic.py` covering retry/error branches (429 retries, 5xx transient, 4xx non-retriable, 401 immediate fail) and error categorization.
2. An eval spec with `grading_provider="openai"` runs L2 extraction and L3 grading end-to-end.
3. `ModelResult.input_tokens` / `output_tokens` populate correctly from Responses API `usage` fields.
4. Auth guard fails with actionable error when `OPENAI_API_KEY` is unset.

### Codebase findings (from Codebase Scout)

**Existing Anthropic surface to mirror:**
- `src/clauditor/_providers/__init__.py:101-173` — `call_model` dispatcher; `provider="openai"` branch at line 168-169 raises `NotImplementedError`.
- `src/clauditor/_providers/_anthropic.py:467-522` — `call_anthropic(prompt, *, model, max_tokens=4096, transport="auto", subject=None) -> ModelResult`.
- `src/clauditor/_providers/_anthropic.py:84-110` — module-level test-indirection aliases `_sleep`, `_monotonic`, `_rand_uniform`, `_rng`.
- `src/clauditor/_providers/_anthropic.py:114-116` — retry constants: `_RATE_LIMIT_MAX_RETRIES = 3`, `_SERVER_MAX_RETRIES = 1`, `_CONN_MAX_RETRIES = 1`.
- `src/clauditor/_providers/_anthropic.py:255-309` — `_compute_backoff` (`2**i` ± 25% jitter) + `_compute_retry_decision` ladder.
- `src/clauditor/_providers/_anthropic.py:194-244` — `ModelResult` dataclass; `provider: Literal["anthropic", "openai"]` + `source: Literal["api", "cli"]` already in place.
- `src/clauditor/_providers/_auth.py:214-252` — `check_any_auth_available(cmd_name)` (key OR claude-CLI). `_auth.py:255-290` — `check_api_key_only(cmd_name)`. Both raise `AnthropicAuthMissingError`.
- `src/clauditor/_anthropic.py:106-150` — back-compat shim `call_anthropic` wrapper.

**Call sites already passing `provider="anthropic"` to `call_model`:**
- `grader.py::_quality_grade` (line ~821)
- `suggest.py::suggest_improvements` (line ~999)
- `triggers.py::extract_json_trigger` (line ~194)
- `quality_grader.py::grade_quality` (lines ~812 and ~820, two parallel calls in `blind_compare`)

All four sites use `from clauditor._providers import call_model`. Once `provider="openai"` works, callers swap by passing the resolved provider value.

**CLI auth wiring (4 LLM-mediated commands):**
- `cli/grade.py:327`, `cli/extract.py:99`, `cli/triggers.py:107`, `cli/propose_eval.py:376` — all call `check_any_auth_available(cmd_name)` AFTER `--dry-run` early-return. (Note: spec mentions 6 commands; only 4 of the modern CLI shape route through `call_model` today.)

**Test surface to mirror:**
- `tests/test_providers_anthropic.py` — 24 test classes covering: extract/excerpt/jitter/backoff/success/rate-limit/server-error/client-error/connection/import-error/type-error/retry-decision/CLI/result-fields/auto-transport/announcements/transport-resolution/classify/model-result/call-model/grader-imports.
- `tests/test_providers_auth.py` — `TestExceptionClassIdentity`, `TestApiKeyIsSet`, etc.

**Other infrastructure:**
- `pyproject.toml:29` — `dependencies = ["anthropic>=0.40.0"]`. `[project.optional-dependencies] grader = []` is an empty back-compat marker.
- No pricing tables exist (cost is heuristic UX-only; `audit.py` has no per-model dicts).
- `cli/__init__.py:58-89` — `_resolve_grader_transport` is global, not per-provider; `transport` is Anthropic-specific (DEC-002 of #144 documents OpenAI ignores it).
- Three announcement flags live in `_providers`: `_announced_cli_transport` (transport), `_announced_implicit_no_api_key` (auth), `_announced_call_anthropic_deprecation` (shim).

### Convention constraints (from Convention Checker)

Applicable rules and the constraint each imposes on this work:

1. **`centralized-sdk-call.md`** — `_openai.py` must dispatch through `call_model`; document the OpenAI backend body shape parallel to the Anthropic one in the rule's "Canonical implementation" section.
2. **`precall-env-validation.md`** — Distinct `OpenAIAuthMissingError(Exception)` class (subclass of `Exception` directly, NOT `OpenAIHelperError`). Pure helper `check_openai_auth(cmd_name)` reads `os.environ`, raises with single template + `{cmd_name}` interpolation. No stderr inside helper.
3. **`llm-cli-exit-code-taxonomy.md`** — `OpenAIAuthMissingError` → exit 2; `OpenAIHelperError` → exit 3. Distinct except branches in CLI dispatchers, not substring matching.
4. **`monotonic-time-indirection.md`** — `_monotonic = time.monotonic` module-level alias in `_openai.py` if measuring duration; tests patch `clauditor._providers._openai._monotonic`.
5. **`pure-compute-vs-io-split.md`** — Pure helpers (e.g. `_extract_openai_result`, `_compute_backoff` if duplicated, body-excerpt helper) split from the async I/O wrapper. Use the same naming convention as the Anthropic counterparts.
6. **`non-mutating-scrub.md`** — Any `strip_openai_auth_keys(env)` helper returns new dict, never mutates input.
7. **`back-compat-shim-discipline.md`** — Does NOT apply (no historical callers to preserve).
8. **`json-schema-version.md`** — If `ModelResult.source` gains an `"openai-api"` value, downstream sidecars (`grading.json`, `extraction.json`) may need a schema bump. Today `source` is `Literal["api", "cli"]` and `provider` is `Literal["anthropic", "openai"]` — `provider` already accommodates the new value, so no bump expected for #145 (defer the audit-loader work to #147).
9. **`mock-side-effect-for-distinct-calls.md`** — Tests calling OpenAI twice (e.g. blind-compare two-arm parity) use `side_effect=[...]`, not `return_value=...`.
10. **`spec-cli-precedence.md`** — Defer the four-layer `grading_provider` precedence to #146; #145 should not introduce that field's resolution chain.

### OpenAI Responses API findings (from Domain Expert)

- **SDK package:** `openai>=1.66.0` (March 2025; first release with `client.responses.create()` + `AsyncOpenAI` async support).
- **Call shape:** `await client.responses.create(model=..., input=prompt, max_output_tokens=4096)`. Note: `input=` not `messages=`; `max_output_tokens` not `max_tokens`.
- **Token usage:** `response.usage.input_tokens` / `response.usage.output_tokens` — confirmed parallel to Anthropic. Detail fields (`input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`) deferred to #154.
- **Text extraction:** prefer `response.output_text` convenience accessor; walk `response.output[].content[].text` filtering `type=="message"` to populate `text_blocks`.
- **Exception taxonomy:** 1:1 parallel — `AuthenticationError`, `PermissionDeniedError`, `RateLimitError`, `APIStatusError`, `APIConnectionError`. Retry policy transplants directly.
- **Refusal/incomplete shape:** `response.status` + `response.incomplete_details.reason` (NOT `stop_reason`). Document divergence; do not normalize.
- **Default models:** issue's `gpt-5.4` / `gpt-5.4-mini` are not real identifiers (knowledge cutoff Jan 2026). Recommend `gpt-4.1` (L3) and `gpt-4.1-mini` (L2) — both Responses-API-native, currently strongest non-reasoning tier.
- **Auth env var:** `OPENAI_API_KEY` only. `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` / `OPENAI_BASE_URL` are SDK-handled; no clauditor guard needed.
- **Reasoning (o-series):** defer entirely. Different kwargs, different output filtering, different cost model — own design problem, likely overlaps with #154.

### Open scoping questions (for user)

(See "Phase 1 scoping" block below — to be answered before architecture review.)

## Phase 1 scoping (questions for user)

**Q1 — Default model strings.** Issue specifies `gpt-5.4` / `gpt-5.4-mini` which don't exist. What should ship as the default fallback when no `--grading-model` / `EvalSpec.grading_model` is set?

- **A.** `gpt-4.1` (L3) + `gpt-4.1-mini` (L2) per domain expert recommendation (Responses-API-native, current strongest non-reasoning tier).
- **B.** `gpt-4o` + `gpt-4o-mini` (older, broader compatibility, cheaper).
- **C.** Pin to a specific snapshot (e.g. `gpt-4.1-2025-04-14`) for reproducibility.
- **D.** No defaults — require `model=` to be set when `provider="openai"`; raise `ValueError` otherwise.

**Q2 — `transport=` parameter semantics for OpenAI.** OpenAI has no CLI subscription path. What happens when a caller passes `transport=` to `call_model(provider="openai", ...)`?

- **A.** Silently ignored. OpenAI always reports `source="api"` on the returned `ModelResult`. (Matches the existing dispatcher docstring at line 137.)
- **B.** `transport="cli"` raises `ValueError("openai provider does not support cli transport")`; `"auto"` and `"api"` both resolve to `"api"`.
- **C.** `"auto"` → silent `"api"`; explicit `"cli"` raises (defensive: catches a misconfigured eval spec).

**Q3 — `grading_provider` spec-field handling.** Acceptance criterion 2 requires "an eval spec with `grading_provider="openai"` runs end-to-end" — but #146 owns the four-layer precedence resolver. How much of the spec-field wiring lands in #145?

- **A.** Minimal spec field: `EvalSpec.grading_provider: Literal["anthropic","openai"] | None = None` with load-time validation; the four grader call sites read `eval_spec.grading_provider or "anthropic"` and pass that to `call_model`. CLI flag deferred to #146.
- **B.** Spec field + CLI flag (`--grading-provider {anthropic,openai}`) but no env-var or auto-inference; #146 adds the env-var layer + auto-inference from the model string.
- **C.** Strictly defer all spec/CLI wiring to #146; #145 makes the dispatch work but acceptance criterion 2 is tested via direct `call_model(provider="openai", ...)` calls in unit tests rather than end-to-end. Document the deferral and update the ticket's acceptance.

**Q4 — `openai` SDK dependency placement.** How is the OpenAI SDK installed?

- **A.** Direct dependency alongside `anthropic`: `dependencies = ["anthropic>=0.40.0", "openai>=1.66.0"]`. Both providers always available; no install-time choice. Matches today's Anthropic shape.
- **B.** Optional extra: `[project.optional-dependencies] openai = ["openai>=1.66.0"]`. Users who only grade with Claude don't pay for the second SDK; importing `_openai.py` raises a clean `ImportError` with `pip install clauditor[openai]` hint.
- **C.** Direct dependency, but bump the existing empty `grader = []` extra to `grader = ["openai>=1.66.0"]` for symmetry (anthropic stays direct).

**Q5 — Reasoning model (o-series) support.** Defer or ship?

- **A.** Defer entirely. No `reasoning=` kwarg, no o-series defaults, no `output[].type == "reasoning"` filtering. Document a follow-up ticket. Keeps #145 mechanical.
- **B.** Accept a `reasoning_effort: str | None = None` kwarg on `call_openai` (not `call_model`) for power users who want it via direct calls; do not wire through the dispatcher or graders.
- **C.** Full support including o-series defaults (e.g. `o4-mini` for L3) — likely 1.5x scope.

## Architecture Review

### Phase 2 (2026-04-29)

| Area | Rating | Key finding |
|---|---|---|
| Security | **blocker** | Multi-provider auth dispatch at 4 CLI seams — `check_any_auth_available` is Anthropic-only; need provider-aware routing |
| API design | concern | Retry helpers (`_compute_backoff`, `_compute_retry_decision`) — hoist to shared `_providers/_retry.py` (DEC-006) or duplicate (symmetry) |
| Test strategy | pass | ~50–55 new test methods estimated; mock target paths clear (`clauditor._providers._openai.AsyncOpenAI`) |
| Data model & observability | pass | `ModelResult.provider` already in place; sidecar v3 deferred to #147 explicitly; no new announcement flags needed |

#### Findings detail

**SEC-1 (BLOCKER) — Multi-provider auth dispatch.** `cli/grade.py:327`, `cli/extract.py:99`, `cli/triggers.py:107`, `cli/propose_eval.py:376` all call `check_any_auth_available(cmd_name)` which only inspects `ANTHROPIC_API_KEY` / `claude` CLI presence. With DEC-003 landing `EvalSpec.grading_provider="openai"` support in #145, an operator running a graded skill against an OpenAI-graded spec WITHOUT `OPENAI_API_KEY` set would (a) pass the Anthropic auth check, (b) reach `call_model(provider="openai", ...)`, (c) hit a `TypeError` deep inside `AsyncOpenAI()` construction (or, if defense-in-depth wrap is in place, an `OpenAIHelperError` mid-run). Either way, the CLI exit-code routing diverges from the structural taxonomy (exit 2 for pre-call env, exit 3 for API). Must resolve before stories.

**SEC-2 (CONCERN) — Provider-aware env-stripping for skill subprocess.** `_harnesses/_claude_code.py:54` (`_API_KEY_ENV_VARS`) hardcodes `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`. When `should_strip_api_key_for_skill_subprocess(args)` returns True (CLI transport), only Anthropic keys are stripped; `OPENAI_API_KEY` would still be visible to a `--transport cli` skill subprocess even when grading uses OpenAI. Forward-compat concern only — does not block #145 (no skill subprocess uses OpenAI). Document and defer to #146.

**API-1 (CONCERN) — Retry helper organization.** `_compute_backoff(retry_index)`, `_compute_retry_decision(...)`, retry constants (`_RATE_LIMIT_MAX_RETRIES`, `_SERVER_MAX_RETRIES`, `_CONN_MAX_RETRIES`) in `_anthropic.py` would apply byte-identically to OpenAI (same exception taxonomy, same backoff curve). Decision needed: hoist to `_providers/_retry.py` (DRY, one source of truth) vs. duplicate per-provider for symmetry/easier review.

**API-2 (DOC) — `ModelResult.raw_message` divergence.** OpenAI Responses API surfaces refusal/incomplete state at `response.status` + `response.incomplete_details.reason`, NOT Anthropic's `stop_reason`. Document in `call_openai` docstring; downstream callers introspecting `raw_message` for refusal semantics must branch on `provider`.

**TEST-1 (PASS) — Test parity is mostly mechanical.** ~24 Anthropic test classes. Of those: ~10 mirror cleanly (success/rate-limit/server/client/conn/import/type-error/result-fields/dispatcher/`_extract_*`); ~3 adapt for OpenAI exception classes; ~6 don't apply (CLI transport, auto resolution, transport announcement, classify-invoke-result, transport-resolve, no-shim-imports). Total: ~50–55 new test methods, ~800 LOC.

**DATA-1 (PASS) — Sidecar v3 + audit loader deferred to #147.** `ExtractionReport`, `GradingReport`, `BlindReport` already carry `provider_source: str` in-memory (`grader.py:139`, `quality_grader.py:93`/`:237`); `to_json` excludes the field today per DEC-006 of #144's plan. Sidecar schema bump (`schema_version: 2 → 3` adding `provider_source`) is #147 territory. No #145 work here beyond stamping `provider="openai"` on the returned `ModelResult`.

## Refinement Log

### Phase 1 scoping decisions (2026-04-29)

**DEC-001 — Default model strings: `gpt-5.4` (L3) + `gpt-5.4-mini` (L2).**
*Rationale:* Confirmed via https://developers.openai.com/api/docs/models — both `gpt-5.4` and `gpt-5.4-mini` are real Responses-API-native models (assistant's January 2026 knowledge cutoff was stale). Ticket's stated defaults stand. `gpt-5.4` for L3 grading ($2.50/$15 per Mtok), `gpt-5.4-mini` for L2 extraction ($0.75/$4.50 per Mtok, 400K context). These strings live in module-level constants in `_providers/_openai.py` (e.g. `_DEFAULT_MODEL_L3`, `_DEFAULT_MODEL_L2`) so #146's resolution chain has a target to fall back to. Note: `gpt-5.5` exists as a stronger tier ($5/$30) but is NOT a default — power users override via `--grading-model`.

**DEC-002 — `transport=` parameter silently ignored for OpenAI; `source="api"` always.**
*Rationale:* OpenAI has no CLI subscription analogue. Silent-ignore matches the existing dispatcher docstring at `_providers/__init__.py:137` ("Ignored for the future openai backend (no transport axis there)"). `call_openai`'s signature still accepts `transport=...` as a keyword for call-site uniformity with `call_anthropic`, but the body does not consult it. `ModelResult.source` is hardcoded to `"api"` for the OpenAI path. No `ValueError` on `transport="cli"` — defensive raising would surface a confusing failure to anyone composing dispatch with `--transport cli` set globally for the Anthropic side.

**DEC-003 — Minimal `EvalSpec.grading_provider` spec field; CLI flag and full precedence deferred to #146.**
*Rationale:* Acceptance criterion 2 ("eval spec with `grading_provider="openai"` runs end-to-end") needs at least the spec-side wire-up to be testable, but the four-layer precedence resolver is explicitly #146's scope. #145 adds: (a) `EvalSpec.grading_provider: Literal["anthropic","openai"] | None = None` with load-time validation, (b) the four grader call sites (`grader.py`, `quality_grader.py`, `suggest.py`, `triggers.py`) read `eval_spec.grading_provider or "anthropic"` and pass that to `call_model(provider=...)`. CLI flag `--grading-provider` and `CLAUDITOR_GRADING_PROVIDER` env-var land in #146. Default-to-anthropic preserves all existing behavior for specs that don't set the field.

**DEC-004 — `openai>=1.66.0` as a direct top-level dependency in `pyproject.toml`.**
*Rationale:* Matches the existing Anthropic-as-direct-dependency shape (`pyproject.toml:29`). Both providers always available; no install-time choice; no `ImportError` branch in `_openai.py` to maintain (in contrast to the existing Anthropic `ImportError` raised un-wrapped per `centralized-sdk-call.md`'s "ImportError raised un-wrapped" subsection — though we keep that defensive branch in `_openai.py` for parity in case a user pip-uninstalls `openai`). Version `>=1.66.0` is the minimum that supports `client.responses.create()` + `AsyncOpenAI` async per the domain expert's findings (March 2025 SDK release).

**DEC-005 — Reasoning models (o-series) deferred entirely.**
*Rationale:* Reasoning models require a different kwarg surface (`reasoning={"effort": ...}`), different `output[]` filtering (skip `type=="reasoning"` items when joining text), and a different cost model (reasoning tokens billed at output rate, often dwarfing the visible output). All three concerns belong to a sibling ticket — likely overlapping with #154 (harness context sidecar) since `output_tokens_details.reasoning_tokens` is the canonical observability signal. #145 stays mechanical: same kwargs as Anthropic, same response shape, same retry policy. A future `propose-reasoning-models` ticket can extend `call_openai` with the `reasoning_effort` knob without touching the dispatcher.

### Phase 2 architecture decisions (2026-04-29)

**DEC-006 — Single `check_provider_auth(provider, cmd_name)` dispatcher in `_providers/_auth.py`.**
*Rationale:* Resolves SEC-1 (BLOCKER). Each of the 4 LLM-mediated CLI commands (`grade`, `extract`, `triggers`, `propose-eval`) calls `check_provider_auth(provider, cmd_name)` once after resolving `provider` from `eval_spec.grading_provider or "anthropic"`. The dispatcher internally branches: `"anthropic"` → `check_any_auth_available(cmd_name)` (preserves existing key-OR-CLI semantics); `"openai"` → `check_openai_auth(cmd_name)`. Distinct exception classes (`AnthropicAuthMissingError`, `OpenAIAuthMissingError`) propagate to the CLI, where structural `except` ladders route each to exit 2 per `.claude/rules/llm-cli-exit-code-taxonomy.md`. Single helper avoids duplicating the if/else at every CLI seam; future `provider="vertex"` or `"bedrock"` extension is one branch in the dispatcher.

**DEC-007 — Hoist retry helpers to a new `src/clauditor/_providers/_retry.py` module.**
*Rationale:* The retry policy is byte-identical between Anthropic and OpenAI (confirmed by domain expert: same exception taxonomy, same backoff curve). Public API of the new module: constants `RATE_LIMIT_MAX_RETRIES = 3`, `SERVER_MAX_RETRIES = 1`, `CONN_MAX_RETRIES = 1`; pure functions `compute_backoff(retry_index)` and `compute_retry_decision(category, retry_counters)`. Both `_anthropic.py` and `_openai.py` import from it. Per-provider concerns stay per-provider: the `_sleep` / `_monotonic` / `_rand_uniform` / `_rng` test-indirection aliases remain on each provider module (so a `monkeypatch.setattr("clauditor._providers._openai._sleep", ...)` patches only OpenAI's sleeping, not Anthropic's). Existing Anthropic tests that monkeypatched `clauditor._providers._anthropic._compute_backoff` follow the symbol to `clauditor._providers._retry.compute_backoff` per `.claude/rules/back-compat-shim-discipline.md` Pattern 3.

**DEC-008 — Add `OPENAI_API_KEY` to `_API_KEY_ENV_VARS` in `_harnesses/_claude_code.py` now.**
*Rationale:* Resolves SEC-2 (CONCERN). One-line tuple extension: `_API_KEY_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")`. When `should_strip_api_key_for_skill_subprocess(args)` returns True (under `--transport cli`), all three keys are stripped from the skill subprocess env, regardless of which provider is actually grading. Defensive: a skill author could spawn a child process that calls OpenAI; stripping the operator's OPENAI_API_KEY ensures the operator's quota is not silently spent by an untrusted skill. Provider-aware stripping (`env_without_api_key(provider=...)`) would be larger; this single-line shim is forward-compat enough for the current threat model and the rule's non-mutating-scrub contract is preserved (the function already returns a new dict).

## Detailed Breakdown

### Story ordering rationale

Architecture-natural ordering: foundations → SDK seam → dispatcher → auth → spec field → wiring → end-to-end → quality gate.

| # | Title | Depends on |
|---|---|---|
| US-001 | Hoist retry helpers to `_providers/_retry.py` | none |
| US-002 | Add `openai>=1.66.0` dependency + `_providers/_openai.py` happy path | US-001 |
| US-003 | OpenAI response parser edges (`_extract_openai_result`) | US-002 |
| US-004 | OpenAI retry/error branches (429/5xx/4xx/auth/conn/import/type-error) | US-002 |
| US-005 | Wire OpenAI into `call_model` dispatcher | US-002, US-004 |
| US-006 | OpenAI auth helper + `check_provider_auth` dispatcher | none |
| US-007 | `EvalSpec.grading_provider` field + load-time validation | none |
| US-008 | Strip `OPENAI_API_KEY` from skill subprocess env | none |
| US-009 | Wire 4 CLI commands to `check_provider_auth(provider, cmd_name)` | US-006, US-007 |
| US-010 | Wire 4 grader call sites to read `eval_spec.grading_provider` | US-005, US-007 |
| US-011 | End-to-end tests for `grading_provider="openai"` path | US-009, US-010 |
| US-012 | Quality Gate — code review × 4 + CodeRabbit + project validation | US-001..US-011 |
| US-013 | Patterns & Memory — update conventions and docs | US-012 |

---

### US-001 — Hoist retry helpers to `_providers/_retry.py`

**Description.** Pure refactor: extract `_compute_backoff`, `_compute_retry_decision`, and the three retry constants from `_providers/_anthropic.py` into a new shared module `_providers/_retry.py`. Both `_anthropic.py` and the (future) `_openai.py` import the helpers. No behavior change.

**Traces to:** DEC-007.

**Acceptance criteria:**
- New file `src/clauditor/_providers/_retry.py` exports public names: `RATE_LIMIT_MAX_RETRIES`, `SERVER_MAX_RETRIES`, `CONN_MAX_RETRIES`, `compute_backoff(retry_index: int) -> float`, `compute_retry_decision(category: str, counters: dict) -> Literal["retry", "raise"]`.
- `_providers/_anthropic.py` uses imported names; private aliases (`_RATE_LIMIT_MAX_RETRIES`, `_compute_backoff`, `_compute_retry_decision`) removed from `_anthropic.py`.
- Existing `tests/test_providers_anthropic.py::TestComputeBackoff` and `TestComputeRetryDecision` move to new `tests/test_providers_retry.py` patching `clauditor._providers._retry.*`.
- `uv run ruff check src/ tests/` clean; `uv run pytest --cov=clauditor --cov-report=term-missing` passes (80% gate).

**Done when:** All Anthropic tests still pass with the moved patch targets per `.claude/rules/back-compat-shim-discipline.md` Pattern 3.

**Files:**
- `src/clauditor/_providers/_retry.py` (NEW)
- `src/clauditor/_providers/_anthropic.py` (delete `_compute_backoff`, `_compute_retry_decision`, retry constants; import from `_retry`)
- `tests/test_providers_retry.py` (NEW; moved from `test_providers_anthropic.py`)
- `tests/test_providers_anthropic.py` (drop the moved test classes; keep all other tests untouched)

**TDD:** Move existing tests verbatim; verify they pass against the new module path before any other change.

---

### US-002 — Add `openai>=1.66.0` dependency + `_providers/_openai.py` happy path

**Description.** Add `openai>=1.66.0` as a direct dependency in `pyproject.toml`. Create `src/clauditor/_providers/_openai.py` with a working `call_openai(prompt, *, model, max_tokens=4096, transport="auto", subject=None) -> ModelResult` for the success path only: construct `AsyncOpenAI()`, call `.responses.create(input=prompt, model=model, max_output_tokens=max_tokens)`, build `ModelResult` from the response. Module-level test-indirection aliases `_sleep = asyncio.sleep`, `_monotonic = time.monotonic`, `_rand_uniform`/`_rng` per `.claude/rules/monotonic-time-indirection.md`. Module-level constants `DEFAULT_MODEL_L3 = "gpt-5.4"` and `DEFAULT_MODEL_L2 = "gpt-5.4-mini"` per DEC-001. `transport=` and `subject=` accepted but ignored per DEC-002. Define `OpenAIHelperError(Exception)` class.

**Traces to:** DEC-001, DEC-002, DEC-004, DEC-007.

**Acceptance criteria:**
- `pyproject.toml` `dependencies` includes `"openai>=1.66.0"`.
- `_providers/_openai.py` defines `call_openai`, `OpenAIHelperError`, `_extract_openai_result`, `DEFAULT_MODEL_L3`, `DEFAULT_MODEL_L2`.
- Happy-path test: mock `AsyncOpenAI` returning a fake response with `output_text="hello"`, `usage.input_tokens=10`, `usage.output_tokens=5`; assert `result.response_text == "hello"`, `result.input_tokens == 10`, `result.output_tokens == 5`, `result.provider == "openai"`, `result.source == "api"`.
- `result.raw_message` is `response.model_dump()` (a dict).
- `result.duration_seconds > 0` via `_monotonic` indirection.
- Docstring on `call_openai` documents `raw_message` divergence from Anthropic (refusal at `response.status` + `incomplete_details.reason`, NOT `stop_reason`) — addresses API-2.

**Done when:** Happy-path test green; `uv run ruff check` clean.

**Files:**
- `pyproject.toml` — add `openai>=1.66.0` to `dependencies`.
- `src/clauditor/_providers/_openai.py` (NEW)
- `tests/test_providers_openai.py` (NEW) — `TestCallOpenAISuccess` + `TestExtractOpenAIResultHappyPath`.

**TDD:** Write `TestCallOpenAISuccess::test_returns_result_with_tokens` first against an `AsyncOpenAI` mock; implement `call_openai` until green.

---

### US-003 — OpenAI response parser edges (`_extract_openai_result`)

**Description.** Harden `_extract_openai_result(response)` to walk `response.output[]` filtering `type=="message"` (skipping `reasoning` items for forward-compat with #154); populate `text_blocks: list[str]` per-message. Handle missing `output_text`, empty `output[]`, `response.status == "incomplete"`, and `response.usage` with non-int / null fields (defensive int coercion).

**Traces to:** DEC-005 (forward-compat with reasoning items), API-2 (refusal divergence), `.claude/rules/stream-json-schema.md` (defensive parser pattern).

**Acceptance criteria:**
- `_extract_openai_result` returns `(response_text, text_blocks, input_tokens, output_tokens, raw_message)` tuple (or equivalent shape).
- Test cases: happy path; `output[]` with mixed `message` + `reasoning` items (only message text in `text_blocks`); empty `output[]` → empty `text_blocks` + `response_text=""`; missing `usage` → 0/0 tokens; null `usage.input_tokens` → 0; `status == "incomplete"` is read but not raised (caller decides).
- Defensive `try/except (TypeError, ValueError)` on token int coercion.

**Done when:** `TestExtractOpenAIResult` covers ≥6 edge cases; coverage of the helper at 100%.

**Files:**
- `src/clauditor/_providers/_openai.py` (extend `_extract_openai_result`)
- `tests/test_providers_openai.py` (extend `TestExtractOpenAIResult`)

**TDD:** Write all edge-case tests first; implement parser until each goes green.

---

### US-004 — OpenAI retry/error branches

**Description.** Implement the full retry/error ladder in `call_openai`: 429 (`RateLimitError`) up to 3 retries with `compute_backoff` from US-001; 5xx (`APIStatusError.status_code >= 500`) 1 retry; 4xx non-auth raises immediately; 401 (`AuthenticationError`) and 403 (`PermissionDeniedError`) raise immediately with auth-hint message naming `OPENAI_API_KEY`; `APIConnectionError` 1 retry; `ImportError` (when `openai` SDK is uninstalled) re-raised un-wrapped per `.claude/rules/centralized-sdk-call.md`'s "ImportError raised un-wrapped" subsection; defensive `TypeError` wrap around `AsyncOpenAI()` construction and `.responses.create()` raises `OpenAIHelperError("OpenAI SDK client initialization failed — verify OPENAI_API_KEY is set.") from exc` per `.claude/rules/precall-env-validation.md`.

**Traces to:** DEC-007, `.claude/rules/centralized-sdk-call.md`, `.claude/rules/precall-env-validation.md`.

**Acceptance criteria:**
- Retries use `_sleep` and `_rand_uniform` indirected aliases (test-patchable).
- Auth-error message includes the literal substring `"OPENAI_API_KEY"`.
- TypeError wrap message is the fixed sanitized string (no `str(exc)`, no `exc.args`).
- Test classes: `TestCallOpenAIRateLimit` (3-retry exhaust + recovery-after-2), `TestCallOpenAIServerError` (1-retry exhaust + recovery), `TestCallOpenAIClientError` (400/422 immediate), `TestCallOpenAIAuthErrors` (401/403 with hint), `TestCallOpenAIConnectionError` (1-retry exhaust + recovery), `TestCallOpenAIImportError` (re-raised un-wrapped), `TestCallOpenAITypeError` (defense-in-depth wrap with `__cause__` preserved).
- Test patches use `clauditor._providers._openai.AsyncOpenAI` and `clauditor._providers._openai._sleep` / `._rand_uniform`.

**Done when:** All retry-branch tests green; coverage of `call_openai` ≥ 95%.

**Files:**
- `src/clauditor/_providers/_openai.py` (extend `call_openai`)
- `tests/test_providers_openai.py` (extend with retry test classes)

**TDD:** Write each retry-branch test first (parametrized on exception type and retry count), implement until green.

---

### US-005 — Wire OpenAI into `call_model` dispatcher

**Description.** Replace `NotImplementedError("openai provider lands in #145")` at `_providers/__init__.py:168-169` with a deferred-import call to `_openai.call_openai`. Match the Anthropic dispatch shape exactly (deferred import per `.claude/rules/back-compat-shim-discipline.md` Pattern 3). Re-export `OpenAIHelperError` from `_providers/__init__.py` so callers and tests have a single import surface.

**Traces to:** DEC-002, `.claude/rules/centralized-sdk-call.md`, `.claude/rules/back-compat-shim-discipline.md` Pattern 3.

**Acceptance criteria:**
- `_providers/__init__.py` `call_model` `provider="openai"` branch dispatches via `from clauditor._providers import _openai as _openai_mod; return await _openai_mod.call_openai(prompt, model=model, transport=transport, max_tokens=max_tokens)`.
- `_providers/__init__.py` exports `OpenAIHelperError` in `__all__`.
- `tests/test_providers_anthropic.py::TestCallModel` (or moved to `test_providers_init.py`) gains tests: `test_call_model_dispatches_to_openai`, `test_call_model_propagates_openai_helper_error`, `test_call_model_unknown_provider_still_raises_value_error`.
- Existing Anthropic dispatch tests still pass.

**Done when:** Dispatcher tests green; no production code path can reach the old `NotImplementedError`.

**Files:**
- `src/clauditor/_providers/__init__.py`
- `tests/test_providers_anthropic.py` (extend `TestCallModel`) — or new `tests/test_providers_init.py` if size warrants.

**TDD:** Write the three dispatcher tests first.

---

### US-006 — OpenAI auth helper + `check_provider_auth` dispatcher

**Description.** Add to `_providers/_auth.py`: (1) `OpenAIAuthMissingError(Exception)` class, (2) `_OPENAI_AUTH_MISSING_TEMPLATE` string with `{cmd_name}` interpolation naming `OPENAI_API_KEY`, (3) pure helper `check_openai_auth(cmd_name) -> None` reading `os.environ["OPENAI_API_KEY"]` (raises on absent/empty/whitespace-only), (4) public dispatcher `check_provider_auth(provider, cmd_name) -> None` routing `"anthropic"` → `check_any_auth_available` and `"openai"` → `check_openai_auth`. Re-export both new public names from `_providers/__init__.py`.

**Traces to:** DEC-006, `.claude/rules/precall-env-validation.md`.

**Acceptance criteria:**
- `OpenAIAuthMissingError` is a subclass of `Exception` (NOT `OpenAIHelperError`) — distinct exit-code routing per `.claude/rules/llm-cli-exit-code-taxonomy.md`.
- Auth message includes literal substrings: `"OPENAI_API_KEY"`, `"console.openai.com"` (or wherever the key is provisioned), the `{cmd_name}` interpolation.
- Helper is pure: no stderr, no `sys.exit`, no logging.
- `check_provider_auth("anthropic", "grade")` calls `check_any_auth_available("grade")`; `check_provider_auth("openai", "grade")` calls `check_openai_auth("grade")`; unknown provider raises `ValueError`.
- Test class `TestCheckOpenAiAuth` parallels `TestAnnounceImplicitNoApiKey` shape: env-set passes; env-unset raises with hint; whitespace-only raises; `cmd_name` interpolation lands in message.
- Test class `TestCheckProviderAuth` covers each branch + unknown-provider.

**Done when:** Both helpers covered; existing Anthropic auth tests still pass.

**Files:**
- `src/clauditor/_providers/_auth.py`
- `src/clauditor/_providers/__init__.py` (add to `__all__`)
- `tests/test_providers_auth.py` (add `TestCheckOpenAiAuth` + `TestCheckProviderAuth`)

**TDD:** Write the helper tests first.

---

### US-007 — `EvalSpec.grading_provider` field + load-time validation

**Description.** Add `grading_provider: Literal["anthropic", "openai"] | None = None` to the `EvalSpec` dataclass in `src/clauditor/schemas.py`. Validate at load time in `EvalSpec.from_dict`: must be a string in the literal set; null permitted; rejection on unknown value with an error that names the allowed values per the existing `transport` field validator pattern.

**Traces to:** DEC-003, `.claude/rules/eval-spec-stable-ids.md` (NB: applies to id'd entries, not single fields, but the load-time-hard-fail philosophy carries).

**Acceptance criteria:**
- `EvalSpec.grading_provider` field declared with the right type.
- `from_dict` validates: rejects `"claude"`, `"gpt"`, `1`, `True`; accepts `None`, `"anthropic"`, `"openai"`.
- Error message includes the literal substrings `"grading_provider"`, `"anthropic"`, `"openai"`.
- Test class `TestGradingProviderValidation` in `tests/test_schemas.py` covers happy + 3 reject cases.

**Done when:** Validation tests green; existing eval-spec tests untouched.

**Files:**
- `src/clauditor/schemas.py`
- `tests/test_schemas.py` (add `TestGradingProviderValidation`)

**TDD:** Write reject-case tests first.

---

### US-008 — Strip `OPENAI_API_KEY` from skill subprocess env

**Description.** Extend `_API_KEY_ENV_VARS` in `src/clauditor/_harnesses/_claude_code.py` to include `"OPENAI_API_KEY"`. Update `env_without_api_key()` docstring to reflect the new key. Add a regression test asserting `OPENAI_API_KEY` is stripped from the returned env dict.

**Traces to:** DEC-008, `.claude/rules/non-mutating-scrub.md`.

**Acceptance criteria:**
- `_API_KEY_ENV_VARS` tuple has 3 entries: `("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")`.
- `env_without_api_key({"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "b", "FOO": "bar"})` returns `{"FOO": "bar"}`.
- Input dict is not mutated (per `.claude/rules/non-mutating-scrub.md`).

**Done when:** Test green; `_API_KEY_ENV_VARS` constant exposes all three.

**Files:**
- `src/clauditor/_harnesses/_claude_code.py`
- `tests/test_runner.py` (or wherever `env_without_api_key` is tested today) — extend.

**TDD:** Write the OPENAI_API_KEY stripping test first.

---

### US-009 — Wire 4 CLI commands to `check_provider_auth`

**Description.** Update `cli/grade.py:327`, `cli/extract.py:99`, `cli/triggers.py:107`, `cli/propose_eval.py:376` to: (1) resolve `provider = spec.eval_spec.grading_provider or "anthropic"` after spec load and `--dry-run` early-return, (2) call `check_provider_auth(provider, cmd_name)`, (3) catch both `AnthropicAuthMissingError` AND `OpenAIAuthMissingError` (distinct `except` branches each printing to stderr and returning exit 2 per `.claude/rules/llm-cli-exit-code-taxonomy.md`).

**Traces to:** DEC-003, DEC-006, `.claude/rules/precall-env-validation.md`, `.claude/rules/llm-cli-exit-code-taxonomy.md`.

**Acceptance criteria:**
- All 4 CLI commands resolve provider and dispatch via `check_provider_auth`.
- Distinct `except AnthropicAuthMissingError:` and `except OpenAIAuthMissingError:` branches; each returns `2`.
- Per-command tests: spec with `grading_provider="openai"` + missing `OPENAI_API_KEY` → exit 2 with stderr containing `"OPENAI_API_KEY"`.
- Existing Anthropic-default tests still pass.

**Done when:** Each of the 4 CLI commands has a positive Anthropic test, a positive OpenAI test, and a negative OpenAI-key-missing test.

**Files:**
- `src/clauditor/cli/grade.py`
- `src/clauditor/cli/extract.py`
- `src/clauditor/cli/triggers.py`
- `src/clauditor/cli/propose_eval.py`
- `tests/test_cli_grade.py`, `tests/test_cli_extract.py`, `tests/test_cli_triggers.py`, `tests/test_cli_propose_eval.py` (extend each)

**TDD:** For each CLI command, write the negative OpenAI-key-missing test first.

---

### US-010 — Wire 4 grader call sites to read `eval_spec.grading_provider`

**Description.** Update each of the 4 places that today pass `provider="anthropic"` literal to `call_model`: `grader.py::extract_and_grade` / `extract_and_report`, `quality_grader.py::grade_quality` / `blind_compare`, `suggest.py::suggest_improvements`, `triggers.py::extract_json_trigger`. Read `provider = eval_spec.grading_provider or "anthropic"` and pass that. The `provider_source` field on the in-memory `GradingReport` / `ExtractionReport` / `BlindReport` is stamped with the resolved provider value (already implemented; just verify).

**Traces to:** DEC-003, `.claude/rules/centralized-sdk-call.md`.

**Acceptance criteria:**
- All 4 grader call sites resolve provider from spec.
- Returned reports carry `provider_source == "openai"` when `eval_spec.grading_provider == "openai"`.
- Existing tests with no `grading_provider` set still see `provider_source == "anthropic"` (back-compat).
- One test per call site: `eval_spec.grading_provider="openai"` + mocked `call_model` returning `ModelResult(provider="openai", ...)` → report stamps openai.

**Done when:** Each grader call site routes through resolved provider; report stamping verified.

**Files:**
- `src/clauditor/grader.py`
- `src/clauditor/quality_grader.py`
- `src/clauditor/suggest.py`
- `src/clauditor/triggers.py`
- `tests/test_grader.py`, `tests/test_quality_grader.py`, `tests/test_suggest.py`, `tests/test_triggers.py` (extend each)

**TDD:** Write the openai-provider stamping test first per call site.

---

### US-011 — End-to-end test for `grading_provider="openai"` path

**Description.** Add 2 end-to-end tests with mocked `call_model`: (1) `tests/test_grader.py::TestExtractAndReportWithOpenAI` runs L2 extraction with `eval_spec.grading_provider="openai"` and asserts the full report shape is produced, (2) `tests/test_quality_grader.py::TestGradeQualityWithOpenAI` does the same for L3. The mock returns `ModelResult(provider="openai", source="api", ...)`. Confirms acceptance criterion 2 of the issue: "an eval spec with `grading_provider='openai'` runs L2 extraction and L3 grading end-to-end."

**Traces to:** Acceptance criterion 2 (issue #145).

**Acceptance criteria:**
- L2 test: feeds an `EvalSpec` with `sections=[...]` + `grading_provider="openai"`; mock `call_model` to return a valid extraction JSON; assert `ExtractionReport` shape, fields populated, `provider_source="openai"`.
- L3 test: feeds an `EvalSpec` with `grading_criteria=[...]` + `grading_provider="openai"`; mock `call_model` for both extraction and grading calls (use `side_effect=[...]` per `.claude/rules/mock-side-effect-for-distinct-calls.md`); assert `GradingReport` populated.

**Done when:** Both tests green; acceptance criterion 2 demonstrably satisfied.

**Files:**
- `tests/test_grader.py` (extend)
- `tests/test_quality_grader.py` (extend)

**TDD:** Write both tests first against the future call-site changes from US-010.

---

### US-012 — Quality Gate

**Description.** Run code-reviewer 4× across the full changeset, fixing all real bugs found each pass. Run CodeRabbit if available. Verify project validation: `uv run ruff check src/ tests/` clean; `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate. Verify all `.claude/rules/*.md` constraints from Phase 1 still hold post-implementation.

**Traces to:** Project quality gate.

**Acceptance criteria:**
- 4 review passes complete; all real bugs fixed.
- `uv run ruff check src/ tests/` exits 0.
- `uv run pytest --cov=clauditor --cov-report=term-missing` exits 0; coverage ≥ 80%.
- No new failures in any pre-existing test.

**Done when:** Last review pass returns no real bugs; project validation green.

**Files:** Any file touched in US-001..US-011.

---

### US-013 — Patterns & Memory

**Description.** Update `.claude/rules/centralized-sdk-call.md` to add `_providers/_openai.py` to the canonical-implementation section and the announcement-family list. Update `.claude/rules/precall-env-validation.md` to add `OpenAIAuthMissingError` + `check_openai_auth` to the canonical-implementation section. Add a new rule `.claude/rules/multi-provider-dispatch.md` if the `check_provider_auth` dispatcher pattern warrants codification (likely yes — future Vertex/Bedrock providers will follow the same shape). Update `docs/architecture.md` (or equivalent) with the multi-provider section reference if such a doc exists.

**Traces to:** `.claude/rules/rule-refresh-vs-delete.md` (refresh-in-place for context-shifted rules).

**Acceptance criteria:**
- `centralized-sdk-call.md` mentions `_openai.py` in canonical implementation.
- `precall-env-validation.md` mentions `OpenAIAuthMissingError` and `check_provider_auth`.
- A new rule may exist (`multi-provider-dispatch.md`) covering the `check_provider_auth(provider, cmd_name)` dispatcher pattern, OR the existing `centralized-sdk-call.md` is extended with that section.
- No rule files contain stale references to "openai provider lands in #145" or `NotImplementedError` for openai.

**Done when:** Rules documented; the next ticket adding a third provider (Vertex, Bedrock) can find the pattern.

**Files:**
- `.claude/rules/centralized-sdk-call.md`
- `.claude/rules/precall-env-validation.md`
- `.claude/rules/multi-provider-dispatch.md` (NEW, if warranted)
- `docs/` (if relevant)

## Beads Manifest

**Epic:** `clauditor-2cq` — #145: OpenAI provider via Responses API
**Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/145-openai-provider`
**Branch:** `feature/145-openai-provider`
**PR:** https://github.com/wjduenow/clauditor/pull/160

| Story | Bead ID | Depends on |
|---|---|---|
| US-001 — Hoist retry helpers to `_providers/_retry.py` | `clauditor-0y2` | (epic) |
| US-002 — Add `openai>=1.66.0` + `_providers/_openai.py` happy path | `clauditor-2pf` | US-001 |
| US-003 — OpenAI response parser edges | `clauditor-n5h` | US-002 |
| US-004 — OpenAI retry/error branches | `clauditor-4uw` | US-002 |
| US-005 — Wire OpenAI into `call_model` dispatcher | `clauditor-52b` | US-002, US-004 |
| US-006 — OpenAI auth helper + `check_provider_auth` dispatcher | `clauditor-4cq` | (epic) |
| US-007 — `EvalSpec.grading_provider` field | `clauditor-jlo` | (epic) |
| US-008 — Strip `OPENAI_API_KEY` from skill subprocess env | `clauditor-ejw` | (epic) |
| US-009 — Wire 4 CLI commands to `check_provider_auth` | `clauditor-5wc` | US-006, US-007 |
| US-010 — Wire 4 grader call sites | `clauditor-iye` | US-005, US-007 |
| US-011 — End-to-end tests for `grading_provider="openai"` | `clauditor-t8t` | US-009, US-010 |
| US-012 — Quality Gate | `clauditor-y16` | US-001..US-011 |
| US-013 — Patterns & Memory | `clauditor-8gx` | US-012 |

**Initially ready** (zero-dependency starting set): `clauditor-0y2` (US-001), `clauditor-4cq` (US-006), `clauditor-jlo` (US-007), `clauditor-ejw` (US-008).

## Session Notes

### 2026-04-29 — Discovery (session 1)

Spawned four parallel research subagents:
- Ticket Analyst — fetched #143/#144/#146/#154; confirmed #144 closed, OpenAI stub at `_providers/__init__.py:168-169`; surfaced 5 ambiguities matching the scoping questions above.
- Codebase Scout — mapped the full Anthropic surface (24 test classes, 3 announcement flags, retry constants, module aliases) and identified all 4 grader call sites passing `provider="anthropic"` today.
- Convention Checker — swept all 30+ `.claude/rules/*.md` files; 10 apply, 1 explicitly does not (`back-compat-shim-discipline.md`).
- Domain Expert — confirmed Responses API field-name parallelism with Anthropic (the issue's central claim), identified `gpt-5.4` model strings as non-existent (recommend `gpt-4.1`/`gpt-4.1-mini`), recommended deferring o-series.

Key surprise: dispatcher already accepts `provider="openai"` literal and `ModelResult.provider` is already `Literal["anthropic", "openai"]` — #144's groundwork makes #145 a fill-in, not a redesign.
