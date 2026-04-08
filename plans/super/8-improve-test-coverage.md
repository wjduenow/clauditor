# Super Plan: #8 — Improve Test Coverage from 44% to 80%+

## Meta

| Field        | Value |
|--------------|-------|
| Ticket       | [#8](https://github.com/wjduenow/clauditor/issues/8) |
| Branch       | `feature/8-improve-test-coverage` |
| Phase        | `discovery` |
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

### Scoping Questions

_See below — awaiting user answers._

---

## Architecture Review

_Pending Phase 2._

---

## Refinement Log

### Sessions

_Pending Phase 3._

### Decisions

_Pending Phase 3._

---

## Detailed Breakdown

_Pending Phase 4._

---

## Beads Manifest

_Pending Phase 7._
