# Super Plan: #8 — Improve Test Coverage from 44% to 80%+

## Meta

| Field        | Value |
|--------------|-------|
| Ticket       | [#8](https://github.com/wjduenow/clauditor/issues/8) |
| Branch       | `feature/8-improve-test-coverage` |
| Phase        | `devolved` |
| Sessions     | 1 |
| Last session | 2026-04-08 |

---

## Discovery

### Ticket Summary

Raise overall test coverage from 44% (116 tests) to 80%+, with no individual module below 60%. Layer 3 modules (triggers.py, quality_grader.py) are already well-covered (94-97%). The gap is concentrated in older modules: cli.py (0%), spec.py (0%), __init__.py (0%), runner.py (18%), pytest_plugin.py (11%), and partially-covered modules like grader.py (46%), schemas.py (38%), assertions.py (57%), comparator.py (68%).

### Codebase Findings

**Source modules:** All 11 modules live in `src/clauditor/`

**Existing tests:** 8 test files in `tests/`, no conftest.py. 116 tests passing.

**Test patterns:**
- Class-based test organization (`TestContains`, `TestGradeQuality`)
- Factory helpers (`_make_spec()`, `_make_results()`, `_make_report()`)
- `unittest.mock` with `MagicMock`, `AsyncMock`, `patch`
- `@pytest.mark.asyncio` with `asyncio_mode = "strict"`
- Plain `assert` statements, `pytest.approx()` for floats

**Config:**
- pytest 8.0+, pytest-asyncio 1.3.0+, pytest-cov 5.0+
- Ruff linting (line length 88, rules E/F/I/N/W/UP) applies to tests
- CI runs on Python 3.11, 3.12, 3.13 with coverage uploaded to CodeCov

**No `.claude/rules/` files, no workflow-project.md, no ARCHITECTURE.md.**

### Proposed Scope

Three tiers matching the issue:
1. **Easy wins** — cli.py, spec.py, __init__.py (0% → 60%+)
2. **Mock-heavy** — runner.py, grader.py, pytest_plugin.py (need subprocess/API mocking)
3. **Gap filling** — assertions.py, schemas.py, comparator.py (raise to 60%+)

### Scoping Answers

- **Q1: CI Coverage Gate** → A) Add `--cov-fail-under=80` to CI
- **Q2: pytest_plugin.py Testing** → A) Use `pytester` for realistic plugin integration tests
- **Q3: spec.py Scope** → A) Test the full `evaluate()` flow with mocked runner/grader
- **Q4: conftest.py** → A) Create a `tests/conftest.py` with shared fixtures
- **Q5: Coverage Measurement** → A) Run coverage first to get current baseline

### Verified Baseline (2026-04-08)

| Module | Stmts | Miss | Cover |
|--------|-------|------|-------|
| __init__.py | 13 | 13 | 0% |
| assertions.py | 82 | 35 | 57% |
| cli.py | 177 | 177 | 0% |
| comparator.py | 53 | 17 | 68% |
| grader.py | 61 | 33 | 46% |
| pytest_plugin.py | 54 | 48 | 11% |
| quality_grader.py | 126 | 7 | 94% |
| runner.py | 78 | 64 | 18% |
| schemas.py | 58 | 36 | 38% |
| spec.py | 38 | 38 | 0% |
| triggers.py | 103 | 3 | 97% |
| **TOTAL** | **843** | **471** | **44%** |

---

## Architecture Review

| Area | Rating | Key Finding |
|------|--------|-------------|
| Security | concern | Mock `clauditor.runner.subprocess.run` (not global `subprocess.run`) to prevent accidental live CLI calls. Use `tmp_path` for all file-based test fixtures. |
| Performance | concern | Pytester tests add ~0.2-0.5s each — keep to 3-5 max. Use `runpytest_inprocess()` when possible for speed and coverage measurement. |
| Testing Strategy | concern | `__init__.py` at 0% is a **coverage config issue** (module imported before measurement starts), not a testing gap. `schemas.py` may also benefit from the same fix. Dead branch bug found in `grader.py` line 145. |
| Data Model | pass | No schema changes needed. |
| API Design | pass | No API changes — pure test additions. |
| Observability | pass | Coverage reporting already configured via CodeCov. Adding `--cov-fail-under=80` is the only change. |

### Key Architectural Findings

1. **Coverage config fix needed first:** `__init__.py` shows 0% because the clauditor plugin imports the package before `pytest-cov` starts measuring. Fix with `--import-mode=importlib` or `.coveragerc` source config. This may also raise `schemas.py` for free.

2. **Dead branch bug in `grader.py:145`:** `elif "```" in json_str` can never execute because the identical `if` condition on line 142 already matched. Tests will expose this — fix or remove the dead branch.

3. **`SkillResult` assertion methods (runner.py:3-89) entirely untested:** These are load-bearing (`spec.evaluate()` and `cli.py` depend on `succeeded`). Zero mocking needed — pure functions.

4. **conftest.py must not shadow plugin fixtures:** Don't redeclare `clauditor_runner`, `clauditor_spec`, `clauditor_grader`, `clauditor_triggers`. Only add new shared test helpers.

5. **Pytester + coverage:** Use `runpytest_inprocess()` instead of `runpytest()` so the outer coverage session measures plugin code without needing `--cov-append`.

6. **`cli.py` inner async functions:** `cmd_grade` defines `_run_grade()` inside the function body — cannot be patched directly. Patch `clauditor.quality_grader.grade_quality` and `clauditor.comparator.compare_ab` at module level instead.

---

## Refinement Log

### Session 1 — 2026-04-08

Resolved all architecture concerns and scoping questions. No blockers remain.

### Decisions

**DEC-001: CI coverage gate**
Add `--cov-fail-under=80` to CI pipeline.
_Rationale: Hard gate ensures coverage never regresses below target._

**DEC-002: Pytester for plugin tests**
Use `pytester` with `runpytest_inprocess()` for realistic integration tests.
_Rationale: Accurate plugin testing; inprocess avoids subprocess overhead and coverage gaps._

**DEC-003: Full spec.py evaluate flow**
Test `from_file()`, `run()`, and `evaluate()` with mocked runner/grader.
_Rationale: spec.py is the main orchestrator — all paths matter._

**DEC-004: Shared conftest.py**
Create `tests/conftest.py` with shared fixtures. Must not shadow plugin fixture names.
_Rationale: Multiple new test files will share sample specs, mock runners, tmp files._

**DEC-005: Baseline first**
Verified baseline: 44% overall, 843 stmts, 471 missed. (Confirmed 2026-04-08.)

**DEC-006: Coverage config fix (both)**
Add both `--import-mode=importlib` and `.coveragerc` with `source = clauditor`.
_Rationale: Belt and suspenders — fixes __init__.py 0% measurement bug and may raise schemas.py for free._

**DEC-007: Fix dead branch in grader.py**
Remove the unreachable `elif` on grader.py:145 as part of this PR.
_Rationale: It's a bug discovered by testing; small fix, in scope._

**DEC-008: Pytester — 5 tests**
5 pytester tests: marker registration + skip logic, fixture creation, CLI option passing, grade marker skip, full integration with mock skill.
_Rationale: Thorough plugin validation without going overboard._

**DEC-009: Test all SkillResult methods exhaustively**
Test all 7 assertion helpers + `succeeded` with happy path and failure path each.
_Rationale: Pure functions, trivial to write, load-bearing for spec.evaluate() and cli.py._

**DEC-010: cli.py — cover all 5 subcommands**
Cover all 5 subcommands (validate, run, init, grade, triggers) with at least one happy path each, plus key error paths.
_Rationale: cli.py is the main entry point; broad coverage catches regressions in all commands._

---

## Detailed Breakdown

### US-001: Fix coverage config + test __init__.py

**Description:** Fix the `module-not-measured` coverage warning by adding `--import-mode=importlib` to pytest config and a `.coveragerc` file (DEC-006). Add tests for `__getattr__` lazy imports and `AttributeError` fallback.

**Traces to:** DEC-005, DEC-006

**Acceptance Criteria:**
- `__init__.py` shows >60% coverage
- No `module-not-measured` warning in pytest output
- Lazy imports (`GradingReport`, `ABResult`, `TriggerReport`) resolve correctly
- `AttributeError` raised for invalid attribute names
- `uv run pytest --cov=clauditor` passes

**Done when:** `__init__.py` coverage ≥60%, no measurement warnings.

**Files:**
- `pyproject.toml` — add `addopts = "--import-mode=importlib"` to `[tool.pytest.ini_options]`
- `.coveragerc` — new file, `[run] source = clauditor`
- `tests/test_init.py` — new file with `TestLazyImports`, `TestAttributeError`

**Depends on:** none

**TDD:**
- `test_lazy_import_grading_report` — `from clauditor import GradingReport` resolves
- `test_lazy_import_ab_result` — `from clauditor import ABResult` resolves
- `test_lazy_import_trigger_report` — `from clauditor import TriggerReport` resolves
- `test_invalid_attribute_raises` — `clauditor.Nonexistent` raises `AttributeError`
- `test_all_exports_importable` — every name in `__all__` is importable

---

### US-002: Create shared conftest.py

**Description:** Create `tests/conftest.py` with shared fixtures used by 3+ upcoming test files: sample eval spec data, temp skill files, and mock runner factory. Must NOT shadow plugin fixture names (DEC-004).

**Traces to:** DEC-004

**Acceptance Criteria:**
- `tests/conftest.py` exists with shared fixtures
- No fixtures named `clauditor_runner`, `clauditor_spec`, `clauditor_grader`, or `clauditor_triggers`
- Existing tests still pass unchanged
- `uv run pytest --cov=clauditor` passes

**Done when:** conftest.py created, existing 116 tests still pass.

**Files:**
- `tests/conftest.py` — new file with `sample_eval_data`, `make_eval_spec`, `tmp_skill_file`, `mock_runner` fixtures

**Depends on:** US-001

---

### US-003: Test SkillResult methods + SkillRunner.run()

**Description:** Test all 7 assertion helper methods on `SkillResult` (lines 39-83) plus the `succeeded` property (line 34) with happy path and failure path each (DEC-009). Also test `SkillRunner.run()` (lines 99-150) — success, timeout, FileNotFoundError — mirroring existing `run_raw` tests.

**Traces to:** DEC-009

**Acceptance Criteria:**
- `runner.py` coverage ≥60%
- All 7 assertion methods tested (pass + fail)
- `succeeded` property tested: `exit_code=0, output="text"` → True; `exit_code=0, output=""` → False; `exit_code=1` → False
- `SkillRunner.run()` tested with mocked `subprocess.run` for success, timeout, not-found
- Mock target is `clauditor.runner.subprocess.run` (not global)
- `uv run pytest --cov=clauditor` passes

**Done when:** runner.py ≥60% coverage.

**Files:**
- `tests/test_runner.py` — extend with `TestSkillResultSucceeded`, `TestSkillResultAssertions`, `TestSkillRunnerRun`

**Depends on:** US-002

**TDD:**
- `test_succeeded_true` — exit_code=0, non-empty output → True
- `test_succeeded_false_empty_output` — exit_code=0, output="" → False
- `test_succeeded_false_whitespace` — exit_code=0, output="  \n" → False
- `test_succeeded_false_nonzero_exit` — exit_code=1 → False
- `test_assert_contains_pass` / `test_assert_contains_fail`
- `test_assert_not_contains_pass` / `test_assert_not_contains_fail`
- `test_assert_matches_pass` / `test_assert_matches_fail`
- `test_assert_min_count_pass` / `test_assert_min_count_fail`
- `test_assert_min_length_pass` / `test_assert_min_length_fail`
- `test_assert_has_urls_pass` / `test_assert_has_urls_fail`
- `test_assert_has_entries_pass` / `test_assert_has_entries_fail`
- `test_run_assertions_delegates`
- `test_runner_run_success` / `test_runner_run_timeout` / `test_runner_run_not_found`

---

### US-004: Test schemas.py — from_file, to_dict, edge cases

**Description:** Extend `test_schemas.py` to cover `EvalSpec.from_file()` with full data, missing file, malformed JSON, partial fields. Test `to_dict()` round-trip. Test `TriggerTests` and `VarianceConfig` loading.

**Traces to:** DEC-005

**Acceptance Criteria:**
- `schemas.py` coverage ≥60%
- `from_file` tested: valid file, missing file raises, malformed JSON raises, partial fields use defaults
- `to_dict` tested: round-trip `from_file → to_dict` produces equivalent data
- `TriggerTests` and `VarianceConfig` optional loading tested
- `uv run pytest --cov=clauditor` passes

**Done when:** schemas.py ≥60% coverage.

**Files:**
- `tests/test_schemas.py` — extend with `TestFromFile`, `TestToDict`, `TestOptionalFields`

**Depends on:** US-002

**TDD:**
- `test_from_file_full` — loads a complete eval.json with all fields
- `test_from_file_minimal` — loads with only `skill_name`
- `test_from_file_missing` — raises on nonexistent file
- `test_from_file_malformed_json` — raises `json.JSONDecodeError`
- `test_to_dict_roundtrip` — `from_file → to_dict` matches original data
- `test_trigger_tests_loaded` — `trigger_tests` field populated correctly
- `test_variance_config_loaded` — `variance` field populated correctly
- `test_no_trigger_tests` — `trigger_tests` is None when absent
- `test_no_variance` — `variance` is None when absent

---

### US-005: Test assertions.py — fill coverage gaps

**Description:** Add tests for `assert_max_length`, `assert_has_urls`, `assert_has_entries`, and edge cases in `run_assertions` (unknown type, empty assertions list). Target the uncovered lines: 6-32, 35-36, 39-40, 45, 54, 64, 74, 85, 96, 106-116, 128, 139, 156, 158, 162.

**Traces to:** DEC-005

**Acceptance Criteria:**
- `assertions.py` coverage ≥60%
- `assert_max_length` tested (pass and fail)
- `run_assertions` tested with unknown type (returns failed result)
- `run_assertions` tested with empty list (returns empty AssertionSet)
- `AssertionSet.summary()`, `.pass_rate`, `.failed` tested
- `uv run pytest --cov=clauditor` passes

**Done when:** assertions.py ≥60% coverage.

**Files:**
- `tests/test_assertions.py` — extend with `TestMaxLength`, `TestRunAssertionsEdgeCases`, `TestAssertionSetProperties`

**Depends on:** US-002

**TDD:**
- `test_max_length_pass` / `test_max_length_fail`
- `test_run_assertions_unknown_type` — returns `AssertionResult(passed=False)`
- `test_run_assertions_empty` — returns empty `AssertionSet`
- `test_assertionset_pass_rate` / `test_assertionset_failed` / `test_assertionset_summary`
- `test_assertionresult_bool` — `bool(result)` matches `result.passed`

---

### US-006: Test spec.py — from_file, run, evaluate

**Description:** Full test coverage for `SkillSpec`: `from_file()` with auto-discovery and explicit eval path, `run()` with and without args, `evaluate()` happy path, failed run path, no eval spec, and explicit output (DEC-003).

**Traces to:** DEC-003

**Acceptance Criteria:**
- `spec.py` coverage ≥60%
- `from_file` tested: file not found, auto-discover eval, explicit eval, no eval
- `run` tested: uses eval_spec.test_args when args=None, passes explicit args
- `evaluate` tested: happy path, failed run returns error AssertionSet, no eval raises ValueError, explicit output skips run
- `_failed_run_result` tested via the evaluate failed-run branch
- `uv run pytest --cov=clauditor` passes

**Done when:** spec.py ≥60% coverage.

**Files:**
- `tests/test_spec.py` — new file with `TestFromFile`, `TestRun`, `TestEvaluate`

**Depends on:** US-002, US-003

**TDD:**
- `test_from_file_with_eval` — auto-discovers sibling .eval.json
- `test_from_file_explicit_eval` — uses provided eval_path
- `test_from_file_no_eval` — spec has eval_spec=None
- `test_from_file_missing_skill` — raises FileNotFoundError
- `test_run_uses_eval_args` — when args=None, uses eval_spec.test_args
- `test_run_explicit_args` — passes explicit args to runner
- `test_evaluate_happy_path` — mock runner returns good output, assertions pass
- `test_evaluate_failed_run` — mock runner returns failed result, get error AssertionSet
- `test_evaluate_no_eval_spec` — raises ValueError
- `test_evaluate_with_explicit_output` — skips running, uses provided output

---

### US-007: Test grader.py — build_extraction_prompt + extract_and_grade

**Description:** Test `build_extraction_prompt()` (pure function, lines 34-64) and `extract_and_grade()` (async, lines 109-168) with mocked `AsyncAnthropic`. Fix the dead branch bug on line 144-145 (DEC-007).

**Traces to:** DEC-007

**Acceptance Criteria:**
- `grader.py` coverage ≥60%
- `build_extraction_prompt` tested with sections and fields
- `extract_and_grade` tested: success path, JSON parse failure, markdown-wrapped JSON
- Dead `elif` on line 144-145 removed
- `uv run pytest --cov=clauditor` passes

**Done when:** grader.py ≥60%, dead branch removed.

**Files:**
- `src/clauditor/grader.py` — remove dead `elif` on line 144-145
- `tests/test_grader.py` — extend with `TestBuildExtractionPrompt`, `TestExtractAndGrade`

**Depends on:** US-002, US-004

**TDD:**
- `test_build_prompt_includes_fields` — output contains field names from eval spec
- `test_build_prompt_includes_section_names` — output contains section names
- `test_extract_and_grade_success` — mock API returns valid JSON, grading succeeds
- `test_extract_and_grade_markdown_json` — mock API returns ```json-wrapped response
- `test_extract_and_grade_parse_failure` — mock API returns garbage, get error AssertionSet
- `test_extract_and_grade_import_error` — when anthropic not importable, raises ImportError

---

### US-008: Test comparator.py — compare_ab async flow

**Description:** Test the `compare_ab()` async function (lines 62-126) with mocked `grade_quality` and mocked spec runner. Cover: success path, no eval spec, empty test_args, baseline with fewer criteria than skill.

**Traces to:** DEC-005

**Acceptance Criteria:**
- `comparator.py` coverage ≥60%
- `compare_ab` tested: success (no regression), regression detected, no eval spec raises, empty test_args raises, baseline shorter than skill criteria
- `uv run pytest --cov=clauditor` passes

**Done when:** comparator.py ≥60% coverage.

**Files:**
- `tests/test_comparator.py` — extend with `TestCompareAB`

**Depends on:** US-002, US-003

**TDD:**
- `test_compare_ab_no_regression` — both pass, no regression
- `test_compare_ab_regression` — baseline passes, skill fails → regression
- `test_compare_ab_no_eval_spec` — raises ValueError
- `test_compare_ab_empty_test_args` — raises ValueError
- `test_compare_ab_baseline_fewer_criteria` — padding with synthetic failing result

---

### US-009: Test cli.py — all 5 subcommands

**Description:** Test all 5 CLI commands via `main(argv=[...])` with mocked `SkillSpec.from_file`, mocked runners, and mocked async functions (DEC-010). Cover happy paths and key error paths for each command.

**Traces to:** DEC-010

**Acceptance Criteria:**
- `cli.py` coverage ≥60%
- All 5 subcommands tested: validate, run, grade, triggers, init
- `cmd_validate`: happy path (with --output), no eval spec error, --json output
- `cmd_run`: happy path, error output
- `cmd_grade`: happy path (with --output), no eval spec, no grading_criteria, --dry-run, --json
- `cmd_triggers`: happy path (with --output), --dry-run, --json
- `cmd_init`: creates file, existing file without --force returns 1, --force overwrites
- `uv run pytest --cov=clauditor` passes

**Done when:** cli.py ≥60% coverage.

**Files:**
- `tests/test_cli.py` — new file with `TestCmdValidate`, `TestCmdRun`, `TestCmdGrade`, `TestCmdTriggers`, `TestCmdInit`

**Depends on:** US-002, US-006

**TDD:**
- `test_validate_with_output_file` — reads file, runs assertions, returns 0
- `test_validate_no_eval_spec` — returns 1
- `test_validate_json_output` — --json flag produces valid JSON
- `test_run_happy_path` — runs skill, prints output
- `test_grade_with_output` — grades pre-captured output
- `test_grade_no_eval_spec` — returns 1
- `test_grade_no_grading_criteria` — returns 1
- `test_grade_dry_run` — prints prompt, returns 0
- `test_grade_json_output` — --json produces valid JSON
- `test_triggers_happy_path` — runs trigger testing
- `test_triggers_dry_run` — prints prompts, returns 0
- `test_init_creates_file` — creates eval.json
- `test_init_existing_no_force` — returns 1
- `test_init_force_overwrites` — --force creates file

---

### US-010: Test pytest_plugin.py with pytester

**Description:** Use pytester (with `runpytest_inprocess()`) to test plugin registration, marker skip logic, fixture creation, CLI option passing, and grade marker skip (DEC-002, DEC-008). 5 tests total.

**Traces to:** DEC-002, DEC-008

**Acceptance Criteria:**
- `pytest_plugin.py` coverage ≥60%
- 5 pytester tests: marker registration, skip without --clauditor-grade, fixture available, CLI options passed through, grade marker enables with flag
- Uses `runpytest_inprocess()` for coverage measurement
- `uv run pytest --cov=clauditor` passes

**Done when:** pytest_plugin.py ≥60% coverage.

**Files:**
- `tests/test_pytest_plugin.py` — rewrite with pytester-based tests

**Depends on:** US-002

**TDD:**
- `test_marker_registered` — `clauditor_grade` marker registered in help output
- `test_grade_marker_skipped_by_default` — test with marker is skipped
- `test_grade_marker_runs_with_flag` — test with marker runs with --clauditor-grade
- `test_runner_fixture_available` — `clauditor_runner` fixture resolves
- `test_cli_options_passed` — --clauditor-timeout value reaches fixture

---

### US-011: Add CI coverage gate

**Description:** Add `--cov-fail-under=80` to the CI pipeline and pytest config (DEC-001). Verify the full test suite achieves 80%+ overall with no module below 60%.

**Traces to:** DEC-001

**Acceptance Criteria:**
- `--cov-fail-under=80` in pytest addopts or CI command
- Full test suite passes with 80%+ coverage
- No module below 60%
- CI pipeline updated
- `uv run pytest --cov=clauditor --cov-fail-under=80` passes

**Done when:** CI enforces 80% coverage floor.

**Files:**
- `pyproject.toml` — add `--cov-fail-under=80` to addopts
- `.github/workflows/ci.yml` — ensure coverage command includes fail-under

**Depends on:** US-001 through US-010

---

### US-012: Quality Gate — code review x4

**Description:** Run code reviewer 4 times across the full changeset, fixing all real bugs found each pass. Run project validation after all fixes.

**Traces to:** all decisions

**Acceptance Criteria:**
- 4 code review passes completed
- All real bugs fixed
- `uv run pytest --cov=clauditor --cov-fail-under=80` passes
- `uv run ruff check src/ tests/` passes
- No module below 60% coverage

**Done when:** All review passes clean, validation green.

**Files:** Any files touched by bug fixes from review.

**Depends on:** US-011

---

### US-013: Patterns & Memory — update conventions and docs

**Description:** Update project conventions with new patterns learned during this work (test patterns, coverage config, pytester usage). Update CLAUDE.md build/test commands.

**Traces to:** all decisions

**Acceptance Criteria:**
- CLAUDE.md updated with actual build/test commands
- Any new conventions documented
- `bd remember` called for persistent insights

**Done when:** Docs updated, memories saved.

**Files:** `CLAUDE.md`, possibly `.claude/rules/` files.

**Depends on:** US-012

---

## Beads Manifest

| Story | Bead ID | Title |
|-------|---------|-------|
| Epic | clauditor-n75 | #8: Improve test coverage from 44% to 80%+ |
| US-001 | clauditor-n75.1 | Fix coverage config + test __init__.py |
| US-002 | clauditor-n75.2 | Create shared conftest.py |
| US-003 | clauditor-n75.3 | Test SkillResult methods + SkillRunner.run() |
| US-004 | clauditor-n75.4 | Test schemas.py from_file, to_dict, edge cases |
| US-005 | clauditor-n75.5 | Test assertions.py coverage gaps |
| US-006 | clauditor-n75.6 | Test spec.py from_file, run, evaluate |
| US-007 | clauditor-n75.7 | Test grader.py + fix dead branch |
| US-008 | clauditor-n75.8 | Test comparator.py compare_ab async flow |
| US-009 | clauditor-n75.9 | Test cli.py all 5 subcommands |
| US-010 | clauditor-n75.10 | Test pytest_plugin.py with pytester |
| US-011 | clauditor-n75.11 | Add CI coverage gate |
| QG | clauditor-n75.12 | Quality Gate — code review x4 + validation |
| P&M | clauditor-n75.13 | Patterns & Memory — update conventions and docs |

**Worktree:** `/home/wesd/Projects/clauditor` (branch: `feature/8-improve-test-coverage`)
