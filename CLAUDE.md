# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->


## Build & Test

```bash
uv sync --dev               # Install dependencies
uv run ruff check src/ tests/  # Lint
uv run pytest --cov=clauditor --cov-report=term-missing  # Test with coverage (80% gate enforced)
```

## Architecture Overview

Three-layer skill evaluation framework:
- **Layer 1** (`assertions.py`): Deterministic checks (regex, string matching, counting)
- **Layer 2** (`grader.py`): LLM-graded schema extraction via Haiku (supports tiered sections with per-tier field requirements)
- **Layer 3** (`quality_grader.py`, `triggers.py`): LLM-graded quality and trigger precision via Sonnet

Supporting modules: `runner.py` (subprocess execution), `spec.py` (orchestrator), `schemas.py` (data models), `cli.py` (CLI entry point), `comparator.py` (A/B testing), `pytest_plugin.py` (pytest integration).

## Conventions & Patterns

### Testing
- Tests in `tests/`, one file per source module (`test_<module>.py`)
- Class-based test organization (`TestFromFile`, `TestEvaluate`, etc.)
- `asyncio_mode = "strict"` — async tests require `@pytest.mark.asyncio`
- Mock external calls with `unittest.mock` (MagicMock, AsyncMock, patch)
- Modules imported by the pytest plugin before coverage starts need `importlib.reload()` at the top of their test file (see `test_schemas.py` for pattern)
- `tests/conftest.py` has shared fixtures; must NOT shadow plugin fixture names (`clauditor_runner`, `clauditor_spec`, `clauditor_grader`, `clauditor_triggers`)
- Use `tmp_path` for file-based tests, not `tempfile` directly
