# Super Plan: Issue #3 — Layer 3: LLM-as-judge quality grading and trigger regression

## Meta

| Field         | Value                                      |
|---------------|--------------------------------------------|
| Ticket        | wjduenow/clauditor#3                       |
| Branch        | `feature/3-llm-judge-grading`              |
| Worktree      | `../worktrees/clauditor/3-llm-judge-grading` |
| Phase         | detailing                                  |
| Created       | 2026-04-08                                 |
| Sessions      | 1                                          |

---

## Discovery

### Ticket Summary

Add Layer 3 semantic quality evaluation to clauditor — four capabilities:

1. **3a: Rubric-Based Quality Grading** — Sonnet judges skill output against natural-language criteria, per-criterion pass/fail with evidence/reasoning
2. **3b: A/B Comparison** — Skill output vs raw-Claude baseline, same rubric, regression detection
3. **3c: Trigger Precision Testing** — LLM classifies whether queries would invoke the skill
4. **3d: Variance Measurement** — Run eval N times, measure consistency/stability

### Codebase Findings

- `src/clauditor/assertions.py` — Layer 1: `AssertionResult`, `AssertionSet`, `run_assertions()`
- `src/clauditor/grader.py` — Layer 2: `AsyncAnthropic` client, `extract_and_grade()`
- `src/clauditor/schemas.py` — `EvalSpec` (already has unused `grading_criteria: list[str]`)
- `src/clauditor/runner.py` — `SkillRunner.run()` executes via `claude` subprocess
- `src/clauditor/spec.py` — `SkillSpec` integration (run + evaluate)
- `src/clauditor/cli.py` — Commands: `validate`, `run`, `init`
- `src/clauditor/pytest_plugin.py` — Fixtures: `clauditor_runner`, `clauditor_spec`

---

## Architecture Review

| Area           | Rating  | Notes                                                       |
|----------------|---------|-------------------------------------------------------------|
| Security       | pass    | API key from env (existing pattern). No user input in prompts beyond skill output. |
| Performance    | pass    | Single API call per grade, parallel `asyncio.gather` for triggers/variance. |
| Data Model     | pass    | New dataclasses + EvalSpec extensions. Backward compatible (new fields optional). |
| API Design     | pass    | CLI commands follow existing `validate`/`run` pattern. Model configurable. |
| Observability  | concern | Need clear cost feedback in CLI output so users know what they're spending. |
| Testing        | pass    | Mock `AsyncAnthropic` for unit tests. Real API tests gated behind `--clauditor-grade`. |

**Concern resolution:** CLI `grade` and `triggers` commands will print the model being used in their header line. Add `--dry-run` flag that prints prompts without making API calls.

---

## Decisions

### DEC-001: Module Layout
**Decision:** Separate modules — `quality_grader.py` (3a + 3d), `comparator.py` (3b), `triggers.py` (3c)
**Rationale:** Keeps Layer 2 `grader.py` untouched. Each concern is independently testable.

### DEC-002: Trigger Testing Approach
**Decision:** LLM classification — send skill description + query to Sonnet, ask "would this trigger?"
**Rationale:** Simulates routing without needing Claude Code internals. Cheaper and faster than live invocation.

### DEC-003: A/B Comparison Scope
**Decision:** Include full A/B comparison in this ticket.
**Rationale:** Core differentiator for regression detection. Requires adding `run_raw()` to `SkillRunner`.

### DEC-004: Model Configuration
**Decision:** Default `claude-sonnet-4-6`, configurable via eval spec `grading_model` field and CLI `--model` flag.
**Rationale:** Sonnet is the right quality/cost balance for grading. Users may want to experiment with other models.

### DEC-005: Result Types
**Decision:** New dataclasses (`GradingResult`, `GradingReport`, etc.) do NOT subclass `AssertionResult`/`AssertionSet`.
**Rationale:** Layer 3 results carry richer data (scores, reasoning, comparisons). But they expose the same `.passed` + `.summary()` interface pattern for consumer compatibility.

### DEC-006: Cost Visibility
**Decision:** CLI commands print model name in header. Add `--dry-run` flag to print prompts without API calls.
**Rationale:** Layer 3 costs real money (~$0.01-0.10 per eval). Users need visibility.

---

## Detailed Breakdown

### US-001: Schema Extensions

**Description:** Add `TriggerTests`, `VarianceConfig` dataclasses and new fields to `EvalSpec`. Update `from_file()` and `to_dict()` serialization. Zero API dependency.

**Traces to:** DEC-004 (grading_model field)

**Acceptance Criteria:**
- `EvalSpec` has `grading_model`, `trigger_tests`, `variance` fields
- `from_file()` parses new fields from JSON (backward compatible — all optional)
- `to_dict()` serializes new fields
- Existing tests still pass
- New tests for round-trip serialization of new fields
- `pytest` passes

**Done when:** `EvalSpec.from_file()` loads an eval.json with all new fields and `to_dict()` round-trips them.

**Files:**
- `src/clauditor/schemas.py` — Add `TriggerTests`, `VarianceConfig` dataclasses; add `grading_model: str`, `trigger_tests: TriggerTests | None`, `variance: VarianceConfig | None` to `EvalSpec`; update `from_file()` and `to_dict()`
- `tests/test_schemas.py` — Add tests for new fields, round-trip, defaults
- `examples/.claude/commands/example-skill.eval.json` — Add `trigger_tests` and `variance` example sections

**Depends on:** none

---

### US-002: Quality Grader Core (3a)

**Description:** Implement rubric-based Sonnet grading. Builds the grading prompt, sends to LLM, parses structured JSON response into `GradingResult` / `GradingReport` dataclasses.

**Traces to:** DEC-001, DEC-004, DEC-005

**Acceptance Criteria:**
- `grade_quality(output, eval_spec, model)` async function returns `GradingReport`
- `GradingReport` has `passed`, `pass_rate`, `mean_score`, `summary()` 
- Prompt includes skill name, description, all criteria, output to evaluate
- Response parsing handles both raw JSON and markdown-wrapped JSON
- Parse failures return a report with a single failed result (not an exception)
- Unit tests with mocked `AsyncAnthropic` verify prompt construction and response parsing
- `pytest` passes

**Done when:** `await grade_quality(output, spec)` returns a `GradingReport` with per-criterion results.

**Files:**
- `src/clauditor/quality_grader.py` (new) — `GradingResult`, `GradingReport` dataclasses; `build_grading_prompt()`, `parse_grading_response()`, `grade_quality()` functions; `_require_anthropic()` helper
- `tests/test_quality_grader.py` (new) — Tests for dataclass aggregation, prompt construction, response parsing, mocked async grading

**Depends on:** US-001

**TDD:**
- `GradingReport` with all-passing results → `passed=True`, `pass_rate=1.0`
- `GradingReport` with one failure → `passed=False`, correct `pass_rate`
- `parse_grading_response` with valid JSON → list of `GradingResult`
- `parse_grading_response` with markdown-wrapped JSON → same result
- `parse_grading_response` with invalid JSON → empty list (graceful failure)
- `grade_quality` with mocked client → correct report

---

### US-003: Trigger Precision Testing (3c)

**Description:** LLM-based classification of whether user queries would invoke a skill. Tests both should-trigger and should-not-trigger lists, computes precision/recall/accuracy.

**Traces to:** DEC-002, DEC-004

**Acceptance Criteria:**
- `test_triggers(eval_spec, model)` async function returns `TriggerReport`
- Each query classified individually (no cross-contamination)
- Queries classified in parallel via `asyncio.gather`
- `TriggerReport` has `passed`, `precision`, `recall`, `accuracy`, `summary()`
- Unit tests with mocked client verify classification logic
- `pytest` passes

**Done when:** `await test_triggers(spec)` returns a `TriggerReport` with per-query results and aggregate metrics.

**Files:**
- `src/clauditor/triggers.py` (new) — `TriggerResult`, `TriggerReport` dataclasses; `build_trigger_prompt()`, `parse_trigger_response()`, `classify_query()`, `test_triggers()` functions
- `tests/test_triggers.py` (new) — Tests for precision/recall/accuracy computation, prompt construction, response parsing, mocked classification

**Depends on:** US-001

**TDD:**
- `TriggerReport` with all correct → `accuracy=1.0`, `passed=True`
- `TriggerReport` with false positive → lower precision
- `TriggerReport` with false negative → lower recall
- `parse_trigger_response` with valid JSON → correct tuple
- `classify_query` with mocked client → correct `TriggerResult`

---

### US-004: A/B Baseline Comparator (3b)

**Description:** Run skill output vs raw-Claude baseline, grade both against same rubric, detect regressions. Requires adding `run_raw()` to `SkillRunner`.

**Traces to:** DEC-003, DEC-005

**Acceptance Criteria:**
- `SkillRunner.run_raw(prompt)` executes raw Claude without skill prefix
- `compare_ab(spec, model)` async function returns `ABReport`
- Regression detected when baseline passes a criterion but skill fails
- `ABReport` has `passed` (no regressions), `regressions` list, `summary()`
- Unit tests verify regression detection logic and `run_raw` prompt construction
- `pytest` passes

**Done when:** `await compare_ab(spec)` returns an `ABReport` with per-criterion comparison.

**Files:**
- `src/clauditor/runner.py` — Add `run_raw(prompt: str) -> SkillResult` method
- `src/clauditor/comparator.py` (new) — `ABResult`, `ABReport` dataclasses; `compare_ab()` function
- `tests/test_comparator.py` (new) — Tests for regression detection, ABReport aggregation
- `tests/test_runner.py` (new) — Test `run_raw` prompt construction (no subprocess execution)

**Depends on:** US-002

**TDD:**
- `ABReport` with no regressions → `passed=True`
- `ABReport` with regression (baseline pass, skill fail) → `passed=False`
- `ABReport` where both fail → not a regression
- `ABReport` where skill wins → not a regression

---

### US-005: Variance Measurement (3d)

**Description:** Run the same eval N times, grade each, compute score statistics and stability metric.

**Traces to:** DEC-001

**Acceptance Criteria:**
- `measure_variance(spec, n_runs, model)` async function returns `VarianceReport`
- Skill runs are sequential (subprocess), grading calls are parallel
- `VarianceReport` has `score_mean`, `score_stddev`, `pass_rate_mean`, `stability`, `passed`, `summary()`
- `stability` = fraction of runs where all criteria passed; `passed` when stability >= eval spec's `min_stability` (default 0.8)
- Unit tests verify statistics computation with pre-built `GradingReport` lists
- `pytest` passes

**Done when:** `await measure_variance(spec, n_runs=3)` returns a `VarianceReport` with stability metrics.

**Files:**
- `src/clauditor/quality_grader.py` — Add `VarianceReport` dataclass and `measure_variance()` function
- `tests/test_quality_grader.py` — Add tests for variance statistics computation

**Depends on:** US-002

**TDD:**
- `VarianceReport` with 5 all-passing runs → `stability=1.0`, `passed=True`
- `VarianceReport` with 3/5 passing → `stability=0.6`, `passed=False` (below 0.8)
- `VarianceReport` stddev computation with known scores

---

### US-006: CLI Integration

**Description:** Add `clauditor grade` and `clauditor triggers` CLI commands. Both use `asyncio.run()` to call async functions. Support `--model`, `--json`, `--dry-run`, `--compare`, `--variance N` flags.

**Traces to:** DEC-004, DEC-006

**Acceptance Criteria:**
- `clauditor grade <skill>` runs rubric grading and prints per-criterion results
- `clauditor grade --compare` also runs A/B comparison
- `clauditor grade --variance N` runs N times with variance measurement
- `clauditor grade --dry-run` prints the prompt without making API calls
- `clauditor triggers <skill>` runs trigger precision testing
- `--model` overrides grading model; `--json` outputs structured JSON
- Exit code 0 if passed, 1 if failed
- Model name printed in header line
- `pytest` passes

**Done when:** All CLI commands work with `--dry-run` producing expected prompt output.

**Files:**
- `src/clauditor/cli.py` — Add `cmd_grade()`, `cmd_triggers()` functions; add `grade` and `triggers` subparsers

**Depends on:** US-002, US-003, US-004, US-005

---

### US-007: pytest Integration

**Description:** Add `@pytest.mark.clauditor_grade` marker gated by `--clauditor-grade` flag. Add `clauditor_grader` and `clauditor_triggers` fixtures.

**Traces to:** DEC-004

**Acceptance Criteria:**
- `--clauditor-grade` pytest option enables Layer 3 tests
- `--clauditor-model` overrides grading model
- Tests marked `@pytest.mark.clauditor_grade` are skipped without the flag
- `clauditor_grader` fixture returns factory that calls `grade_quality()`
- `clauditor_triggers` fixture returns factory that calls `test_triggers()`
- Marker registered in `pytest_configure`
- `pytest` passes

**Done when:** A test marked `@pytest.mark.clauditor_grade` is skipped by default and runs with `--clauditor-grade`.

**Files:**
- `src/clauditor/pytest_plugin.py` — Add options, marker registration, skip logic, fixtures
- `pyproject.toml` — Add `pytest-asyncio>=0.23.0` to dev dependencies

**Depends on:** US-002, US-003

---

### US-008: Exports and Polish

**Description:** Export new public types from `__init__.py`. Update `cmd_init` to include Layer 3 fields in generated eval specs. Bump version.

**Acceptance Criteria:**
- All new public types in `__all__`
- Layer 3 types use lazy imports to avoid requiring `anthropic` at import time
- `clauditor init` generates eval spec with `grading_criteria`, `trigger_tests`, `variance` stubs
- `pytest` passes

**Done when:** `from clauditor import GradingReport, TriggerReport, ABReport` works without `anthropic` installed.

**Files:**
- `src/clauditor/__init__.py` — Add lazy imports for Layer 3 types
- `src/clauditor/cli.py` — Update `cmd_init` starter dict with new fields

**Depends on:** US-001 through US-007

---

### US-009: Quality Gate

**Description:** Run code review across full changeset. Fix all real bugs found. Ensure all tests pass.

**Acceptance Criteria:**
- 4 code review passes across all changed files
- All identified bugs fixed
- `pytest` passes with no warnings
- Ruff linting passes

**Done when:** Clean code review and green test suite.

**Depends on:** US-001 through US-008

---

### US-010: Patterns & Memory

**Description:** Update documentation or rules with patterns learned during implementation.

**Acceptance Criteria:**
- Any new conventions documented
- Example eval spec updated with realistic Layer 3 usage

**Done when:** Documentation reflects new capabilities.

**Depends on:** US-009
