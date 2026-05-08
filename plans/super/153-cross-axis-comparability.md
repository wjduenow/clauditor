# Super Plan: #153 — Comparability: audit/trend/compare refuse cross-{harness,provider} averaging

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/153
- **Branch:** `feature/153-cross-axis-comparability`
- **Worktree:** `/Users/wesduenow/Projects/worktrees/clauditor/153-cross-axis-comparability`
- **Phase:** `devolved`
- **Sessions:** 2
- **Last session:** 2026-05-07
- **Total decisions:** 11 (DEC-001 through DEC-011)
- **PR URL:** https://github.com/wjduenow/clauditor/pull/168 (draft, approved)
- **Beads epic:** `clauditor-h2l`
- **Beads tasks:** `clauditor-h2l.1` (US-001) through `clauditor-h2l.7` (US-007)

---

## Discovery

### Ticket summary

**What:** Add cross-axis comparability guardrails to `clauditor trend` and `clauditor compare` (delta mode) so iteration sets that mix `harness` or `provider` dimensions cannot silently produce misleading averages or deltas. `clauditor audit` already groups by `(harness, provider, layer, id)` (#152) and is out of scope per ticket.

**Why:** A user who runs the same eval under Claude+Sonnet one week and Codex+gpt-5.4 the next, then runs `clauditor trend`, sees a smooth pass-rate line that is silently averaging across two non-comparable execution stacks. The user-protection principle is **surface, don't normalize** — refuse the dangerous read by default and force opt-in for cross-axis comparison.

**Who benefits:** Multi-provider/multi-harness evaluators; long-running history users (regression tracking); CI pipelines that consume trend/compare exit codes.

**Done when:**

1. `trend` on mixed-harness history fails with a clear stderr message naming the dimension and suggesting either `--harness <value>` filter or `--cross-harness` opt-in.
2. `trend` adds a `--harness` filter mirroring the existing `--provider` filter from #147.
3. `trend` adds `--cross-harness` and `--cross-provider` opt-in flags that allow averaging across the dimension while emitting a stderr WARNING that the result is informational.
4. `compare` (delta mode, NOT `--blind`) detects harness/provider mismatch between the two iteration dirs being compared and refuses unless `--cross-harness` / `--cross-provider` is passed.
5. `compare --blind` is unaffected.
6. `audit` keeps current behavior (groups by 4-tuple; no refusal).
7. Tests cover the refusal path AND the opt-in path for each new flag and each command.
8. Coverage stays ≥80%; ruff passes.

### Scope adjustment from the ticket

The ticket says "trend / compare / audit refuse cross-{harness,provider} averaging." Two clarifications:

- **`audit` is effectively a no-op for #153.** #152 already shipped 4-tuple grouping `(harness, provider, layer, id)` and the rendered output visually separates groups. Mixed-dimension audit output is acceptable per the ticket itself ("groups are visually separated").
- **`trend` already has half the work (`--provider` filter + refusal).** #147 DEC-003 shipped the per-command refusal and `--provider` filter for the provider axis; the explicit `--cross-provider` opt-in was deferred to #153. So #153's trend work is: (a) mirror the existing `--provider` plumbing for `--harness`, (b) add `--cross-{harness,provider}` opt-in flags, (c) wire the new flags as alternatives to the filter.

Net effect: most of the work is on `compare` (greenfield) plus the opt-in flags on `trend`.

### Codebase findings

#### Current state (on `dev` branch — all #144-#152 work merged)

**`src/clauditor/audit.py`** (post-#152):
- `IterationRecord` dataclass (l.198–200): carries `provider: str = "anthropic"` + `harness: str = "claude-code"`.
- `AuditAggregate` dataclass (l.205+): groups by `(harness, provider, layer, id)`.
- `_provider_or_default(value)` (l.90+) and `_harness_or_default(value)` (l.107+): defensive helpers that coerce malformed/missing fields back to canonical defaults. Already exported from this module — reuse for #153.
- Loaders `_records_from_assertions` / `_records_from_extraction` / `_records_from_grading` already inject defaults on legacy reads.
- `MAX_SCHEMA_VERSION` map: `assertions.json=2, extraction.json=4, grading.json=4`.

**`src/clauditor/cli/trend.py`** (post-#147 — provider plumbing only; harness plumbing pending in #153):
- `_normalized_provider(rec)` (l.11–24): pure helper that coerces a history record's `provider` to a safe non-empty string defaulting to `"anthropic"`. Mirror this with `_normalized_harness`.
- `--provider` argument (l.72–80): registered as `_provider_concrete_choice` argparse type (rejects `"auto"`).
- Mixed-provider refusal (l.126–133): exit 2 with stderr `"ERROR: Mixed providers detected in history for skill ..."`. Computes from full filtered set BEFORE `--last` slicing so a narrow window can't slip past.
- Filter path (l.135–147): when `--provider X` passed, narrow records; if empty result, exit 1.

**`src/clauditor/cli/compare.py`** (delta mode, l.297+):
- `cmd_compare` (l.297+): three call shapes — positional `before after`, numeric `--skill --from --to`, and `--blind`. Numeric form constructs `iteration-{N}/{skill}` paths.
- `_load_assertion_set` (l.97+): loads either `.txt` capture or iteration-dir `grading.json`.
- `diff_assertion_sets` from `comparator.py:27` returns flips (regression/improvement/new/removed).
- **No harness/provider check anywhere.** Greenfield surface for #153.

**`src/clauditor/history.py`** (post-#152):
- v3 records carry `provider` + `harness` mandatory keyword fields.
- `read_records` defaults missing `provider`→`"anthropic"`, `harness`→`"claude-code"` on legacy v1/v2 reads.
- `append_record` validates non-blank, rejects `"auto"` (must be a resolved name).

**`src/clauditor/cli/__init__.py`** (helpers):
- `_provider_choice`, `_harness_choice`, `_provider_concrete_choice` (l.59–112): argparse type helpers; reuse for new flags.
- `_resolve_harness(args, eval_spec)` (l.115+): existing four-layer precedence resolver — relevant for understanding the harness model but #153 flags are CLI-only opt-in (no spec field).

#### Test surfaces

- `tests/test_audit.py`: `_make_iteration_fixture()` (l.29) + `_write_sidecars()` (l.56). Schema-version compat tests already cover legacy reads.
- `tests/test_cli.py:2227-2619`: compare tests (txt/grade.json/iteration dirs/blind). No unified fixture helper for compare; tests construct paths inline.
- `tests/test_cli.py:4794+`: trend tests — `test_trend_skips_bad_version_records`. Existing `test_trend_*provider*` tests cover #147's provider refusal/filter path; mirror these.

### Convention checker findings (rule constraints for #153)

Critical rules and how they shape the plan:

- **`pure-compute-vs-io-split.md`** — extract pure helpers `detect_mixed_dimension(records, *, dimension)` (returns `(is_mixed: bool, values_seen: list[str])`) and let CLI commands own I/O + stderr + exit-code routing. Existing `_normalized_provider` is the precedent shape.
- **`llm-cli-exit-code-taxonomy.md`** — non-LLM commands: 0/1/2 only. Refusal is exit 2 (input-validation: the user's data is structurally fine but logically refuses to be averaged). Existing #147 trend refusal uses exit 2 — mirror.
- **`spec-cli-precedence.md`** — `--cross-{harness,provider}` are CLI-only opt-in flags. **No spec field.** No precedence chain.
- **`json-schema-version.md`** — defaults already injected at load time; #153 inherits this and does NOT bump any schema version.
- **`pre-llm-contract-hard-validate.md`** — refusal is whole-run; no partial output.
- **`centralized-sdk-call.md`** announcement family — the opt-in stderr WARNING follows the same one-time-per-process advisory shape as `announce_implicit_no_api_key()` (but per-invocation rather than per-process here, since each CLI invocation is fresh).

### Ambiguities (drive the scoping questions below)

1. `--cross-{harness,provider}` vs. existing `--{harness,provider}` filter — both are now ways to satisfy the refusal. What's the precedence/error if both are passed?
2. For `trend`: keep #147's exit-2 refusal verbatim or harmonize the message format with the new harness mirror?
3. For `compare` delta with two-positional paths (`grading.json` files, not iteration dirs) — does the harness/provider check still apply, or only on iteration-dir mode?
4. Does `audit` get any new behavior — say, a stderr advisory WHEN groups span multiple dimensions, or stay silent?
5. Does `--cross-harness` imply `--cross-provider` (or vice versa) when both axes are mixed? Or must both flags be passed?
6. Stderr WARNING shape on opt-in — single line or header+items?
7. What's the dimension-detection scope for compare — just the two compared iterations, or include earlier iterations the deltas chain through?

---

## Decisions (Phase 1 — Discovery)

### DEC-001 — `--cross-<dim>` and `--<dim>` filter flags are mutually exclusive (Q1=A)

If a user passes both `--provider X` and `--cross-provider` (or both `--harness Y` and `--cross-harness`), argparse rejects with exit 2 and a clear message. Same shape on `trend` and `compare`. *Rationale:* matches the ticket's "or" framing; matches `cmd_trend`'s existing `--metric / --list-metrics` mutex group precedent at trend.py:60.

### DEC-002 — Per-axis flags are strictly orthogonal (Q2=A)

`--cross-harness` allows mixed harness only. `--cross-provider` allows mixed provider only. A history mixed on both axes requires both flags. No combined `--cross-axis` shortcut. *Rationale:* per-axis is the most teachable model; refusal messages name exactly which dimension is mixed and which flag opens it.

### DEC-003 — `compare` check applies to `.grade.json` + iteration-dir mode; `.txt` capture pairs silent-skip (Q3=A)

When both inputs are loadable as `GradingReport` (positional `.grade.json` paths or numeric `--skill --from --to`), the check runs. When both inputs are raw `.txt` captures, the check silent-skips (no metadata available). Mixed-shape (`.txt` + `.grade.json`) is already rejected upstream by the file-kind matcher. *Rationale:* check where the data exists; don't manufacture warnings for legacy captures.

### DEC-004 — Zero changes to `audit` (Q4=A)

`audit` keeps the 4-tuple grouping shipped in #152. No new flags, no advisories, no rendering changes. *Rationale:* ticket is explicit; row-by-row dimension columns already make grouping visible.

### DEC-005 — Match #147's single-line refusal format, templated by dimension (Q5=A)

Refusal stderr message follows the existing `--provider` refusal shape verbatim, with `provider`/`harness` substituted:

```
ERROR: Mixed harnesses detected in history for skill 'X' ('claude-code', 'codex'). Pass --harness claude-code (or --harness codex) to filter, or --cross-harness to allow averaging.
```

Opt-in WARNING is similarly terse, single-line, on stderr:

```
WARNING: averaging across harnesses ('claude-code', 'codex') — results may not be comparable.
```

*Rationale:* the existing `--provider` refusal proved itself; uniform message taxonomy across both axes; tests can use stable substring anchors.

---

---

## Architecture Review (Phase 2)

| Area | Rating | Headline |
|---|---|---|
| Security | pass | CLI-only opt-in; argparse types reject path-traversal/shell-meta; no new trust boundaries. |
| Performance | pass | O(N) single-pass detection on already-loaded records; two `.grade.json` for compare. No quadratic patterns proposed. |
| Data Model | pass | No schema bumps. Defaults already injected at load (`_provider_or_default` / `_harness_or_default`). Idempotent coercion. |
| API / CLI Design | concern | (1) Need new `_harness_concrete_choice`; (2) two separate mutex groups, not one global; (3) retrofit #147's existing provider refusal message to mention `--cross-provider`; (4) skip filter flags on `compare` (only ship `--cross-*`); (5) help-text wording aligned to existing `--provider`. |
| Testing Strategy | concern | (1) Fixture choice for compare: reuse `_make_iteration_fixture()` from `test_audit.py` vs. build minimal helper; (2) coverage pre-check before merge — ~25 new tests, must keep 80%; (3) verify harness-default `"claude-code"` flows through `history.read_records` for legacy lines. |
| Observability | pass | Single-line stderr at "ERROR:" / "WARNING:" prefixes; CI-parseable stable substrings (`"Mixed harnesses"`, `"Mixed providers"`, `"averaging across"`); per-invocation announcement (no module flag). |

### Findings

**Security (pass).** All user-controlled CLI input routes through existing argparse type handlers (`_provider_concrete_choice` rejects `"auto"`; `validate_skill_name` rejects path traversal). Stderr message construction interpolates only validated loader output. No new trust boundaries.

**Performance (pass).** Detection is `set(rec[dim] for rec in records)` — single pass, O(N). Computed BEFORE `--last` slicing so a narrow window can't slip past refusal (mirror of trend.py:126). For `compare` delta: two dict lookups on already-deserialized GradingReports. Negligible.

**Data Model (pass).** On `dev`: assertions.json=2, extraction.json=4, grading.json=4, history.jsonl=v3. `_provider_or_default` (audit.py:90) and `_harness_or_default` (audit.py:107) defensively coerce non-string/blank/None to canonical defaults. `history.read_records` defaults missing fields on legacy v1/v2 reads (history.py:296-302). `GradingReport.from_json` defaults via `str(...or default)` (qg.py:220-224). #153 reads existing fields; no schema bumps.

**API Design (concern).** Five hygiene items must land in implementation:

1. **`_harness_concrete_choice` is missing** in `cli/__init__.py`. `_harness_choice` (l.77) accepts `"auto"`; trend reads pre-resolved history values, so it needs a concrete-only variant. Add the helper next to `_provider_concrete_choice` (l.97-112) and use it for the new `--harness` flag.
2. **Two separate mutex groups, not one global** — argparse `add_mutually_exclusive_group()` per axis: `--harness / --cross-harness` and `--provider / --cross-provider` are independent. `--harness X --cross-provider` should be valid (filter harness, allow mixed provider).
3. **Retrofit #147's provider refusal message** — current trend.py:131-135 says `"Pass --provider X or Y to filter."` but doesn't mention `--cross-provider` (which didn't exist in #147). When #153 lands the opt-in, update the existing message to also suggest `--cross-provider`. Tests for the retrofit may need to update stable substrings.
4. **Skip filter flags on `compare`** — compare has only TWO inputs, so a "filter" doesn't fit the model semantically. Ship only `--cross-harness` and `--cross-provider` opt-in flags on compare. (DEC-006 below codifies this.)
5. **Help text consistency** — new flags should use "Allow averaging across mixed ..." for `--cross-*`, mirroring `--provider`'s "Filter history records by ..." style.

**Testing Strategy (concern).** Three items need a refinement-phase decision:

1. **Compare fixture choice** — `tests/test_audit.py::_make_iteration_fixture()` is the cleanest fixture builder; reuse it from a shared `tests/conftest.py` (or import directly across files) rather than re-rolling minimal grading-json writers in `test_cli.py`. Decide whether to refactor `_make_iteration_fixture` to a shared helper now, or duplicate inline (faster, slightly worse).
2. **Coverage pre-check** — repo gates at 80% (`pyproject.toml`). New code paths: pure helper (4-6 unit tests) + 9 trend integration tests + 11 compare integration tests ≈ ~25 tests. Run `uv run pytest --cov=clauditor --cov-report=term-missing` before merge to spot gaps (especially the `print(WARNING)` lines).
3. **Default verification** — confirm `history.append_record` accepts and `read_records` defaults handle the harness field for legacy lines. Verified: history.py:296-302 defaults missing `harness` to `"claude-code"`; tests assume this.

**Observability (pass).** Single-line stderr messages with stable `"Mixed harnesses"` / `"Mixed providers"` / `"averaging across"` substrings for CI parsing. Per-invocation announcement (each CLI invocation is a fresh process, so the announcement-family flag pattern from `centralized-sdk-call.md` is not needed). No PII; dimension values are public identifiers.

### Blockers

None. Both concerns are hygiene items that resolve in refinement, not architectural blockers.

---

---

## Decisions (Phase 3 — Refinement)

### DEC-006 — `_harness_concrete_choice` argparse helper added next to `_provider_concrete_choice` in `cli/__init__.py` (Q6=A)

Mirror the shape of the existing provider helper (l.97-112): `def _harness_concrete_choice(value: str) -> str:` rejects anything outside `{"claude-code", "codex"}` and rejects `"auto"` because trend reads pre-resolved history. *Rationale:* smallest readable diff; consistent error-message style; same place as the sibling helper.

### DEC-007 — `compare` ships only `--cross-harness` / `--cross-provider`; no filter flags (Q7=A)

Compare has exactly two inputs (before/after); a "filter" is just an equality check the user already controls by choosing iteration dirs. *Rationale:* simpler, semantically cleaner, matches API Design review.

### DEC-008 — Update existing `--provider` refusal message suffix; preserve stable substring (Q8=C)

Keep the byte-stable lead-in `"Mixed providers detected in history for skill"` so existing tests don't break, but extend the suffix to mention BOTH `--provider X` filter and `--cross-provider` opt-in. New harness refusal message follows the same template:

```
ERROR: Mixed providers detected in history for skill 'X' ('anthropic', 'openai').
Pass --provider anthropic (or --provider openai) to filter, or --cross-provider to allow averaging.

ERROR: Mixed harnesses detected in history for skill 'X' ('claude-code', 'codex').
Pass --harness claude-code (or --harness codex) to filter, or --cross-harness to allow averaging.
```

*Rationale:* minimum disruption to existing test substrings while adding the new actionable suffix; symmetric across axes.

### DEC-009 — Compare cross-axis tests use an inline `_write_grading_with_harness` helper in `test_cli.py` (Q9=B)

A 10-line localized helper that writes a minimal `grading.json` with specific harness/provider metadata. Avoids cross-file refactor and the test-fixture-shadowing hazard noted in `CLAUDE.md`'s testing convention block. *Rationale:* compare needs only grading.json (not the full L1+L2+L3 sidecar set); inline is clearer.

### DEC-010 — Pure helper `detect_mixed_dimension` lives in `src/clauditor/audit.py` (Q10=A)

Sibling of `_provider_or_default` and `_harness_or_default`. Signature: `def detect_mixed_dimension(records: list[dict], *, dimension: Literal["harness", "provider"]) -> tuple[bool, list[str]]` — returns `(is_mixed, sorted_unique_values)`. Reuses the existing `_*_or_default` helpers for safe coercion. *Rationale:* match the existing axis-utility siblings; promote to a `comparability.py` module later if #154/#155 add more functions.

### DEC-011 — Multi-axis refusal: refuse + name every still-uncovered axis in one error (Q11=C)

When a user passes `--cross-harness` but the history is also mixed on provider (and `--cross-provider` is not passed), refuse with exit 2 and a single error message naming every still-uncovered axis. The message instructs which additional flag(s) to pass. *Rationale:* most teachable refusal shape; user fixes the command in one round-trip; aligns with DEC-002's strictly orthogonal contract.

Example message:

```
ERROR: Mixed providers detected in history for skill 'X' ('anthropic', 'openai').
Pass --provider anthropic (or --provider openai) to filter, or --cross-provider to allow averaging.
(--cross-harness was provided but the harness axis is not the only mixed dimension.)
```

When BOTH axes are mixed and NO opt-in flags are passed, both refusal messages print (one per axis), then exit 2.

---

## Detailed Breakdown (Phase 4)

Architecture ordering: pure helper → argparse helper → trend (mirror + opt-in) → compare → quality gate → patterns & memory. The pure helper from US-001 is shared by US-003 (harness filter on trend) and US-005 (compare). US-002 unblocks US-003.

### US-001 — Pure helper `detect_mixed_dimension(records, *, dimension)` in `audit.py`

**Description:** Add a pure function that consumes a list of dict records (from `history.read_records` or `IterationRecord` rows) and returns `(is_mixed, sorted_unique_values)` for the named dimension. Defensively coerces missing/non-string values via the existing `_provider_or_default` / `_harness_or_default` helpers. No I/O, no side effects, no raises.

**Traces to:** DEC-010 (helper placement), DEC-001/002 (consumed by trend + compare for refusal logic), `pure-compute-vs-io-split.md` (the pure-helper rule).

**TDD — write these tests first (in `tests/test_audit.py::TestDetectMixedDimension`):**

1. Single value, not mixed: `[{"provider": "anthropic"}, {"provider": "anthropic"}]` → `(False, ["anthropic"])`.
2. Two values, mixed (sorted): `[{"provider": "openai"}, {"provider": "anthropic"}]` → `(True, ["anthropic", "openai"])`.
3. Missing key defaults to `"anthropic"` for provider, `"claude-code"` for harness: `[{"provider": "openai"}, {}]` → `(True, ["anthropic", "openai"])`.
4. Whitespace-only / non-string values default safely: `[{"provider": "  "}, {"provider": None}, {"provider": 42}]` → `(False, ["anthropic"])`.
5. Harness mirror: `[{"harness": "codex"}, {"harness": "claude-code"}]` → `(True, ["claude-code", "codex"])`.
6. Empty input: `[]` → `(False, [])`.

**Acceptance criteria:**

- `detect_mixed_dimension` exported from `clauditor.audit`; signature exactly `(records: list[dict], *, dimension: Literal["harness", "provider"]) -> tuple[bool, list[str]]`.
- Function reads `dimension` value from each record via `dict.get` (not `[]`).
- Coercion via the existing `_*_or_default` helpers (DO NOT inline a new coercer).
- Tests cover the six TDD cases; no `tmp_path`, no subprocess mocks.

**Done when:**

- [ ] `TestDetectMixedDimension` 6 tests pass.
- [ ] Function is referenced from `__all__` if `audit.py` has one (else add to it).
- [ ] `uv run ruff check src/ tests/` passes.
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate.

**Files:**
- `src/clauditor/audit.py` — add helper near `_provider_or_default` / `_harness_or_default` (around l.107).
- `tests/test_audit.py` — new `TestDetectMixedDimension` class with 6 tests.

**Depends on:** none (foundational).

---

### US-002 — `_harness_concrete_choice` argparse type helper in `cli/__init__.py`

**Description:** Add a CLI-input validator that accepts only resolved harness names (`"claude-code"`, `"codex"`) and rejects `"auto"`. Mirrors `_provider_concrete_choice` (l.97-112). Used by the new `--harness` filter on `trend`.

**Traces to:** DEC-006.

**TDD — write these tests first (in `tests/test_cli_helpers.py` or wherever `_provider_concrete_choice` is currently tested; check repo first):**

1. Accepts `"claude-code"` and returns it.
2. Accepts `"codex"` and returns it.
3. Rejects `"auto"` with `argparse.ArgumentTypeError`.
4. Rejects unknown values like `"foo"` with `argparse.ArgumentTypeError`.

**Acceptance criteria:**

- Helper sits adjacent to `_provider_concrete_choice` in `cli/__init__.py`.
- Error message style mirrors the provider helper.
- Function exported (or accessible via `from clauditor.cli import _harness_concrete_choice`).

**Done when:**

- [ ] 4 unit tests pass.
- [ ] `uv run ruff check src/ tests/` passes.
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate.

**Files:**
- `src/clauditor/cli/__init__.py` — add helper near l.97.
- `tests/test_cli_helpers.py` (or existing test file for `_provider_concrete_choice`) — 4 new tests.

**Depends on:** none.

---

### US-003 — Trend mixed-harness detection + `--harness` filter (mirror of #147 for harness axis)

**Description:** Mirror #147's provider plumbing for the harness axis. Add `_normalized_harness` pure helper, `--harness` argparse argument with `_harness_concrete_choice` type, and mixed-harness refusal/filter branches in `cmd_trend`. **The refusal message in this story mentions ONLY the `--harness X` filter — not `--cross-harness`**, which doesn't exist yet. US-004 will retrofit BOTH the harness and provider refusal-message suffixes to mention their respective `--cross-*` opt-in flags when those flags actually land. The existing `--provider` plumbing is untouched in this story.

**Traces to:** DEC-005 (refusal format), DEC-006 (uses `_harness_concrete_choice` from US-002), DEC-008 (refusal suffix template), DEC-010 (uses `detect_mixed_dimension` from US-001 for the harness check).

**TDD — write these tests first (in `tests/test_cli.py::TestCmdTrend`):**

1. `test_mixed_harness_history_refuses_exit_2` — seed history with 3 claude-code + 2 codex records; no flag → exit 2; stderr contains `"Mixed harnesses detected"` and `"--harness claude-code"`. (US-004 will add a follow-up assertion that the suffix also mentions `--cross-harness` once that flag exists.)
2. `test_harness_filter_renders_filtered_records` — same fixture, `--harness codex` → exit 0; stdout has 2 data rows.
3. `test_harness_filter_anthropic_on_mixed` — same fixture, `--harness claude-code` → exit 0; stdout has 3 data rows.
4. `test_harness_filter_empty_result_exits_1` — `--harness codex` on a claude-code-only history → exit 1; stderr `"no records for harness 'codex'"`.
5. `test_harness_concrete_choice_rejects_auto` — `--harness auto` → exit 2 (argparse).

**Acceptance criteria:**

- `_normalized_harness(rec)` pure helper added to `trend.py` near `_normalized_provider` (l.11-24); coerces missing/non-string/blank to `"claude-code"`.
- `--harness` argparse argument added in `add_parser` (with `_harness_concrete_choice` type).
- Mixed-harness detection runs on the FULL filtered set BEFORE `--last` slicing (mirror existing provider check at trend.py:126-133).
- Refusal message follows DEC-008 template **except the `--cross-harness` opt-in suffix is omitted** (US-004 retrofits it once the flag exists). The refusal stays at: `"ERROR: Mixed harnesses detected in history for skill 'X' ('claude-code', 'codex'). Pass --harness claude-code (or --harness codex) to filter."`.
- Filter behavior on `--harness X`: empty result → exit 1 with the same stderr style as the existing provider empty-result branch.

**Done when:**

- [ ] 5 tests pass.
- [ ] `_normalized_harness` is unit-tested or implicitly covered by integration tests (mirror existing provider testing convention).
- [ ] Existing `--provider` tests still pass unchanged.
- [ ] `uv run ruff check src/ tests/` passes.
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate.

**Files:**
- `src/clauditor/cli/trend.py` — add `_normalized_harness`, `--harness` argument, mixed-harness check, filter branch.
- `tests/test_cli.py` — 5 new tests in `TestCmdTrend`. Extend or add `_seed_mixed_harness` fixture helper.

**Depends on:** US-001 (uses `detect_mixed_dimension` for the new harness branch), US-002 (uses `_harness_concrete_choice`).

---

### US-004 — Trend `--cross-harness` / `--cross-provider` opt-in flags + multi-axis refusal logic + retrofit `--provider` message

**Description:** Add `--cross-harness` and `--cross-provider` opt-in flags to `trend`. Wrap each `--<dim>` filter and its `--cross-<dim>` opt-in in its own argparse `add_mutually_exclusive_group` (DEC-001 — two groups, not one). When opt-in flag is present and the corresponding axis is mixed, emit a stderr `"WARNING: averaging across <dim>s ('a', 'b') — results may not be comparable."` line and proceed. Implement DEC-011 multi-axis refusal: if user passes only one `--cross-*` and the OTHER axis is also mixed, refuse with both refusal lines (or extend the message to name the still-uncovered axis). **Retrofit BOTH refusal-message suffixes** in this story: the existing `--provider` refusal gains `or --cross-provider to allow averaging.`, and the harness refusal added in US-003 gains `or --cross-harness to allow averaging.` (DEC-008 — both suffixes land together once both `--cross-*` flags exist).

**Traces to:** DEC-001 (mutex groups per axis), DEC-002 (per-axis flags strictly orthogonal), DEC-005 (warning format), DEC-008 (refusal-message retrofit), DEC-011 (multi-axis refusal naming all uncovered axes).

**TDD — write these tests first (extend `tests/test_cli.py::TestCmdTrend`):**

1. `test_cross_provider_flag_allows_mixed` — mixed-provider history + `--cross-provider` → exit 0; stderr contains `"WARNING: averaging across providers"`.
2. `test_cross_harness_flag_allows_mixed` — mixed-harness history + `--cross-harness` → exit 0; stderr contains `"WARNING: averaging across harnesses"`.
3. `test_cross_provider_and_provider_filter_mutex_error` — `--provider X --cross-provider` → argparse mutex error (exit 2).
4. `test_cross_harness_and_harness_filter_mutex_error` — `--harness X --cross-harness` → argparse mutex error (exit 2).
5. `test_both_axes_mixed_only_cross_harness_refuses` — history mixed on BOTH axes + only `--cross-harness` → exit 2; stderr contains `"Mixed providers"` refusal naming `--cross-provider` (per DEC-011).
6. `test_both_axes_mixed_both_flags_succeeds_with_two_warnings` — both flags → exit 0; stderr contains both `"averaging across providers"` AND `"averaging across harnesses"`.
7. `test_provider_refusal_message_now_mentions_cross_provider` — existing mixed-provider refusal stderr now contains `--cross-provider` substring (DEC-008 retrofit).
8. `test_harness_refusal_message_now_mentions_cross_harness` — harness refusal added in US-003 now also contains `--cross-harness` substring (DEC-008 retrofit).
9. Update existing `test_mixed_provider_history_refuses_exit_2` (or its sibling) to keep its stable `"Mixed providers detected"` substring; add a new assertion that the suffix mentions `--cross-provider`.

**Acceptance criteria:**

- Two argparse mutex groups (one per axis), each containing the `--<dim>` filter + its `--cross-<dim>` opt-in.
- The provider-refusal message string in `cmd_trend` updates: `"Mixed providers detected in history for skill"` lead-in unchanged; suffix extended to include `or --cross-provider to allow averaging.`
- Same shape for the new harness-refusal (DEC-008 template).
- Multi-axis refusal: when one axis is opt-in-flagged and the other is still mixed without its flag, refuse and name the still-mixed axis (DEC-011).
- WARNING-on-opt-in: single line stderr per opted-in axis, exit 0, stdout output proceeds normally (TSV / table).
- `--cross-*` flags use `action="store_true"`, no values.
- Help text: `"Allow averaging across mixed harnesses (results may not be comparable)"` and `"Allow averaging across mixed providers (...)"`.

**Done when:**

- [ ] 9 new/updated tests pass.
- [ ] All previously-passing trend tests still pass.
- [ ] `uv run ruff check src/ tests/` passes.
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate (verify the new `print(WARNING)` lines have coverage).

**Files:**
- `src/clauditor/cli/trend.py` — add mutex groups, opt-in flags, refusal-suffix update, WARNING emit, multi-axis logic.
- `tests/test_cli.py` — 7 new tests + 1 updated assertion.
- `docs/cli-reference.md` — `## trend` section: document new flags + their semantics.

**Depends on:** US-003 (harness filter must exist before opt-in mutex group can pair with it).

---

### US-005 — Compare delta cross-axis detection + `--cross-{harness,provider}` flags

**Description:** Add `--cross-harness` and `--cross-provider` opt-in flags to `clauditor compare` (delta mode only — `--blind` is unaffected per ticket). Detect harness/provider mismatch between the two compared inputs when both are loadable as `GradingReport` (positional `.grade.json` paths or numeric `--skill --from --to` iteration-dir mode). Silent-skip on `.txt` capture pairs (DEC-003). Use the pure helper from US-001. Refuse with exit 2 + stderr message when mismatch and no opt-in (DEC-005 template). Implement DEC-011 multi-axis logic for compare too. Emit WARNING on opt-in success.

**Traces to:** DEC-001 (no filter flags on compare per DEC-007 — only the opt-in side; no mutex groups needed since there are no filter siblings), DEC-002, DEC-003 (silent-skip on `.txt`), DEC-005, DEC-007 (compare ships only opt-in), DEC-009 (inline test fixture), DEC-010 (uses pure helper), DEC-011.

**TDD — write these tests first (in new `tests/test_cli.py::TestCmdCompareCrossAxis`):**

1. `test_compare_two_iters_same_harness_silent` — iteration-1 + iteration-2, both claude-code → no warning, normal exit code.
2. `test_compare_two_iters_mixed_harness_refuses_exit_2` — iter-1 (claude-code) + iter-2 (codex) → exit 2; stderr contains `"Mixed harnesses"` and `"--cross-harness"`.
3. `test_compare_two_iters_mixed_harness_cross_flag_succeeds` — same + `--cross-harness` → normal exit; stderr contains `"WARNING: averaging across harnesses"`.
4. `test_compare_two_grade_json_mixed_harness_refuses` — two `.grade.json` files with different `harness` metadata → exit 2.
5. `test_compare_two_grade_json_cross_harness_flag_succeeds` — same + `--cross-harness` → normal exit + WARNING.
6. Provider-axis mirror: `test_compare_two_iters_mixed_provider_refuses_exit_2`, `test_compare_provider_cross_flag_succeeds`.
7. `test_compare_two_txt_files_silent_skip` — two `.txt` captures (no metadata) → no warning, no refusal, normal diff (DEC-003).
8. `test_compare_both_axes_mixed_only_cross_harness_refuses` — both axes mixed + only `--cross-harness` → exit 2 with provider refusal naming `--cross-provider` (DEC-011).
9. `test_compare_both_axes_mixed_both_flags_succeeds_with_two_warnings` — both flags → normal exit + two WARNINGs.

**Acceptance criteria:**

- `--cross-harness` and `--cross-provider` flags on `compare` (NOT on `compare --blind`).
- Detection runs after `_load_assertion_set` for both inputs, before `diff_assertion_sets`.
- Silent-skip when either input is a `.txt` (no harness/provider metadata available — DEC-003).
- Refusal message uses the DEC-005 template (single-line, stable substring `"Mixed harnesses"` / `"Mixed providers"`).
- WARNING-on-opt-in: single line stderr per opted-in axis.
- Multi-axis refusal: if user passes one `--cross-*` and the OTHER axis is also mismatched without its flag, refuse + name the still-uncovered axis (DEC-011).
- `compare --blind` path is untouched.

**Done when:**

- [ ] 10 new tests pass.
- [ ] `compare --blind` regression test (existing) still passes.
- [ ] Inline `_write_grading_with_harness(path, *, harness, provider)` helper in `test_cli.py` (DEC-009).
- [ ] `uv run ruff check src/ tests/` passes.
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate.

**Files:**
- `src/clauditor/cli/compare.py` — add flags to `cmd_compare` argparse; detection + refusal + warning logic for delta mode (NOT blind branch).
- `tests/test_cli.py` — new `TestCmdCompareCrossAxis` class with 10 tests + the inline `_write_grading_with_harness` helper.
- `docs/cli-reference.md` — `## compare` section: document new flags.

**Depends on:** US-001 (uses `detect_mixed_dimension`), US-003 (refusal-message template established).

---

### US-006 — Quality Gate: code review × 4 + CodeRabbit + validation

**Description:** Run code-reviewer subagent four times across the full changeset, fixing all real bugs found each pass. Run CodeRabbit review if available. Ensure `uv run ruff check src/ tests/` passes and `uv run pytest --cov=clauditor --cov-fail-under=80` passes. Address any code-review findings before submitting the Quality Gate close.

**Traces to:** project Quality Gate convention (every super-plan ends with this gate before patterns & memory).

**Acceptance criteria:**

- 4 code-reviewer passes; each pass's findings either fixed or explicitly justified in a comment.
- CodeRabbit pass (if accessible).
- `uv run ruff check src/ tests/` exits 0.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes; coverage ≥ 80%.
- No skipped tests added by #153 work.

**Done when:**

- [ ] 4 code-reviewer passes complete with all findings addressed.
- [ ] CodeRabbit pass complete (if available).
- [ ] `uv run ruff check src/ tests/` passes.
- [ ] `uv run pytest --cov=clauditor --cov-report=term-missing` passes 80% gate.

**Files:**
- Anywhere findings land — typically `src/clauditor/cli/trend.py`, `src/clauditor/cli/compare.py`, `src/clauditor/audit.py`, `tests/test_cli.py`, `tests/test_audit.py`.

**Depends on:** US-001, US-002, US-003, US-004, US-005 (all implementation must be complete).

---

### US-007 — Patterns & Memory

**Description:** Update `.claude/rules/` and/or memory with patterns learned from #153. Specifically, codify the **cross-axis comparability refusal pattern** as a project rule for future axes (the next one is `transport_source` per #86 — and it's an open question whether to apply this same shape there). Also note any updates needed to existing rules (e.g. the `pure-compute-vs-io-split.md` canonical-implementation list gains another anchor; `llm-cli-exit-code-taxonomy.md` may benefit from a sibling section on the non-LLM 0/1/2 shape with an audit/trend/compare anchor).

**Traces to:** project convention (every super-plan ends with this).

**Acceptance criteria:**

- Either a new `.claude/rules/cross-axis-comparability-refusal.md` rule documenting the pattern, OR an update to existing rule(s) that captures the symmetric per-axis refusal+filter+opt-in shape.
- Update `.claude/rules/pure-compute-vs-io-split.md` canonical-implementation list to add `detect_mixed_dimension` as another anchor.
- Optional: a project memory entry (`bd remember`) noting the design decisions for future cross-axis work.

**Done when:**

- [ ] New or updated rule(s) committed.
- [ ] Memory entry recorded (if applicable).

**Files:**
- `.claude/rules/cross-axis-comparability-refusal.md` (new, optional) or update to one or more existing rule files.

**Depends on:** US-006.

---

## Rules Compliance Gate (validation against `.claude/rules/`)

Each story validated against the rules surfaced by Convention Checker:

| Rule | US affected | Compliance |
|---|---|---|
| `pure-compute-vs-io-split.md` | US-001 | Helper is pure; CLI commands own I/O + stderr + exit codes. ✓ |
| `llm-cli-exit-code-taxonomy.md` | US-003, US-004, US-005 | Non-LLM 0/1/2 shape; refusal at exit 2; stable `"ERROR:"` / `"WARNING:"` prefixes. ✓ |
| `spec-cli-precedence.md` | (N/A) | `--cross-*` are CLI-only opt-in; no spec field counterpart. ✓ |
| `json-schema-version.md` | (N/A) | No schema bumps; defaults already injected upstream. ✓ |
| `permissive-parser-strict-validator.md` | US-001, US-005 | Detection runs AFTER permissive load; refusal is the strict-validator layer. ✓ |
| `pre-llm-contract-hard-validate.md` | US-004, US-005 | Whole-run refusal; no partial output on mismatched axes. ✓ |
| `centralized-sdk-call.md` (announcement family) | US-004, US-005 | Per-invocation WARNING (each CLI invocation is a fresh process); no module-level flag needed. ✓ |
| CLAUDE.md (testing convention) | US-005 | Tests use `tmp_path`; inline fixture per DEC-009 avoids shadowing pytest plugin fixture names. ✓ |

---

## Phase 4 status

Detailing complete. 7 stories defined (5 implementation + 1 quality gate + 1 patterns & memory). All stories trace to DECs and `.claude/rules/`. PR #168 published and approved.

---

## Beads Manifest (Phase 7 — Devolved)

| Story | Bead ID | Type | Depends on |
|---|---|---|---|
| Epic | `clauditor-h2l` | epic | — |
| US-001 — Pure helper `detect_mixed_dimension` | `clauditor-h2l.1` | task | (none) |
| US-002 — `_harness_concrete_choice` argparse helper | `clauditor-h2l.2` | task | (none) |
| US-003 — Trend mixed-harness detection + `--harness` filter | `clauditor-h2l.3` | task | h2l.1, h2l.2 |
| US-004 — Trend `--cross-{harness,provider}` opt-in + multi-axis refusal + retrofit | `clauditor-h2l.4` | task | h2l.3 |
| US-005 — Compare delta cross-axis detection + `--cross-*` flags | `clauditor-h2l.5` | task | h2l.1, h2l.3 |
| US-006 — Quality Gate (code review × 4 + CodeRabbit + validation) | `clauditor-h2l.6` | task | h2l.1, h2l.2, h2l.3, h2l.4, h2l.5 |
| US-007 — Patterns & Memory | `clauditor-h2l.7` | task (P4) | h2l.6 |

**Ready-to-claim** at devolve time: `clauditor-h2l.1` and `clauditor-h2l.2` (no dependencies). Ralph or a manual claimant should start with one of these.

**Worktree:** `/Users/wesduenow/Projects/worktrees/clauditor/153-cross-axis-comparability` on branch `feature/153-cross-axis-comparability` (off `dev`).
