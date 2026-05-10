# 170: Reasoning tokens — capture separately-billed reasoning tokens in `ModelResult`

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/170
- **Branch:** `feature/170-reasoning-tokens-capture`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/170-reasoning-tokens-capture`
- **PR:** https://github.com/wjduenow/clauditor/pull/174
- **Phase:** devolved
- **Epic:** clauditor-o7u
- **Sessions:** 1 (2026-05-09)
- **Total decisions:** 8 (DEC-001 through DEC-008)
- **Depends on:** #154 (CLOSED — `context.json` sidecar with `reasoning_tokens: int | None = None` placeholder)
- **Sibling:** #169 (cost_usd capture — same shape, different field)

---

## Discovery

### Ticket summary

**What:** Light up the `IterationContext.reasoning_tokens` placeholder field that #154 shipped. Capture separately-billed reasoning tokens from the LLM grader call(s) and surface the value into `context.json` at sidecar-write time.

**Scope (verbatim from ticket):**
- Add `reasoning_tokens: int | None` field to `ModelResult`.
- Anthropic backend: surface from `Message.usage` if a thinking-token field is present.
- OpenAI backend: surface from the API's reasoning-token usage section for o-series and GPT-5 reasoning models.
- Wire the value through to `IterationContext.reasoning_tokens` at sidecar-write time.
- Defensive: missing field → `None`, never raises.
- Tests: stubbed API response with reasoning tokens → field populated; legacy response without → `None`.

**Out of scope (per ticket):**
- Reasoning-token cost calculation (lives in #169 `cost_usd`).
- Aggregation / trend analysis of reasoning tokens (separate concern).

### Codebase findings

**`ModelResult` dataclass** — defined at `src/clauditor/_providers/_anthropic.py:192–241` (NOT `__init__.py` — actually lives in the Anthropic-specific module for historical reasons; re-exported from `_providers/__init__.py:200`). Current shape: 9 fields. No `reasoning_tokens`. Back-compat alias `AnthropicResult = ModelResult`. Defined at `_anthropic.py:220` per the convention checker.

**Anthropic backend** (`src/clauditor/_providers/_anthropic.py`):
- Usage extraction: lines 289–297 — extracts `usage.input_tokens` / `usage.output_tokens` only. No reasoning fields read.
- ModelResult construction: lines 299–306.
- **SDK reality (research finding):** `anthropic.types.Usage` has NO dedicated `thinking_tokens` / `reasoning_tokens` field. The fields are: `cache_creation`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `inference_geo`, `input_tokens`, `output_tokens`, `server_tool_use`, `service_tier`. For extended-thinking models (Opus 4.x with `thinking={"type":"enabled",...}`), `output_tokens` ALREADY INCLUDES the thinking tokens — they are NOT counted separately by the SDK. Thinking content surfaces as `ThinkingBlock` entries inside `Message.content` but those carry text only, not a count.
- **Implication:** Honest Anthropic capture is `None` (no separately-billed field exists today). To get a separate number we would need to call `client.messages.count_tokens(...)` on each `ThinkingBlock.thinking` string — extra round-trip per call.

**OpenAI backend** (`src/clauditor/_providers/_openai.py`):
- Usage extraction: `_extract_openai_result` at lines 220–228 — `input_tokens` / `output_tokens` only.
- ModelResult construction: lines 485–492 (approx).
- **SDK reality:** `openai.types.responses.ResponseUsage` carries `output_tokens_details: OutputTokensDetails`, which has `reasoning_tokens: int` (typed non-optional). Always present for Responses API calls. `0` for non-reasoning models, real count for o-series / GPT-5 reasoning. Note: `output_tokens` already INCLUDES `reasoning_tokens` (breakdown, not additive).

**`IterationContext`** (`src/clauditor/context.py:58–211`):
- Already carries `reasoning_tokens: int | None = None` (DEC-002 of #154).
- `to_json()` emits `"reasoning_tokens": self.reasoning_tokens` (line 89).
- `from_dict()` validates `int | None` with `bool` guard (lines 161–172).
- Always-v1 contract per `json-schema-version.md` — populating this field does NOT bump schema.

**Sidecar write sites:**
- `src/clauditor/cli/grade.py:887` — `_write_context_sidecar(...)`. Currently passes `reasoning_tokens=None` hardcoded (line 921). Called at line 797 with `model_grader=primary_report.model`. Has access to `primary_skill_result` and `primary_report` (a `GradingReport`).
- `src/clauditor/cli/validate.py:318–329` — `validate` command writes the sidecar with `reasoning_tokens=None` hardcoded; no LLM grader involved, so reasoning is structurally `None` for this path.

**Token plumbing in graders:**
- `GradingReport` (`quality_grader.py:94`) carries `input_tokens` / `output_tokens` as summed totals (across retries, blind-compare's two calls, etc.). Persisted to `grading.json`.
- `ExtractionReport` (`grader.py:139`) — same.
- Internal helpers fan out via tuples like `(text, source, provider, input_tokens, output_tokens)` (`grader.py:842`, `quality_grader.py:635`).
- Sum sites: `grader.py:870`, `quality_grader.py:660`, `quality_grader.py:1228`. All sum across multiple `ModelResult`s into the report's totals.

**Audit / badge readers** (downstream of `IterationContext.reasoning_tokens`):
- `audit.py:868` — display in context table.
- `audit.py:1155` — JSON export.
- `badge.py` — no read today.

**Test fixtures:**
- `tests/test_providers_anthropic.py:37–59` — `_mock_response` builds a MagicMock with `usage = MagicMock(input_tokens=..., output_tokens=...)`. No reasoning field.
- `tests/test_providers_openai.py:30–67` — same pattern.

### Conventions / rules consulted

**Apply — drive implementation:**

1. **`json-schema-version.md`** — `IterationContext` stays at `schema_version: 1`; the field was pre-declared nullable in v1 specifically so this PR can populate it without bumping. ALSO: if we add `reasoning_tokens` to `GradingReport` / `ExtractionReport` (a real on-disk shape change), THOSE files would need bumps (`grading.json` v4→v5, `extraction.json` v4→v5). DEC needed on whether to persist there.
2. **`centralized-sdk-call.md`** — `ModelResult` is the right home for the new field. Both `call_anthropic` and `call_openai` must populate it independently. Per DEC-007 of #149, `harness_metadata` is the forward-compat surface for harness-shape data — but reasoning_tokens is a model-call concern, not harness-shape, so it lives on `ModelResult`, not `harness_metadata`.
3. **`back-compat-shim-discipline.md`** Pattern 2 — `AnthropicResult = ModelResult` is an alias re-exported through `clauditor._anthropic`. Adding a field is safe (the alias is `is`-identical), but a regression test asserting `clauditor._anthropic.AnthropicResult is clauditor._providers.ModelResult` should remain green. The existing `tests/test_providers_auth.py::TestExceptionClassIdentity` covers this; verify no breakage.
4. **`pure-compute-vs-io-split.md`** — Defensive extraction belongs in a pure helper (small `_extract_reasoning_tokens(usage) -> int | None` function in each provider module), called inside the SDK-result extractor. Sixth or seventh anchor candidate for the rule.
5. **`pre-llm-contract-hard-validate.md`** + **`stream-json-schema.md`** (defensive-read shape only) — `getattr(usage, "output_tokens_details", None)` then `getattr(details, "reasoning_tokens", None)`, `isinstance(value, int) and not isinstance(value, bool)` per `constant-with-type-info.md`. Returns `None` on any malformed input; never raises.
6. **`constant-with-type-info.md`** — `bool is not int` guard applies; `True` would otherwise pass `isinstance(value, int)` and corrupt the count.
7. **`mock-side-effect-for-distinct-calls.md`** — When tests verify summing across multiple grader calls (e.g. blind-compare's two), use `side_effect=[result1, result2]` with distinct `reasoning_tokens` values so the sum arithmetic is actually exercised.
8. **`rule-refresh-vs-delete.md`** — `centralized-sdk-call.md`'s `ModelResult` field list (cited verbatim in the rule's "Why this shape" section) needs a one-line refresh to mention the new field.

**N/A:**

`stream-json-schema.md` for non-defensive purposes (reasoning tokens come from SDK, not stream-json), `non-mutating-scrub.md`, `multi-provider-dispatch.md` (no auth/dispatch change), `subprocess-cwd.md`, `path-validation.md`, `eval-spec-stable-ids.md`, `spec-cli-precedence.md`, `dual-version-external-schema-embed.md`, `llm-cli-exit-code-taxonomy.md`, `monotonic-time-indirection.md`, `bundled-skill-docs-sync.md`, `readme-promotion-recipe.md`, `positional-id-zip-validation.md`, `pytester-inprocess-coverage-hazard.md`, `in-memory-dict-loader-path.md`, `precall-env-validation.md`, `per-type-drift-hints.md`, `permissive-parser-strict-validator.md`, `internal-skill-live-test-tmp-symlink.md`, `skill-identity-from-frontmatter.md`, `test-infra-shutil-which-coupling.md`, `llm-judge-prompt-injection.md`, `project-root-home-exclusion.md`, `harness-protocol-shape.md`, `cross-axis-comparability-refusal.md`, `data-vs-asserter-split.md`, `sidecar-during-staging.md` (no new sidecar; existing write site already follows the pattern).

### Scoping questions

These shape DEC-001 through DEC-005.

**Q1: Anthropic capture strategy.** No separately-billed reasoning-token field exists in `anthropic.types.Usage`. For extended-thinking models, `output_tokens` already INCLUDES thinking tokens. Options:

- **A.** Always return `None` for Anthropic. Document that Anthropic bundles thinking into `output_tokens`. (Simplest. Honest. Means GPT-5 grading shows real numbers, Claude Opus 4.x thinking grading shows `None`.)
- **B.** Approximate by calling `client.messages.count_tokens(thinking_text)` on each `ThinkingBlock` after the response returns. (Adds 1 SDK round-trip per grader call; defeats the purpose of free-feeling token capture.)
- **C.** Approximate by char-count / 4 of the thinking block text. (Fast but inaccurate; misleading number.)
- **D.** Same as A, but ALSO surface a separate `thinking_block_count: int` (number of thinking blocks present) on `ModelResult` as a proxy signal. (Adds scope.)

**Q2: 0-vs-None semantic for OpenAI.** OpenAI Responses API always returns `output_tokens_details.reasoning_tokens: int` — `0` for non-reasoning models, real count for reasoning. Options:

- **A.** Treat `0` as a real value: store `0` for non-reasoning calls. `None` only when SDK shape is malformed/absent. (Distinguishes "model didn't reason" from "couldn't read.")
- **B.** Coerce `0 → None`: only store positive counts. (Loses the "we measured zero" signal but makes "reasoning happened" a simple `is not None` check.)
- **C.** Always store `0` (never `None`) for OpenAI when SDK is well-formed. Keep the malformed branch as `None`. (Same as A in practice.)

**Q3: Sum-vs-pick across multiple grader calls.** A grading run can issue several model calls (extract, grade, blind-compare = 2 calls). `IterationContext.reasoning_tokens` is a single `int | None`. Options:

- **A.** Sum across all grader calls in the iteration (parallels how `GradingReport.input_tokens` / `output_tokens` are summed). `None` if every component is `None`; otherwise sum of non-None values.
- **B.** Pick a specific call (e.g. just the L3 grading call, ignore extraction). Simpler but loses extraction signal.
- **C.** Sum but track whether ANY component was None (mixed-source ambiguity). Out of scope for v1.

**Q4: Where does `reasoning_tokens` live on the report side?** Need a path from grader's `ModelResult` chain to `_write_context_sidecar`. Options:

- **A.** Add `reasoning_tokens: int | None` field to `GradingReport` and `ExtractionReport`. Persist to `grading.json` / `extraction.json`. **Cost:** schema bump on both files (v4→v5). **Benefit:** symmetric with input/output token persistence; future audit tooling can read it directly.
- **B.** Add to `GradingReport` / `ExtractionReport` as in-memory-only fields (NOT in `to_json` / `from_json`). No schema bump. **Cost:** asymmetric (other token totals persist, this one doesn't); future tooling cannot read it from sidecars.
- **C.** Skip the report entirely; thread reasoning_tokens out-of-band as a new return value from grader entry points. **Cost:** signature churn on every grader entry point + every call site; back-compat shim concerns.
- **D.** Same as A but use the "always-vN-by-design pre-declare nullable" trick — add the field to GradingReport/ExtractionReport but pre-declare `cost_usd_grader: float | None = None` at the same time so the `cost_usd` (#169) follow-up doesn't bump again. **Cost:** speculative-field whiff, but matches the IterationContext shape.

**Q5: Blind-compare scope.** `blind_compare_from_spec` produces a `BlindReport`. Does this PR populate `reasoning_tokens` for blind-compare runs too?

- **A.** Yes — add to `BlindReport` and thread through. Blind-compare doesn't write `IterationContext` today, but the field would be available in-memory and via `to_json()`.
- **B.** Defer. Skip BlindReport for this PR.

### Scoping answers (2026-05-09)

- **Q1 → A.** Anthropic always returns `None`. No SDK round-trip; no char-count heuristic. Document the rationale.
- **Q2 → A.** OpenAI: `0` is a real value (model didn't reason); `None` only when SDK shape is malformed/absent.
- **Q3 → A.** Sum across all grader calls in the iteration. `None` if every component is `None`; otherwise sum of non-None values.
- **Q4 → C.** Persist on `GradingReport`/`ExtractionReport` (schema bump v4→v5 on both) AND wire to `BlindReport`. Symmetric with input/output token persistence.
- **Q5 → A** (folded into Q4=C). BlindReport gets the field too. Note: BlindReport has `to_json()` but no CLI command writes it to disk today (`grep` confirms); BlindReport.to_json() is invoked by tests / programmatic callers only. No `from_json()` exists. Adding `reasoning_tokens` to its `to_json()` payload is safe at the existing schema_version=1 (always-v1-by-design pattern, mirroring IterationContext) — the absence of a `from_json()` reader means there is no legacy-read compat to worry about.

---

## Architecture Review

| Area | Rating | Notes |
|---|---|---|
| Security | **pass** | Token counts are non-sensitive observability data. No new I/O surface. Test fixtures use mock SDK responses (no live keys). |
| Performance | **pass** | One additional `getattr` per `_extract_openai_result` call. Anthropic path adds nothing (always returns `None`). No new I/O, no network round-trips. |
| Data model | **pass** | Two schema bumps (`grading.json` v4→v5, `extraction.json` v4→v5) follow the established pattern from #147 and #152. Loaders default missing `reasoning_tokens` to `None` for v4 reads. `IterationContext` stays at v1 (always-v1 contract). `BlindReport` stays at v1 (no `from_json` reader → no legacy concern; field is additive in `to_json`). |
| API design | **pass** | `ModelResult` adds one kwarg-only field after existing fields. All call sites use kwargs (verified — no positional `ModelResult(...)` in tests). Back-compat alias `AnthropicResult is ModelResult` preserved (alias is `is`-identical, no separate class). |
| Observability | **pass** | New field flows through `IterationContext.reasoning_tokens` to `audit.py:868` (display) and `audit.py:1155` (JSON export) — already wired by #154. |
| Testing | **concern** | Three-state semantic (`None`/`0`/`positive`) requires explicit tests for each case in OpenAI extraction. Sum logic needs the "all-None → None, mixed → sum-of-non-None" edge case covered. Legacy-read tests for v4 grading.json/extraction.json must default `reasoning_tokens` to `None`. Mitigated: existing test patterns from `provider_source` v3-vs-v4 and the canonical mock-side-effect pattern apply directly. |
| Back-compat | **pass** | `clauditor._anthropic.ModelResult is clauditor._providers.ModelResult` identity test already exists (`test_providers_auth.py:491`); will continue to pass. No positional-kwarg breakage since all callers use kwargs. |

No blockers. Two concerns folded into the test plan (US-005, US-006).

---

## Refinement Log

### Decisions

- **DEC-001: Anthropic always returns `None` for `reasoning_tokens`.**
  Rationale: `anthropic.types.Usage` carries no separately-billed thinking-token field. For extended-thinking models, `output_tokens` already includes thinking tokens. Approximating via `count_tokens` round-trip would defeat the free-feeling capture; char-count heuristics are inaccurate. Honest `None` is the only correct representation. (Q1=A.)

- **DEC-002: OpenAI stores `0` as a real value; `None` only on SDK malformation.**
  Rationale: `Response.usage.output_tokens_details.reasoning_tokens` is typed `int` (Stainless-generated, always present). `0` means "model didn't reason" (a real per-call signal). `None` means "couldn't read." Conflating loses the signal. (Q2=A.)

- **DEC-003: Sum across all grader calls in an iteration.**
  Rationale: Parallels existing summing of `input_tokens` / `output_tokens` at `quality_grader.py:1228` and `grader.py:870`. The semantic for sum: `None` if every component is `None` (no reasoning-capable call happened); otherwise `sum(r.reasoning_tokens for r in results if r.reasoning_tokens is not None)`. A single `None` component does not poison a sum that has at least one real value. (Q3=A.)

- **DEC-004: Persist `reasoning_tokens` on `GradingReport` and `ExtractionReport`; bump both schemas v4→v5.**
  Rationale: Symmetric with `input_tokens` / `output_tokens` persistence. Future audit tooling can read from sidecars without re-running graders. Loader pattern follows #152's `harness` v3→v4 default-on-read shape. `MAX_SCHEMA_VERSION` bumps via the one-number-per-file edit established by DEC-008 of #147. (Q4=C, half 1.)

- **DEC-005: Wire `reasoning_tokens` into `BlindReport.to_json()` at the existing `schema_version: 1`.**
  Rationale: `BlindReport` has `to_json()` but no `from_json()` reader, no CLI command writes it as a sidecar (grep-verified), and the `.claude/rules/json-schema-version.md` "always-v1 by design" pattern (same as `context.json`) applies — additive nullable fields in v1 do not require a bump. The field is available in-memory for programmatic callers. No legacy-read compat surface to maintain. (Q4=C, half 2.)

- **DEC-006: Defensive extraction lives in pure helpers per provider.**
  Rationale: `_providers/_openai.py::_extract_reasoning_tokens(usage) -> int | None` is pure, testable in isolation, and follows the sixth-anchor pattern from `pure-compute-vs-io-split.md` (the runner classification helpers). The `bool is not int` guard is enforced per `constant-with-type-info.md`. Anthropic does not get an analogous helper (always-`None` is one line in `_extract_result`).

- **DEC-007: Loaders default missing `reasoning_tokens` to `None` for v4 reads.**
  Rationale: Mirrors #147's `provider_source` default-to-`"anthropic"` and #152's `harness` default-to-`"claude-code"` for legacy reads. The default `None` correctly represents "we don't know whether reasoning happened" for pre-#170 history. Per the `_check_schema_version` "version-and-up" loader pattern.

- **DEC-008: `IterationContext.reasoning_tokens` sources from `primary_report.reasoning_tokens`.**
  Rationale: `_write_context_sidecar` already reads `model_grader=primary_report.model`. The reasoning-tokens source is the same dataclass — no new threading required. Validation paths (`_write_context_sidecar` call site at `cli/grade.py:797`) only need to swap `reasoning_tokens=None` (line 921) for `reasoning_tokens=primary_report.reasoning_tokens`. The `validate` command's path stays `None` (no LLM grader → structurally `None`).

---

## Detailed Breakdown

The natural ordering: provider extractors first (US-001 OpenAI, US-002 Anthropic), then `ModelResult` field (US-003 — actually goes first since both extractors populate it), then the report dataclasses + sums (US-004), then the CLI seam wiring (US-005), then the schema bumps + loader compat (US-006), then test gap closure (US-007), then Quality Gate + Patterns & Memory.

Re-sequenced for dependency correctness:

### US-001: Add `reasoning_tokens: int | None = None` to `ModelResult`

- **Description:** Add the new optional field to `ModelResult`. Kwarg-only, default `None`, placed after existing fields. Update the back-compat alias `AnthropicResult` (no change required — it's `= ModelResult`). Verify `clauditor._anthropic.ModelResult is clauditor._providers.ModelResult` still holds.
- **Traces to:** DEC-001, DEC-002, DEC-006.
- **Acceptance Criteria:**
  - `ModelResult.reasoning_tokens: int | None = None` field present in `src/clauditor/_providers/_anthropic.py`.
  - All existing tests pass (no positional-kwarg breakage).
  - Class-identity test `tests/test_providers_auth.py::TestExceptionClassIdentity` continues to pass.
  - `uv run ruff check src/ tests/` clean. `uv run pytest --cov=clauditor --cov-report=term-missing` passes (≥80%).
- **Done when:** Field is on the dataclass; existing test suite green; identity test still passes.
- **Files:**
  - `src/clauditor/_providers/_anthropic.py` (add field to `ModelResult` ~line 192).
- **Depends on:** none.
- **TDD:** Add a small test asserting `ModelResult().reasoning_tokens is None` and `ModelResult(reasoning_tokens=42).reasoning_tokens == 42`. Identity test (`AnthropicResult is ModelResult`) remains unchanged.

### US-002: OpenAI backend extracts `reasoning_tokens` from `output_tokens_details`

- **Description:** Add a pure helper `_extract_reasoning_tokens(usage) -> int | None` in `_providers/_openai.py` that defensively reads `usage.output_tokens_details.reasoning_tokens`. Returns the int (including `0`), or `None` on any malformed input (missing attribute, wrong type, bool, exception). Wire into `_extract_openai_result` so the returned `ModelResult` carries the field. Per DEC-002, `0` is preserved (NOT coerced to `None`).
- **Traces to:** DEC-002, DEC-006.
- **Acceptance Criteria:**
  - `_extract_reasoning_tokens(usage)` is a module-level pure function (no I/O, never raises).
  - Returns `int` (including `0`) for well-formed input.
  - Returns `None` for: `usage is None`, missing `output_tokens_details`, missing `reasoning_tokens`, non-int value, `bool` value (per `constant-with-type-info.md`), any exception during read.
  - `call_openai` populates `ModelResult.reasoning_tokens` via this helper.
  - `uv run ruff check src/ tests/` clean.
- **Done when:** Helper exists, is unit-tested in isolation, and `call_openai`'s `ModelResult` carries the field on every code path.
- **Files:**
  - `src/clauditor/_providers/_openai.py` (add helper; thread into `_extract_openai_result` ~line 220).
- **Depends on:** US-001.
- **TDD:**
  - `_extract_reasoning_tokens(None) is None`.
  - `_extract_reasoning_tokens(usage_with_42) == 42`.
  - `_extract_reasoning_tokens(usage_with_0) == 0` (the load-bearing zero-vs-None test for DEC-002).
  - `_extract_reasoning_tokens(usage_missing_details) is None`.
  - `_extract_reasoning_tokens(usage_with_bool) is None` (bool guard).
  - `_extract_reasoning_tokens(usage_with_string) is None`.
  - End-to-end: `call_openai` with mock response carrying `reasoning_tokens=99` → `result.reasoning_tokens == 99`.
  - End-to-end: `call_openai` with mock response carrying `reasoning_tokens=0` → `result.reasoning_tokens == 0`.

### US-003: Anthropic backend documents `reasoning_tokens` always returns `None`

- **Description:** In `_providers/_anthropic.py::_extract_result` (or wherever `ModelResult` is constructed), explicitly populate `reasoning_tokens=None` with a brief inline comment citing DEC-001. No SDK reading required. Add a unit test asserting this invariant so a future contributor doesn't accidentally wire it up via a copy-paste from the OpenAI side.
- **Traces to:** DEC-001.
- **Acceptance Criteria:**
  - `call_anthropic` populates `ModelResult.reasoning_tokens` as `None` on every code path (success, retry, both transports).
  - One inline comment in the construction site naming DEC-001 + the rationale ("Anthropic SDK has no separately-billed reasoning-token field; output_tokens already includes thinking").
  - Test asserts `result.reasoning_tokens is None` for both a normal mock and a thinking-style mock.
- **Done when:** Anthropic backend always returns `None`; test guards against future drift.
- **Files:**
  - `src/clauditor/_providers/_anthropic.py` (~line 299 ModelResult construction).
- **Depends on:** US-001.
- **TDD:**
  - `test_anthropic_reasoning_tokens_always_none` — mock response with `usage = MagicMock(input_tokens=10, output_tokens=5)` → `result.reasoning_tokens is None`.
  - `test_anthropic_reasoning_tokens_none_for_thinking_style_response` — mock response with `content=[ThinkingBlock-like, TextBlock]` → still `None`.

### US-004: Add `reasoning_tokens` to `GradingReport`, `ExtractionReport`, `BlindReport` + sum logic

- **Description:** Add `reasoning_tokens: int | None = None` field to all three report dataclasses. Update the existing token-summing sites to compute `reasoning_tokens` per DEC-003: `sum(r.reasoning_tokens for r in results if r.reasoning_tokens is not None) or None` (the `or None` collapses an empty sum to `None`). Wire through every report constructor that today accepts `input_tokens=` / `output_tokens=`. Bump `GradingReport.schema_version` and `ExtractionReport.schema_version` from 4 to 5; emit the field in `to_json()` AFTER existing fields. `BlindReport.to_json()` adds the field at the existing `schema_version: 1` per DEC-005.
- **Traces to:** DEC-003, DEC-004, DEC-005.
- **Acceptance Criteria:**
  - `GradingReport.reasoning_tokens: int | None = None` field present; `to_json` emits it; `schema_version` bumped to 5.
  - `ExtractionReport.reasoning_tokens: int | None = None` field present; `to_json` emits it; `schema_version` bumped to 5.
  - `BlindReport.reasoning_tokens: int | None = None` field present; `to_json` emits it; `schema_version` stays 1.
  - All sum sites (`quality_grader.py:1228`, `quality_grader.py:660`, `grader.py:870`, blind-compare two-call site at `quality_grader.py:757`) sum reasoning tokens with the all-None→None semantic.
  - Per `mock-side-effect-for-distinct-calls.md`: tests covering the sum logic use `side_effect=[result_with_none, result_with_42]` to verify mixed-component summing.
- **Done when:** All three reports carry the field; sums work correctly across multi-call scenarios; tests for None/0/sum semantics pass.
- **Files:**
  - `src/clauditor/quality_grader.py` (`GradingReport` ~line 81, `BlindReport` ~line 242, sum sites at ~660, ~757, ~1228).
  - `src/clauditor/grader.py` (`ExtractionReport` ~line 131, sum site at ~870).
- **Depends on:** US-002, US-003.
- **TDD:**
  - `test_grading_report_reasoning_tokens_default_none`.
  - `test_grading_report_reasoning_tokens_round_trips_42`.
  - `test_grading_report_to_json_emits_schema_version_5`.
  - `test_grading_report_sum_all_none_returns_none` (two `ModelResult`s each with `reasoning_tokens=None` → final `report.reasoning_tokens is None`).
  - `test_grading_report_sum_mixed_returns_sum_of_non_none` (`[None, 42]` → `42`, NOT `0`).
  - `test_grading_report_sum_two_ints_returns_sum` (`[10, 20]` → `30`).
  - Mirror tests for `ExtractionReport` (schema_version 5) and `BlindReport` (schema_version stays 1).
  - Blind-compare-specific: two parallel `call_anthropic` results with `reasoning_tokens=15` and `reasoning_tokens=20` → `BlindReport.reasoning_tokens == 35`.

### US-005: `_write_context_sidecar` reads `primary_report.reasoning_tokens`

- **Description:** Update `cli/grade.py::_write_context_sidecar` (line 887) to read `reasoning_tokens=primary_report.reasoning_tokens` instead of the hardcoded `None`. The call site at line 797 already has `primary_report` in scope. `cli/validate.py` stays hardcoded `None` (no LLM grader for that command — structurally None per DEC-008). Add an integration-shape test verifying the wired value reaches `context.json` on disk.
- **Traces to:** DEC-008.
- **Acceptance Criteria:**
  - `_write_context_sidecar` accepts `reasoning_tokens` from caller (or pulls from `primary_report` directly — simplest is direct read).
  - `cli/grade.py:921` no longer has `reasoning_tokens=None` hardcoded; reads from `primary_report.reasoning_tokens`.
  - `cli/validate.py` still passes `reasoning_tokens=None` (validate has no grader call; field is structurally None).
  - Integration test: a graded run with a mocked OpenAI grader returning `reasoning_tokens=50` produces a `context.json` with `"reasoning_tokens": 50`.
  - Integration test: a graded run with mocked Anthropic grader produces `context.json` with `"reasoning_tokens": null`.
- **Done when:** Real value flows from `ModelResult` → grader sum → `GradingReport.reasoning_tokens` → `IterationContext.reasoning_tokens` → `context.json`.
- **Files:**
  - `src/clauditor/cli/grade.py` (~line 797 call site, ~line 887 helper signature, ~line 921 hardcoded value).
- **Depends on:** US-004.
- **TDD:**
  - `test_write_context_sidecar_reads_reasoning_tokens_from_report`.
  - `test_grade_command_e2e_openai_writes_reasoning_tokens_to_context_json` (with mocked grader).
  - `test_grade_command_e2e_anthropic_writes_null_reasoning_tokens_to_context_json`.
  - `test_validate_command_writes_null_reasoning_tokens_to_context_json` (no grader → structurally None).

### US-006: Audit loader compat — bump `MAX_SCHEMA_VERSION` and verify v4 default-on-read

- **Description:** Bump `audit.py::MAX_SCHEMA_VERSION` to `{"grading.json": 5, "extraction.json": 5}` (assertions.json and context.json unchanged). Verify `GradingReport.from_json` and `ExtractionReport.from_json` default missing `reasoning_tokens` to `None` for v4 reads (mirrors the `provider_source`/`harness` legacy-default pattern). Audit's `_records_from_grading` / `_records_from_extraction` do NOT need to read `reasoning_tokens` (per the data-model review — reasoning_tokens is per-iteration, not per-record).
- **Traces to:** DEC-004, DEC-007.
- **Acceptance Criteria:**
  - `MAX_SCHEMA_VERSION["grading.json"] == 5` and `["extraction.json"] == 5`.
  - `GradingReport.from_json` accepts v1/v2/v3/v4/v5 payloads; v1-v4 default `reasoning_tokens` to `None`.
  - `ExtractionReport.from_json` mirrors.
  - `_records_from_grading` / `_records_from_extraction` unchanged (no new field consumed).
  - Existing `tests/test_audit.py` continues to pass.
- **Done when:** Audit reads pre-#170 history without warnings; new history round-trips through v5 cleanly.
- **Files:**
  - `src/clauditor/audit.py` (`MAX_SCHEMA_VERSION` table).
  - `src/clauditor/quality_grader.py` (`GradingReport.from_json` legacy-default).
  - `src/clauditor/grader.py` (`ExtractionReport.from_json` legacy-default).
- **Depends on:** US-004.
- **TDD:**
  - `test_grading_report_from_json_v4_defaults_reasoning_tokens_to_none`.
  - `test_grading_report_from_json_v5_round_trip_with_reasoning_tokens_42`.
  - Mirror two tests for `ExtractionReport`.
  - `test_audit_max_schema_version_grading_is_5` / `_extraction_is_5`.

### US-007: Quality Gate — code review x4 + CodeRabbit + project validation

- **Description:** Run code-reviewer agent four times across the full changeset, fixing all real bugs found each pass. Run CodeRabbit if available. Project validation (`uv run ruff check src/ tests/`, `uv run pytest --cov=clauditor --cov-report=term-missing` ≥80%) must pass after all fixes. Verify no positional-arg breakage in test suites; verify legacy-read tests for v4 sidecars all pass.
- **Traces to:** All DECs (verifies the implementation honors them).
- **Acceptance Criteria:**
  - 4 passes of code review with all real bugs fixed.
  - CodeRabbit (if available) findings addressed.
  - `uv run ruff check src/ tests/` clean.
  - `uv run pytest --cov=clauditor --cov-report=term-missing` ≥80%.
  - All schema-bump regression tests pass.
- **Done when:** All quality gates green, no real findings outstanding.
- **Files:** Any file touched by US-001 through US-006.
- **Depends on:** US-001, US-002, US-003, US-004, US-005, US-006.

### US-008: Patterns & Memory — update conventions and docs

- **Description:** Update `.claude/rules/centralized-sdk-call.md` to include `reasoning_tokens` in the `ModelResult` field-list reference. Update `.claude/rules/json-schema-version.md` "Schema version bumps for #147" / "for #152" precedent with a short "Schema version bumps for #170" subsection documenting `grading.json` v4→v5, `extraction.json` v4→v5, BlindReport stays v1, IterationContext stays v1. Update `.claude/rules/pure-compute-vs-io-split.md` if `_extract_reasoning_tokens` warrants a new anchor (probably folds into the existing seventh anchor — Codex helpers — since it's the same pattern, just a one-line addition to the existing anchor). Add a "honest None for Anthropic" note to relevant rules. No new rule file needed.
- **Traces to:** All DECs (codifies the patterns for future tickets).
- **Acceptance Criteria:**
  - `centralized-sdk-call.md` updated with new field reference.
  - `json-schema-version.md` updated with #170 schema-bump precedent.
  - `pure-compute-vs-io-split.md` reviewed; updated if a new anchor is warranted.
  - Per `rule-refresh-vs-delete.md`: refresh-in-place, no parallel rule files.
- **Done when:** Memory and rules reflect the new patterns; future contributors will inherit the discipline.
- **Files:**
  - `.claude/rules/centralized-sdk-call.md`.
  - `.claude/rules/json-schema-version.md`.
  - `.claude/rules/pure-compute-vs-io-split.md` (light touch if any).
- **Depends on:** US-007.

---

## Beads Manifest

- **Epic:** `clauditor-o7u` — 170: Reasoning tokens — capture separately-billed reasoning tokens in ModelResult
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/170-reasoning-tokens-capture`
- **Branch:** `feature/170-reasoning-tokens-capture`
- **PR:** https://github.com/wjduenow/clauditor/pull/174

| Story | Bead ID | Depends on |
|---|---|---|
| US-001 — Add reasoning_tokens field to ModelResult | `clauditor-oynv` | (none — ready) |
| US-002 — OpenAI backend extracts reasoning_tokens | `clauditor-s0vu` | US-001 |
| US-003 — Anthropic backend documents always-None | `clauditor-3wdi` | US-001 |
| US-004 — Add field to reports + sum logic | `clauditor-4l6s` | US-002, US-003 |
| US-005 — `_write_context_sidecar` reads from report | `clauditor-3k5b` | US-004 |
| US-006 — Audit loader bumps + v4 default-on-read | `clauditor-0mv1` | US-004 |
| Quality Gate — code review x4 + CodeRabbit | `clauditor-q54i` | US-001..US-006 |
| Patterns & Memory | `clauditor-rzif` | Quality Gate |
