# Super Plan — #25: Always-pass assertion auditor

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/25
- **Branch:** `feature/25-assertion-auditor`
- **Phase:** devolved
- **PR:** https://github.com/wjduenow/clauditor/pull/34
- **Sessions:** 1 (2026-04-14)

## Discovery

### Ticket
`clauditor audit <skill>` analyzes recent eval runs and flags assertions that
always pass (stale signal) or fail to discriminate between with-skill and
without-skill (no value). Emits a human-readable markdown report + stdout
summary with a "suggest removal" list, plus `--json` for CI.

### Scope (locked)
- **Q1 — Layers covered:** C — all three (L1 assertions, L2 schema fields, L3 criteria)
- **Q2 — Baseline:** B — wire `run_raw()` into `cmd_grade` with `--baseline` flag, persist baseline results
- **Q3 — Data source:** iteration-*/ dirs (authoritative per-assertion data); default N=20, configurable via `--last N`
- **Q4 — Output:** markdown report written to file + stdout summary
- **Q5 — Thresholds:** hardcoded defaults with CLI override

### Codebase facts (confirmed)

- **Layer 1**: `AssertionSet` (assertions.py:44-71) holds
  `list[AssertionResult]` with `name, passed, message, kind, evidence,
  raw_data`. **Not persisted anywhere.** Runs inside `spec.run()` but results
  are discarded by every current caller.
- **Layer 2**: `grader.extract_and_grade()` returns `AssertionSet` with
  per-field results (grader.py:128-142). **Not persisted anywhere.**
  `cmd_grade` does not currently invoke L2 — only `cmd_extract` does, and
  that discards results after printing.
- **Layer 3**: `GradingReport.to_json()` → `.clauditor/iteration-N/<skill>/grading.json`.
  Per-criterion `passed`, `score`, `evidence`, `reasoning`.
- **Iteration dir layout (#22)**: staging `iteration-N-tmp/<skill>/` → atomic
  rename to `iteration-N/<skill>/`. **Post-finalize append is unsafe.** New
  audit inputs must be written during the same staging cycle.
- **`run_raw`**: runner.py:156 — only caller today is `comparator.compare_ab()`
  at comparator.py:157. Returns `SkillResult` tagged `skill_name="__baseline__"`.
- **cmd_grade phases** (cli.py:273-547): (1) run skill, (2) variance runs, (3)
  Layer 3 grade, (4) write staging (run-K/, grading.json, timing.json), (5)
  finalize + report.
- **Stable IDs**: EvalSpec assertions (schemas.py:119) are `list[dict]` —
  **position-only**. Sections/criteria are also position-based; criterion
  *name* is used only for display.
- **History.jsonl**: schema v3 — aggregates only (`pass_rate`, `mean_score`,
  tokens, `workspace_path`). Not usable for per-assertion audit.

## Architecture Review

| Area                 | Rating   | Finding |
|----------------------|----------|---------|
| **Data model**       | blocker  | **Stable assertion IDs.** Position-based matching is fragile: inserting or reordering one assertion in the spec invalidates all historical per-assertion data for that skill. Must introduce a stable identifier before writing L1/L2 results to disk — otherwise the auditor will give wrong answers after any spec edit. |
| **Data model**       | concern  | **Back-compat with pre-#25 iteration dirs.** Existing `iteration-*/` have no `assertions.json`/`extraction.json`. The auditor must degrade gracefully (warn + skip) rather than crash. |
| **Data model**       | concern  | **L2 not invoked in cmd_grade today.** Scope C requires wiring Layer 2 into `cmd_grade` so extraction results get persisted alongside grading.json. This is a behavior change for every `clauditor grade` run, not just the audit code path. |
| **API / CLI design** | concern  | **`--baseline` cost.** Enabling baseline doubles skill invocations and L1/L2/L3 work per grade run. Must be explicit opt-in; not default. Needs clear docs that history is only useful for baseline comparison on runs captured with `--baseline`. |
| **Atomic write**     | pass     | Writing new sidecars (`assertions.json`, `extraction.json`, `baseline.json`) during the staging phase before `workspace.finalize()` is the established pattern — slots cleanly at cli.py:440-460. |
| **Security / injection** | pass | Offline analysis command, no new LLM prompts, no untrusted web input. New persisted fields are internal data shapes, not prompts. |
| **Performance**      | pass     | Audit reads ≤20 iteration dirs × small JSON files. O(N × assertions) — trivial. No DB, no network. |
| **Observability**    | pass     | CLI command; stdout + exit code are the observability surface. Non-zero exit when any assertion is flagged (matches ticket intent for CI use). |
| **Testing**          | concern  | **Fixture burden.** Integration tests need realistic `iteration-*/` trees with sidecar jsons for multiple runs and a mixture of always-pass / discriminating / failing assertions. Plan reusable `_make_iteration_fixture()` helper in `tests/conftest.py`. |
| **Back-compat — history.jsonl** | pass | No schema bump required; audit reads iteration dirs, not history. |

### Blockers (must resolve before refinement)

**BLOCKER-1 — Stable assertion identifiers.** Before persisting L1/L2 results,
each assertion must have a stable id. Options:
- **(a)** Require an explicit `id` field on each assertion/section/criterion in
  the EvalSpec YAML (breaking-change; every existing spec updated).
- **(b)** Synthesize a content-hash id: sha256 of the assertion dict. Stable
  across insertion/reordering as long as the assertion itself isn't edited;
  edit = new id = history resets for that assertion (correct semantics).
- **(c)** Hybrid: honor explicit `id` if present, else synthesize content-hash.
  Non-breaking; authors can pin when they want history to survive edits.

Recommendation: **(c)**. Non-breaking, correct defaults, author has an escape
hatch when they want to rename an assertion without losing history.

## Refinement

### Decisions

- **DEC-001 — Stable IDs:** Require explicit `id` on every L1 assertion, L2
  field (inside each SectionRequirement / TierRequirement / FieldRequirement),
  and L3 criterion in EvalSpec YAML. Validated in `EvalSpec.from_file()` —
  missing id or duplicate-within-skill is a hard error. Pre-release, so all
  existing eval specs in this repo are updated as part of US-001.
- **DEC-002 — Pre-audit iteration dirs:** Auditor silently skips any
  `iteration-*/` dir missing the expected sidecar jsons, printing a single
  info line (`"skipped N iteration dirs without assertion data"`).
- **DEC-003 — L2 wiring in cmd_grade:** Bundle into this ticket. `cmd_grade`
  now invokes `grader.extract_and_grade()` (when the spec declares L2
  sections) and persists per-field results to `extraction.json`.
- **DEC-004 — Baseline storage:** Sidecar files inside the same
  `iteration-N/<skill>/` dir: `baseline.json` (run metadata/output ref),
  `baseline_assertions.json`, `baseline_extraction.json`,
  `baseline_grading.json`. Paired with-vs-without data lives together.
  `--baseline` flag on `cmd_grade` is explicit opt-in; never default.
- **DEC-005 — Thresholds:** Flag assertion if any of: (i) 100% pass across
  last N, (ii) zero recorded failures, (iii) |with − baseline| pass-rate
  delta < 0.05. CLI overrides: `--min-fail-rate FLOAT`,
  `--min-discrimination FLOAT`, `--last N` (default 20).
- **DEC-006 — Report output:** Markdown at `.clauditor/audit/<skill>-<ts>.md`
  + stdout summary table. Exit 1 if any assertion flagged (CI gate). `--json`
  emits to stdout instead of writing markdown.
- **DEC-007 — Unified "assertion" in report:** L1, L2, L3 results all appear
  in one report grouped by layer, each row keyed by its explicit id and
  layer tag.

## Detailed Breakdown

Natural ordering: schema foundation → L1/L2 persistence → baseline capture →
audit command → report rendering → quality gate → memory.

---

### US-001 — Stable `id` fields on EvalSpec assertions/sections/criteria

**Description:** Introduce required explicit `id` on every L1 assertion, L2
field, and L3 criterion. Validate uniqueness at load time. Update every
eval spec in the repo to the new schema. Pre-release, so no migration path.

**Traces to:** DEC-001

**Files:**
- `src/clauditor/schemas.py` — add `id: str` to assertion dict validation,
  `FieldRequirement`, criterion entries. Add uniqueness validation in
  `EvalSpec.from_file()`.
- `tests/test_schemas.py` — new cases: missing id errors, duplicate id within
  skill errors, valid spec loads.
- All `eval-specs/**/*.yaml` (or equivalent paths) — add `id:` to every
  assertion, field, and criterion.

**TDD:**
- `test_assertion_missing_id_rejected`
- `test_assertion_duplicate_id_rejected`
- `test_field_requirement_missing_id_rejected`
- `test_criterion_missing_id_rejected`
- `test_valid_spec_with_ids_loads`

**Acceptance criteria:**
- `EvalSpec.from_file()` raises `ValueError` with clear path (e.g.
  `"assertions[2]: missing 'id'"`) on any missing or duplicate id.
- Every eval spec in the repo loads under the new schema.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with
  ≥80% gate.

**Done when:** schema enforces ids, tests cover the validation paths,
repo specs updated, quality gate green.

**Depends on:** none

---

### US-002 — Persist Layer 1 assertion results as `assertions.json`

**Description:** Capture the `AssertionSet` produced by `spec.run()` and
write it to `iteration-N-tmp/<skill>/assertions.json` during the staging
phase of `cmd_grade`, keyed by the new stable ids. Include per-result
`id`, `passed`, `message`, `kind`, `evidence`.

**Traces to:** DEC-001, DEC-007

**Files:**
- `src/clauditor/assertions.py` — ensure `AssertionResult` carries the
  stable `id`; add `AssertionSet.to_json()` serializer.
- `src/clauditor/spec.py` — plumb the `AssertionSet` out of `spec.run()`
  instead of discarding (may already return it — verify).
- `src/clauditor/cli.py` — in `_cmd_grade_with_workspace` (cli.py:273-547),
  write `tmp_path / "assertions.json"` alongside `grading.json` at ~L445.
- `tests/test_cli.py` (or `test_spec.py`) — fixture iteration dir round-trips
  `assertions.json` with stable ids.

**TDD:**
- `test_cmd_grade_writes_assertions_json`
- `test_assertions_json_keyed_by_stable_id`
- `test_assertion_set_roundtrip_json`

**Acceptance:**
- After `clauditor grade <skill>`, `iteration-N/<skill>/assertions.json`
  exists and contains every L1 assertion result keyed by its spec id.
- Variance runs each get their own assertions.json entry (array or per-run).
- Pytest green, coverage ≥80%.

**Done when:** cmd_grade persists L1 results; tests verify shape.

**Depends on:** US-001

---

### US-003 — Wire Layer 2 into `cmd_grade`, persist `extraction.json`

**Description:** `cmd_grade` currently skips Layer 2. Invoke
`grader.extract_and_grade()` when the spec declares `sections` and persist
per-field pass/fail + score to
`iteration-N-tmp/<skill>/extraction.json`, keyed by field id.

**Traces to:** DEC-003, DEC-007

**Files:**
- `src/clauditor/cli.py` — in `_cmd_grade_with_workspace`, after primary
  run, call `extract_and_grade(primary_output, spec.sections)`, persist.
- `src/clauditor/grader.py` — add `ExtractionReport.to_json()` (or reuse
  AssertionSet.to_json()) carrying field ids.
- `tests/test_cli.py` — fixture spec with sections, assert extraction.json
  written.

**TDD:**
- `test_cmd_grade_invokes_layer2_when_sections_declared`
- `test_cmd_grade_skips_layer2_when_no_sections`
- `test_extraction_json_keyed_by_field_id`

**Acceptance:**
- After `clauditor grade <skill>` on a spec with sections,
  `extraction.json` exists with per-field records keyed by field id.
- Spec without sections does not produce extraction.json (no wasted work).
- Pytest green, coverage ≥80%.

**Done when:** L2 runs and persists during cmd_grade; tests cover both
branches.

**Depends on:** US-001

---

### US-004 — `--baseline` flag on `cmd_grade`, capture baseline sidecars

**Description:** Add opt-in `--baseline` to `clauditor grade`. When set,
after the primary (with-skill) run, invoke `runner.run_raw()` to produce a
baseline output, then run L1 + L2 + L3 against that output. Persist
`baseline.json` (metadata), `baseline_assertions.json`,
`baseline_extraction.json`, `baseline_grading.json` into the same
`iteration-N/<skill>/` staging dir.

**Traces to:** DEC-004

**Files:**
- `src/clauditor/cli.py` — add `--baseline` arg to `p_grade`; new helper
  `_run_baseline_phase(spec, primary_result, workspace)`; integrate before
  finalize.
- `src/clauditor/runner.py` — confirm `run_raw` returns the same SkillResult
  shape suitable for passing through L1/L2/L3.
- `src/clauditor/quality_grader.py` — no changes expected; `grade_quality()`
  takes raw output.
- `tests/test_cli.py` — test `--baseline` writes all 4 baseline sidecars;
  test default (no flag) writes none.

**TDD:**
- `test_grade_without_baseline_flag_writes_no_baseline_files`
- `test_grade_with_baseline_flag_writes_all_baseline_sidecars`
- `test_baseline_results_keyed_by_same_ids_as_primary`

**Acceptance:**
- `clauditor grade <skill> --baseline` produces 4 baseline_*.json sidecars in
  the iteration dir.
- Omitting `--baseline` preserves current behavior exactly.
- Baseline LLM calls are only made when flag is set (no accidental cost).
- Pytest green, coverage ≥80%.

**Done when:** flag wired, baseline sidecars persisted, tests cover both
paths, no-flag path is bit-for-bit unchanged.

**Depends on:** US-002, US-003

---

### US-005 — `clauditor audit` command: CLI + iteration dir reader + aggregation

**Description:** New subcommand that scans the last N iteration dirs for a
skill, loads `assertions.json`, `extraction.json`, `grading.json` (and
baseline_*.json when present), aggregates per-id pass rates, and hands the
aggregate to a reporter (US-006). Silently skips dirs missing sidecars
(DEC-002).

**Traces to:** DEC-002, DEC-005, DEC-007

**Files:**
- `src/clauditor/cli.py` — register `p_audit = subparsers.add_parser("audit")`
  with args: `skill` (positional), `--last N` (default 20),
  `--min-fail-rate`, `--min-discrimination`, `--json`, `--output-dir`. Add
  `cmd_audit(args)` and `elif parsed.command == "audit":` dispatch.
- `src/clauditor/audit.py` — **new module** containing:
  - `IterationRecord` dataclass (per-iteration per-assertion pass/fail,
    by layer + id).
  - `load_iterations(skill: str, last: int) -> list[IterationRecord]` —
    reads iteration dirs, handles missing files gracefully.
  - `AuditAggregate` dataclass: per-id aggregate across N runs (with_rate,
    baseline_rate, total_runs, total_fails).
  - `aggregate(records) -> dict[(layer, id), AuditAggregate]`.
- `tests/test_audit.py` — **new file** — fixture builder
  `_make_iteration_fixture(tmp_path, skill, per_run_results)`; tests for
  loader + aggregator.

**TDD:**
- `test_load_iterations_last_n_ordered_newest_first`
- `test_load_iterations_skips_dirs_missing_sidecars`
- `test_load_iterations_returns_empty_when_no_data`
- `test_aggregate_computes_with_rate_per_id`
- `test_aggregate_computes_baseline_rate_when_baseline_present`
- `test_aggregate_groups_results_by_layer_and_id`

**Acceptance:**
- `clauditor audit <skill>` runs without error on a real iteration tree.
- Skipping behavior logged once per run (not per dir).
- Aggregate handles mixed (some-with-baseline, some-without) iteration sets.
- Pytest green, coverage ≥80%.

**Done when:** audit command loads + aggregates; reporter stub prints
aggregate count.

**Depends on:** US-002, US-003

---

### US-006 — Audit thresholds, markdown report, stdout summary, `--json`, exit code

**Description:** Apply the three flagging rules (DEC-005) to the aggregate
from US-005. Render a markdown report to
`.clauditor/audit/<skill>-<ts>.md` with a "Suggest removal" section, plus
a compact stdout table. With `--json`, emit machine form to stdout instead
of writing the markdown file. Exit code 1 if any assertion flagged.

**Traces to:** DEC-005, DEC-006, DEC-007

**Files:**
- `src/clauditor/audit.py` — add:
  - `Verdict` enum (keep / flag-always-pass / flag-no-discrimination /
    flag-zero-failures).
  - `apply_thresholds(aggregate, min_fail_rate, min_discrimination) ->
    dict[..., Verdict]`.
  - `render_markdown(aggregate, verdicts, skill, n) -> str`.
  - `render_stdout_table(aggregate, verdicts) -> str`.
  - `render_json(aggregate, verdicts) -> dict`.
- `src/clauditor/cli.py` — finish `cmd_audit`: write markdown (unless
  `--json`), print stdout summary, return exit code.
- `tests/test_audit.py` — rule application tests, markdown snapshot test,
  json shape test.

**TDD:**
- `test_threshold_flags_100_percent_pass`
- `test_threshold_flags_zero_failures`
- `test_threshold_flags_low_discrimination_when_baseline_present`
- `test_threshold_passes_discriminating_assertion`
- `test_threshold_override_via_cli_args`
- `test_render_markdown_contains_suggest_removal_section`
- `test_render_json_shape_stable`
- `test_cmd_audit_exit_1_when_any_flagged`
- `test_cmd_audit_exit_0_when_all_clean`

**Acceptance:**
- Markdown file written to `.clauditor/audit/<skill>-<ts>.md` with clear
  "Suggest removal" list + per-assertion detail.
- `--json` emits to stdout, no file written.
- Exit 1 iff any flag fires.
- CLI threshold overrides work as documented.
- Pytest green, coverage ≥80%.
- Running `clauditor audit find-restaurants` on a real fixture produces the
  canonical example output from the ticket ("suggests dropping them").

**Done when:** ticket's "Done when" clause is literally satisfiable.

**Depends on:** US-005

---

### US-007 — Quality Gate (code review ×4 + CodeRabbit)

**Description:** Run `/code-review` (or code-reviewer agent) four times
across the full changeset, fixing all real bugs found each pass. Run
CodeRabbit review if available. Rerun full quality gate:
`uv run ruff check src/ tests/` and
`uv run pytest --cov=clauditor --cov-report=term-missing` ≥80%.

**Traces to:** all DECs (final validation)

**Acceptance:**
- 4 review passes completed; each real finding either fixed or documented
  as a false positive with justification.
- CodeRabbit review addressed.
- Ruff + pytest ≥80% green.

**Done when:** all gates green, no outstanding findings.

**Depends on:** US-001, US-002, US-003, US-004, US-005, US-006

---

### US-008 — Patterns & Memory

**Description:** Capture patterns learned during this ticket into
`.claude/rules/`. Likely new rules:
- Stable-ids-for-eval-specs pattern (why position-based was fragile).
- Sidecar-during-staging pattern (how new per-iteration files are persisted
  before `workspace.finalize()`).

Update any memory worth preserving across sessions (non-obvious facts
about audit/baseline workflow).

**Traces to:** DEC-001, DEC-004

**Acceptance:**
- At least one new `.claude/rules/*.md` file capturing the stable-id pattern.
- No stale references to position-based assertion IDs in rules/docs.

**Done when:** rules committed alongside the code changes.

**Depends on:** US-007

## Beads Manifest

- **Epic:** clauditor-qcg
- **Worktree / branch:** `feature/25-assertion-auditor`
- **PR:** #34 (draft)
- **Tasks:**
  - clauditor-dmo — US-001 Stable ids
  - clauditor-k60 — US-002 Persist L1 assertions.json (deps: US-001)
  - clauditor-eid — US-003 Wire L2 + extraction.json (deps: US-001)
  - clauditor-b9o — US-004 `--baseline` flag + sidecars (deps: US-002, US-003)
  - clauditor-jth — US-005 audit command loader+aggregator (deps: US-002, US-003)
  - clauditor-8qo — US-006 thresholds+reporter+exit code (deps: US-005)
  - clauditor-8dm — Quality Gate (deps: US-001..US-006)
  - clauditor-rbb — Patterns & Memory P4 (deps: Quality Gate)
