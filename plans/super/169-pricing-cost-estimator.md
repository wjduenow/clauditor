# 169: Pricing — `cost_usd` estimation module for `context.json`

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/169
- **Branch:** `feature/169-pricing-cost-estimator`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/169-pricing-cost-estimator`
- **Phase:** published
- **Sessions:** 1 (2026-05-09)
- **Depends on:** #154 (CLOSED — `IterationContext.cost_usd: float | None = None` placeholder ships in `context.py`)
- **Related:** #170 (`reasoning_tokens` capture — sibling follow-up to #154; #169 accepts a `reasoning_tokens` parameter so it composes forward-compat without depending on #170)

---

## Discovery

### Ticket summary

**What.** Add `src/clauditor/_providers/_pricing.py` exposing a pure
`estimate_cost(provider, model, input_tokens, output_tokens, reasoning_tokens=None) -> float | None`
helper backed by a hardcoded per-provider price table. Wire it into the
`context.json` write seam so `IterationContext.cost_usd` is populated
with a real estimate (vs the `None` placeholder #154 ships) whenever the
`(provider, model)` pair is recognized. Unknown pairs continue to write
`cost_usd: null` cleanly with no exception.

**Why.** #154 stabilized the `context.json` schema with
`cost_usd: float | None = None` always-null. Lighting up real values
turns the existing audit/badge surface into a cost-attribution tool:
operators can see per-iteration USD spend, compare cost across
harness/provider/model axes (the cross-axis machinery from #153 already
groups these), and spot regressions (a model bump that doubles cost
shows up immediately). Without this, `cost_usd` is a permanent dead
column.

**Pre-plan research (2026-05-09).** Investigated whether either
provider exposes a programmatic rate card so we could fetch live
prices instead of hardcoding. **Verdict: no.** Anthropic's
`/v1/models` carries no pricing fields; `/v1/organizations/cost_report`
requires an admin key (not a regular API key) and returns realized
USD aggregate, not a forward rate card. OpenAI's `/v1/models` carries
no pricing fields (`openai/openai-python#2074` closed as not
planned); `/v1/organization/costs` is admin-key gated and
realized-cost only. Reasoning tokens are billed at the model's output
rate by both providers; there is no separate reasoning rate to look
up. The hardcoded-table approach in the ticket is the right shape;
the issue body was updated with a "Research notes" block capturing
the verdict and four concrete implications (reasoning-token contract,
version + last-verified date, source-of-truth URLs as code comments,
cache-token pricing deferral). See:
- https://platform.claude.com/docs/en/about-claude/pricing
- https://openai.com/api/pricing/

### Codebase findings (from Codebase Scout)

#### `IterationContext` is already wired — only `cost_usd` is dead

`src/clauditor/context.py:59–77` defines the dataclass. `cost_usd:
float | None = None` is line 76 (placeholder). Schema version is 1
and stays at 1 — `context.json` is forward-compat-by-design (DEC-007
of #154's plan: nullable fields populated later don't bump the
version).

Bool-guard precedent already in place: `context.py:165–167` rejects
`isinstance(reasoning_tokens, bool)` before the `int` check; same
guard at `cost_usd` (lines 178–179). #169 inherits the validator —
no `from_dict` changes needed for `cost_usd`.

#### Write seam: `_write_context_sidecar` already builds the dataclass

**Primary write site** (`src/clauditor/cli/grade.py:887–926`):
- `IterationContext(...)` constructed at lines 912–923.
- In scope at construction: `harness_name: str`, `provider: str`
  (the **grader** provider, resolved at line 386 via
  `_resolve_grading_provider(args, spec.eval_spec)`),
  `primary_skill_result: SkillResult` (carries
  `harness_metadata["model"]`, `harness_metadata["system_prompt_source"]`,
  `harness_metadata.get("sandbox_mode")`), `primary_report:
  GradingReport` (carries `model: str`, `input_tokens: int`,
  `output_tokens: int`).
- **Fields hardcoded today**: `cost_usd=None` (line 921),
  `reasoning_tokens=None` (line 922).
- **Token sources NOT YET PASSED to the constructor**:
  `primary_skill_result.input_tokens` /
  `primary_skill_result.output_tokens` (runner-side) and
  `primary_report.input_tokens` / `primary_report.output_tokens`
  (grader-side). Both fields exist and are populated; they're just
  not threaded into the `IterationContext` builder.

**Second write site** (`src/clauditor/cli/validate.py:318–332`):
- Validate-only iterations: `provider=None`, `model_grader=None`,
  `cost_usd=None`, `reasoning_tokens=None`. No grader call ran, so
  cost is genuinely null. Only the runner side could conceivably
  contribute, but with no provider context the answer is still
  `None` (and probably should stay so for validate-only iterations
  to avoid a confusing "$0.0001 for the runner alone" line).

#### Token surfaces inventory

| Source | Fields | Notes |
|---|---|---|
| `SkillResult` (`runner.py:54–112`) | `input_tokens: int = 0`, `output_tokens: int = 0` | Populated from stream-json parse in `SkillRunner._invoke`. Reliability varies by transport (api vs cli) — `claude` CLI's stream-json `result` carries token counts; SDK transport carries them too. |
| `GradingReport` (`quality_grader.py:75–115`) | `input_tokens`, `output_tokens`, `model: str` | Reliably populated from `ModelResult` during grading. |
| `ExtractionReport` (`grader.py:86–159`) | `input_tokens`, `output_tokens` | Layer 2 grader call when `EvalSpec.sections` declared — separate API call, separate cost. |
| `ModelResult` (`_providers/_anthropic.py:192–241`) | `input_tokens`, `output_tokens`, `provider: Literal["anthropic", "openai"]`, `source: Literal["api", "cli"]` | No `reasoning_tokens` field today (reserved for #170). |

#### Critical asymmetry: runner provider vs grader provider

At `_write_context_sidecar`, the `provider` parameter is the
**grader** provider. The runner's harness binary
(`claude`/`codex`) is independently selected via
`_resolve_harness`. A `(harness=claude-code, provider=openai)`
combo is legal — it means Claude Code ran the skill subprocess
(billed against `ANTHROPIC_API_KEY`) and the OpenAI SDK served the
grader call. **The runner cost and grader cost may use different
providers** and even different price tables.

`model_runner` is `harness_metadata["model"]` for Codex (`_codex.py:747`)
but is unreliably populated for ClaudeCodeHarness — DEC-007 of
#154's plan: when neither the constructor nor a per-call override
pinned a model, `model_runner` is `None` because the `claude` CLI's
stream-json `result` carries no model field. So the runner cost is
*unknown* for any ClaudeCodeHarness invocation that didn't pin
`--model` explicitly, even when tokens are known.

Inferring the runner's *provider* is also non-trivial:
ClaudeCodeHarness → "anthropic" runner provider; CodexHarness →
"openai" runner provider. The Codex backend's auth allows either
`OPENAI_API_KEY` or `CODEX_API_KEY`, so the SDK is OpenAI either
way for cost-table purposes. This mapping is one new
helper.

#### `_providers/` package layout (where `_pricing.py` lands)

```
src/clauditor/_providers/
├── __init__.py    (call_model dispatcher, resolve_harness, resolve_grading_provider, ...)
├── _anthropic.py  (call_anthropic, AnthropicHelperError, ...)
├── _openai.py     (call_openai, OpenAIHelperError, ...)
├── _auth.py       (check_provider_auth, announce_*, ...)
└── _retry.py      (compute_backoff, compute_retry_decision, ...)
```

`_pricing.py` slots in as a sibling — independent of `call_model`,
no SDK calls, no I/O. Mirrors the structural shape of `_retry.py`
(also a pure-compute sibling that doesn't consume `call_model`).

#### Audit + badge already read `cost_usd`

- `src/clauditor/audit.py::_read_context` reads `context.json` via
  `IterationContext.from_dict`. Every `IterationContext` field
  (including `cost_usd`) is loaded and exposed to renderers.
- `src/clauditor/badge.py::ClauditorExtension.context:
  IterationContext | None = None` (line 209) round-trips through
  the badge sidecar's clauditor-extension payload.
- Both render `cost_usd` as `null` today. **No reader-side changes
  needed for #169** — once the writer stamps a real number, every
  surface starts showing it.

### Conventions / rules consulted (from Convention Checker)

**APPLY — drive implementation:**

1. **`pure-compute-vs-io-split.md`** — `_pricing.py` is a pure
   module: zero I/O, zero subprocess, zero network. The price
   table is a module-level constant; `estimate_cost` takes plain
   primitives and returns `float | None`. Adds a ninth canonical
   anchor sibling to `compute_benchmark` /
   `blind_compare_from_spec` / etc. The rule's "two-callers
   threshold" is borderline (one production call site today: the
   `_write_context_sidecar` writer; tests are the second consumer
   by design).
2. **`constant-with-type-info.md`** — module-level constants must
   carry explicit type annotations and the **bool-vs-int guard**
   on any int parameter. `_PRICING_TABLE_VERSION: Final[int]`,
   `_LAST_VERIFIED: Final[str]`, `_PRICING_TABLE: Final[dict[str,
   dict[str, ProviderPriceCard]]]`. `estimate_cost` must reject
   `isinstance(input_tokens, bool)` etc. before the `int` check —
   `True` would otherwise compute a "1-token cost" silently.
3. **`pre-llm-contract-hard-validate.md` (input-side)** —
   load-bearing input contract is `int` for all token fields and
   `str` for `provider`/`model`. Wrong types → `ValueError` with
   actionable message. **Do not** raise on unknown
   `(provider, model)` — that's a *lookup miss*, not a contract
   violation, and the ticket explicitly mandates `None` (cleanly)
   for that case. Two different error categories.
4. **`multi-provider-dispatch.md`** — `estimate_cost(provider=...)`
   is a new dispatch surface but a *graceful-fallback* one: unknown
   provider returns `None`, never raises. Mirrors the
   `_pricing.py` price table being keyed on the same
   `{"anthropic", "openai"}` literal set the auth dispatcher uses.
5. **`sidecar-during-staging.md`** — the cost computation happens
   inside `_write_context_sidecar`, which already runs inside
   `workspace.tmp_path` BEFORE `workspace.finalize()`. No new
   staging-discipline work; we inherit the existing envelope.
6. **`centralized-sdk-call.md` (sibling, not consumer)** —
   `_pricing.py` lives in `_providers/` because pricing is a
   provider-axis concern. It does NOT call `call_model`; it does
   NOT need the announcement-family or the centralized retry
   policy. Same package as `_retry.py` — pure-compute siblings of
   the SDK callers.
7. **`json-schema-version.md`** — explicitly does NOT bump.
   `context.json` stays at `schema_version: 1` (forward-compat-by-
   design per DEC-007 of #154). Confirms the rule's "Always-v1
   contract" subsection.
8. **`back-compat-shim-discipline.md`** — N/A (new module, no
   existing callers, no shim).
9. **`non-mutating-scrub.md`** — N/A (the price table is a
   module-level `Final` constant, never mutated; primitives in /
   primitives out).
10. **`data-vs-asserter-split.md`** — N/A (no `assert_*` test
    helpers; the price table is a typed constant, not a class
    growing methods).

**N/A — explicitly ruled out:** every other rule in
`.claude/rules/` (32 rules) — none touch a pure pricing-table
seam. Notably ruled out: `harness-protocol-shape.md` (provider-
agnostic infrastructure), `spec-cli-precedence.md` (no CLI flag,
no spec field — the ticket explicitly states this), `eval-spec-
stable-ids.md` (no spec field), `precall-env-validation.md` (no
env vars), `llm-cli-exit-code-taxonomy.md` (library, not CLI),
`cross-axis-comparability-refusal.md` (audit-side; #154 already
groups by `(harness, provider, ...)`), `bundled-skill-docs-
sync.md` (not a skill).

**Rules drift candidates (Patterns & Memory story):**

No rule's canonical-implementation section names a file that
moves or renames. Optional informational refreshes for the last
story:

- `centralized-sdk-call.md` — add a brief note that `_pricing.py`
  is a sibling pure-compute module alongside `_retry.py`,
  providing cost estimation independently of call routing.
- `multi-provider-dispatch.md` — note that the price-table lookup
  follows the same provider-dispatch shape (return `None` on
  unknown, never raise).
- `pure-compute-vs-io-split.md` — add `_pricing.estimate_cost` as
  the ninth canonical anchor.

These are non-load-bearing prose adds; not strictly required for
the ticket but worth doing while context is fresh.

### Scoping questions for the user

These are the choices that drive DEC-001 through DEC-005 below.

#### Q1: Cost composition — runner + grader, or grader-only?

Ticket says "sum of harness runner cost + grader call cost." But
the runner cost is unreliable today: `model_runner` is `None` for
any ClaudeCodeHarness invocation that didn't pin `--model`
explicitly (DEC-007 of #154's plan).

- **A. Sum runner + grader (ticket-as-written).** Compute
  runner cost from `SkillResult.input_tokens/output_tokens` +
  the runner provider derived from harness identity (`claude-
  code → anthropic`, `codex → openai`) + `model_runner` from
  `harness_metadata`. When `model_runner is None`, runner cost
  contributes `None` and the whole `cost_usd = None`
  (ticket-aligned: "any unknown component → null"). Grader cost
  reads `primary_report.input_tokens/output_tokens` +
  `primary_report.model` + the resolved grader `provider`. Sum
  is the final `cost_usd`.
- **B. Grader-only for now, runner deferred.** Compute only
  Layer 2 + Layer 3 cost (`primary_report` + optional
  `extraction_report` tokens). Runner cost waits on a follow-up
  ticket that hardens `model_runner` capture for ClaudeCodeHarness.
  Cleaner; smaller blast radius; the grader signal is the
  dominant cost in most real workflows.
- **C. Grader-only with explicit follow-up filed.** Same as B
  but file an issue capturing the runner-cost gap and link it
  from the module docstring. Most honest about scope.

#### Q2: Failure-mode semantics for partial-info iterations

When *some* of the cost components are computable but others
aren't (e.g. grader cost known, runner cost unknown):

- **A. All-or-nothing.** Any unknown component → `cost_usd =
  None` whole-iteration. Ticket-aligned acceptance ("Unknown
  models still produce a clean sidecar with cost_usd=null").
  Cleanest for downstream readers; no risk of misleading
  partial sums.
- **B. Best-effort partial.** Stamp whatever is computable. Risk:
  a "$0.05" sidecar that only priced half the call is more
  misleading than a `null`.

#### Q3: `_LAST_VERIFIED` enforcement

Both research agents independently suggested a freshness signal.
Range from cheapest to strictest:

- **A. Documented-only.** Module-level constant + comment
  pointing at the source-of-truth URL. Maintainer reads the
  comment when refreshing. Zero runtime cost. (Cheapest.)
- **B. Stderr warning when stale.** If `today - _LAST_VERIFIED >
  90 days`, emit a one-time per-process warning at the first
  `estimate_cost` call. Surfaces to operators; requires a
  module-level announce flag (precedent: the announcement-
  family pattern in `_providers/_auth.py`).
- **C. Test-side staleness gate.** A unit test asserts the table
  is < N days old; CI fails when stale. Forces refresh
  discipline but blocks merges on calendar drift.

#### Q4: Price-table coverage scope

- **A. Only currently-shipped-and-graded-with models.**
  Anthropic: `claude-sonnet-4-6`, `claude-opus-4-7`,
  `claude-haiku-4-5`. OpenAI: `gpt-5.4`, `gpt-5.4-mini`, plus
  one o-series model we actually grade with (e.g. `o4-mini`).
  Anything else → `None`. Matches the ticket's "Unknown models
  return None" semantic. Smallest table. (Recommended.)
- **B. Family + explicit overrides.** Per-model rows for
  shipped models + a fallback "anthropic family / openai
  family" default rate for unrecognized prefixes. More forgiving
  but invents a "rate that probably is roughly right" — risks
  being silently wrong for a model that bills very differently
  (Claude Opus is ~5x Sonnet).
- **C. Comprehensive table.** Every Anthropic + OpenAI model
  ever shipped. Most accurate, highest maintenance.

### Decisions captured (DEC-001 — DEC-005)

- **DEC-001 — Cost composition: grader-only, runner deferred (no
  follow-up filed).** `cost_usd` sums Layer 2 + Layer 3 grader call
  cost only: `extraction_report.input_tokens/output_tokens` (when
  Layer 2 ran) + `primary_report.input_tokens/output_tokens` (Layer
  3, always present at the grade write seam). Runner cost is out of
  scope for this ticket; the module docstring states this
  explicitly. Rationale: ClaudeCodeHarness reliably populates
  `model_runner` only when `--model` is pinned (DEC-007 of #154's
  plan), so a runner-cost wiring would silently null-out for the
  common case anyway. The grader signal is the dominant cost in
  most workflows; ship the v1 honest about its scope.
- **DEC-002 — All-or-nothing failure mode.** When any cost
  component is unknown (`estimate_cost` returns `None` for either
  the L2 or L3 lookup), the whole `cost_usd = None` for the
  iteration. Verbatim acceptance: "Unknown models still produce a
  clean sidecar with cost_usd=null." No best-effort partial sums.
  Rationale: a half-priced "$0.05" line is more misleading than a
  `null`; the all-or-nothing rule keeps every recorded value
  trustworthy.
- **DEC-003 — Staleness signal: stderr warning at >90 days.** Use
  the announcement-family pattern from
  `.claude/rules/centralized-sdk-call.md` ("Implicit-coupling
  announcements — an emerging family"). Module-level
  `_announced_pricing_table_stale: bool = False` flag +
  `_PRICING_TABLE_STALE_ANNOUNCEMENT: Final[str]` constant + public
  helper `announce_pricing_table_stale_if_old()`. The helper is
  idempotent (one-shot per process), reads
  `_LAST_VERIFIED`, computes `today - last_verified_date`, and emits
  to stderr only when `> 90 days`. Called once at the first
  `estimate_cost(...)` invocation per process. The announcement names
  the source-of-truth URLs so a maintainer's next move is obvious.
  Days are computed via a `_today` indirection alias (per
  `.claude/rules/monotonic-time-indirection.md` analog) so tests can
  pin the date without patching `datetime`. Rationale: documented-
  only is too quiet (an outdated table silently produces wrong USD
  numbers); a CI gate is too loud (blocks merges on calendar drift
  unrelated to the change). 90 days is roughly a quarterly cadence
  and gives operators a chance to refresh between provider price
  changes.
- **DEC-004 — Coverage: ship only models we grade with today.**
  Anthropic models: `claude-sonnet-4-6`, `claude-opus-4-7`,
  `claude-haiku-4-5`. OpenAI models: `gpt-5.4`, `gpt-5.4-mini`,
  `o4-mini` (pre-resolved at planning time as the o-series model
  most likely in actual grading use; planner confirms with current
  fixtures during implementation, swaps if a different o-series is
  the real default). Anything else → `None`. Matches the ticket's
  "Unknown models return None" acceptance verbatim. The table is
  small, easy to audit by eye, and the maintenance cost on
  refresh is exactly N model rows. No family-fallback heuristic.
  Rationale: a fallback that's "roughly right" is silently wrong
  for a model that bills very differently (Opus is ~5x Sonnet);
  null-on-unknown is the safe default and matches downstream-
  reader expectations.
- **DEC-005 — Validation contract split.** `estimate_cost` raises
  `ValueError` on a contract violation (non-int / bool token args,
  non-string `provider`/`model`); returns `None` on a lookup miss
  (unknown provider, unknown model, unknown
  `(provider, model)` pair). Two distinct categories per
  `.claude/rules/pre-llm-contract-hard-validate.md` (input-side) +
  `.claude/rules/multi-provider-dispatch.md` (graceful fallback on
  unknown provider). Test coverage: one parametrized class for
  each category. Rationale: programmer errors (bool sneaking
  through) should fail loudly; lookup misses (a new model not yet
  in the table) should not crash production grading runs.

---

## Architecture Review

Three review subagents covered the non-trivial axes (Security and
Performance are trivial for a pure-compute table-lookup module —
zero I/O, zero subprocess, zero secrets, O(1) dict lookup).

### Ratings

| Area | Rating | Notes |
|---|---|---|
| **API Design + Data Model (A1–A7)** | PASS w/ 2 concerns | A1/A2/A3/A5/A6 PASS. A4 (helper shape) and A7 (unknown-model UX) raised; both resolved below. |
| **Observability + Stale-warning (B1–B7)** | PASS w/ 1 concern | B1/B2/B4/B5/B6/B7 PASS. B3 (ISO date robustness) raised; baked into the implementation plan. |
| **Testing Strategy (T1–T8)** | PASS w/ 1 concern | T1/T2/T3/T4/T5/T6/T8 PASS. T7 (mock side_effect for L2+L3 wiring) raised; baked into US-005 acceptance. |
| Security | PASS | Pure compute, no I/O, no secrets in scope. The price table is public information. |
| Performance | PASS | One dict lookup per call; module load is a small `Final` constant. No hotspot. |

### Concerns resolved (baked in, no separate decisions needed)

- **A4 — Composition helper.** Add a thin pure helper
  `compute_iteration_cost_usd(primary_report, extraction_report,
  provider) -> float | None` in `_pricing.py`. Handles
  `extraction_report is None` (Layer 2 didn't run → contributes 0
  tokens) cleanly. Per DEC-002, when either internal
  `estimate_cost` returns `None`, the composition returns `None`
  (all-or-nothing). The writer's call site at
  `_write_context_sidecar` then becomes one line.
- **B3 — ISO date arithmetic robustness.** Wrap
  `datetime.date.fromisoformat(_LAST_VERIFIED)` in `try/except
  ValueError`; on parse failure, treat the table as stale and
  emit the announcement (defensive — a maintainer's typo in the
  constant must not crash production). Use a `_today =
  datetime.date.today` module-level indirection alias so tests
  pin the date via `monkeypatch.setattr(_pricing, "_today",
  lambda: date(2027, 1, 1))` (same shape as
  `.claude/rules/monotonic-time-indirection.md`).
- **T7 — Mock side-effect for L2+L3 composition test.** US-005's
  wiring test that mocks `estimate_cost` to verify the
  composition path MUST use `side_effect=[v_l2, v_l3]` not
  `return_value=v` per
  `.claude/rules/mock-side-effect-for-distinct-calls.md`.
  Acceptance criterion on US-005.

### Concern resolved by user decision

- **A7 — Unknown-model UX (DEC-006 below).** When
  `estimate_cost(provider="anthropic", model="claude-3-5-sonnet-old",
  ...)` misses the table (provider known, model unknown), emit a
  one-shot stderr warning **per (provider, model) pair per
  process**. Catches typos and unrecognized model names that
  would otherwise silently produce null sidecars indistinguishable
  from a deliberate unknown-model lookup. Same announcement-
  family pattern as DEC-003's stale warning, except keyed on
  `frozenset[tuple[str, str]]` to track unique (provider, model)
  pairs already announced.

### Decisions captured (DEC-006)

- **DEC-006 — Unknown-model one-shot stderr warning per pair.**
  When `estimate_cost(provider, model, ...)` is called with a
  recognized `provider` but a `model` not in
  `_PRICING_TABLE[provider]`, the lookup returns `None` and
  emits a one-shot stderr warning naming the missing
  `(provider, model)` pair. Tracking state:
  `_announced_unknown_models: set[tuple[str, str]]` —
  mutated-in-place per call site. (This violates the
  `non-mutating-scrub.md` rule's spirit for module-level state,
  but the rule explicitly does not apply to module-private
  observability flags — the existing announcement-family
  members all mutate module-level booleans the same way.)
  The first call with each new pair appends to the set and
  emits; subsequent calls with the same pair are silent.
  Durable substrings tests pin: the literal `pricing:`,
  the literal `not in rate table`, and the message includes
  both the provider and model strings verbatim. Rationale:
  the silent-null failure mode masks real operator
  misconfiguration (a typo in `eval.json`'s `grading_model`,
  or a fresh model name that landed before the price table
  was refreshed); a one-shot warning per pair is loud enough
  to surface but quiet enough to avoid spamming. Per-pair
  granularity (vs strictly one-shot per process) gives
  diagnostic value when multiple models are misnamed; the
  set-membership check stays O(1).

---

## Detailed Breakdown

Six implementation stories + Quality Gate + Patterns & Memory.
Architecture ordering: pure-compute module first
(table → `estimate_cost`), then announcement helpers (staleness,
then unknown-model), then composition helper, then writer wire-in,
then validate-side regression, then quality gate, then memory.

### US-001 — Pricing module skeleton: `_pricing.py` + `_PRICING_TABLE` + `estimate_cost` core

**Description.** Create `src/clauditor/_providers/_pricing.py`. Define the typed price-table constants, the `_PriceCard` NamedTuple, and the core `estimate_cost(provider, model, input_tokens, output_tokens, reasoning_tokens=None) -> float | None` function with input-validation guards. No announcement helpers yet — they land in US-002 and US-003. Module docstring documents reasoning-tokens-billed-at-output-rate, source-of-truth URLs, and the grader-only scope (DEC-001, DEC-005).

**Traces to:** DEC-001 (grader-only scope mentioned in module docstring), DEC-002 (returns `None` on lookup miss), DEC-004 (table coverage), DEC-005 (validation contract split).

**TDD — write these tests first:**
- `TestEstimateCost.test_known_anthropic_model_returns_positive_float` — for each of `claude-sonnet-4-6`, `claude-opus-4-7`, `claude-haiku-4-5`: `estimate_cost("anthropic", model, 1000, 500) > 0.0`.
- `TestEstimateCost.test_known_openai_model_returns_positive_float` — for each of `gpt-5.4`, `gpt-5.4-mini`, `o4-mini`: same shape.
- `TestEstimateCost.test_unknown_provider_returns_none` — `estimate_cost("vertex", "claude-sonnet-4-6", 1000, 500) is None`.
- `TestEstimateCost.test_unknown_model_returns_none` — `estimate_cost("anthropic", "claude-3-5-sonnet-old", 1000, 500) is None`.
- `TestEstimateCost.test_zero_tokens_returns_zero_cost` — `estimate_cost("anthropic", "claude-sonnet-4-6", 0, 0) == 0.0`.
- `TestEstimateCost.test_reasoning_tokens_billed_at_output_rate` — for `provider="openai", model="o4-mini"`, two calls produce equal cost: one with `reasoning_tokens=N`, one with `output_tokens` increased by `N` (Research-notes contract from DEC-001).
- `TestEstimateCostInputValidation.test_bool_input_tokens_raises_value_error` — parametrized across all three int kwargs (input_tokens, output_tokens, reasoning_tokens): `True` and `False` both raise `ValueError`.
- `TestEstimateCostInputValidation.test_negative_tokens_raises_value_error` — parametrized across all three: `-1` raises `ValueError`.
- `TestEstimateCostInputValidation.test_non_string_provider_raises_value_error` — `provider=42` raises `ValueError`.
- `TestEstimateCostInputValidation.test_non_string_model_raises_value_error` — `model=None` raises `ValueError`.
- `TestPricingTableMetadata.test_pricing_table_version_is_int` — `isinstance(_PRICING_TABLE_VERSION, int)` and `_PRICING_TABLE_VERSION >= 1`.
- `TestPricingTableMetadata.test_last_verified_is_iso_date` — `datetime.date.fromisoformat(_LAST_VERIFIED)` succeeds.
- `TestPricingTableMetadata.test_table_contains_expected_models` — every model from DEC-004 is keyed under its provider.

**Acceptance criteria:**
- `src/clauditor/_providers/_pricing.py` exists with the public surface `estimate_cost(...)` and module-private constants `_PRICING_TABLE`, `_PRICING_TABLE_VERSION`, `_LAST_VERIFIED`, `_PriceCard`.
- All TDD tests above pass.
- Module docstring includes (a) one-line description, (b) reasoning-tokens contract, (c) the two source-of-truth URLs, (d) one-line "scope: grader cost only" note (DEC-001), (e) one-line "cache-token pricing deferred" note.
- Source-of-truth URLs appear in source comments next to each provider's sub-table inside `_PRICING_TABLE`.
- `_PRICING_TABLE_VERSION: Final[int] = 1`, `_LAST_VERIFIED: Final[str] = "2026-05-09"`, `_PriceCard = NamedTuple("...", [("input_per_mtok", float), ("output_per_mtok", float)])` (no separate reasoning rate per the contract).
- Bool-guard pattern applied to all three int parameters (precedent: `src/clauditor/context.py:165–179`).
- Validation: `uv run ruff check src/ tests/` clean; `uv run pytest tests/test_providers_pricing.py -v` passes; `uv run pytest --cov=clauditor --cov-report=term-missing` overall coverage ≥ 80%.

**Done when:** All TDD tests green; lint clean; module imports without side effects.

**Files:**
- New: `src/clauditor/_providers/_pricing.py`.
- New: `tests/test_providers_pricing.py`.

**Depends on:** none (independent — does not touch any existing module).

---

### US-002 — Staleness announcement: `_LAST_VERIFIED` >90-day stderr warning

**Description.** Add the staleness one-shot stderr warning per DEC-003. New module-level state in `_pricing.py`: `_announced_pricing_table_stale: bool = False`, `_PRICING_TABLE_STALE_ANNOUNCEMENT: Final[str]` (4-line message including N days, 90-day threshold, both source-of-truth URLs, file path of `_pricing.py`). New public helper `announce_pricing_table_stale_if_old() -> None` invoked at `estimate_cost`'s entry; helper is one-shot per process via the flag. New `_today = datetime.date.today` indirection alias (per B3's resolution) so tests pin the date. Defensive `try/except ValueError` around `date.fromisoformat(_LAST_VERIFIED)` so a typo treats the table as stale.

**Traces to:** DEC-003.

**TDD — write these tests first:**
- `TestStaleAnnouncement` autouse fixture: `monkeypatch.setattr("clauditor._providers._pricing._announced_pricing_table_stale", False)` per test (precedent: `tests/test_providers_auth.py::TestAnnounceImplicitNoApiKey:113-117`).
- `test_no_warning_when_within_90_days` — patch `_today` to return `date(2026, 5, 10)` (one day after `_LAST_VERIFIED`). Call `estimate_cost(...)`; capsys shows no stderr.
- `test_warning_fires_when_over_90_days` — patch `_today` to return `date(2027, 1, 1)`. Call `estimate_cost(...)`; capsys captures stderr containing the three durable substrings: `"90 days"`, `"pricing"`, and at least one of `"platform.claude.com"` / `"openai.com/api/pricing"`.
- `test_warning_fires_only_once_per_process` — same stale-date patch; call `estimate_cost(...)` twice; capsys shows exactly one warning line.
- `test_malformed_last_verified_treats_as_stale` — patch `_LAST_VERIFIED` to `"2026-05-9"` (typo, single-digit day). Call `estimate_cost(...)`; warning fires (defensive treat-as-stale).
- `test_announce_helper_directly` — call `announce_pricing_table_stale_if_old()` standalone; same stale-vs-fresh behavior as via `estimate_cost`.

**Acceptance criteria:**
- `_announced_pricing_table_stale: bool` and `_PRICING_TABLE_STALE_ANNOUNCEMENT: Final[str]` defined in `_pricing.py`.
- Public helper `announce_pricing_table_stale_if_old()` exists and is invoked at `estimate_cost`'s entry (idempotent — one-shot per process).
- `_today = datetime.date.today` indirection alias defined at module level.
- `try/except ValueError` around `date.fromisoformat(_LAST_VERIFIED)` treats parse failure as stale.
- All TDD tests above pass.
- The announcement message includes the exact N-day count and both source-of-truth URLs.

**Done when:** All TDD tests green; lint clean; the staleness-warning fires exactly once per stale process via integration with `estimate_cost`.

**Files:**
- Modified: `src/clauditor/_providers/_pricing.py`.
- Modified: `tests/test_providers_pricing.py` (new `TestStaleAnnouncement` class).

**Depends on:** US-001.

---

### US-003 — Unknown-model announcement: per-pair one-shot stderr warning

**Description.** Add the per-(provider, model) one-shot stderr warning per DEC-006. New module-level state: `_announced_unknown_models: set[tuple[str, str]] = set()`. New `_UNKNOWN_MODEL_ANNOUNCEMENT_TEMPLATE: Final[str]` (3-line message with `pricing:` prefix, `not in rate table` substring, the literal `(provider, model)` pair, and a hint at how to refresh). New public helper `announce_unknown_model(provider: str, model: str) -> None` invoked from `estimate_cost`'s known-provider/unknown-model branch. Helper is set-membership-gated — first call per pair emits; subsequent calls with the same pair stay silent.

**Traces to:** DEC-006.

**TDD — write these tests first:**
- `TestUnknownModelAnnouncement` autouse fixture: `monkeypatch.setattr("clauditor._providers._pricing._announced_unknown_models", set())` per test.
- `test_unknown_model_emits_warning_first_call` — `estimate_cost("anthropic", "claude-fake-1", 100, 50)`; capsys captures stderr containing `"pricing:"`, `"not in rate table"`, `"claude-fake-1"`, `"anthropic"`.
- `test_unknown_model_silent_on_repeated_call` — first call emits; second call with same pair → capsys shows exactly one warning total.
- `test_different_unknown_models_each_warn` — `estimate_cost("anthropic", "claude-fake-1", ...)` then `estimate_cost("anthropic", "claude-fake-2", ...)`; both emit (capsys shows two warnings).
- `test_known_provider_known_model_no_warning` — `estimate_cost("anthropic", "claude-sonnet-4-6", ...)`; no stderr from this code path (the staleness warning may still fire — autouse fixture suppresses it independently).
- `test_unknown_provider_no_unknown_model_warning` — `estimate_cost("vertex", "anything", ...)` returns `None` but does NOT emit the unknown-model warning (different code path; only fires when provider is recognized).

**Acceptance criteria:**
- `_announced_unknown_models: set[tuple[str, str]]` and `_UNKNOWN_MODEL_ANNOUNCEMENT_TEMPLATE: Final[str]` defined.
- Public helper `announce_unknown_model(provider, model)` defined and invoked from `estimate_cost`'s known-provider-unknown-model branch.
- Three durable substrings (`"pricing:"`, `"not in rate table"`, the model string) appear in every emitted warning.
- All TDD tests above pass.

**Done when:** All TDD tests green; the warning fires once per unique (provider, model) miss; known-provider-known-model and unknown-provider paths are silent on this axis.

**Files:**
- Modified: `src/clauditor/_providers/_pricing.py`.
- Modified: `tests/test_providers_pricing.py` (new `TestUnknownModelAnnouncement` class).

**Depends on:** US-001.

---

### US-004 — Composition helper: `compute_iteration_cost_usd`

**Description.** Add a thin pure helper in `_pricing.py` that consumes a `GradingReport` (always present at the grade write seam, Layer 3) and an optional `ExtractionReport` (Layer 2, present only when `EvalSpec.sections` was declared) and returns the summed `cost_usd: float | None` for the iteration. Per DEC-002 all-or-nothing: if either internal `estimate_cost` returns `None`, the composition returns `None`. When `extraction_report is None`, the L2 contribution is `0.0` (no Layer-2 call happened — not a lookup miss). The signature accepts the resolved grader `provider: str` and reads `model` from each report's `model` field.

**Traces to:** DEC-001 (grader-only scope), DEC-002 (all-or-nothing on partial info).

**TDD — write these tests first:**
- `TestComputeIterationCostUsd.test_grading_report_only_returns_cost` — fake `GradingReport` with `model="claude-sonnet-4-6"`, `input_tokens=1000`, `output_tokens=500`; `extraction_report=None`; provider=`"anthropic"`. Returns the same float as `estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 500)`.
- `TestComputeIterationCostUsd.test_with_extraction_report_sums_costs` — both reports populated; returns the sum of two `estimate_cost` calls.
- `TestComputeIterationCostUsd.test_unknown_grading_model_returns_none` — `GradingReport.model` is unknown; returns `None` regardless of extraction_report state.
- `TestComputeIterationCostUsd.test_unknown_extraction_model_returns_none` — `GradingReport.model` known, `ExtractionReport.model` unknown; returns `None` (all-or-nothing).
- `TestComputeIterationCostUsd.test_unknown_provider_returns_none` — `provider="vertex"` (unknown); returns `None`.
- `TestComputeIterationCostUsd.test_extraction_report_with_zero_tokens` — `extraction_report` present but `input_tokens=output_tokens=0`; L2 contribution is `0.0`, total equals L3 cost.

**Acceptance criteria:**
- `compute_iteration_cost_usd(grading_report: GradingReport, extraction_report: ExtractionReport | None, provider: str) -> float | None` defined in `_pricing.py`.
- `ExtractionReport` is imported from `clauditor.grader`; `GradingReport` from `clauditor.quality_grader`.
- All TDD tests above pass.
- The helper is pure: no I/O, no announcement firing on its own (announcements fire transitively through `estimate_cost` only).

**Done when:** All TDD tests green; the helper handles every combination of (extraction present/absent × known/unknown models × known/unknown provider).

**Files:**
- Modified: `src/clauditor/_providers/_pricing.py`.
- Modified: `tests/test_providers_pricing.py` (new `TestComputeIterationCostUsd` class).

**Depends on:** US-001.

---

### US-005 — Wire `compute_iteration_cost_usd` into `_write_context_sidecar`

**Description.** Replace the hardcoded `cost_usd=None` at `src/clauditor/cli/grade.py:921` with a call to `compute_iteration_cost_usd(primary_report, extraction_report_or_none, provider)`. The `extraction_report` value is in scope at the call site (see Codebase Scout findings — Layer 2 runs conditionally on `EvalSpec.sections`). No new parameters threaded through `_write_workspace_sidecars`. The change is additive at the construction site; no signature changes elsewhere.

**Traces to:** DEC-001, DEC-002, the ticket's headline acceptance criterion ("`IterationContext.cost_usd` is non-null in `context.json` when the (provider, model) pair is known").

**TDD — write these tests first:**
- `TestCmdGradeContextCostUsd.test_known_model_writes_non_null_cost_usd` — full grade flow with a happy-path fake. Mock `call_model` to return a `ModelResult` with known token counts (e.g. `input_tokens=1000`, `output_tokens=500`). Spec uses `grading_model="claude-sonnet-4-6"` and a known provider. Run `cmd_grade`. Read the resulting `context.json`. Assert `cost_usd` is non-null and matches `estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 500)` exactly.
- `TestCmdGradeContextCostUsd.test_unknown_model_writes_null_cost_usd` — same flow with `grading_model="claude-fake-1"`. Resulting `context.json` has `cost_usd: null`. Per DEC-002 all-or-nothing.
- `TestCmdGradeContextCostUsd.test_l2_l3_composition_uses_side_effect_mock` — per `.claude/rules/mock-side-effect-for-distinct-calls.md` (T7), if this test mocks `compute_iteration_cost_usd` (or `estimate_cost`) to verify composition, it MUST use `side_effect=[v_l2, v_l3]` not `return_value=v`. Concrete shape: spec declares `sections` (so L2 runs); mock `estimate_cost` with `side_effect=[0.0015, 0.0045]`; assert resulting `context.json.cost_usd == 0.0060`.

**Acceptance criteria:**
- `src/clauditor/cli/grade.py:_write_context_sidecar` constructs `IterationContext(...)` with `cost_usd=compute_iteration_cost_usd(primary_report, extraction_report, provider)` (the existing line 921 is the change site).
- Imports added: `from clauditor._providers._pricing import compute_iteration_cost_usd`.
- All three integration tests above pass.
- The L2+L3 composition test uses `side_effect=[v_l2, v_l3]` per T7 / `.claude/rules/mock-side-effect-for-distinct-calls.md`.
- Validation: `uv run pytest tests/test_cli.py -v` passes; coverage ≥ 80%.

**Done when:** A grade run with a known (provider, model) writes a non-null float `cost_usd`; an unknown model writes `null`; the integration tests above pass.

**Files:**
- Modified: `src/clauditor/cli/grade.py` (line 921 region + new import).
- Modified: `tests/test_cli.py` (new `TestCmdGradeContextCostUsd` class — placement parallel to existing `TestCmdGrade*` classes).

**Depends on:** US-004.

---

### US-006 — `cli/validate.py` regression: `cost_usd=null` preserved

**Description.** Confirm (and add a regression test for) the property that validate-only iterations continue to write `cost_usd: null`. No grader call ran for `cmd_validate`, so there is no L2 or L3 to price. The change is purely test-side; the existing `cli/validate.py:cmd_validate` already passes `cost_usd=None` explicitly and the change in US-005 does not touch this site.

**Traces to:** DEC-001 (grader-only scope means validate has no cost).

**TDD — write these tests first:**
- `TestCmdValidateContextCostUsd.test_validate_writes_null_cost_usd` — full validate flow against a fake skill that passes assertions. No `call_model` mock needed (validate doesn't call any grader). Read `context.json`. Assert `cost_usd is None`.
- `TestCmdValidateContextCostUsd.test_validate_writes_null_cost_usd_even_for_known_provider_in_spec` — even when `eval.json` has `grading_provider: "anthropic"` and `grading_model: "claude-sonnet-4-6"`, `cmd_validate` does not call the grader, so `cost_usd` is still `null`.

**Acceptance criteria:**
- Both regression tests pass.
- `src/clauditor/cli/validate.py` is unmodified by this story (the `cost_usd=None` placeholder at line ~327 remains intentional).
- Validation: `uv run pytest tests/test_cli.py::TestCmdValidate* -v` passes.

**Done when:** Validate-only iterations are confirmed to write `cost_usd: null` via two regression tests.

**Files:**
- Modified: `tests/test_cli.py` (new `TestCmdValidateContextCostUsd` class).

**Depends on:** US-005 (so the wiring is in place; the regression confirms it doesn't bleed into validate).

---

### US-007 — Quality Gate

**Description.** Run code reviewer 4 times across the full changeset; fix every real bug each pass. Run CodeRabbit if available on the PR. After all fixes, full project validation must pass.

**Traces to:** all DEC-### (defends every decision against drift introduced during implementation).

**Acceptance criteria:**
- Code reviewer pass 1, 2, 3, 4 each run; every flagged real bug is fixed (false positives may be documented).
- CodeRabbit review run on the PR (if available); every actionable finding addressed.
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` ≥ 80%.
- All decisions DEC-001 through DEC-006 audited for compliance in the final state.

**Done when:** Four code-review passes complete with all real findings fixed; CodeRabbit clean; lint + tests + coverage gate green.

**Files:** (all touched by US-001 through US-006)

**Depends on:** US-006 (last implementation story).

---

### US-008 — Patterns & Memory

**Description.** Refresh the rules whose canonical-implementation sections benefit from a brief mention of `_pricing.py`. None require restructure (the pattern doesn't shift); these are non-load-bearing prose adds.

**Traces to:** the Rules drift candidates from Discovery's Convention Checker section.

**Acceptance criteria:**
- `.claude/rules/centralized-sdk-call.md` — under "Implicit-coupling announcements — an emerging family", add a fourth/fifth member entry for the pricing module's two announcements (`_announced_pricing_table_stale` + `_announced_unknown_models`) with their canonical-location and durable-substring sets. (The OpenAI section in #145 explicitly opted out of an announcement family member; #169's pricing announcements are the first family additions since.)
- `.claude/rules/multi-provider-dispatch.md` — add a brief note under "Companion rules" or "When this rule applies" that the price-table lookup follows the same provider-dispatch shape (return `None` on unknown provider, never raise).
- `.claude/rules/pure-compute-vs-io-split.md` — add `_pricing.estimate_cost` + `_pricing.compute_iteration_cost_usd` as the ninth canonical anchor, parallel in shape to `_retry.py`'s pure helpers. Document the four pure functions (`estimate_cost`, `compute_iteration_cost_usd`, `announce_pricing_table_stale_if_old`, `announce_unknown_model`) and their split.
- All rule edits are byte-stable on existing prose (no rewrites of historical validation notes per `.claude/rules/rule-refresh-vs-delete.md`).

**Done when:** Three rules updated with the additions above; lint/tests still pass.

**Files:**
- Modified: `.claude/rules/centralized-sdk-call.md`.
- Modified: `.claude/rules/multi-provider-dispatch.md`.
- Modified: `.claude/rules/pure-compute-vs-io-split.md`.

**Depends on:** US-007 (Quality Gate).

---

## Refinement Log

- **2026-05-09 session 1.** Discovery + Architecture Review + Detailing in one session. Six DECs locked (DEC-001 through DEC-006). Eight stories scoped (US-001 through US-008). Pre-plan research confirmed neither Anthropic nor OpenAI exposes a programmatic rate card; ticket body updated with research notes before super-planning began.

