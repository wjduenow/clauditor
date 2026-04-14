---
ticket: 22
title: Per-iteration workspace layout for eval runs
phase: devolved
sessions: 1
---

# 22 — Per-iteration workspace layout for eval runs

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/22
- **Phase:** devolved (all stories merged; Quality Gate + Patterns & Memory complete)
- **Branch:** feature/22-iteration-workspace

## Context

Today `clauditor grade <skill>` writes a single `.clauditor/<skill>.grade.json`, overwritten each run. Skill stdout lives only in memory. We want each run preserved as `iteration-N/` so users can re-inspect, re-grade, and root-cause regressions.

## Discovery findings

- `cmd_grade` (cli.py:130–363) writes `.clauditor/<skill>.grade.json` under `--save`, overwrites each run.
- Skill stdout is in-memory only (`SkillResult.output`, runner.py:55) — never persisted today.
- Timing/tokens (#21) already embedded in `.grade.json` and `history.jsonl` — no separate `timing.json` exists.
- `clauditor compare` reads `.txt` or `.grade.json` via `_load_assertion_set` (cli.py:366–405, comparator.py).
- `history.jsonl` schema v2: `{schema_version, command, ts, skill, pass_rate, mean_score, metrics}`.
- README flags per-iteration workspace as deliberately out of scope until #22.
- No `.claude/rules/`, no `workflow-project.md`, pre-production (no backwards-compat constraint).

## Scoping decisions (Phase 1)

- **DEC-001 — Iteration numbering:** Hybrid. Auto-increment by default (scan `.clauditor/iteration-*`, pick max+1); `--iteration N` overrides. Rationale: ergonomic default, explicit escape hatch.
- **DEC-002 — `--save` flag:** Remove it. Every `grade` run writes `iteration-N/` and appends to `history.jsonl`. Rationale: pre-production, no users to break; the point of #22 is "stop losing runs." Add `--no-save` later if clutter appears.
- **DEC-003 — Directory layout:** Simplified. `.clauditor/iteration-N/<skill>/{output.txt, grading.json, timing.json}`. No with/without split (that's #23). Rationale: matches today's single-run model; extensible for #23.
- **DEC-004 — `compare` inputs:** Dual mode. Accept iteration dirs (`compare .clauditor/iteration-1 .clauditor/iteration-2`) OR short numeric refs (`compare --skill foo 1 2`). Rationale: dirs for explicit cross-skill use; numbers for common same-skill case.
- **DEC-005 — history.jsonl iteration field:** Add `iteration: N` and `workspace_path: ".clauditor/iteration-N/<skill>"`. Bump to schema v3. Rationale: trend queries can link back to raw artifacts.

## Architecture Review (Phase 2)

| Area | Rating | Finding |
|---|---|---|
| Concurrent grade runs | blocker→resolved | TOCTOU on auto-increment; history.jsonl append not atomic. Fix: DEC-006. |
| `compare` numeric refs | blocker→resolved | `compare` positional args parse as paths. Fix: DEC-007. |
| Partial writes | concern→resolved | 3-file sequence non-atomic. Fix: DEC-012 (tmp+rename). |
| `--iteration N` collision | concern→resolved | DEC-008 (error + `--force`). |
| Path resolution (CWD-relative) | concern→resolved | DEC-009 (walk-up repo root). |
| history.jsonl v2→v3 | concern→resolved | DEC-013 (graceful mixed readback). |
| Disk usage | pass | `SkillResult.output` already in memory. |
| Other commands (trend/doctor/extract/init/pytest_plugin) | pass | None read `.grade.json`. |
| Variance semantics | resolved | DEC-011 (per-run subdirs + aggregated grading.json). |
| Tests | concern | ~21 refs in test_cli.py, ~4 methods to rewrite, ~8–10 new cases. |

## Refinement Log (Phase 3)

### Decisions

- **DEC-001 — Iteration numbering.** Auto-increment by default (scan `.clauditor/iteration-*`, pick max+1); `--iteration N` overrides. _Phase 1._
- **DEC-002 — Remove `--save` flag.** Every `grade` run persists. Pre-production; the point of #22 is "stop losing runs." _Phase 1._
- **DEC-003 — Workspace layout.** `.clauditor/iteration-N/<skill>/` containing aggregated `grading.json` + `timing.json` at the skill level, and `run-K/{output.txt, output.jsonl}` per variance run. Single-run grade still uses `run-0/` for consistency. _Phase 1 + Q11._
- **DEC-004 — `compare` inputs.** Dual: file or directory paths (auto-detected), OR `--skill foo --from N --to M` for same-skill numeric refs. _Phase 1 + Q7._
- **DEC-005 — history.jsonl schema v3.** Add `iteration` and `workspace_path` fields. _Phase 1._
- **DEC-006 — Concurrency (Q6=A).** Optimistic atomic `mkdir(iteration-N)`, catch `FileExistsError`, retry with N+1. Wrap `history.append_record()` in `fcntl.flock` on `.clauditor/.lock`. No global lock on the grade run.
- **DEC-007 — `compare` shape (Q7=A).** Auto-detect: if positional arg is a dir, read `grading.json` from within; if file, current behavior. Alternate form: `--skill foo --from N --to M`. No new subcommand.
- **DEC-008 — Collision policy (Q8).** `--iteration N` where `iteration-N/` exists → hard error with message "iteration-N already exists; use --force to overwrite". `--force` required to replace.
- **DEC-009 — Repo-root resolution (Q9).** Walk up from CWD looking for `.git/` or `.claude/`; use the first match as repo root. Anchor `.clauditor/` there. If neither found, fall back to CWD with a warning.
- **DEC-010 — Output artifacts (Q10=C).** Write both `output.txt` (rendered text blocks — what `SkillResult.output` already produces) and `output.jsonl` (raw stream-json events, one JSON object per line).
- **DEC-011 — Variance layout (Q11=C).** Each variance run gets its own `run-K/` subdir with `output.txt` + `output.jsonl`. Aggregated `grading.json` + `timing.json` sit at `iteration-N/<skill>/` level. Single-run grade uses `run-0/` (always, for consistency).
- **DEC-012 — Atomic writes (Q12=B).** Write to `iteration-N-tmp/` (repo-root-relative), atomic `rename()` to `iteration-N/` on success. On exception: `shutil.rmtree` the tmp dir.
- **DEC-013 — Schema bump (Q13=A).** `SCHEMA_VERSION = 3`. `history.read_records()` tolerates v2 records (missing `iteration`/`workspace_path` → None). `cmd_trend` gets an explicit `rec.get("schema_version", 2)` guard before touching new fields.
- **DEC-014 — `--force` semantics (Q14=A).** `--force` does `shutil.rmtree(iteration-N)` first, then writes fresh. Clean slate; no mixed state from prior run.
- **DEC-015 — Legacy `.grade.json` (Q15=A).** Ignore existing `.clauditor/<skill>.grade.json`. Not deleted, not migrated. Users can clean manually. Pre-production, low-friction.

### Implications for runner.py

- **DEC-010 requires capturing raw stream-json.** Today `SkillResult.output` is only the concatenated text; the raw events are parsed and discarded. US-003 adds a field (e.g. `stream_events: list[dict]`) populated during stream parsing.

## Detailed Breakdown (Phase 4)

### US-001 — Repo-root detection helper
- **Description:** Add a `resolve_clauditor_dir()` utility that walks up from CWD looking for `.git/` or `.claude/` and returns `<repo_root>/.clauditor`. Falls back to `Path.cwd() / ".clauditor"` with a single-line warning when neither marker is found.
- **Traces to:** DEC-009
- **Files:** `src/clauditor/paths.py` (new, ~40 LOC), `src/clauditor/cli.py` (replace `Path(".clauditor")` call sites), `src/clauditor/history.py` (use helper for default path)
- **Acceptance:**
  - `resolve_clauditor_dir()` returns the same path whether invoked from repo root or a nested subdir of a repo
  - Fallback path triggered only when no `.git`/`.claude` ancestor exists
  - `uv run pytest tests/test_paths.py -v` passes
  - `uv run ruff check src/ tests/` clean
- **Done when:** `cli.py` and `history.py` no longer contain `Path(".clauditor")` literals
- **Depends on:** none
- **TDD:**
  - `test_resolve_from_repo_root` — CWD = repo root with `.git/` → returns `<root>/.clauditor`
  - `test_resolve_from_nested_subdir` — CWD = deep subdir → still returns `<root>/.clauditor`
  - `test_resolve_with_claude_only` — `.claude/` but no `.git/` still anchors root
  - `test_resolve_no_markers_fallback` — emits warning, returns `cwd/.clauditor`

### US-002 — Iteration workspace allocator
- **Description:** Pure library module (`workspace.py`) that given a clauditor dir, skill name, and optional explicit iteration, allocates an iteration slot: scans existing `iteration-*` dirs, picks `max+1` or honors `--iteration`, handles `FileExistsError` retry (DEC-006), creates the `iteration-N-tmp/<skill>/` staging path, and returns an object with `finalize()` (atomic rename per DEC-012) and `abort()` (rmtree tmp).
- **Traces to:** DEC-001, DEC-006, DEC-008, DEC-012, DEC-014
- **Files:** `src/clauditor/workspace.py` (new, ~120 LOC), `tests/test_workspace.py` (new)
- **Acceptance:**
  - Auto-increment handles gaps (iteration-1, -3 present → N=4)
  - Concurrent allocation via threaded test: two allocators never return the same N
  - `--iteration 5` when `iteration-5/` exists raises `IterationExistsError` with clear message
  - `--iteration 5 --force` removes existing `iteration-5/` (rmtree) before allocation
  - Crash between allocation and finalize leaves no `iteration-N/` — only an orphan `iteration-N-tmp/` that a future run can safely ignore
  - `abort()` removes the tmp dir cleanly
- **Done when:** `test_workspace.py` covers all 6 cases; 80% branch coverage for the module
- **Depends on:** US-001
- **TDD:**
  - `test_auto_increment_empty` (N=1)
  - `test_auto_increment_with_gaps` (N jumps over gaps)
  - `test_explicit_iteration_no_collision`
  - `test_explicit_iteration_collision_raises`
  - `test_force_replaces_existing`
  - `test_concurrent_allocation_threaded` (2 threads, distinct N values)
  - `test_finalize_atomic_rename`
  - `test_abort_removes_tmp`

### US-003 — Runner captures raw stream events
- **Description:** Extend `SkillResult` with a `stream_events: list[dict]` field populated by `runner.py` during stream-json parsing. Every `{"type": ...}` object from the CLI's stream-json output is appended. Preserves existing `output` field (rendered text blocks) unchanged.
- **Traces to:** DEC-010
- **Files:** `src/clauditor/runner.py` (add field + populate in stream parser), `src/clauditor/schemas.py` (if `SkillResult` lives there — check), `tests/test_runner.py` (extend existing tests)
- **Acceptance:**
  - `SkillResult.output` unchanged from pre-story behavior
  - `SkillResult.stream_events` contains every JSON object emitted on stdout, in order
  - Non-JSON lines in stdout are ignored (not crashed on)
  - Existing runner tests still pass unchanged
- **Done when:** New field is populated and a new test asserts round-trip of a captured stream
- **Depends on:** none (independent)
- **TDD:**
  - `test_stream_events_populated` — mock subprocess emits 3 JSON lines; field has 3 dicts
  - `test_stream_events_skips_non_json` — garbage line between JSON objects doesn't crash
  - `test_output_field_still_renders_text_blocks` — no regression

### US-004 — Rewrite `cmd_grade` for iteration workspace
- **Description:** Replace `--save`-gated single-file write with always-on iteration workspace. Remove `--save` flag. Add `--iteration N` and `--force`. Wire variance to per-run subdirs. Uses `workspace.py` allocator and writes `run-K/output.txt`, `run-K/output.jsonl`, aggregated `grading.json`, and aggregated `timing.json`. Single grade run uses `run-0/`. Prints iteration path on completion.
- **Traces to:** DEC-002, DEC-003, DEC-010, DEC-011, DEC-012, DEC-014
- **Files:** `src/clauditor/cli.py` (cmd_grade 130–363, argparse 457–579), `src/clauditor/quality_grader.py` (if `GradingReport.to_json()` needs a `timing.json`-only split), `tests/test_cli.py` (rewrite `TestCmdGradeSaveDiff`, `TestCmdGrade`, `TestCmdGradeHistory`)
- **Acceptance:**
  - `clauditor grade foo` with no flags writes `.clauditor/iteration-1/foo/{grading.json,timing.json,run-0/output.txt,run-0/output.jsonl}`
  - Second invocation writes `iteration-2/`
  - `--iteration 5` creates `iteration-5/`; re-running without `--force` errors
  - `--iteration 5 --force` overwrites (clean slate)
  - `--variance 3` writes `run-0/`, `run-1/`, `run-2/` under one iteration dir
  - `--save` flag removed (argparse rejects it)
  - Crash mid-grade leaves no `iteration-N/` (only an orphan tmp dir)
  - `uv run ruff check src/ tests/` clean
  - `uv run pytest --cov=clauditor --cov-report=term-missing` ≥80% and passes
- **Done when:** Grade command writes the new layout; `--save` is gone; all updated tests green
- **Depends on:** US-001, US-002, US-003
- **TDD:**
  - `test_grade_writes_iteration_one_when_empty`
  - `test_grade_auto_increments_across_runs`
  - `test_grade_iteration_explicit_collision_errors`
  - `test_grade_iteration_force_overwrites`
  - `test_grade_variance_produces_run_subdirs`
  - `test_grade_save_flag_removed` (argparse rejects)
  - `test_grade_crash_leaves_no_iteration_dir` (exception between allocate and finalize)

### US-005 — history.jsonl schema v3
- **Description:** Bump `SCHEMA_VERSION = 3` in `history.py`. Extend `append_record()` signature with `iteration: int` and `workspace_path: str` parameters. Add `fcntl.flock` on `.clauditor/.lock` around the append (DEC-006). Update `read_records()` to tolerate v2 records (missing new fields → None). Add explicit `schema_version` guard in `cmd_trend` before accessing new fields.
- **Traces to:** DEC-005, DEC-006, DEC-013
- **Files:** `src/clauditor/history.py` (schema, append_record, read_records, lock), `src/clauditor/cli.py` (cmd_grade passes new fields; cmd_trend guards), `tests/test_history.py`, `tests/test_cli.py` (trend tests)
- **Acceptance:**
  - New records written with `schema_version: 3` and include `iteration` + `workspace_path`
  - Reading a file with mixed v2/v3 records returns all of them without error
  - `cmd_trend` renders sparklines over mixed history without crashing
  - Concurrent `append_record()` from two processes produces two well-formed lines (no interleaving)
- **Done when:** Mixed-schema fixture file reads cleanly; concurrent append test passes
- **Depends on:** US-002 (needs `workspace_path` from allocator)
- **TDD:**
  - `test_append_v3_record_shape`
  - `test_read_mixed_v2_v3_records`
  - `test_trend_over_mixed_schema`
  - `test_concurrent_append_no_interleave` (2 processes via `multiprocessing`)

### US-006 — `compare` auto-detects iteration dirs
- **Description:** Rewrite `_load_assertion_set()` (cli.py:366–405) so that positional args to `compare` can be iteration dirs: if the path is a directory, resolve to `<dir>/grading.json`. Existing `.txt`/`.grade.json` file support preserved. Add `--skill NAME --from N --to M` alternate form that resolves to `.clauditor/iteration-N/<skill>/` / `...-M/<skill>/`. Mutually exclusive with positional args.
- **Traces to:** DEC-004, DEC-007
- **Files:** `src/clauditor/cli.py` (cmd_compare subparser 993–1001; `_load_assertion_set`, `_file_kind`), `src/clauditor/comparator.py` (if any `.grade.json` assumptions leak there), `tests/test_cli.py` / `tests/test_comparator.py`
- **Acceptance:**
  - `compare .clauditor/iteration-1/foo .clauditor/iteration-2/foo` reads `grading.json` from each dir and diffs
  - `compare --skill foo --from 1 --to 2` resolves to the same paths under the repo-root-anchored `.clauditor`
  - Legacy `compare old.grade.json new.grade.json` still works unchanged
  - Passing positional args AND `--skill/--from/--to` errors with clear message
  - Missing `grading.json` inside a supplied dir errors with a clear "no grading.json found" message
- **Done when:** Three compare modes (file, dir, numeric refs) all tested; legacy mode not regressed
- **Depends on:** US-001 (repo-root), US-004 (iteration dirs exist to compare against)
- **TDD:**
  - `test_compare_two_iteration_dirs`
  - `test_compare_numeric_refs`
  - `test_compare_legacy_grade_json_files` (regression)
  - `test_compare_positional_and_numeric_conflict_errors`
  - `test_compare_dir_missing_grading_json_errors`

### US-007 — Update README and docs
- **Description:** Update `README.md` to remove the "deliberately out of scope: per-iteration workspace dirs" line, document the new layout with an example tree, show the two `compare` forms, and note the `--save` removal. Add a short section on iteration numbering and `--force`. No `docs/` deep-dive.
- **Traces to:** DEC-002, DEC-003, DEC-004, DEC-007, DEC-008, DEC-014
- **Files:** `README.md`
- **Acceptance:**
  - Example tree matches actual layout produced by US-004
  - Both compare forms shown
  - `--save` deprecation noted in changelog / breaking-changes section
  - `uv run ruff check src/ tests/` still clean (no code changes, but sanity)
- **Done when:** README reflects the new behavior; grep finds no stale references to `--save` or the old `.grade.json` path
- **Depends on:** US-004, US-006

### Quality Gate
- **Description:** Run code reviewer 4 times over the full changeset. Fix every real bug found each pass. Run CodeRabbit if available. Run `uv run ruff check src/ tests/` and `uv run pytest --cov=clauditor --cov-report=term-missing` — both must pass with ≥80% coverage.
- **Depends on:** US-001..US-007

### Patterns & Memory
- **Description:** Capture any patterns learned (e.g., repo-root resolution helper, workspace allocator idiom, atomic-write-via-tmp-dir pattern, schema-version guard pattern) in a short note. If a `.claude/rules/` directory is created for this project, add an entry; otherwise note in `bd remember`.
- **Depends on:** Quality Gate

## Beads Manifest

- **Epic:** `clauditor-yng` — #22: Per-iteration workspace layout for eval runs
- **PR:** https://github.com/wjduenow/clauditor/pull/31
- **Branch:** `feature/22-iteration-workspace`

| Task | Story | Depends on |
|---|---|---|
| `clauditor-yng.1` | US-001 Repo-root detection helper | — |
| `clauditor-yng.2` | US-002 Iteration workspace allocator | .1 |
| `clauditor-yng.3` | US-003 Runner captures raw stream events | — |
| `clauditor-yng.4` | US-004 Rewrite cmd_grade | .1, .2, .3 |
| `clauditor-yng.5` | US-005 history.jsonl schema v3 | .2 |
| `clauditor-yng.6` | US-006 compare auto-detect dirs | .1, .4 |
| `clauditor-yng.7` | US-007 README update | .4, .6 |
| `clauditor-yng.8` | Quality Gate | .4, .5, .6, .7 |
| `clauditor-yng.9` | Patterns & Memory | .8 |

Ready at devolve time: `.1`, `.3` (parallelizable immediately), plus the epic row.
