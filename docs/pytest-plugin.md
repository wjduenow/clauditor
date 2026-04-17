# Pytest Integration

Reference for clauditor's pytest plugin: the fixtures it registers, the command-line options it adds, and how to wire Layer 3 grading into a test run. Read this when you're authoring tests against skills and want the plugin's full surface rather than the copy-paste-from-Quick-Start subset.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

clauditor registers as a pytest plugin automatically. Available fixtures:

- `clauditor_runner` — pre-configured `SkillRunner`
- `clauditor_asserter` — factory wrapping a `SkillResult` with `assert_*` helpers (`assert_contains`, `assert_not_contains`, `assert_matches`, `assert_has_urls`, `assert_has_entries`, `assert_min_count`, `assert_min_length`, `run_assertions`) — see `.claude/rules/data-vs-asserter-split.md`. **Migration note:** the `assert_*` methods previously lived directly on `SkillResult`; they now live on a separate `SkillAsserter` class (`src/clauditor/asserters.py`). Existing test code calling `result.assert_contains(...)` must switch to `clauditor_asserter(result).assert_contains(...)` — this is a hard break with no deprecation shim (pre-1.0 project; see `CHANGELOG.md` for details).
- `clauditor_spec` — factory for loading `SkillSpec` from skill files
- `clauditor_grader` — factory for Layer 3 quality grading
- `clauditor_triggers` — factory for trigger precision testing
- `clauditor_blind_compare` — factory wrapping `blind_compare_from_spec` for A/B comparison of two skill outputs (requires `user_prompt` on the eval spec)
- `clauditor_capture` — factory returning a `Path` to `tests/eval/captured/<skill>.txt` for captured-output tests

Options:

```bash
pytest --clauditor-project-dir /path/to/project
pytest --clauditor-timeout 300
pytest --clauditor-claude-bin /usr/local/bin/claude
pytest --clauditor-grade              # Enable Layer 3 tests (costs money)
pytest --clauditor-model claude-sonnet-4-6  # Override grading model
```

Mark tests that need Layer 3 with `@pytest.mark.clauditor_grade`; they are skipped by default and only run under `--clauditor-grade`.
