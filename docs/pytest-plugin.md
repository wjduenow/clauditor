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

### SkillResult fields

The following fields on `SkillResult` are the supported public surface that tests may assert on:

- `output: str` — concatenated assistant text from all turns.
- `exit_code: int` — subprocess exit code (0 = clean exit, -1 = clauditor-internal failure like FileNotFound or timeout).
- `error: str | None` — user-facing error message when the run failed. May come from subprocess stderr or from a stream-json `is_error: true` result message (see `docs/stream-json-schema.md`).
- `error_category: Literal["rate_limit", "auth", "api", "interactive", "subprocess", "timeout"] | None` — classification of the failure when `error` is set. `None` on success. Enables category-aware test branching (e.g. `if result.error_category == "rate_limit": pytest.skip(...)`).
- `succeeded: bool` — `True` when `exit_code == 0 and output.strip() != ""`. Lenient by design: a run that emitted output **and** hit an API error or interactive-hang heuristic may still be `succeeded`. Example: an interactive-hang run produces `exit_code=0`, `output="What color do you want?"`, `error=None`, `error_category="interactive"` → `succeeded is True`.
- `succeeded_cleanly: bool` — stricter predicate: `True` only when `succeeded` AND `error is None` AND `error_category is None` AND no entry in `warnings` starts with the interactive-hang prefix. Use this when your test means "actually completed cleanly, with nothing weird in the transcript." On the interactive-hang example above, `succeeded_cleanly is False`.
- `input_tokens: int` — Anthropic input token count (0 if not reported).
- `output_tokens: int` — Anthropic output token count (0 if not reported).
- `duration_seconds: float` — wall-clock seconds from start of subprocess to exit.

The following fields on `SkillResult` are internal-observability-only and may change without notice; do not assert on them in tests: `raw_messages`, `stream_events`, `warnings`, `outputs`.

Options:

```bash
pytest --clauditor-project-dir /path/to/project
pytest --clauditor-timeout 300
pytest --clauditor-claude-bin /usr/local/bin/claude
pytest --clauditor-grade              # Enable Layer 3 tests (costs money)
pytest --clauditor-model claude-sonnet-4-6  # Override grading model
```

Mark tests that need Layer 3 with `@pytest.mark.clauditor_grade`; they are skipped by default and only run under `--clauditor-grade`.
