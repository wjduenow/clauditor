# Super Plan: #11 — First Pass Improvements

## Meta

| Field        | Value |
|--------------|-------|
| Ticket       | [#11](https://github.com/wjduenow/clauditor/issues/11) |
| Branch       | `feature/11-first-pass-improvements` |
| Phase        | `detailing` |
| Sessions     | 1 |
| Last session | 2026-04-09 |

---

## Discovery

### Ticket Summary

Seven improvements identified from the first real-world eval run against my_claude_agent's `/find-restaurants` skill. The issue bundles infrastructure fixes, new features, and developer experience improvements into one tracking ticket.

**Stories from the ticket:**
1. Support file-based skill output (output_path/output_glob in eval specs)
2. ~~Add `--live` mode to validate command~~ → Dropped (DEC-001)
3. Fix `--no-input` flag in runner (broken with current Claude CLI)
4. Support multi-file skill output
5. Add score thresholds for Layer 3 (min_mean_score instead of all-or-nothing)
6. Add diff mode for A/B regression (compare against prior runs)
7. Publish extraction prompt for debugging (--dry-run for Layer 2)

### Codebase Findings

**Core files that will be touched across multiple stories:**

| File | Stories | Current Role |
|------|---------|-------------|
| `schemas.py` (62 lines) | 1, 4, 5, 6 | EvalSpec dataclass, all config fields |
| `runner.py` (198 lines) | 1, 3, 4 | SkillRunner subprocess execution |
| `cli.py` (418 lines) | 5, 6, 7 | All CLI commands (validate, grade, etc.) |
| `quality_grader.py` (229 lines) | 5, 6 | GradingReport pass/fail logic |
| `spec.py` (76 lines) | 1, 4 | SkillSpec orchestrator |
| `comparator.py` (126 lines) | 6 | A/B comparison |
| `grader.py` (172 lines) | 4 | Layer 2 extraction |

**Key patterns to preserve:**
- All dataclasses use `@dataclass` with optional fields defaulting to `None` or `field(default_factory=list)`
- New EvalSpec fields must be backward-compatible (old JSON files without the field still load)
- Result types expose `.passed` property + `.summary()` method
- Layer 3 types use lazy imports in `__init__.py` to avoid requiring `anthropic` at import time
- Tests use `unittest.mock` with `MagicMock`/`AsyncMock`/`patch`
- Coverage gate: 80% overall, enforced in CI

**The `--no-input` bug (Story 3):**
- `runner.py` lines 118 and 166 pass `--no-input` to `claude` CLI
- Current Claude CLI uses `-p`/`--print` for non-interactive mode; `--no-input` doesn't exist
- Both `run()` and `run_raw()` already pass `-p` as the first flag, making `--no-input` both broken and redundant
- Fix: simply remove `--no-input` from the command array

**GradingReport.passed (Story 5):**
```python
@property
def passed(self) -> bool:
    return all(r.passed for r in self.results)
```
Currently all-or-nothing. VarianceReport already has configurable `min_stability` threshold — extend this pattern.

**Dry-run pattern (Story 7):**
- `cmd_grade` already has `--dry-run` that calls `build_grading_prompt()` and prints without API call
- `grader.py` has `build_extraction_prompt()` but no CLI surface for it
- Pattern is identical: build prompt, print, exit

### Scoping Answers

- **Q1: Story ordering** → B) Plan all 7 as one cohesive release
- **Q2: Multi-file output** → A) Yes, include now — `/market-research` produces 5+ files
- **Q3: Diff mode storage** → Alongside eval.json in `.clauditor/` directory
- **Q4: Score thresholds** → B) Ship with lenient default — no one is using this yet
- **Q5: --live mode** → ~~B) Add to both~~ → Dropped per DEC-001

---

## Architecture Review

| Area | Rating | Findings |
|------|--------|----------|
| **Data Model** | pass | All new EvalSpec fields have safe defaults (`None`, `field(default_factory=list)`). Old JSON files load without changes. `to_dict()` already skips `None` fields. |
| **API Design** | pass | `--live` dropped (redundant). New `extract` subcommand mirrors 3-layer architecture. Thresholds in EvalSpec keeps function signatures clean. |
| **Backward Compat** | pass | All changes additive. No breaking changes to existing CLI flags, Python API, or eval spec format. |
| **Testing Strategy** | pass | ~38 new tests, mostly unit. Existing conftest fixtures reusable. No coverage risk. |

### Concerns Resolved

- **C1 (--live redundancy):** Dropped entirely → DEC-001
- **C2 (flag naming):** `--save` and `--diff` adopted → DEC-002
- **C3 (thresholds location):** In EvalSpec, not function params → DEC-003

---

## Refinement Log

### Decisions

**DEC-001: Drop --live flag**
- Commands already run the skill when `--output` is not provided
- Adding `--live` is redundant and confusing
- Instead: document existing behavior in CLI help text
- Rationale: API reviewer found validate and grade both default to running the skill

**DEC-002: Use --save and --diff for grade persistence**
- `--save` writes the GradingReport to `.clauditor/<skill_name>.grade.json`
- `--diff` loads prior results from the same path and compares
- Naming is short, clear, and doesn't collide with `--compare` (A/B baseline)
- Rationale: `--save-results` and `--compare-prior` were vague

**DEC-003: Thresholds live in EvalSpec**
- New field: `grade_thresholds: GradeThresholds | None` on EvalSpec
- `GradeThresholds` dataclass with `min_pass_rate: float = 0.7`, `min_mean_score: float = 0.5`
- When `None`, default GradeThresholds values apply (lenient)
- `GradingReport.passed` checks thresholds instead of `all(r.passed)`
- Rationale: Follows VarianceConfig/min_stability pattern. Keeps grade_quality() signature clean.

**DEC-004: Prior results storage structure**
- Directory: `.clauditor/` at project root
- File: `.clauditor/<skill_name>.grade.json` (latest report only)
- Format: JSON with skill_name, model, timestamp, results array
- `--save` overwrites latest; no history tracking in v1
- Rationale: Simple, no history management complexity. Can add timestamped history later.

**DEC-005: New `extract` CLI subcommand for Layer 2**
- Mirrors three-layer architecture: `validate` (L1), `extract` (L2), `grade` (L3)
- Supports `--dry-run` (print prompt), `--output` (pre-captured), `--json`, `--model`
- Default behavior: run skill, then extract
- Rationale: Cleaner than adding flags to validate. Discoverable. Consistent.

**DEC-006: SkillResult multi-file via `outputs` dict**
- Add `outputs: dict[str, str] = field(default_factory=dict)` to SkillResult
- `output: str` stays as the primary output (first/main file)
- When runner detects multi-file output via `output_files` in EvalSpec, populates both
- All existing code using `result.output` continues working
- Rationale: Backward compatible, simple, clear semantics

---

## Detailed Breakdown

### US-001: Fix --no-input flag in runner

**Description:** Remove the broken `--no-input` flag from `SkillRunner.run()` and `run_raw()`. The flag doesn't exist in the current Claude CLI and `-p` already provides non-interactive mode.

**Traces to:** DEC-001 (simplify, don't add flags)

**Acceptance Criteria:**
- `--no-input` removed from subprocess command in both `run()` and `run_raw()`
- Command array is `[claude_bin, "-p", prompt]` with no extra flags
- Existing tests updated to match new command structure
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** `--no-input` string appears nowhere in `runner.py`

**Files:**
- `src/clauditor/runner.py` — remove `"--no-input"` from lines 118 and 166
- `tests/test_runner.py` — update mock expectations (3 tests)

**Depends on:** none

---

### US-002: Add output_file and output_files to EvalSpec

**Description:** Add `output_file: str | None` and `output_files: list[str]` fields to EvalSpec so eval specs can declare where skills write their output files. This is the schema foundation for Stories 1 and 4.

**Traces to:** DEC-006 (multi-file support)

**Acceptance Criteria:**
- `output_file` (single path) and `output_files` (list of paths/globs) added to EvalSpec
- `from_file()` loads both fields from JSON; missing fields default to `None` / `[]`
- `to_dict()` includes them when non-default
- Old eval.json files without these fields still load correctly
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** EvalSpec can round-trip `output_file` and `output_files` through JSON

**Files:**
- `src/clauditor/schemas.py` — add fields to EvalSpec, update `from_file()` and `to_dict()`
- `tests/test_schemas.py` — add TestEvalSpecOutputFields (5-6 tests)

**Depends on:** none

---

### US-003: Add outputs dict to SkillResult

**Description:** Add `outputs: dict[str, str]` to SkillResult for multi-file skill output. Keep `output: str` as primary output for backward compatibility.

**Traces to:** DEC-006

**Acceptance Criteria:**
- `outputs` field added with `field(default_factory=dict)`
- Existing code using `result.output` unaffected
- `succeeded` property still works (considers both `output` and `outputs`)
- All assertion methods (`assert_contains`, etc.) operate on `output` string
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** SkillResult has `outputs` dict and all existing tests pass

**Files:**
- `src/clauditor/runner.py` — add `outputs` field to SkillResult
- `tests/test_runner.py` — add TestSkillResultOutputs (5 tests)

**Depends on:** none

---

### US-004: Wire file-based output through SkillSpec and runner

**Description:** When `eval_spec.output_file` or `output_files` is set, SkillSpec.run() reads the output from files after skill execution instead of relying on stdout capture. Runner populates `outputs` dict.

**Traces to:** DEC-006

**Acceptance Criteria:**
- `SkillSpec.run()` checks `eval_spec.output_file` / `output_files` after skill execution
- If `output_file` set: reads that file into `result.output`
- If `output_files` set: globs/reads all matching files into `result.outputs`, sets `output` to first match
- Falls back to stdout capture if neither field is set
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** `SkillSpec.run()` returns file content when eval spec declares output paths

**Files:**
- `src/clauditor/spec.py` — update `run()` to read output files
- `src/clauditor/runner.py` — pass `outputs` dict population through
- `tests/test_spec.py` — add file-based output tests (3-4 tests, mock filesystem)

**Depends on:** US-002, US-003

**TDD:**
- Test: eval spec with `output_file`, mock runner, assert `result.output` is file content
- Test: eval spec with `output_files` glob, mock filesystem with 3 files, assert `result.outputs` has all 3
- Test: eval spec with neither field, assert stdout capture behavior unchanged

---

### US-005: Add GradeThresholds to EvalSpec

**Description:** Add `GradeThresholds` dataclass and `grade_thresholds` field to EvalSpec. Default: `min_pass_rate=0.7`, `min_mean_score=0.5`. Update `GradingReport.passed` to use thresholds.

**Traces to:** DEC-003

**Acceptance Criteria:**
- `GradeThresholds` dataclass with `min_pass_rate` (default 0.7) and `min_mean_score` (default 0.5)
- `EvalSpec.grade_thresholds` field, defaults to `None` (which means use GradeThresholds defaults)
- `GradingReport` accepts thresholds, `passed` property evaluates against them
- When `grade_thresholds` is `None` in EvalSpec, `GradingReport` uses default `GradeThresholds()`
- Old eval specs without `grade_thresholds` still work
- `grade_quality()` passes thresholds from eval spec to report
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** `GradingReport.passed` respects configurable thresholds

**Files:**
- `src/clauditor/schemas.py` — add GradeThresholds dataclass, add field to EvalSpec
- `src/clauditor/quality_grader.py` — update GradingReport.passed, update grade_quality()
- `src/clauditor/cli.py` — pass thresholds from spec to grade_quality()
- `tests/test_schemas.py` — GradeThresholds loading/roundtrip (3 tests)
- `tests/test_quality_grader.py` — threshold logic (4-6 tests)

**Depends on:** none

**TDD:**
- Test: all criteria pass with scores above thresholds → `passed=True`
- Test: pass_rate 60% but min_pass_rate is 0.5 → `passed=True`
- Test: pass_rate 60% but min_pass_rate is 0.8 → `passed=False`
- Test: mean_score 0.45 with min_mean_score 0.5 → `passed=False`
- Test: no thresholds in eval spec → uses defaults (0.7/0.5)

---

### US-006: Add GradingReport JSON serialization

**Description:** Add `to_json()` and `from_json()` methods to GradingReport for persisting results to `.clauditor/`. Required for diff mode (US-008).

**Traces to:** DEC-004

**Acceptance Criteria:**
- `GradingReport.to_json() -> str` serializes to JSON with timestamp
- `GradingReport.from_json(data: str) -> GradingReport` round-trips correctly
- All fields preserved: skill_name, model, duration, results (criterion, passed, score, evidence, reasoning)
- Float scores round-trip accurately
- Missing optional fields handled gracefully in `from_json()`
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** `GradingReport.from_json(report.to_json())` produces equivalent report

**Files:**
- `src/clauditor/quality_grader.py` — add `to_json()` and `from_json()` to GradingReport
- `tests/test_quality_grader.py` — serialization roundtrip tests (5 tests)

**Depends on:** none

---

### US-007: Add `extract` CLI subcommand

**Description:** New CLI command `clauditor extract <skill.md>` for Layer 2 schema extraction with `--dry-run` support. Mirrors the three-layer architecture: validate (L1), extract (L2), grade (L3).

**Traces to:** DEC-005

**Acceptance Criteria:**
- `clauditor extract <skill.md>` runs skill and extracts structured data
- `--dry-run` prints the extraction prompt without API calls
- `--output <file>` uses pre-captured output
- `--json` outputs structured JSON
- `--model` overrides extraction model
- Exits 0 on pass, 1 on failure
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** `clauditor extract --help` shows all flags and `--dry-run` prints the prompt

**Files:**
- `src/clauditor/cli.py` — add `cmd_extract()` function, add argparse subparser
- `tests/test_cli.py` — extract command tests (4-6 tests: dry-run, output file, json, error cases)

**Depends on:** none (reuses existing `build_extraction_prompt()` and `extract_and_grade()`)

---

### US-008: Add --save and --diff to grade command

**Description:** Add `--save` flag to persist GradingReport to `.clauditor/<skill>.grade.json`, and `--diff` flag to load prior results and compare for regressions.

**Traces to:** DEC-002, DEC-004

**Acceptance Criteria:**
- `--save` writes GradingReport JSON to `.clauditor/<skill_name>.grade.json`
- Creates `.clauditor/` directory if it doesn't exist
- `--diff` loads prior report from same path, compares scores, shows regressions
- `--save --diff` together: load prior, run grading, compare, then save new results
- If `--diff` and no prior results exist, warns and continues (no error)
- Regression = criterion score dropped by > 0.1 or went from pass to fail
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80%

**Done when:** `clauditor grade skill.md --save --diff` shows regression table and persists results

**Files:**
- `src/clauditor/cli.py` — add `--save` and `--diff` flags to grade argparser, add comparison logic
- `tests/test_cli.py` — file I/O tests (4-5 tests: save, diff, both, missing prior, regression detection)

**Depends on:** US-006 (GradingReport serialization)

---

### US-009: Quality Gate

**Description:** Run code review across the full changeset. Validate all tests pass, linting clean, coverage maintained.

**Acceptance Criteria:**
- `uv run ruff check src/ tests/` — zero errors
- `uv run pytest --cov=clauditor --cov-report=term-missing` — all pass, >=80% coverage
- No regressions in existing test suite
- All new features have test coverage

**Done when:** CI-equivalent checks pass locally

**Files:** All changed files

**Depends on:** US-001 through US-008

---

### US-010: Patterns & Memory

**Description:** Update documentation and conventions with patterns learned during implementation.

**Acceptance Criteria:**
- README updated with `extract` command in CLI reference
- README updated with `--save` and `--diff` flags on grade command
- README updated with `grade_thresholds` in eval spec format
- README updated with `output_file`/`output_files` in eval spec format

**Done when:** README reflects all new features

**Files:**
- `README.md`

**Depends on:** US-009
