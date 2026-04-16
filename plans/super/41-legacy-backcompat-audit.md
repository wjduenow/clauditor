# Super Plan: #41 — Audit and remove legacy / backcompat code

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/41
- **Branch:** `feature/41-legacy-audit`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/41-legacy-audit`
- **Phase:** detailing
- **Sessions:** 1 (2026-04-15)

## Ticket summary

clauditor has never been published. Any backcompat artifact exists for a user who does not exist. Sweep the codebase, inventory every finding, get per-category approval, then plan removal. The audit IS the ticket — no code changes in Phase 1.

## Discovery — Inventory

Three parallel audit subagents (keyword sweep, structural sweep, docs sweep) produced the consolidated findings below. Each item has `file:line`, category, and confidence.

### A. Active rejection code for already-removed features

Code that hard-fails on old eval-spec shapes. In pre-publication context, the friendly error messages are unnecessary — nobody is migrating from them.

| # | Location | What | Confidence |
|---|---|---|---|
| A1 | `src/clauditor/schemas.py:114-124` (`_resolve_field_format`) | `raise ValueError("the 'pattern' key is no longer supported…")` — rejects removed `pattern` field with friendly migration message | high |
| A1t | `tests/test_schemas.py:675-690` | `test_from_file_legacy_pattern_key_rejected` | high |
| A1d | `README.md:606-609` | "Migration note (April 2026): The legacy `pattern` key on FieldRequirement has been removed" | high |
| A2 | `tests/test_cli.py:1334-1340` | `test_grade_save_flag_removed` — asserts argparse rejects `--save` | high |
| A2d | `README.md:613` | "`clauditor grade --save` has been removed" migration note | high |

### B. Legacy-format fallbacks in parsers/loaders

Defensive branches in sidecar/history readers that exist only to tolerate shapes no longer written anywhere.

| # | Location | What | Confidence |
|---|---|---|---|
| B1 | `src/clauditor/audit.py:162` | `rid = result.get("id") or result.get("name")` — fallback to pre-stable-id `name` key in L1 assertion sidecars | high |
| B2 | `src/clauditor/audit.py:194-200` | Fallback to `presence_passed`+`format_passed` when top-level `passed` missing in old extraction.json | high |
| B3 | `src/clauditor/assertions.py:64` + `tests/test_assertions.py:980-1044` | `AssertionResult.from_json_dict` "tolerates missing keys for older on-disk fixtures" (`transcript_path`) + two tests pinning the tolerance | high |
| B4 | `src/clauditor/history.py:9-28,77` + README.md:407 | Reader explicitly tolerates v1 (no schema_version, no command) and v2 (no iteration, no workspace_path) history records. Writer only emits v3. | high |
| B4t | `tests/test_cli.py:3809` + `tests/test_history.py:224` | Tests pinning mixed-version read behavior | high |
| B5 | `src/clauditor/spec.py:263-312` + `tests/test_schemas.py:73,1122-1148` | "Legacy fields-style: normalize to single default tier" — `EvalSpec.from_file` has a parallel parser for pre-tiers flat `fields` shape | high |
| B6 | `tests/test_cli.py:2867-2890` + README.md:395 | `test_compare_legacy_grade_json_files` + docs line describing `clauditor compare before.grade.json after.grade.json` — supports a sidecar format clauditor no longer writes | high |

### C. Optional dataclass fields kept for test-fixture convenience

Fields typed `| None = None` only because direct-construction fixtures skip them; production callers always set them.

| # | Location | What | Confidence |
|---|---|---|---|
| C1 | `src/clauditor/quality_grader.py:59,62` | `GradingReport.thresholds: GradeThresholds \| None` and `metrics: dict \| None` — always populated by `grade_quality()`, optional only for ~10 test fixtures | med |

### D. Grandfathered rule counter-examples

| # | Location | What | Confidence |
|---|---|---|---|
| D1 | `src/clauditor/cli.py::_run_baseline_phase` (~lines 552-649) + `.claude/rules/pure-compute-vs-io-split.md:114-118` + `.claude/rules/json-schema-version.md:67` | Bundles spec-resolve + subprocess + LLM grading + sidecar writes. Explicitly grandfathered in two rule files. Either refactor (extract pure compute helper) or accept as permanent and scrub the "grandfathered" framing from the rules. | high (finding), decision-needed (action) |

### E. Borderline / explicit non-findings

Noted for completeness; recommended **leave alone** unless user disagrees.

- `src/clauditor/quality_grader.py:128-151` — `GradingReport.from_json` defensive `.get(…, default)` defaults. Purpose is **crash/corruption tolerance**, not backcompat. Keep.
- `src/clauditor/cli.py:82-85,178-179,447-448` — `getattr(args, …, default)` patterns. Standard argparse defensive shape; not legacy.
- `src/clauditor/runner.py` stream-json permissive parsing. Governed by `.claude/rules/stream-json-schema.md` — intentional defensive-read of a third-party format. Keep.
- `src/clauditor/schemas.py:101` — `grading_criteria` tolerates plain strings "for in-memory construction in tests". Test-ergonomics, not backcompat. Keep unless user disagrees.
- `docs/stream-json-schema.md:10-11` — "verified live against `claude` 2.1.x" version pin note. Keep (it's a freshness marker, not legacy code).
- `plans/super/*.md` historical plan docs referencing "legacy" — archive material, do not touch.
- `pyproject.toml` Python lower bound — unremarkable.
- `src/clauditor/spec.py:76` `test_args` — active CLI-args source, not legacy. The `test_args` vs `user_prompt` split is complete; both fields are load-bearing.

## Categorization by removal risk

| Category | Items | Risk | Notes |
|---|---|---|---|
| **Safe-delete** | A1, A1t, A1d, A2, A2d, B1, B2, B3, B6 | low | Pure deletion; existing tests protect non-legacy paths. README lines go with them. |
| **Small refactor** | B4, B4t, B5, C1 | low-med | Drop parser branches + update affected tests/fixtures. B4 needs a `schema_version == 3` hard check per `.claude/rules/json-schema-version.md`. B5 needs eval-spec fixtures rewritten to tiered shape. C1 needs ~10 test fixtures updated to pass required fields. |
| **Architectural decision** | D1 | med | `_run_baseline_phase` refactor is non-trivial (it coordinates subprocess + grading + sidecar atomicity). User decides: refactor now, or scrub "grandfathered" language and accept as permanent. |
| **Leave alone** | E items | — | Not legacy. |

## Scope question (Deliverable 4)

The ticket asks whether this should be split across multiple issues. **Recommendation: one epic, ~4-5 stories, manageable in a single ticket.**

- Safe-delete category is mechanical and can be one story
- Small-refactor items each get one story (B4, B5, C1)
- D1 gets its own story only if the user chooses "refactor now"

Total: 4 stories if D1 is deferred, 5 if refactored. All fit within #41 without a split.

## Approval checkpoint

**Awaiting user go/no-go on each category.** No code changes until approval. Once approved, I'll run Phase 2 (architecture review — likely a light pass given mechanical nature) and Phase 3+ (decisions, story breakdown, PR, devolve to beads).

### Specific questions for the user

1. **Safe-delete (A + B1/B2/B3/B6):** approve bulk removal? (A/B: A=yes, B=hold any specific item)
2. **B4 history v1/v2:** remove tolerance and enforce `schema_version == 3`? Any chance there are real `.clauditor/history.jsonl` files in your working dirs with v1/v2 records you want to preserve?
3. **B5 flat-fields eval-spec parser:** remove? Any eval-spec files outside the repo (in your experiment dirs) that still use the flat `fields` shape instead of `tiers`?
4. **C1 `GradingReport` optional fields:** make required? Tolerable to update ~10 test fixtures.
5. **D1 `_run_baseline_phase`:**
   - (a) refactor now into pure compute + thin I/O wrapper (one more story, medium effort), OR
   - (b) accept as permanent architectural exception and scrub "grandfathered" framing from the two rule files
6. **E items:** confirm leave-alone, or call out anything you want included?

## Decisions

- **DEC-001** — Bulk-delete Category A (pattern-key rejection code + `test_from_file_legacy_pattern_key_rejected` + README migration note; `test_grade_save_flag_removed` + `--save` README note). *Rationale:* No pre-existing users to migrate; the friendly error and grave-marker test are dead weight.
- **DEC-002** — Drop Category B fallback branches: `audit.py:162` id-or-name, `audit.py:194-200` presence/format fallback, `assertions.py::from_json_dict` missing-key tolerance, `test_compare_legacy_grade_json_files` + README `.grade.json` reference. *Rationale:* No on-disk sidecars predate the current shape (user has no working dirs).
- **DEC-003** — Drop history v1/v2 tolerance; `history.py` reader enforces `schema_version == 3` per `.claude/rules/json-schema-version.md`. *Rationale:* User confirmed no legacy history.jsonl files exist anywhere.
- **DEC-004** — Drop flat-`fields` parser in `spec.py::EvalSpec.from_file`; tiered shape becomes the only accepted form. *Rationale:* User confirmed no external eval-specs use the flat shape.
- **DEC-005** — Make `GradingReport.thresholds` and `metrics` required (non-Optional) fields. Update all direct-construction test fixtures to populate them. *Rationale:* Production callers always set them; optionality exists only for test ergonomics, which is a weak reason.
- **DEC-006** — Refactor `_run_baseline_phase` into a pure `compute_baseline(...)` helper returning a dataclass + thin CLI I/O wrapper, following `.claude/rules/pure-compute-vs-io-split.md`. Scrub the "Grandfathered counter-example" section from that rule AND the `_run_baseline_phase` loader reference from `.claude/rules/json-schema-version.md:67`. *Rationale:* User chose refactor over permanent exception; eliminates the only documented rule-violator in the codebase.
- **DEC-007** — Sweep README.md for any lingering legacy references after US-001/002 have touched it: the Migration Notes section header itself, `.grade.json` compare example, history v2 mention, pattern→format note. *Rationale:* Explicit user ask in approval checkpoint.

## Detailed Breakdown

Stories are sequenced linearly because several touch README.md and risk merge churn if parallelized. Each is sized for a single Ralph context window.

### US-001 — Safe-delete bulk removal

**Description:** Delete pattern-key rejection, `--save` grave-marker test, legacy sidecar fallbacks in `audit.py` and `assertions.py`, and the `.grade.json` compare test. Pure mechanical deletion; no behavior changes on current on-disk shapes.

**Traces to:** DEC-001, DEC-002

**Files:**
- `src/clauditor/schemas.py:114-124` — delete `if "pattern" in field_dict: raise ValueError(...)` block in `_resolve_field_format`; simplify docstring
- `src/clauditor/audit.py:162` — replace `result.get("id") or result.get("name")` with `result["id"]` (hard required)
- `src/clauditor/audit.py:194-200` — replace fallback block with `passed = bool(entry["passed"])` (hard required)
- `src/clauditor/assertions.py:64` + surrounding `from_json_dict` — remove "tolerates missing keys" behavior; require `transcript_path`
- `tests/test_schemas.py:675-690` — delete `test_from_file_legacy_pattern_key_rejected`
- `tests/test_cli.py:1334-1340` — delete `test_grade_save_flag_removed`
- `tests/test_cli.py:2867-2890` — delete `test_compare_legacy_grade_json_files`
- `tests/test_assertions.py:980-1044` — delete `test_from_json_dict_missing_key_tolerant` and `test_assertion_set_from_json_back_compat`
- `README.md:606-609` — delete "Migration note (April 2026)" for pattern→format
- `README.md:613` — delete `--save` removal bullet
- `README.md:395` — remove the "Diff two legacy grade reports" example line

**Acceptance criteria:**
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% coverage
- No remaining references to "pattern" key rejection, `--save` flag, or `.grade.json` file inputs
- `grep -rn "pattern.*no longer supported"` returns zero hits

**Done when:** Tests green, files shrunk, README rendered preview has no lingering migration-note for pattern or `--save`.

**Depends on:** none

**Rules:** `json-schema-version.md` (tightening is allowed), `eval-spec-stable-ids.md` (removing `name` fallback enforces id-only)

### US-002 — Enforce history.jsonl schema v3

**Description:** Drop v1/v2 tolerance in `history.py`. Reader validates `schema_version == 3` and hard-skips (with stderr warning) anything else, matching the pattern in `.claude/rules/json-schema-version.md`.

**Traces to:** DEC-003

**TDD:**
1. Write failing test: feeding a v2 record (no `iteration`/`workspace_path`) to `load_iterations` returns `[]` and emits stderr warning naming `schema_version=2`
2. Write failing test: feeding a v1 record (no `schema_version`) returns `[]` with warning
3. Write failing test: v3 record still loads successfully
4. Implement `_check_schema_version` helper in `history.py`; wire into the reader
5. Delete obsolete mixed-version tolerance docstring (history.py:9-28)

**Files:**
- `src/clauditor/history.py` — add `_SCHEMA_VERSION = 3`, add `_check_schema_version` helper, call from reader; delete mixed-version tolerance prose in module docstring
- `tests/test_history.py:224` — replace `TestMixedVersions` with v2/v1-rejection tests per TDD list
- `tests/test_cli.py:3809` — delete `test_trend_renders_mixed_v2_v3_history` (or rewrite to assert warning + skip)
- `README.md:407` — rewrite "Legacy v2 records still read cleanly" to "All history records are schema v3"

**Acceptance criteria:**
- `uv run pytest tests/test_history.py tests/test_cli.py -q` passes
- `grep -rn "v2.*history" README.md src/` returns zero hits
- Full test suite still ≥80% coverage

**Done when:** Reader hard-requires v3; README no longer promises v2 compat.

**Depends on:** US-001

**Rules:** `json-schema-version.md` (canonical loader pattern)

### US-003 — Drop flat-fields eval-spec parser

**Description:** Remove the "Legacy fields-style: normalize to single default tier" branch in `EvalSpec.from_file`. Tiered shape becomes the only accepted form; `fields` at section level is a `ValueError`.

**Traces to:** DEC-004

**TDD:**
1. Failing test: `EvalSpec.from_file` on a spec with section-level `fields` raises `ValueError("sections[i]: expected 'tiers' key …")`
2. Failing test: existing tiered-shape spec still loads (regression guard)
3. Remove the `else` branch in `spec.py:263-312`; simplify the loader
4. Rewrite `tests/test_schemas.py::test_legacy_fields_normalized_to_default_tier` → `test_from_file_rejects_flat_fields_shape`
5. Audit `tests/fixtures/` and `examples/` for any flat-`fields` spec files; convert or delete

**Files:**
- `src/clauditor/spec.py:263-312` — delete legacy branch, keep only the `"tiers" in s` path
- `src/clauditor/schemas.py:291` — delete "Legacy fields-style" comment
- `tests/test_schemas.py:73` — remove legacy comment
- `tests/test_schemas.py:1122-1148` — rewrite test per TDD step 4
- Any fixture/example using flat `fields` — convert to tiered shape

**Acceptance criteria:**
- `uv run pytest tests/test_schemas.py tests/test_spec.py -q` passes
- `grep -rn "legacy.*fields\|fields-style" src/ tests/` returns zero hits
- Full suite ≥80% coverage

**Done when:** Only tiered eval-specs load; rejection is explicit.

**Depends on:** US-002

**Rules:** `eval-spec-stable-ids.md`

### US-004 — GradingReport required fields

**Description:** Make `GradingReport.thresholds` and `metrics` non-Optional. Update every direct-construction fixture in tests to pass valid values.

**Traces to:** DEC-005

**TDD:**
1. Failing test: `GradingReport(...)` without `thresholds` raises `TypeError` (dataclass missing arg)
2. Update fixture factory (likely `tests/conftest.py` or a helper) to always pass `thresholds=GradeThresholds(...)` and `metrics={}`
3. Remove `| None` from the dataclass fields; delete `None` defaults
4. Fix any now-broken test construction sites

**Files:**
- `src/clauditor/quality_grader.py:59,62` — change type to non-Optional, remove `= None`
- `tests/test_quality_grader.py` — update fixture factories and direct constructions (~10 sites per audit)
- `tests/conftest.py` — if shared fixture exists, update there first
- Check `tests/test_cli.py` and `tests/test_comparator.py` for any fallout

**Acceptance criteria:**
- `uv run pytest -q` passes (~10 fixture sites updated)
- `grep -n "thresholds.*None\|metrics.*None" src/clauditor/quality_grader.py` returns zero hits
- Coverage ≥80%

**Done when:** `GradingReport` fields are structurally required; no `| None` dead branch in readers.

**Depends on:** US-003

**Rules:** none specific

### US-005 — Refactor `_run_baseline_phase` into pure compute + thin I/O

**Description:** Extract a `compute_baseline(...)` pure function from `cli.py::_run_baseline_phase` that returns a dataclass (e.g. `BaselineReports`), and keep only file I/O + stderr progress in the CLI wrapper. Scrub "grandfathered" framing from the two rule files. This is the plan's architectural story.

**Traces to:** DEC-006

**Design notes:**
- Model after `compute_benchmark` (canonical impl in `.claude/rules/pure-compute-vs-io-split.md`)
- Pure helper takes already-run `SkillResult`s + `SkillSpec` + `EvalSpec`, returns a dataclass of baseline reports
- CLI wrapper handles: subprocess invocation (`runner.run(...)`), `.write_text(...)` for `baseline_grading.json` / `baseline_assertions.json` / `baseline_extraction.json`, and stderr progress lines
- Must respect `.claude/rules/sidecar-during-staging.md`: writes happen inside `workspace.tmp_path` before `workspace.finalize()`
- `schema_version` stays on the dataclass's `to_json()`, not inlined

**TDD:**
1. Failing unit test for `compute_baseline(...)` returning a dataclass with the right aggregated shape — no tmp_path, no subprocess, no file I/O
2. Failing test for `BaselineReports.to_json()` emitting `schema_version: 1` as first key
3. Implement the pure helper
4. Rewrite `_run_baseline_phase` as thin I/O wrapper delegating to the helper
5. Existing integration tests for the `--baseline` flag must still pass unchanged
6. Delete "Grandfathered counter-example" section from `.claude/rules/pure-compute-vs-io-split.md:114-118`
7. Delete `_run_baseline_phase` writer-mention from `.claude/rules/json-schema-version.md:67`

**Files:**
- `src/clauditor/cli.py:552-649` — split into pure helper + thin wrapper
- new `src/clauditor/baseline.py` (or put helper in `cli.py` above wrapper if sibling module feels heavy — prefer sibling module for testability)
- `tests/test_baseline.py` (new) — pure compute tests, no fixtures
- `tests/test_cli.py` — keep existing `--baseline` integration coverage
- `.claude/rules/pure-compute-vs-io-split.md` — remove grandfathered section; add `compute_baseline` as a second canonical implementation anchor
- `.claude/rules/json-schema-version.md:67` — remove `_run_baseline_phase` from writer list; add new helper

**Acceptance criteria:**
- `uv run pytest tests/test_baseline.py tests/test_cli.py -q` passes
- `grep -rn "grandfathered" .claude/rules/` returns zero hits
- `_run_baseline_phase` (if kept as name) is ≤30 lines and only calls the pure helper + writes files
- Pure helper has no `Path` I/O, no subprocess, no `print`
- Coverage ≥80%

**Done when:** Rule violator eliminated; `.claude/rules/pure-compute-vs-io-split.md` has no "grandfathered counter-example" section.

**Depends on:** US-004

**Rules:** `pure-compute-vs-io-split.md` (applied), `sidecar-during-staging.md`, `json-schema-version.md`

### US-006 — README.md final sweep

**Description:** After US-001/002 have touched README, do a consolidated pass for internal consistency: delete the "Migration Notes" section header if it's empty, scrub any other stray "legacy"/"deprecated"/"migration" references, and verify the doc reads cleanly end-to-end.

**Traces to:** DEC-007

**Files:**
- `README.md` — grep-then-read pass for: "legacy", "deprecated", "migration", "pattern key", "`--save`", "`.grade.json`", "v2 records"

**Acceptance criteria:**
- `grep -nE "legacy|deprecat|migrat|pre-v1|backcompat" README.md` returns zero hits (or only clearly non-legacy hits like "migration" in a migration-file-handling context, if any)
- Manual readthrough confirms no dangling references to removed features
- `uv run pytest -q` still passes (no code changes expected, but safety check)

**Done when:** README has no backcompat prose. Ready for eventual publication.

**Depends on:** US-005

**Rules:** none

### QG-001 — Quality Gate

**Description:** Run `code-reviewer` 4 times across the full changeset for US-001..006, fixing every real bug each pass. Run CodeRabbit if available. `uv run ruff check src/ tests/` and `uv run pytest --cov=clauditor --cov-report=term-missing` must both pass with ≥80% coverage after final fix pass.

**Depends on:** US-006

### PM-001 — Patterns & Memory

**Description:** Update `.claude/rules/pure-compute-vs-io-split.md` to reflect `compute_baseline` as a new canonical anchor (done in US-005, but verify). If any new invariant emerged during the audit/removal work, capture it as a new rule. Save any surprising findings as `bd remember` entries. Priority 99.

**Depends on:** QG-001

## Rules compliance check

- `pure-compute-vs-io-split.md` — US-005 applies this pattern; removes the counter-example. ✓
- `json-schema-version.md` — US-002 adds `_check_schema_version` to history reader; US-005 ensures `BaselineReports` dataclass emits `schema_version` as first key. ✓
- `eval-spec-stable-ids.md` — US-001 (audit.py id-only) and US-003 (tiered-only) enforce stable-id invariants harder. ✓
- `sidecar-during-staging.md` — US-005 preserves the staging-before-finalize contract. ✓
- `llm-judge-prompt-injection.md` — no LLM prompt changes. N/A
- `pytester-inprocess-coverage-hazard.md` — no pytester tests added. N/A
- All other rules — no impact.

No story violates any rule.

## Beads Manifest

*(populated in Phase 7)*

## Session Notes

**2026-04-15 — Session 1:** Discovery phase. Fetched #41, created worktree, ran three parallel audit subagents (keyword, structural, docs). Consolidated 17 primary findings into 4 action categories + 7 explicit non-findings. Awaiting approval checkpoint before Phase 2.
