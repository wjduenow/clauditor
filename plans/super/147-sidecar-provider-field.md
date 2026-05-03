# 147: Multi-provider — sidecar v3 with `provider` field

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/147
- **Branch:** `feature/147-sidecar-provider-field`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/147-sidecar-provider-field`
- **PR:** https://github.com/wjduenow/clauditor/pull/165
- **Phase:** devolved
- **Epic:** clauditor-6ne
- **Sessions:** 1 (2026-05-02)
- **Total decisions:** 12 (DEC-001 through DEC-012)
- **Depends on:** #146 (CLOSED — `EvalSpec.grading_provider` four-layer precedence shipped; `provider_source` in-memory field bookmarked for #147)
- **Blocks:** #153 (cross-provider opt-in for audit/trend/compare)
- **Sibling:** #152 (harness field, OPEN, blocked by #151)

---

## Discovery

### Ticket summary

**What:** Bump `grading.json` and `extraction.json` from `schema_version: 2` to
`schema_version: 3`, adding a `provider: str = "anthropic"` field that records
which provider's SDK produced the grading call. Update the audit loader to
accept `{1, 2, 3}` with `provider="anthropic"` defaulted on legacy reads.
Update `clauditor audit` to group by `(provider, layer, id)` instead of
`(layer, id)` so mixed-provider history groups separately. Update
`clauditor trend` to refuse mixed-provider history by default and accept a
`--provider <name>` filter.

**Why:** Post-#146, the `grading_provider` precedence resolver decides
which SDK serves the grading call. Audit/trend already read sidecar history,
but average across providers — that produces meaningless aggregates the
moment a user runs the same eval under both Anthropic and OpenAI. The
sidecars need to record which provider produced each verdict so consumers
can group/filter by it.

**Out of scope:**
- `harness` field (separate issue #152, blocked by #151).
- Cost normalization across providers (intentionally not doing this).
- `assertions.json` schema bump — L1 has no LLM call, so no provider to
  record. L1 will be bumped by #152 to record the harness instead.
- The full `--cross-provider` opt-in pattern for compare/trend lives in
  #153; #147 ships only the per-command refusal + `--provider` filter for
  trend.

### Codebase findings (from Codebase Scout)

**Provider plumbing already in place post-#146.** The in-memory dataclass
fields are bookmarked with explicit comments:

- `src/clauditor/grader.py::ExtractionReport` line 139 — `provider_source: str = "anthropic"`, with comment "the `schema_version: 3` bump that lights it up on disk is owned by #147"
- `src/clauditor/quality_grader.py::GradingReport` line 103 — same
- Both `to_json()` methods currently emit `schema_version: 2` and DO NOT include the `provider_source` field on disk
- Provider value already threaded into the report constructors via
  `extract_and_grade(..., provider_source=...)`,
  `extract_and_report(..., provider_source=...)`,
  `grade_quality(..., provider_source=...)`
- CLI sidecar writers (`cli/grade.py:722` and `cli/grade.py:737`) already
  have the resolved `provider` in scope and pass it through

**Schema-version contract — single source of truth.**
`src/clauditor/audit.py::_check_schema_version` (line 59) reads
`_ACCEPTED_SCHEMA_VERSIONS` (line 42) — a per-filename `dict[str, set[int]]`.
Today: `{"assertions.json": {1}, "extraction.json": {1, 2}, "grading.json": {1, 2}}`.

**v1→v2 default-on-read precedent.**
- `quality_grader.py::GradingReport.from_json` line 200 defaults missing
  `transport_source` to `"api"`
- `grader.py::ExtractionReport.from_json` line 213 — same shape
- The on-disk field name and the in-memory field name are byte-identical
  for `transport_source` (no rename across the boundary)

**Audit grouping (the dimension to expand).**
`src/clauditor/audit.py::IterationRecord` (line 108) carries
`(iteration, layer, id, passed, with_skill)`. Grouping happens in
`audit.py::aggregate` line 366 keyed on `(layer, id)`. To add a `provider`
dimension we need:
1. Add `provider: str = "anthropic"` to `IterationRecord`.
2. Populate from sidecars in `_records_from_extraction` /
   `_records_from_grading` (NOT `_records_from_assertions` — L1 stays
   provider-agnostic).
3. Expand the `(layer, id)` key to `(provider, layer, id)` in `aggregate`.
4. Update `AuditAggregate` to carry `provider`.
5. Update render paths (`render_stdout_table`, `render_markdown`,
   `render_json`) to surface the provider dimension.
6. Decide whether to bump the audit-output JSON schema_version (currently
   `1` at `audit.py:654`) — see DEC-006 below.

**Trend command (the refusal gate to add).**
`src/clauditor/cli/trend.py` lines 57+ iterates iteration history via
`history.read_records`. No mixed-provider gate exists today. Insertion
points:
- Read records, derive `providers_seen = {r.provider for r in records}`
- If `len(providers_seen) > 1` AND `args.provider` is None → exit 2 with a
  stderr message
- If `args.provider` is set → filter records to that provider, error on
  empty result

**Tests scaffolding for the schema bump.**
`tests/test_audit.py` lines 727-826 holds the v1→v2 migration tests.
Helper `_write_grading_sidecar(..., version=, transport_source=)`
parameterizes on schema_version; mirror with `provider` parameter for the
v2→v3 tests.

### Conventions / rules consulted (from Convention Checker)

**Applies — drives implementation:**

1. **`json-schema-version.md`** — Direct governance for this bump. Writers
   emit `schema_version: 3` as first key; loaders accept `{1, 2, 3}` with
   per-version parsers; `provider` defaults to `"anthropic"` on legacy
   reads. **Action:** the rule's "Schema version bumps" section needs a new
   paragraph describing this v3 bump (last paragraph of the rule today
   covers #86's v1→v2).
2. **`pure-compute-vs-io-split.md`** — `to_json()` stays on the dataclass
   (pure); the I/O at the call site stays a one-liner. The new field is a
   pure-data addition; no I/O changes shape.
3. **`centralized-sdk-call.md`** — `ModelResult.provider` already carries
   the resolved provider string. Sidecar-write site sees it via the
   resolved `provider` variable threaded through CLI commands.
4. **`multi-provider-dispatch.md`** — Provider resolution is settled at the
   CLI seam (post-#146); #147 only consumes the resolved string.
5. **`spec-cli-precedence.md`** — `_resolve_grading_provider(args, eval_spec)`
   already produces the value to record; no precedence changes needed.

**Not applicable:**
- `back-compat-shim-discipline.md` — no symbol moves.
- `permissive-parser-strict-validator.md` — internal sidecar, strict.
- `non-mutating-scrub.md` — no I/O scrubbing involved.
- `pre-llm-contract-hard-validate.md` — sidecar shape is internal, not
  LLM-produced.
- `eval-spec-stable-ids.md` — no per-entry id changes.

**`workflow-project.md`** — does not exist. CLAUDE.md mandates beads (`bd`)
for task tracking, not TaskCreate.

### Proposed scope

1. Add `provider: str = "anthropic"` to `GradingReport` and `ExtractionReport`
   serialized output (in-memory `provider_source` field already exists; the
   on-disk field name is decided in DEC-001 below).
2. Bump `grading.json` and `extraction.json` writers to emit
   `schema_version: 3`.
3. Update `_ACCEPTED_SCHEMA_VERSIONS` and the `from_json` default-on-read
   logic to accept v3 and default missing `provider` to `"anthropic"` for
   v1/v2 reads.
4. Add `provider: str = "anthropic"` to `IterationRecord` and `AuditAggregate`.
5. Populate `provider` from sidecars in `_records_from_extraction` and
   `_records_from_grading`. `_records_from_assertions` writes
   `provider="anthropic"` as a placeholder OR — better — assertions records
   participate in audit grouping under their own provider-agnostic dimension
   (see DEC-005 below).
6. Group audit by `(provider, layer, id)` and surface in default text +
   markdown + JSON renderers.
7. Add `--provider` flag to `clauditor trend`; refuse mixed-provider
   history by default with a stderr message naming the dimension and
   suggesting `--provider anthropic` (or `--provider openai`).
8. Update `.claude/rules/json-schema-version.md` "Schema version bumps"
   section with a new paragraph for v2→v3.
9. Tests: v1, v2, v3 sidecars all load; mixed-provider audit aggregates
   into separate groups; trend refuses mixed history; trend `--provider`
   filter works; legacy v1/v2 reads default to `provider="anthropic"`.

### Scoping questions

See "Refinement Log" below; questions to resolve before stories.

---

## Architecture Review

| Area | Verdict | Finding |
|---|---|---|
| Data model & migration | pass | `data.get(...) or "anthropic"` pattern handles legacy reads + null edge cases; `IterationRecord`/`AuditAggregate` use kwargs throughout (safe to add field) |
| BlindReport scope | pass | Audit loader never reads blind sidecars; out-of-scope confirmed |
| Atomic-publication | pass | Field addition doesn't change staging order |
| `_provider_choice` validator for trend | concern → DEC-007 | Existing validator accepts `"auto"` — wrong for trend (no precedence context); split into sibling validator |
| Aggregate dict key change | concern → DEC-003 follow-on | `(layer, id)` → `(provider, layer, id)` breaks `apply_thresholds()` unpacking (audit.py:461) and tests; mechanical fix, internal-only |
| Forward-compat for #152 | concern → DEC-008 | Explicit frozenset will grow; refactor to "version-and-up" check while we're touching it |
| Trend refusal placement | pass → DEC-011 | After `--command` filter, before `--last` slicing; refusal sees the full filtered set |
| Stderr message style | pass | House style: `ERROR: <reason>\n<actionable next step>` |
| `--filter` syntax vs. dedicated flag | pass | No existing `--filter` on trend; use dedicated `--provider` |
| Audit-JSON v2 output shape | pass → DEC-005, DEC-010 | Already flat per-assertion (`assertions: [{...}]`); add `provider` field, `providers_seen` summary, bump `schema_version` |
| Test matrix | pass | v1/v2/v3 round-trip, mixed-provider grouping, trend refusal, filter-to-empty, malformed records |
| `history.jsonl` provider plumbing | concern → DEC-012 | Trend reads `history.jsonl`, not sidecars directly; bump history.jsonl to v2 with `provider` field |

No blockers.

---

## Refinement Log

### DEC-001 — On-disk field name: `provider_source`

The on-disk field name in v3 sidecars is `provider_source`, byte-identical
to the in-memory `provider_source` dataclass field. Mirrors the
`transport_source` precedent (in-memory↔disk byte-identity introduced in
`#86`) where the same field name lives on both sides of the I/O boundary.
Diverges from the ticket text's literal `provider`, but the consistency
win with `transport_source` outweighs the ticket-fidelity loss. (Q1=B)

### DEC-002 — L1 records carry `provider="anthropic"` placeholder

`assertions.json` is NOT bumped (L1 has no LLM call), but `IterationRecord`
gains a `provider: str = "anthropic"` field that L1 records carry as a
placeholder. Audit groups L1 records under `("anthropic", "L1", id)`
regardless of which provider produced the underlying skill output. This
is a small lie in mixed-harness scenarios but matches the ticket's "L1
not bumped" constraint while keeping `IterationRecord` shape uniform
across layers. The honest harness dimension lives in #152 (which bumps
assertions.json to v2). (Q2=A)

### DEC-003 — Trend mixed-provider hard refusal, exit 2; no `--cross-provider` flag in #147

When `clauditor trend` detects mixed providers in the filtered history
and `--provider` is not passed, refuse with stderr message naming the
providers seen and suggesting `--provider anthropic` or
`--provider openai`. Exit 2 (input-validation error). The
`--cross-provider` opt-in flag pattern lives in #153, which also covers
audit and compare. #147 ships only the per-command refusal + filter, not
the opt-in. (Q3=A)

### DEC-004 — Audit default-output adds leftmost `provider` column

`render_stdout_table` and `render_markdown` get a new leftmost `PROVIDER`
column, ~11 chars wide. Compact, one row per `(provider, layer, id)`.
Sort order: `(provider, layer, id)`. (Q4=A)

### DEC-005 — Audit-output JSON bumps to `schema_version: 2`

`render_json` (today emits `schema_version: 1`) bumps to `2`, adds a
`provider` field per `assertions[]` entry. The bump signals a structural
change to any consumer parsing the audit JSON output. There are no
known external consumers today. (Q5=A)

### DEC-006 — Strictly separable from #152

`#147` bumps `grading.json`/`extraction.json` to v3 with `provider_source`
ONLY. When #152 lands (after #151 — harness identity), it bumps to v4
with a `harness` field. Two migrations, clean traceability, no
coordination dependency on #151. (Q6=A)

### DEC-007 — New `_provider_concrete_choice()` validator for trend

Add `_provider_concrete_choice()` in `cli/__init__.py` accepting only
`{"anthropic", "openai"}`. Trend's `--provider` flag uses this validator;
the six LLM-mediated commands keep the existing `_provider_choice` (which
accepts `"auto"` for four-layer-precedence resolution). Each validator
has one job; trend has no model/spec context to resolve `"auto"`
against. (Q7=A)

### DEC-008 — Refactor `_ACCEPTED_SCHEMA_VERSIONS` to `MAX_SCHEMA_VERSION` map + helper

Replace the explicit `frozenset({1, 2})` per filename with a
`MAX_SCHEMA_VERSION: dict[str, int]` map and a pure helper
`_is_accepted_version(filename, version)` returning
`1 <= version <= MAX_SCHEMA_VERSION[filename]`. Future bumps (#152's
v3→v4) become a one-number-per-file edit instead of re-listing the set.
The "version-and-up" check assumes monotonic forward compatibility —
the audit loader's per-version `_records_from_*` helpers stay
responsible for handling shape differences across versions. (Q8=A)

### DEC-009 — Trend `--provider <X>` filter matching zero records → exit 1

E.g., `clauditor trend --provider openai` on all-anthropic history
exits 1 with a stderr "no records found for provider openai" message.
Same exit code as the existing "no records found" path; provider
mismatch is a missing-data condition, not an input-validation
error. (Q9=A)

### DEC-010 — Audit-JSON v2 adds top-level `providers_seen: list[str]`

In addition to the per-assertion `provider` field (DEC-005), the
audit-output JSON v2 adds a top-level `"providers_seen": ["anthropic",
"openai"]` array sorted alphabetically. Lets JSON consumers detect
mixed history without iterating `assertions[]`. (Q10=B)

### DEC-011 — Trend refusal computed from full filtered history, BEFORE `--last` slice

The `providers_seen` set is computed over records AFTER the `--command`
filter is applied but BEFORE `--last N` window slicing. So a user with
100 mixed iterations and `--last 20` (where the trailing 20 happen to be
single-provider) still gets refused — they must fix the global mismatch
or pass `--provider`. Argument: trend's job is to surface the full
history shape; suppressing the refusal via slicing would let users
silently slip past mixed history. (Q11=A)

### DEC-012 — Bump `history.jsonl` to `schema_version: 2` with `provider` field

`clauditor trend` reads `history.jsonl`, not sidecars directly. To make
DEC-011's refusal work, history records must carry `provider`. Bump
`history.jsonl` from `schema_version: 1` to `2`, add `provider` (string)
field to each record. Update both `history.append_record` call sites
(`cli/grade.py:1418`, `cli/extract.py:238`) to pass the resolved
provider. `read_records` defaults missing `provider` to `"anthropic"` for
legacy v1 records. **Note:** The history.py `_check_schema_version` is
strict-equality (`!= SCHEMA_VERSION`) today; we bump `SCHEMA_VERSION`
itself and add a small accept-list `{1, 2}` (or use a max-version
helper, mirroring DEC-008's pattern in audit.py). (Q12=A)

---

## Detailed Breakdown

Stories follow architecture order: pure dataclasses → loader → grouping →
rendering → history → CLI → docs. Each story is one-context-window
sized. TDD applies where the story creates or modifies pure logic.

### US-001 — Sidecar writers/readers v3 (`GradingReport`, `ExtractionReport`)

**Description.** Bump `GradingReport.to_json()` and `ExtractionReport.to_json()`
to emit `schema_version: 3` and include `provider_source` as a top-level
key. Update the corresponding `from_json()` methods to default missing
`provider_source` to `"anthropic"` so v1 and v2 reads stay byte-identical
to today's behavior.

**Traces to:** DEC-001, DEC-006.

**Files:**
- `src/clauditor/quality_grader.py` — `GradingReport.to_json()` (~line 138):
  bump `"schema_version": 2` → `3`; add `"provider_source": self.provider_source`
  next to `"transport_source"`. `GradingReport.from_json()` (~line 200): read
  `data.get("provider_source") or "anthropic"`. Update the docstring's
  schema example.
- `src/clauditor/grader.py` — same shape on `ExtractionReport.to_json()`
  (~line 167) and `ExtractionReport.from_json()` (~line 213). Update
  the docstring schema example.
- `tests/test_quality_grader.py` — round-trip tests for v3.
- `tests/test_grader.py` — round-trip tests for v3.

**Acceptance criteria.**
1. `GradingReport(provider_source="openai", ...).to_json()` → JSON has
   `"schema_version": 3` as first key, `"provider_source": "openai"` field present.
2. `GradingReport.from_json(<v3 payload>)` round-trips `provider_source` faithfully.
3. `GradingReport.from_json(<v2 payload>)` (no `provider_source`) returns
   instance with `provider_source == "anthropic"`.
4. `GradingReport.from_json(<v1 payload>)` (no transport_source, no
   provider_source) returns instance with both fields defaulted.
5. Same four invariants for `ExtractionReport`.
6. `uv run ruff check src/ tests/` clean.
7. `uv run pytest --cov=clauditor --cov-report=term-missing` ≥ 80%.

**Done when:** All four round-trip invariants pass for both reports;
docstrings updated; existing v2 test fixtures still load (back-compat).

**Depends on:** none.

**TDD test cases (write failing tests first):**
- `test_grading_report_to_json_v3_emits_provider_source`
- `test_grading_report_from_json_v3_round_trips`
- `test_grading_report_from_json_v2_defaults_provider_source_to_anthropic`
- `test_grading_report_from_json_v1_defaults_both_legacy_fields`
- Mirror four for `ExtractionReport`.

---

### US-002 — Audit loader: `MAX_SCHEMA_VERSION` refactor + accept v3

**Description.** Refactor `audit.py::_ACCEPTED_SCHEMA_VERSIONS` (a
per-filename `frozenset[int]`) to `MAX_SCHEMA_VERSION: dict[str, int]`
plus a pure helper `_is_accepted_version(filename, version) -> bool`
that returns `1 <= version <= MAX_SCHEMA_VERSION[filename]`. Bump
`grading.json` and `extraction.json` max to `3` (assertions.json stays
at `1`). Update `_check_schema_version` to use the helper.

**Traces to:** DEC-008, DEC-006.

**Files:**
- `src/clauditor/audit.py` — replace `_ACCEPTED_SCHEMA_VERSIONS` constant
  with `MAX_SCHEMA_VERSION` dict; add `_is_accepted_version` helper;
  update `_check_schema_version` call site (line 59-80) to use the helper.
  Update the constant's docstring/comment (lines 36-46).
- `tests/test_audit.py` — extend `_check_schema_version` tests to cover
  v3 acceptance and v4 rejection (which should warn-and-skip per the
  existing pattern).

**Acceptance criteria.**
1. v1, v2, v3 sidecars load cleanly through `_check_schema_version`.
2. v4 grading.json (a hypothetical future bump) is rejected with a
   stderr warning matching the existing format
   `"warning: ... has schema_version=4, expected 1..3 — skipping"`
   (or the existing wording, whichever lands).
3. Unknown filename argument to `_is_accepted_version` raises `KeyError`
   (or returns `False` — tests pin behavior).
4. Existing tests for v1/v2 schema acceptance unchanged.
5. Lint + 80% coverage gate.

**Done when:** Helper exists, three filenames map cleanly, audit loader
accepts v3, all existing tests pass.

**Depends on:** none (parallel with US-001).

**TDD test cases:**
- `test_is_accepted_version_grading_json_accepts_1_2_3`
- `test_is_accepted_version_grading_json_rejects_4`
- `test_is_accepted_version_assertions_json_accepts_only_1`
- `test_check_schema_version_uses_helper_for_v3_grading`

---

### US-003 — `IterationRecord`/`AuditAggregate` + grouping by `(provider, layer, id)`

**Description.** Add `provider: str = "anthropic"` to `IterationRecord`
(audit.py:108) and `AuditAggregate` (line 119). Populate from sidecars
in `_records_from_extraction` and `_records_from_grading` via
`data.get("provider_source") or "anthropic"` per record. L1
(`_records_from_assertions`) writes the placeholder `"anthropic"` per
DEC-002. Update `aggregate()` (line 366) to group by
`(provider, layer, id)`. Fix the downstream unpack in `apply_thresholds()`
(line 461) and any test fixtures asserting on the old key tuple.

**Traces to:** DEC-002, DEC-001.

**Files:**
- `src/clauditor/audit.py` — `IterationRecord` (add `provider` field);
  `AuditAggregate` (add `provider` field); `_records_from_assertions`,
  `_records_from_extraction`, `_records_from_grading` (populate from
  sidecar dict or placeholder); `aggregate()` (regroup); `apply_thresholds()`
  (unpack 3-tuple key); `AuditVerdict` if it exists and carries layer/id
  (add provider).
- `tests/test_audit.py` — extend grouping tests to cover mixed-provider
  history (one anthropic + one openai grading.json under the same skill)
  produces two distinct aggregates; existing single-provider tests pass
  unchanged with `provider="anthropic"` defaulted.

**Acceptance criteria.**
1. `IterationRecord(iteration=1, layer="L3", id="x", passed=True,
   with_skill=True)` (positional/kwargs as today) constructs cleanly
   with `provider == "anthropic"`.
2. Mixed-provider history under one skill (one v3 grading.json with
   `provider_source: "anthropic"` + one with `provider_source: "openai"`,
   same layer/id) → `aggregate()` returns two `AuditAggregate` entries
   keyed on `("anthropic", "L3", "x")` and `("openai", "L3", "x")`.
3. Single-provider history continues to render the single aggregate
   bucket under `("anthropic", ...)`.
4. `apply_thresholds()` consumes the new 3-tuple key without raising.
5. v2 sidecars (no `provider_source`) → records default to
   `provider="anthropic"`.
6. Lint + 80% coverage gate.

**Done when:** Mixed-provider grouping works end-to-end; all existing
audit tests pass; new mixed-provider tests pass.

**Depends on:** US-001 (provider in sidecars), US-002 (loader accepts v3).

**TDD test cases:**
- `test_iteration_record_defaults_provider_to_anthropic`
- `test_records_from_grading_reads_provider_source`
- `test_records_from_grading_v2_defaults_provider_to_anthropic`
- `test_records_from_assertions_uses_anthropic_placeholder`
- `test_aggregate_groups_by_provider_layer_id`
- `test_apply_thresholds_consumes_three_tuple_key`

---

### US-004 — Audit render paths: leftmost `provider` column + JSON v2 with `providers_seen`

**Description.** Update the three audit render paths:
1. `render_stdout_table` — add leftmost `PROVIDER` column (~11 chars wide,
   `<11}` left-align).
2. `render_markdown` — add leftmost `| provider |` column.
3. `render_json` — bump output `schema_version: 1` → `2`; add `provider`
   to each `assertions[]` entry; add top-level `"providers_seen": [...]`
   array (sorted alphabetically).
Sort order across all three: `(provider, layer, id)`.

**Traces to:** DEC-004, DEC-005, DEC-010.

**Files:**
- `src/clauditor/audit.py` — `render_stdout_table` (~line 537),
  `render_markdown` (~line 550-624), `render_json` (line 626-660).
- `tests/test_audit.py` — extend render tests for the new column /
  field; explicitly verify `providers_seen` is sorted.

**Acceptance criteria.**
1. `render_json` output has `"schema_version": 2` as first key.
2. Each entry in `"assertions"` carries a `"provider"` field.
3. `"providers_seen"` is present at the top level, sorted alphabetically.
4. Single-provider history → `providers_seen == ["anthropic"]`.
5. Mixed history → `providers_seen == ["anthropic", "openai"]`.
6. `render_stdout_table` produces a `PROVIDER` column header and one row
   per `(provider, layer, id)` with the provider string left-padded.
7. `render_markdown` table has `| provider |` as the first column.
8. Lint + 80% coverage gate.

**Done when:** All three render paths surface provider; JSON output
schema bumped; tests cover both single- and mixed-provider snapshots.

**Depends on:** US-003.

**TDD test cases:**
- `test_render_json_v2_includes_provider_per_assertion`
- `test_render_json_v2_includes_providers_seen_sorted`
- `test_render_stdout_table_has_provider_column`
- `test_render_markdown_has_provider_column`

---

### US-005 — `history.jsonl` schema v2 with `provider` field

**Description.** Bump `history.jsonl` from `schema_version: 1` to `2`
adding `provider: str` to each record. Update `history.append_record`
to require `provider` as a keyword arg. Update `read_records` (and the
schema-check helper) to accept `{1, 2}` and default missing `provider`
to `"anthropic"` for legacy reads. Wire the resolved `provider` value
through to the two call sites in `cli/grade.py:1418` and
`cli/extract.py:238`.

**Traces to:** DEC-012.

**Files:**
- `src/clauditor/history.py` — `SCHEMA_VERSION` bump; `_check_schema_version`
  to accept `{1, 2}` (or mirror DEC-008's max-version helper if it
  generalizes); `append_record` signature gains `provider` kwarg;
  `read_records` defaults `provider` on legacy lines.
- `src/clauditor/cli/grade.py` — `_append_grade_history_record` (line 1413):
  pass `provider=` from the resolved provider (already in scope at
  `cmd_grade`).
- `src/clauditor/cli/extract.py` — line 238 call: pass `provider=`.
- `tests/test_history.py` (or wherever history tests live) — v1 read
  defaults to anthropic; v2 round-trip; mixed v1/v2 history reads cleanly.
- `tests/test_cli.py` — `cli/grade.py` and `cli/extract.py` history-write
  tests assert `provider` lands in the record.

**Acceptance criteria.**
1. `history.append_record(provider="openai", ...)` writes a record with
   `"schema_version": 2` and `"provider": "openai"`.
2. `history.append_record` without `provider` raises `TypeError`
   (missing required kwarg).
3. `read_records` over a file containing v1 + v2 lines returns both;
   v1 lines surface `provider == "anthropic"` (defaulted); v2 lines
   preserve `provider`.
4. CLI grade and extract paths thread the resolved provider into
   the history append.
5. Lint + 80% coverage gate.

**Done when:** Both append sites pass `provider=`; `read_records`
handles legacy reads cleanly; tests cover both versions.

**Depends on:** none (parallel with US-001-004).

**TDD test cases:**
- `test_append_record_v2_writes_provider_field`
- `test_append_record_requires_provider_kwarg`
- `test_read_records_legacy_v1_defaults_provider_to_anthropic`
- `test_read_records_v2_preserves_provider`
- `test_grade_appends_history_with_resolved_provider`
- `test_extract_appends_history_with_resolved_provider`

---

### US-006 — `clauditor trend`: `--provider` flag + mixed-provider refusal

**Description.** Add `_provider_concrete_choice()` argparse validator in
`cli/__init__.py` accepting `{"anthropic", "openai"}` only (rejecting
`"auto"`). Add `--provider` flag to `cli/trend.py` using the new
validator. After the `--command` filter is applied (line 68-84) but
BEFORE the `--last` window slice (line 86), compute
`providers_seen = {r["provider"] for r in records}` (treating missing
`provider` as `"anthropic"`). If `len(providers_seen) > 1` and
`args.provider is None` → exit 2 with stderr message naming the
providers. If `args.provider` is set → filter records to that provider;
if filter result is empty → exit 1 with "no records for provider X"
message.

**Traces to:** DEC-003, DEC-007, DEC-009, DEC-011.

**Files:**
- `src/clauditor/cli/__init__.py` — new `_provider_concrete_choice()`
  helper next to `_provider_choice` (line 59-74), with rejection of
  `"auto"`.
- `src/clauditor/cli/trend.py` — add `--provider` argparse arg (with
  `type=_provider_concrete_choice`); insert refusal logic between
  command-filter and `--last` slice; insert filter logic when
  `args.provider` is set; format stderr messages.
- `tests/test_cli.py` (trend section ~line 4825-5060) — refusal,
  filter-to-some, filter-to-empty, single-provider passes.

**Acceptance criteria.**
1. `clauditor trend --skill X` on mixed-provider history → exit 2,
   stderr matches `ERROR: Mixed providers detected in history` and
   names both providers and suggests `--provider <name>`.
2. `clauditor trend --skill X --provider auto` → argparse rejects with
   exit 2 (validator).
3. `clauditor trend --skill X --provider openai` on mixed history →
   filters to openai records and renders TSV normally.
4. `clauditor trend --skill X --provider openai` on all-anthropic
   history → exit 1, stderr "no records for provider openai".
5. `clauditor trend --skill X` on single-provider history → renders
   TSV as today (no behavior change).
6. Refusal computed from full filtered set, not the `--last` slice
   (DEC-011): mixed history with `--last 5` (last 5 happen to be
   single-provider) still refuses.
7. `_provider_concrete_choice` rejects `"auto"`, `""`, `"openi"`.
8. Lint + 80% coverage gate.

**Done when:** All five behavioral invariants pass; the new validator
is unit-tested in isolation.

**Depends on:** US-005 (trend reads `provider` from history records).

**TDD test cases:**
- `test_provider_concrete_choice_accepts_anthropic_openai`
- `test_provider_concrete_choice_rejects_auto`
- `test_trend_mixed_provider_refuses_exit_2`
- `test_trend_provider_filter_renders_filtered_records`
- `test_trend_provider_filter_empty_exits_1`
- `test_trend_single_provider_unchanged_behavior`
- `test_trend_refusal_uses_full_filtered_history_not_last_slice`

---

### US-007 — Update `.claude/rules/json-schema-version.md` "Schema version bumps"

**Description.** Append a new paragraph to the "Schema version bumps for
`#86`" section documenting the v2→v3 bump introduced by `#147` (provider_source
on grading.json and extraction.json) and the parallel v1→v2 bump on
history.jsonl. Note that assertions.json was NOT bumped (L1 has no
provider). Document the `MAX_SCHEMA_VERSION` refactor (DEC-008) so future
bumps follow the same pattern.

**Traces to:** DEC-008, DEC-012, DEC-002.

**Files:**
- `.claude/rules/json-schema-version.md` — add a "Schema version bumps
  for #147" subsection mirroring the existing #86 subsection's shape.

**Acceptance criteria.**
1. New subsection exists with anchor-stable heading.
2. Documents the v2→v3 bump for grading.json and extraction.json.
3. Documents the v1→v2 bump for history.jsonl.
4. Documents the `MAX_SCHEMA_VERSION` map + `_is_accepted_version` helper
   refactor (DEC-008).
5. Mentions L1 stays at v1 with the harness-axis bump deferred to #152.

**Done when:** Rule file has the new subsection and points at the
canonical implementation file:line anchors.

**Depends on:** US-002, US-005 (rule must accurately describe what
landed).

---

### US-008 — Quality Gate: code review x4 + CodeRabbit + project validation

**Description.** Run the project's quality gate across the full #147
changeset.

**Steps.**
1. Pass 1 — code-reviewer agent on full changeset; fix any real bugs.
2. Pass 2 — code-reviewer agent on the fix commits; fix.
3. Pass 3 — code-reviewer agent again; fix.
4. Pass 4 — code-reviewer agent again; fix until clean.
5. CodeRabbit review on the PR; address all real findings.
6. Run `uv run ruff check src/ tests/` — must be clean.
7. Run `uv run pytest --cov=clauditor --cov-report=term-missing` —
   must pass with ≥ 80% coverage.

**Acceptance criteria.**
1. Four code-reviewer passes complete with all real findings fixed.
2. CodeRabbit review addressed (real findings fixed; false positives
   documented).
3. Lint clean.
4. Test gate passes (`pytest` exit 0, coverage ≥ 80%).

**Done when:** All gates green; PR is ready for human review.

**Depends on:** US-001 through US-007.

---

### US-009 — Patterns & Memory

**Description.** Update conventions, docs, and memory with patterns
learned from #147 implementation.

**Steps.**
1. Audit `.claude/rules/json-schema-version.md` — DOES it now correctly
   describe the `MAX_SCHEMA_VERSION` map + `_is_accepted_version` helper
   pattern? (US-007 establishes; US-009 verifies and polishes.)
2. If `MAX_SCHEMA_VERSION` generalizes meaningfully across this PR
   (audit.py + history.py both use it), consider extracting a shared
   helper module — defer to a follow-up if the duplication is two call
   sites only.
3. Audit `.claude/rules/centralized-sdk-call.md` — does the
   `provider_source` field need a mention next to the existing
   `transport_source` discussion? Add a line if so.
4. Update `MEMORY.md` if any session-level discoveries are worth
   persisting (e.g. "trend reads history.jsonl, not sidecars" was a
   non-obvious finding).
5. Update `docs/cli-reference.md` if `clauditor trend --provider` is a
   user-visible flag worth documenting (it is).

**Acceptance criteria.**
1. Rules accurately reflect post-#147 state.
2. `docs/cli-reference.md` documents `--provider` on trend.
3. Any new patterns are captured in `.claude/rules/` or memory.

**Done when:** Rule files refreshed; user-facing docs updated.

**Depends on:** US-008.

---

## Beads Manifest

- **Epic:** `clauditor-6ne` — #147: Multi-provider — sidecar v3 with provider field
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/147-sidecar-provider-field`
- **Branch:** `feature/147-sidecar-provider-field`

| Task | Story | Depends on |
|---|---|---|
| `clauditor-6ne.1` | US-001 — Sidecar writers/readers v3 | (ready) |
| `clauditor-6ne.2` | US-002 — Audit loader `MAX_SCHEMA_VERSION` + v3 | (ready) |
| `clauditor-6ne.3` | US-003 — `IterationRecord`/`AuditAggregate` group | `.1`, `.2` |
| `clauditor-6ne.4` | US-004 — Audit render: provider column + JSON v2 | `.3` |
| `clauditor-6ne.5` | US-005 — `history.jsonl` v2 with `provider` | (ready) |
| `clauditor-6ne.6` | US-006 — Trend `--provider` + refusal | `.5` |
| `clauditor-6ne.7` | US-007 — Update json-schema-version.md | `.2`, `.5` |
| `clauditor-6ne.8` | US-008 — Quality Gate (code review x4 + CodeRabbit) | `.1`–`.7` |
| `clauditor-6ne.9` | US-009 — Patterns & Memory | `.8` |

**Initial parallel ready:** `.1`, `.2`, `.5`.

---

## Session Notes

### 2026-05-02 — Session 1 (planning)

- Fetched ticket #147 from GitHub Issues.
- Verified provider plumbing is already bookmarked in `GradingReport.provider_source` and `ExtractionReport.provider_source` (in-memory fields with explicit `# 147` comments).
- Confirmed v1→v2 migration test pattern in `tests/test_audit.py:727-826` is mirrorable for v2→v3.
- Confirmed no `workflow-project.md` and no existing `clauditor trend` mixed-X gate.

### 2026-05-02 — US-009 (Patterns & Memory)

- Audited `.claude/rules/json-schema-version.md` — US-007's "Schema version bumps for #147" subsection accurately describes both the sidecar v2→v3 bump (with `provider_source` default-on-read) and the `history.jsonl` v1→v2 bump (with keyword-only `provider=` on `append_record` and `read_records` defaulting to `"anthropic"` for legacy lines). DEC-008's `MAX_SCHEMA_VERSION` map + `_is_accepted_version` helper refactor is documented with file anchors. No further polish needed.
- Audited `.claude/rules/centralized-sdk-call.md` — added a "Provider-axis stamping (`provider_source`, #147)" subsection parallel to the existing "Multi-transport routing (CLI + SDK, #86) — Anthropic only" subsection. Documents the byte-identical in-memory↔disk field-name shape, the four-layer precedence resolution at the CLI seam, the v2→v3 schema bump cross-reference, and the strict-separability of #152's harness-axis sibling.
- Audited `.claude/rules/multi-provider-dispatch.md` — no changes needed. The dispatcher pattern is unchanged by #147 (which only adds sidecar fields and a trend-side filter, not new auth dispatch surface).
- Updated `docs/cli-reference.md` — added `--provider {anthropic,openai}` flag entry to the `trend` subsection, including the mixed-provider exit-2 refusal, the "computed before `--last` slice" semantic, the exit-1 empty-filter behavior, the legacy-default-to-anthropic for pre-#147 history, and the validator's distinction from `--grading-provider` (no `auto`). Also extended the `audit` subsection: added a one-line note that aggregates group by `(provider, layer, id)`, called out the v1→v2 audit-JSON shape (`provider` per assertion, `providers_seen` top-level array), and noted the leftmost `PROVIDER` column on the text/Markdown renderers + the L1 placeholder lie pending #152.
- Deferred: MEMORY.md update. The "trend reads history.jsonl, not sidecars" finding is now captured in the rule itself (post-#147 update in `json-schema-version.md`); persisting it separately in MEMORY.md would duplicate. CLAUDE.md additionally directs persistent knowledge to `bd remember`, not MEMORY.md.
- Deferred: shared `MAX_SCHEMA_VERSION` helper module. Today only two call sites exist (`audit.py` for sidecars, `history.py` for the JSONL stream) and the `json-schema-version.md` rule explicitly says "two call sites is below the extraction threshold". A third sidecar family adopting the same shape would trip the threshold and warrant the extraction.
- Deferred: any new `.claude/rules/*.md` file. Default-deferral per US-009 instructions; the audit-JSON v2 shape change and the `--provider` filter are both adequately covered by existing rules (`json-schema-version.md` for the schema bump, `spec-cli-precedence.md` for the future precedence-shape adjacent to it, `multi-provider-dispatch.md` for the dispatcher framing). No two-caller foot-gun emerged that the existing rule set does not cover.
