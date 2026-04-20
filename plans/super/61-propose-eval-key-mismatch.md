# Super Plan: #61 — `propose-eval` emits wrong assertion keys; silent false-positive passes

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/61
- **Branch:** `feature/61-propose-eval-key-mismatch`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/61-propose-eval-key-mismatch`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/65
- **Sessions:** 1
- **Last session:** 2026-04-20

---

## Discovery

### Ticket Summary

**What:** `clauditor propose-eval` generates L1 assertions with keys
(`pattern`, `min`, `max`) that do not match the contract read by the
assertion handlers in `src/clauditor/assertions.py`, which read the
canonical key `value`. Because the handlers fall back to falsy
defaults (`""` for strings, `0` for ints), several assertion types
silently pass vacuously:
- `regex` with `value=""` → `re.search("", output)` matches at
  position 0 → **always passes**.
- `min_count` with `value=""` → `re.findall("", output)` returns many
  empty matches → passes arithmetic.
- `min_length` with `value=0` → `len >= 0` → **always passes**.
- `max_length` with `value=0` → `len <= 0` → **always fails** (the
  only branch that surfaces the bug today).

**Why:** New users running `propose-eval` to bootstrap a spec get
back an `eval.json` that reports high pass rates (reporter saw
21/22 passes with **8 vacuous** on a 22-assertion spec). This lands
in audit / trend history as legitimate passes and the spec can
never fail. The proposer is "worse than no validation" — it looks
like a working safety net.

**Done when:**
1. `propose-eval` prompt emits the canonical schema for every
   assertion `type` (no `min`/`max`/`pattern`/`minimum`/`maximum`
   aliases).
2. `EvalSpec.from_dict` hard-validates per-type required keys and
   rejects unknown/missing keys at load time with a descriptive
   `ValueError`.
3. End-to-end reproduction from the ticket: running `propose-eval`
   then `validate` on the same skill produces no vacuous passes.

**Who benefits:** Every user who runs `clauditor propose-eval` —
currently the primary onboarding path for bootstrapping a new
skill's spec. Also maintainers of hand-authored specs who
previously could silently use the wrong key.

### Codebase Findings

- **Dispatch table** — `src/clauditor/assertions.py:430-457`. 10
  assertion types total; every handler reads `value` (and
  sometimes `minimum` for `min_count`, `format` for `has_format`).
  Full list: `contains`, `not_contains`, `regex`, `min_count`,
  `min_length`, `max_length`, `has_urls`, `has_entries`,
  `urls_reachable`, `has_format`. Silent-pass mechanism: every
  handler uses `.get("value", <falsy>)` so a missing key yields a
  no-op assertion that passes.
- **Prompt drift source** — `src/clauditor/propose_eval.py:382-391`.
  Inside `build_propose_eval_prompt`, a single bullet line tells
  the LLM: `"...type-specific fields (e.g. 'value', 'pattern',
  'format', 'min', 'max')..."` — enumerating both the real key
  (`value`, `format`) and legacy-looking aliases (`pattern`, `min`,
  `max`) plus forgetting `minimum` entirely. The LLM picks
  `pattern`/`min`/`max` because they read better.
- **Loader validation seam** — `src/clauditor/schemas.py:259-285`.
  `_require_id(entry, ctx)` is the per-assertion validation
  helper called in a loop at line 283. No per-type required-key
  check exists today. The natural slot for a new
  `_require_assertion_keys(entry, ctx)` is immediately after
  `_require_id` in the same loop.
- **LLM validator already routes through `from_dict`** —
  `src/clauditor/propose_eval.py::validate_proposed_spec` calls
  `EvalSpec.from_dict(data, spec_dir=...)` and collects
  `ValueError` messages into `validation_errors`. So any loader-
  side hard-fail automatically propagates through the
  orchestrator → CLI → exit 2 path per
  `.claude/rules/llm-cli-exit-code-taxonomy.md`.
- **Tests touching the prompt** — `tests/test_propose_eval.py::
  TestBuildProposeEvalPrompt` (lines 359-440). It pins framing,
  untrusted tag placement, and the stable-id phrase. It does NOT
  currently pin the `"type-specific fields (e.g. …)"` line, so
  rewriting that line does not require test deletion — just a new
  positive assertion on the replacement shape.
- **Tests touching assertion dispatch** —
  `tests/test_assertions.py::TestRunAssertionsEdgeCases` already
  passes dicts through `run_assertions()`. The canonical shape for
  new "rejects missing `value`" tests is a per-type
  `ValueError`-expecting test added to `test_schemas.py`
  (validation-error tests live there, not in `test_assertions.py`).

### Applicable `.claude/rules/`

- **`pre-llm-contract-hard-validate.md`** — THIS IS THE LOAD-
  BEARING RULE. The fix is literally the canonical shape of this
  rule: write the invariant into the prompt (step 1 — enumerate
  per-type required keys authoritatively) AND hard-validate in
  the parser (step 2 — `_require_assertion_keys` in `from_dict`).
- **`in-memory-dict-loader-path.md`** — already compliant:
  `validate_proposed_spec` uses `EvalSpec.from_dict`, not a
  tempfile. The new loader-side validation lands inside
  `from_dict`, so the LLM path and the on-disk `from_file` path
  both gain the check automatically.
- **`llm-cli-exit-code-taxonomy.md`** — new validation errors from
  `from_dict` land in `report.validation_errors` and route to
  exit 2 (post-call invariant failure) at the CLI layer with no
  new plumbing.
- **`eval-spec-stable-ids.md`** — documents the existing
  `_require_id` pattern. The new helper sits next to it and uses
  the same "hard-fail at load time with a clear path like
  `assertions[2]: …`" shape.
- **`positional-id-zip-validation.md`** — NOT applicable. The
  proposed spec is keyed by explicit `id`, not positional index.
- **`bundled-skill-docs-sync.md`** — NOT applicable. The SKILL.md
  workflow steps do not change; the fix is entirely inside
  `propose_eval.py` + `schemas.py`. (The SKILL.md mentions
  `propose-eval` as a fallback for Step 3 but does not describe
  the prompt schema.)
- **`centralized-sdk-call.md`** — already compliant; no new SDK
  usage, just a prompt edit.
- **`json-schema-version.md`** — NOT applicable; no new sidecars.
- All other rules — N/A (no new I/O seams, no new subprocess, no
  time-indirection need, no README churn, no path-bearing fields).

### Project validation commands

- Lint: `uv run ruff check src/ tests/`
- Test + coverage: `uv run pytest --cov=clauditor --cov-report=term-missing`
- Coverage gate: 80% (enforced).

### Ambiguities → resolved via scoping questions

See Phase 1 scoping questions below. Will become DEC-001 through
DEC-005 once answered.

---

## Scoping Questions (Phase 1)

**Q1 (DEC-001) — Alias acceptance policy in the loader.**

The ticket presents three fix options. You stated a preference for
A + C. A few sub-choices remain for how strict C should be:

- **A)** Hard-reject unknown keys. Any assertion dict carrying
  `pattern`, `min`, `max`, `minimum`, `maximum`, or any other
  non-canonical key raises `ValueError`. Cleanest; surfaces drift
  immediately. **Breaks hand-authored specs in the wild that use
  the intuitive key names.**
- **B)** Hard-reject unknown keys, but emit a one-shot migration
  hint in the error message (e.g. `"assertions[2]: unknown key
  'pattern' — did you mean 'value'? (see docs/eval-spec-reference.md)"`).
  Same strictness as A; nicer onboarding.
- **C)** Accept legacy aliases (`pattern`→`value`, `min`→`value`
  for length types, `max`→`value`, `minimum`→`minimum` as-is) with
  a stderr deprecation warning at load time; a future release
  removes the alias layer. This is the ticket's Option B, which
  you tagged as "slightly uglier".
- **D)** Something else — please describe.

*Recommendation:* **B** — strict rejection with a helpful error
message. It's aligned with your stated A+C preference and
`pre-llm-contract-hard-validate.md` ("never silently accept
output that violates it"), and the helpful message gives hand-
authors a fast fix. No legacy specs exist in the repo (grep
confirms all `eval.json` use `value`).

**Q2 (DEC-002) — Scope of per-type key validation.**

The hard-validator can cover just the four keys named in the
ticket (`regex`, `min_count`, `min_length`, `max_length`) or
exhaustively every assertion type (all 10).

- **A)** Exhaustive — validate every `type` in the dispatch table
  (all 10 types). Tighter net; catches any future drift (e.g. the
  ticket's Related list flags `has_urls`, `has_entries`,
  `urls_reachable`, `has_format` as also vulnerable).
- **B)** Only the 4 types named in the ticket. Smallest diff.
- **C)** Exhaustive for required-keys (all 10) but permissive on
  unknown keys — only reject missing keys, allow extras with a
  warning. Middle ground.

*Recommendation:* **A**. The dispatch table is 10 lines; the
validation table is 10 lines. Matching them one-to-one is the
only way to prevent this class of bug from recurring.

**Q3 (DEC-003) — Prompt schema teaching shape.**

The current prompt's drift-source is a single bullet line with an
ellipsis and mixed-valid-invalid keys. We can replace it with:

- **A)** **Per-type enumeration table**. A literal block in the
  prompt listing each `type` and its exact required keys (and
  allowed-but-optional keys). LLM has no room to improvise.
- **B)** **Full JSON example per type**. Show a worked example
  assertion for each of the 10 types. More tokens, but the
  strongest form of teaching by demonstration.
- **C)** **One compact example per "shape family"** (string-value,
  int-value, int-value + pattern, etc.). Mid-density.
- **D)** **Schema reference link only** — point at
  `docs/eval-spec-reference.md` and trust the model to fetch. Low
  tokens, weak invariant.

*Recommendation:* **A** (per-type enumeration table) for the
primary block, possibly paired with one worked JSON example that
shows the dict shape. Keeps the prompt concise and the teaching
authoritative; the hard-validator is the real safety net.

**Q4 (DEC-004) — Handling assertions the LLM emits with no
`value` at all.**

Today, an LLM that forgets the `value` field entirely produces an
assertion that silently passes (the ticket's root cause). After
the fix, `from_dict` rejects it and the whole `propose-eval` run
fails with exit 2. An alternative is to let the orchestrator catch
this and retry with a repair prompt.

- **A)** Hard-fail the run on first validation error; let the
  user re-invoke `propose-eval` (matches current
  `llm-cli-exit-code-taxonomy` exit-2 behavior).
- **B)** One-shot repair retry: on validation failure, feed the
  errors back to the LLM with "please fix these fields and re-
  emit". Adds complexity + cost.
- **C)** Just surface the error clearly and exit; no retry.

*Recommendation:* **A/C** — hard-fail, no retry. Repair loops are
out of scope for a bug fix; file a follow-up issue if the
re-invocation UX is rough in practice.

**Q5 (DEC-005) — Test coverage shape.**

Where should the regression tests live?

- **A)** Add per-type "rejects missing `value`" tests in
  `tests/test_schemas.py` (one test per type), plus a prompt-text
  test in `tests/test_propose_eval.py` asserting the new
  enumeration block is present. Tight, targeted.
- **B)** Add one big table-driven test in `tests/test_schemas.py`
  that exercises all 10 types via parametrize.
- **C)** All of the above + a property-based test that generates
  random keys and asserts rejection. Overkill for a bug fix.

*Recommendation:* **B** (parametrized) for the loader, plus one
prompt-shape test for the propose-eval prompt. Keeps the test
file from ballooning with 10 near-duplicate classes.

---

## Architecture Review

Scope is narrow (one prompt edit + one loader helper + a bounded
repair-retry loop). Review areas that are trivially `pass` for this
shape are compressed into one-line findings; the two concerns drive
Phase 3 decisions.

| Area | Rating | Finding |
|---|---|---|
| Security | pass | No new inputs. Repair-retry feeds previous LLM response + our own error strings back to the LLM; errors are formatted by our code (not free-form) so no new injection surface. Response is still wrapped in `<response_1>`-style fences per the existing prompt (see `llm-judge-prompt-injection.md`); the repair prompt inherits the same framing. |
| Performance | pass | Repair-retry at most doubles worst-case Anthropic spend on a failing run (bounded by 1 extra call). No loader hot-path change — the new `_require_assertion_keys` runs once per spec load, O(n) over assertions. |
| Data Model | pass | No schema shape change. The contract (`value`/`minimum`/`format`) already exists in handlers; the loader is newly *enforcing* it. Grep confirms no checked-in specs or docs use the legacy aliases (`pattern`/`min`/`max`). Strict rejection is safe for the repo. |
| API Design | pass | The LLM-facing "prompt schema" contract tightens. The `propose-eval` CLI flags and exit-code taxonomy are unchanged — new validation errors route to exit 2 via the existing `validation_errors` list. |
| **Observability** | concern | Repair-retry needs a visible stderr signal ("validation failed, retrying once with repair prompt") so the user can tell why token usage roughly doubled. The two underlying `call_anthropic` invocations also need separate duration/token accounting in the report, not merged. |
| **Testing Strategy** | concern | Loader adds 10 type-specific required-key rules. Parametrized table-driven test per DEC-005 (Q5=B) is the right shape, but the parametrize table needs to be DRIVEN BY the same source of truth as the validator itself — otherwise the table and validator drift. Also need: (a) prompt-shape assertion on the new enumeration block, (b) end-to-end repair-retry test (LLM returns bad spec → orchestrator repairs → second call succeeds), (c) repair-retry test where BOTH calls fail → exit 2 path. |
| **Repair-Retry Failure Semantics** | concern | New concern introduced by Q4=B+C. What counts as "the same error" on the repair-retry? Do we pass the full validation error list verbatim, a summary, or the original response text + errors? Is the repair prompt a whole new call (new system prompt, new user prompt) or does it reuse the original? Bad choices here cause the LLM to re-emit the same bad spec, burning tokens with zero recovery rate. |

No blockers. The three concerns resolve as DEC-006, DEC-007, DEC-008
in the Refinement Log.

## Refinement Log

### Decisions

- **DEC-001 — Alias acceptance policy: strict rejection with
  helpful error message (Q1=B).** Loader-side
  `_require_assertion_keys` emits `ValueError` of the form
  `"assertions[{i}] ({id!r}): unknown key {k!r} for type
  {type!r} — did you mean {suggestion!r}? See
  docs/eval-spec-reference.md"`. Suggestions cover the three
  drift keys (`pattern`→`value`, `min`→`value`,
  `max`→`value`). Rationale: aligns with
  `pre-llm-contract-hard-validate.md` ("never silently accept
  output that violates it"); grep of the repo confirms no
  checked-in spec uses aliases, so there is nothing to migrate.

- **DEC-002 — Validation scope: exhaustive (Q2=A).** All 10
  assertion types in `assertions.py`'s dispatch table
  (`contains`, `not_contains`, `regex`, `min_count`,
  `min_length`, `max_length`, `has_urls`, `has_entries`,
  `urls_reachable`, `has_format`) get per-type required-key and
  unknown-key validation. Rationale: the dispatch table is 10
  entries; not covering all 10 leaves the same class of bug
  open for the other 6 types.

- **DEC-003 — Prompt schema teaching: per-type enumeration
  table (Q3=A).** Replace the single `"type-specific fields
  (e.g. …)"` bullet at `propose_eval.py:388-391` with a literal
  table block in the prompt — one row per `type` naming its
  required and optional keys. Keeps teaching authoritative
  without bloating to a full JSON-per-type example; the hard-
  validator is the real safety net.

- **DEC-004 — Repair-retry policy: one-shot repair retry, then
  hard-fail exit 2 (Q4=B+C).** On validation failure after the
  first `call_anthropic`, the orchestrator makes exactly ONE
  additional `call_anthropic` with a repair prompt (see
  DEC-007). If the repair response also fails validation, the
  report's `validation_errors` is populated and the CLI exits
  2. Bounded retry count = 1; never retry a second time.

- **DEC-005 — Test coverage shape: parametrized loader test +
  prompt-shape test + both repair-retry branches (Q5=B).**
  Single parametrized test in `tests/test_schemas.py` driven
  by the `ASSERTION_TYPE_REQUIRED_KEYS` constant (DEC-008) that
  iterates every type × each required key × missing/unknown
  scenario. Prompt test in `tests/test_propose_eval.py` pins
  the enumeration block's structural shape (contains the
  literal row `"min_count" → required: value, minimum`).
  Repair-retry tests in `tests/test_propose_eval.py` cover (a)
  first call fails → repair succeeds → exit 0, (b) first call
  fails → repair also fails → exit 2.

- **DEC-006 — Observability of repair-retry: stderr signal +
  per-attempt accounting + `repair_attempted` flag (Q6=C).**
  One stderr line when the repair retry fires
  (`"propose-eval: spec validation failed ({N} errors),
  retrying once with repair prompt..."`). The
  `ProposeEvalReport` grows an `attempts: list[AttemptMetrics]`
  field where each element captures `{input_tokens,
  output_tokens, duration_seconds}` for that
  `call_anthropic`; it also grows a `repair_attempted: bool`.
  Rationale: the flag is one extra bool and unblocks future
  audit/trend infra that wants to surface repair rate as a
  skill-quality signal.

- **DEC-007 — Repair prompt shape: original response + error
  list, fresh `call_anthropic` (Q7=A+D).** The repair prompt
  is a brand-new `call_anthropic` invocation, NOT a
  continuation. Its user prompt is structured as:
  1. The original propose-eval system+user prompt unchanged
     (so the LLM has full context).
  2. A `<previous_response>` fenced block containing the exact
     text of the first LLM response (so the LLM sees what it
     emitted).
  3. A `<validation_errors>` fenced block containing the
     verbatim `ValueError` message list (one line per error).
  4. A closing instruction: "Re-emit the full corrected spec
     as JSON. Fix every key listed in `<validation_errors>`."
  All three blocks carry the XML-like fencing framing per
  `.claude/rules/llm-judge-prompt-injection.md` — the
  `<previous_response>` and `<validation_errors>` blocks are
  both flagged as untrusted data in the framing sentence. Pure
  prompt-builder `build_repair_propose_eval_prompt(...)` lives
  in `propose_eval.py` alongside the existing
  `build_propose_eval_prompt`, tested without any SDK mocks.

- **DEC-008 — Single source of truth for required keys
  (Q8=A).** Export a module-level constant in `schemas.py`:
  ```python
  ASSERTION_TYPE_REQUIRED_KEYS: dict[str, AssertionKeySpec] = {
      "contains":       AssertionKeySpec(required={"value"}),
      "not_contains":   AssertionKeySpec(required={"value"}),
      "regex":          AssertionKeySpec(required={"value"}),
      "min_count":      AssertionKeySpec(required={"value", "minimum"}),
      "min_length":     AssertionKeySpec(required={"value"}),
      "max_length":     AssertionKeySpec(required={"value"}),
      "has_urls":       AssertionKeySpec(required={"value"}),
      "has_entries":    AssertionKeySpec(required={"value"}),
      "urls_reachable": AssertionKeySpec(required={"value"}),
      "has_format":     AssertionKeySpec(required={"format", "value"}),
  }
  ```
  where `AssertionKeySpec` is a frozen dataclass with
  `required: frozenset[str]` (and room for future `optional`,
  `value_type`, `description` fields — see follow-up DEC-009).
  Both the loader (`_require_assertion_keys`) and the
  parametrized test import this constant. The prompt builder
  in `propose_eval.py` also imports it to render the per-type
  enumeration table — eliminating the three-way drift between
  handlers, validator, and prompt.

- **DEC-009 — Semantic-key schema redesign deferred to a
  follow-up issue (Q9 meta-question).** Ticket #61 keeps the
  existing `value`/`minimum`/`format` key contract. The
  broader design smell — `value` is an overloaded slot meaning
  different things per type, which is what invited the LLM to
  invent `pattern`/`min`/`max` in the first place — is
  acknowledged and filed as a separate follow-up issue after
  devolve. Out of scope for #61 because: (a) it is a breaking
  schema change requiring a migration path for hand-authored
  specs, (b) it needs docs + rubric + README updates beyond
  this bug fix's scope, (c) the hard-validator from DEC-001
  gives us the safety net to ship the followup later without
  risk of silent regressions in between.

### Session Notes

**Session 1 (2026-04-20)** — Discovery + Architecture + Refinement
in a single session. All scoping and concern questions resolved.
Three applicable rules: `pre-llm-contract-hard-validate.md`
(load-bearing — the fix IS this rule's canonical shape),
`in-memory-dict-loader-path.md` (already compliant —
`validate_proposed_spec` goes through `from_dict`),
`llm-cli-exit-code-taxonomy.md` (new errors route to existing
exit-2 path with zero CLI plumbing changes). No blockers; three
concerns resolved as DEC-006/007/008. Scope includes a bounded
repair-retry loop (Q4=B+C) which adds ~40 lines to the
orchestrator and two new test cases.

## Detailed Breakdown (Stories)

Ordering: data (constant) → loader validator → prompt update →
orchestrator repair-retry → follow-up issue filing → Quality Gate →
Patterns & Memory. US-002 and US-003 can run in parallel after
US-001 lands.

Every story's acceptance criteria include the project validation
command from CLAUDE.md:
`uv run ruff check src/ tests/ && uv run pytest --cov=clauditor
--cov-report=term-missing` must pass with the 80% coverage gate.

### US-001 — Export `ASSERTION_TYPE_REQUIRED_KEYS` + `AssertionKeySpec`

**Description:** Add the single-source-of-truth constant that
every downstream consumer (loader validator, prompt builder,
parametrized test) will import. No consumers yet — pure data +
dataclass.

**Traces to:** DEC-008.

**Acceptance Criteria:**
- `src/clauditor/schemas.py` exports `AssertionKeySpec` — a frozen
  `@dataclass(frozen=True)` with one field: `required:
  frozenset[str]`.
- `src/clauditor/schemas.py` exports
  `ASSERTION_TYPE_REQUIRED_KEYS: dict[str, AssertionKeySpec]` —
  10 entries, one per assertion type in `assertions.py`'s
  dispatch table, with exactly these required-key sets:
  - `contains`, `not_contains`, `regex` → `{"value"}`
  - `min_count` → `{"value", "minimum"}`
  - `min_length`, `max_length` → `{"value"}`
  - `has_urls`, `has_entries`, `urls_reachable` → `{"value"}`
  - `has_format` → `{"format", "value"}`
- `tests/test_schemas.py` adds `TestAssertionKeySpec` with one
  test asserting each of the 10 types is present and each
  required-key set matches the expected value.
- `tests/test_schemas.py` adds a cross-check test that asserts
  every key in `ASSERTION_TYPE_REQUIRED_KEYS[type].required`
  appears in the corresponding handler lambda's
  `.get(<key>, ...)` call in `assertions.py::_HANDLERS` — prevents
  future handler-edit drift. (Implementation: parse handler
  lambdas via `inspect.getsource` + regex, or import the
  dispatch and introspect.)
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing`
  passes with ≥80% coverage.

**Done when:** Constant importable as
`from clauditor.schemas import ASSERTION_TYPE_REQUIRED_KEYS,
AssertionKeySpec`. No existing tests regress.

**Files:**
- `src/clauditor/schemas.py` — add `AssertionKeySpec` dataclass
  and `ASSERTION_TYPE_REQUIRED_KEYS` constant near the top-level
  exports (above `EvalSpec`).
- `tests/test_schemas.py` — add `TestAssertionKeySpec` class.

**Depends on:** none.

**TDD:**
- `test_constant_contains_all_ten_assertion_types` — assert
  `set(ASSERTION_TYPE_REQUIRED_KEYS.keys()) == {10 types}`.
- `test_contains_required_keys` — parametrized over (type,
  expected_required_set) for all 10 types.
- `test_handler_signature_agrees_with_constant` — introspect
  `assertions._HANDLERS` to verify every required key is read
  by that type's handler lambda.

---

### US-002 — Add `_require_assertion_keys` loader validator

**Description:** Wire the DEC-008 constant into
`EvalSpec.from_dict` so per-assertion dicts are hard-validated
at load time: missing required keys and unknown keys both raise
`ValueError` with a helpful message. Both `from_file` (disk
path) and `from_dict` (LLM path via `propose_eval.py`) inherit
the check.

**Traces to:** DEC-001, DEC-002, DEC-008.

**Acceptance Criteria:**
- `src/clauditor/schemas.py` adds `_require_assertion_keys(entry:
  dict, ctx: str) -> None`. Signature mirrors `_require_id`.
- The helper:
  1. Reads `entry.get("type")`. If missing or not in
     `ASSERTION_TYPE_REQUIRED_KEYS`, raises `ValueError(f"{ctx}:
     unknown or missing 'type' (got {type_val!r})")`.
  2. For each key in `ASSERTION_TYPE_REQUIRED_KEYS[type].required`,
     verifies the entry has that key with a non-None value.
     Missing → `ValueError(f"{ctx} (type={type!r}): missing
     required key {key!r}")`.
  3. For every key in `entry` NOT in the union of
     `{"id", "type", "name"}` ∪ `required_keys`, raises
     `ValueError(f"{ctx} (type={type!r}): unknown key {key!r}{hint}")`
     where `hint` is `" — did you mean 'value'?"` if `key` is one
     of `{"pattern", "min", "max"}`, `" — did you mean 'minimum'?"`
     if `key == "threshold"`, else empty.
- `EvalSpec.from_dict` calls `_require_assertion_keys(a, f"assertions[{i}]")`
  immediately after `_require_id(a, ...)` in the assertion loop.
- `tests/test_schemas.py` adds `TestRequireAssertionKeys` —
  parametrized over (type, bad_entry, expected_error_substring)
  for every type × (missing required key | unknown key | drift
  alias). Test count ≈ 30.
- Existing `TestEvalSpecFromFile` and `TestEvalSpecFromDict`
  tests do NOT regress (confirm no checked-in fixture uses
  alias keys).
- Project validation passes.

**Done when:** A spec dict with `{type: "regex", id: "x",
pattern: "..."}` raises `ValueError` at `EvalSpec.from_dict`
time naming `assertions[0] (type='regex'): unknown key 'pattern'
— did you mean 'value'?`.

**Files:**
- `src/clauditor/schemas.py` — add `_require_assertion_keys`
  helper, call it in `EvalSpec.from_dict`.
- `tests/test_schemas.py` — add `TestRequireAssertionKeys` class.

**Depends on:** US-001.

**TDD:** Write the parametrized test table first, listing every
(type, bad_entry, expected_substring) triple. Implement the
helper to make the table pass.

**Rules applied:** `pre-llm-contract-hard-validate.md` (step 2 —
hard-validate in parser), `eval-spec-stable-ids.md` (same shape
as `_require_id`).

---

### US-003 — Update `propose-eval` prompt with per-type enumeration table

**Description:** Replace the drift-source single-line ellipsis
bullet at `propose_eval.py:388-391` with a literal table block
enumerating each assertion type's required (and optional) keys.
Import `ASSERTION_TYPE_REQUIRED_KEYS` from `schemas.py` and
render the table programmatically so the prompt stays in sync
with the validator.

**Traces to:** DEC-003, DEC-008.

**Acceptance Criteria:**
- `src/clauditor/propose_eval.py::build_propose_eval_prompt`
  renders a per-type table. Exact shape TBD during
  implementation, but must contain, for each of the 10 types, a
  line of the form `- <type> → required: <keys>` (e.g.
  `- min_count → required: value, minimum`).
- The table is rendered from `ASSERTION_TYPE_REQUIRED_KEYS`, NOT
  hardcoded — adding an 11th type in `assertions.py` +
  updating the constant makes it appear in the prompt.
- The old line (`"...type-specific fields (e.g. 'value',
  'pattern', 'format', 'min', 'max')..."`) is fully removed.
- `tests/test_propose_eval.py::TestBuildProposeEvalPrompt` adds:
  - `test_prompt_contains_per_type_table` — asserts the literal
    row `"min_count → required: value, minimum"` is present.
  - `test_prompt_has_no_alias_keys` — asserts the prompt does
    NOT contain the strings `"'pattern'"`, `"'min'"`, `"'max'"`
    anywhere (they would be the drift source).
  - `test_prompt_table_is_rendered_from_constant` — mutate
    `ASSERTION_TYPE_REQUIRED_KEYS` via monkeypatch, build
    prompt, verify the mutation is reflected. (Protects against
    a future "helpful" hardcoding.)
- Project validation passes.

**Done when:** Running `clauditor propose-eval <skill> --dry-run`
prints a prompt whose assertion-schema block enumerates each
type's required keys literally, with no `pattern`/`min`/`max`
aliases.

**Files:**
- `src/clauditor/propose_eval.py` — replace lines 388-391 with
  the table render.
- `tests/test_propose_eval.py` — add the three tests above.

**Depends on:** US-001.

**Rules applied:** `pre-llm-contract-hard-validate.md` (step 1 —
invariant asserted in prompt), `centralized-sdk-call.md`
(unchanged — no new SDK usage), `llm-judge-prompt-injection.md`
(the new table is author-controlled; no framing change needed).

---

### US-004 — One-shot repair-retry loop + repair prompt builder

**Description:** When the first `call_anthropic` returns a spec
that fails validation, the orchestrator makes exactly one
additional call with a repair prompt (fresh call, not a
continuation; see DEC-007). If the repair also fails, the
report's `validation_errors` is populated and the CLI exits 2.
Both attempts are tracked in a new `attempts:
list[AttemptMetrics]` field on `ProposeEvalReport`, plus a
`repair_attempted: bool` flag (DEC-006).

**Traces to:** DEC-004, DEC-006, DEC-007.

**Acceptance Criteria:**
- `src/clauditor/propose_eval.py` adds pure helper
  `build_repair_propose_eval_prompt(original_prompt: str,
  previous_response: str, validation_errors: list[str]) -> str`.
  The returned prompt contains:
  - The original propose-eval prompt body verbatim.
  - A `<previous_response>` fenced block with
    `{previous_response}`.
  - A `<validation_errors>` fenced block with a newline-joined
    error list.
  - A closing instruction: "Re-emit the full corrected spec as
    JSON. Fix every key listed in `<validation_errors>`."
  - A framing sentence BEFORE the first untrusted tag flagging
    `<previous_response>` and `<validation_errors>` as untrusted
    data (per `.claude/rules/llm-judge-prompt-injection.md`).
- `src/clauditor/propose_eval.py` adds
  `AttemptMetrics` dataclass with `{input_tokens: int,
  output_tokens: int, duration_seconds: float}`.
- `ProposeEvalReport` grows two fields:
  - `attempts: list[AttemptMetrics]` (1 element on success, up
    to 2 if repair was attempted).
  - `repair_attempted: bool`.
  - Existing aggregate `input_tokens`/`output_tokens`/
    `duration_seconds` fields become the sum across attempts
    (backward-compatible with existing consumers).
- `propose_eval()` orchestrator:
  1. First `call_anthropic` → parse → validate.
  2. If `validation_errors` is non-empty, print to stderr:
     `"propose-eval: spec validation failed ({N} errors),
     retrying once with repair prompt..."`.
  3. Build repair prompt → second `call_anthropic` → parse →
     validate.
  4. If second validation also fails, report.validation_errors
     = second attempt's errors (the first attempt's errors are
     logged but not surfaced — the repair is authoritative).
  5. `report.repair_attempted = True` whenever step 2 fired.
- CLI layer at `cli/propose_eval.py` is unchanged — existing
  `validation_errors → exit 2` routing handles both attempts.
- `tests/test_propose_eval.py` adds:
  - `TestBuildRepairProposeEvalPrompt` — pure builder tests
    (framing sentence placement, fence tags, prompt-injection
    hardening check, no SDK mocks).
  - `TestProposeEvalRepairRetry` — four cases:
    a) First call returns bad spec → repair returns good spec →
       exit 0, `report.repair_attempted == True`,
       `len(report.attempts) == 2`.
    b) First call returns bad spec → repair ALSO returns bad
       spec → exit 2, `validation_errors` populated from second
       attempt, `repair_attempted == True`.
    c) First call returns good spec → no repair call →
       `repair_attempted == False`, `len(report.attempts) == 1`.
    d) First call raises `AnthropicHelperError` → no repair
       attempted (API errors ≠ validation errors), exit 3.
- Project validation passes.

**Done when:** Running a scripted-mock `propose-eval` with a
first-response-bad/repair-response-good fixture writes a
canonical spec to disk, exits 0, and stderr contains the retry
line.

**Files:**
- `src/clauditor/propose_eval.py` — add `AttemptMetrics`,
  `build_repair_propose_eval_prompt`, `ProposeEvalReport`
  fields, retry loop.
- `tests/test_propose_eval.py` — add two new test classes.

**Depends on:** US-002 (validator must exist to trigger
retry), US-003 (initial prompt must be shaped correctly).

**TDD:**
1. Write `TestBuildRepairProposeEvalPrompt` first (pure
   helper); implement the builder.
2. Write `TestProposeEvalRepairRetry` cases (a) and (b) with
   SDK mocked via existing `AsyncMock` patterns from
   `tests/test_propose_eval.py`; implement the orchestrator
   retry loop.
3. Add case (c) to lock in the "no retry on success" invariant.
4. Add case (d) to lock in the API-error-is-not-validation-error
   boundary.

**Rules applied:**
- `pre-llm-contract-hard-validate.md` — the repair-retry is the
  full shape of "prompt-side assert + hard-validate; on failure
  try again, then hard-fail".
- `llm-judge-prompt-injection.md` — repair prompt wraps
  untrusted LLM-emitted content (`<previous_response>`) in a
  framed fence.
- `llm-cli-exit-code-taxonomy.md` — both successful repair
  (exit 0) and exhausted repair (exit 2) land in the taxonomy's
  existing buckets via the existing
  `validation_errors`/`api_error` split.
- `centralized-sdk-call.md` — both attempts go through
  `call_anthropic`; no direct SDK usage.
- `mock-side-effect-for-distinct-calls.md` — retry tests MUST
  use `side_effect=[first_response, repair_response]` not
  `return_value=...`.

---

### US-005 — File follow-up issue for semantic-key schema redesign

**Description:** Per DEC-009, the underlying schema drift (the
`value` slot meaning different things per type) is out of scope
for #61 but deserves a tracked follow-up. File a GitHub issue
during devolve so the work is queued without blocking #61.

**Traces to:** DEC-009.

**Acceptance Criteria:**
- A new GitHub issue is filed on wjduenow/clauditor with:
  - **Title:** `"Redesign assertion schema with per-type
    semantic keys (supersedes value-slot overload)"`.
  - **Body** covering: (a) the design smell — `value` is an
    overloaded slot with different semantics per type; (b) the
    cleaner target — per-type semantic keys
    (`{type: "regex", pattern: "..."}`,
    `{type: "min_count", pattern: "...", count: N}`,
    `{type: "min_length", length: N}`, etc.); (c) impact —
    breaking schema change, needs migration path + docs +
    rubric updates; (d) why deferred — #61's hard-validator
    gives the safety net to ship this later without silent
    regressions; (e) link back to #61 and `plans/super/61-*.md`
    DEC-009.
- The issue number is recorded in this plan doc (Meta section
  "Follow-ups filed").
- No code change — this story is pure issue filing.

**Done when:** `gh issue view <NEW-ISSUE-NUMBER>` shows the
filed issue.

**Files:** None (plan doc gets the followup number recorded at
devolve time).

**Depends on:** none (can run in parallel with any story).

---

### US-006 — Quality Gate

**Description:** Run code reviewer 4× across the full changeset,
address every real bug, run CodeRabbit if available, re-run
project validation. Per CLAUDE.md and the standard super-plan
template.

**Traces to:** project-wide quality standards.

**Acceptance Criteria:**
- Code reviewer agent run 4 times across the full diff
  (US-001 → US-004). Each pass's findings triaged and fixed
  (or documented as false-positive) before the next pass.
- CodeRabbit review run if the PR is open (note: this happens
  post-PR; the Quality Gate accommodates both pre- and
  post-PR review cycles).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing`
  passes with ≥80% coverage.
- No failing tests, no new lint findings, no open reviewer
  objections.

**Done when:** All four reviewer passes clean (or
documented-as-false-positive), CodeRabbit satisfied, project
validation green.

**Files:** Whatever reviewer passes surface.

**Depends on:** US-001, US-002, US-003, US-004, US-005.

---

### US-007 — Patterns & Memory

**Description:** After Quality Gate lands, capture any new
patterns worth codifying. Candidates (to be evaluated during
the story, not pre-committed):
- `ASSERTION_TYPE_REQUIRED_KEYS` as a "single source of truth
  across handler + validator + prompt" pattern. If this shape
  appears in future work (similar 3-way drift elsewhere), it
  deserves a rule — likely a new file under `.claude/rules/`.
- The bounded-repair-retry pattern for LLM invariant
  violations. If `quality_grader.py` or other SDK consumers
  would benefit from the same loop, codify in
  `.claude/rules/`.
- Docs update — `docs/eval-spec-reference.md` may want an
  explicit list of "valid keys per assertion type" section if
  it currently lacks one.

**Traces to:** standard closeout pattern.

**Acceptance Criteria:**
- Every pattern worth keeping has been either (a) codified in
  `.claude/rules/<name>.md`, (b) documented in `docs/...`, or
  (c) explicitly evaluated and rejected with a one-line note
  in this plan's Session Notes.
- No regression in existing rules — new rules are additive.
- If a docs update lands, the README teaser (if any) is
  adjusted per `.claude/rules/readme-promotion-recipe.md`.

**Done when:** Either new rule/docs files committed, or Session
Notes contain "evaluated X, Y, Z — chose to defer because …".

**Files:** `.claude/rules/*.md`, `docs/*.md` (TBD),
`plans/super/61-propose-eval-key-mismatch.md` (Session Notes).

**Depends on:** US-006.

## Beads Manifest

- **Worktree:** `/home/wesd/dev/worktrees/clauditor/61-propose-eval-key-mismatch`
- **Branch:** `feature/61-propose-eval-key-mismatch`
- **PR:** https://github.com/wjduenow/clauditor/pull/65

### Task graph

| Bead ID | Story | Priority | Depends on |
|---|---|---|---|
| `clauditor-lof` | Epic — #61 propose-eval key-mismatch bug fix | P2 | — |
| `clauditor-hyl` | US-001 — Export `ASSERTION_TYPE_REQUIRED_KEYS` + `AssertionKeySpec` | P2 | none (ready) |
| `clauditor-o7q` | US-002 — Add `_require_assertion_keys` loader validator | P2 | US-001 |
| `clauditor-5zl` | US-003 — Update propose-eval prompt with per-type table | P2 | US-001 |
| `clauditor-0rl` | US-004 — One-shot repair-retry loop + repair prompt builder | P2 | US-002, US-003 |
| `clauditor-vnn` | US-005 — File follow-up issue for semantic-key redesign | P3 | none (ready) |
| `clauditor-6iq` | US-006 — Quality Gate (reviewer ×4 + CodeRabbit + validation) | P2 | US-001..US-005 |
| `clauditor-5fj` | US-007 — Patterns & Memory | P3 | US-006 |

### Follow-ups filed

- *(to be populated by US-005 — GitHub issue number for semantic-key schema redesign)*

### Ready to work

- `clauditor-hyl` (US-001) — **entry point; US-002 and US-003 unblock once this lands.**
- `clauditor-vnn` (US-005) — parallel-free, can be done any time.
