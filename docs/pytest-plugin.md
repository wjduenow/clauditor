# Pytest Integration

Reference for clauditor's pytest plugin: the fixtures it registers, the command-line options it adds, and how to wire Layer 3 grading into a test run. Read this when you're authoring tests against skills and want the plugin's full surface rather than the copy-paste-from-Quick-Start subset.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

clauditor registers as a pytest plugin automatically. Available fixtures:

- `clauditor_runner` — **factory** returning a configured `SkillRunner`. Signature: `clauditor_runner(harness: str | None = None) → SkillRunner`. Call with no args (`runner = clauditor_runner()`) to get the default-resolved harness, or pass `harness="codex"` / `harness="claude-code"` to pin one for the test. **Migration note (#155):** previously a value fixture (`runner = clauditor_runner`); now you must invoke it (`runner = clauditor_runner()`). Hard break, no deprecation shim — see `CHANGELOG.md` under `[Unreleased]` for the one-line migration.
- `clauditor_asserter` — factory wrapping a `SkillResult` with `assert_*` helpers (`assert_contains`, `assert_not_contains`, `assert_matches`, `assert_has_urls`, `assert_has_entries`, `assert_min_count`, `assert_min_length`, `run_assertions`) — see `.claude/rules/data-vs-asserter-split.md`. **Migration note:** the `assert_*` methods previously lived directly on `SkillResult`; they now live on a separate `SkillAsserter` class (`src/clauditor/asserters.py`). Existing test code calling `result.assert_contains(...)` must switch to `clauditor_asserter(result).assert_contains(...)` — this is a hard break with no deprecation shim (pre-1.0 project; see `CHANGELOG.md` for details).
- `clauditor_spec` — factory for loading `SkillSpec` from skill files. Honors `eval_spec.harness` automatically; eagerly fires `check_codex_auth` at factory call time when the spec declares `harness: "codex"` so a missing `CODEX_API_KEY` / `OPENAI_API_KEY` surfaces as a crisp `CodexAuthMissingError` rather than a deep subprocess failure.
- `clauditor_grader` — factory for Layer 3 quality grading. Signature: `clauditor_grader(skill_path, eval_path=None, output=None, *, provider=None, model=None) → GradingReport`. Both `provider=` and `model=` are operator-intent overrides at the top of the precedence stack.
- `clauditor_triggers` — factory for trigger precision testing. Signature: `clauditor_triggers(skill_path, eval_path=None, *, provider=None, model=None)`.
- `clauditor_blind_compare` — factory wrapping `blind_compare_from_spec` for A/B comparison of two skill outputs (requires `user_prompt` on the eval spec). Signature: `clauditor_blind_compare(skill_path, output_a, output_b, eval_path=None, *, provider=None, model=None) → BlindReport`.
- `clauditor_capture` — factory returning a `Path` to `tests/eval/captured/<skill>.txt` for captured-output tests

> **Auth required for grading fixtures — per-provider model.** `clauditor_grader`, `clauditor_blind_compare`, and `clauditor_triggers` resolve the active grading provider per the precedence chain below and dispatch a per-provider auth guard at fixture-invocation time. Both providers raise distinct exception classes (sibling of `Exception`, not subclasses of each other) so tests can route on a structural `except` ladder per `.claude/rules/multi-provider-dispatch.md`.
>
> - **`provider="anthropic"`** (default) — strict `ANTHROPIC_API_KEY` check; raises `AnthropicAuthMissingError` when the key is missing. Strict by default **even when the `claude` CLI is on PATH** so a CI run under subscription-only auth surfaces a config regression rather than silently routing through the CLI transport. To opt into CLI transport (relaxed Anthropic guard accepting subscription auth via the `claude` CLI on PATH), set `CLAUDITOR_FIXTURE_ALLOW_CLI=1` in the test environment.
> - **`provider="openai"`** — strict `OPENAI_API_KEY` check; raises `OpenAIAuthMissingError` when the key is missing. **There is intentionally no `CLAUDITOR_FIXTURE_ALLOW_OPENAI` env var** — OpenAI has no CLI-fallback / subscription analog (per #145 DEC-002), so there is no relaxed-mode to opt into. The asymmetry is deliberate (DEC-001 of #155).
>
> See [`docs/transport-architecture.md`](transport-architecture.md) for the auth-state matrix on the Anthropic side.

### SkillResult fields

The following fields on `SkillResult` are the supported public surface that tests may assert on:

- `output: str` — concatenated assistant text from all turns.
- `exit_code: int` — subprocess exit code (0 = clean exit, -1 = clauditor-internal failure like FileNotFound or timeout).
- `error: str | None` — user-facing error message when the run failed. May come from subprocess stderr or from a stream-json `is_error: true` result message (see `docs/stream-json-schema.md`). May be `None` even on failure (e.g. the interactive-hang heuristic sets `error_category="interactive"` without an error string — check `error_category` and `warnings` for a complete picture).
- `error_category: Literal["rate_limit", "auth", "api", "interactive", "subprocess", "timeout"] | None` — classification of any non-clean signal. `None` on a clean run. May be set even when `error` is `None` (e.g. the interactive-hang case). Enables category-aware test branching (e.g. `if result.error_category == "rate_limit": pytest.skip(...)`).
- `succeeded: bool` — `True` when `exit_code == 0 and output.strip() != ""`. Lenient by design: a run that emitted output **and** hit an API error or interactive-hang heuristic may still be `succeeded`. Example: an interactive-hang run produces `exit_code=0`, `output="What color do you want?"`, `error=None`, `error_category="interactive"` → `succeeded is True`.
- `succeeded_cleanly: bool` — stricter predicate: `True` only when `succeeded` AND `error is None` AND `error_category is None` AND no entry in `warnings` starts with the interactive-hang prefix. Use this when your test means "actually completed cleanly, with nothing weird in the transcript." On the interactive-hang example above, `succeeded_cleanly is False`.
- `input_tokens: int` — Anthropic input token count (0 if not reported).
- `output_tokens: int` — Anthropic output token count (0 if not reported).
- `duration_seconds: float` — wall-clock seconds from start of subprocess to exit.
- `api_key_source: str | None` — auth source the child `claude -p` reported (parsed from the stream-json `system/init` message's `apiKeySource`). Example values: `"ANTHROPIC_API_KEY"`, `"claude.ai"`, `"none"`. `None` when the field was absent (older CLI builds) or malformed. Useful for asserting which tier a test ran against (e.g. `assert result.api_key_source == "claude.ai"` when running under `--clauditor-no-api-key`). See `docs/stream-json-schema.md` for the parser contract.

The following fields on `SkillResult` are internal-observability-only and may change without notice; do not assert on them in tests: `raw_messages`, `stream_events`, `warnings`, `outputs`.

Options:

```bash
pytest --clauditor-project-dir /path/to/project
pytest --clauditor-timeout 300
pytest --clauditor-claude-bin /usr/local/bin/claude
pytest --clauditor-no-api-key                   # Strip ANTHROPIC_{API_KEY,AUTH_TOKEN} + OPENAI_API_KEY (codex preserves OPENAI_API_KEY)
pytest --clauditor-grade                        # Enable Layer 3 tests (costs money)
pytest --clauditor-model claude-sonnet-4-6      # Override grading model
pytest --clauditor-harness codex                # Override harness for this session ({claude-code,codex,auto})
pytest --clauditor-grading-provider openai      # Override grading provider ({anthropic,openai,auto})
```

`--clauditor-no-api-key` is the plugin-option counterpart to `--no-api-key` on the CLI: strips `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, **and `OPENAI_API_KEY`** (the default `env_without_api_key()` strip set) from the skill subprocess environment so the child falls back to whatever auth is cached in `~/.claude/` (typically a Pro/Max subscription). The codex harness branch preserves `OPENAI_API_KEY` so the codex subprocess can still authenticate — `clauditor_spec` computes the scrub per-call with the resolved harness name so codex callers retain their key. Scoped to the `clauditor_spec` fixture's `env_override` wiring; the bare `clauditor_runner` fixture is unaffected (its `SkillRunner` is constructed without the env scrub). For per-test overrides, `spec.run(env_override=..., timeout_override=...)` accepts both kwargs directly — the fixture wrapper forwards caller-provided values over the fixture-level default.

`--clauditor-harness` (#155) overrides the harness used by `clauditor_runner` for the entire pytest session. Operator-intent precedence on `clauditor_runner`: factory `harness=` kwarg > `--clauditor-harness` > `CLAUDITOR_HARNESS` env > default `"auto"`. Auto-resolution mirrors the CLI: `shutil.which("claude")` first, then `shutil.which("codex")`, with a one-time stderr announcement when auto picks codex. **Scope note:** the option does NOT override `clauditor_spec`'s harness selection — that fixture honors only `EvalSpec.harness` (author intent). Set `harness:` in `eval.json` for per-skill author preference; use `clauditor_runner(harness=...)` or this CLI option for operator-intent selection of the bare runner.

`--clauditor-grading-provider` (#155) overrides the grading provider used by `clauditor_grader`, `clauditor_blind_compare`, and `clauditor_triggers` for the entire pytest session. Operator-intent precedence: factory `provider=` kwarg > `--clauditor-grading-provider` > `CLAUDITOR_GRADING_PROVIDER` env > `EvalSpec.grading_provider` > default `"auto"` (auto-inferred from `--clauditor-model` / `EvalSpec.grading_model` per `claude-*` → anthropic, `gpt-*` / `o[0-9]+*` → openai).

Mark tests that need Layer 3 with `@pytest.mark.clauditor_grade`; they are skipped by default and only run under `--clauditor-grade`.

## Parametrizing harness × provider

clauditor exposes harness (skill runtime) and grading-provider (judge runtime) as independent axes, so a single test can sweep across `{claude-code, codex} × {anthropic, openai}` without changing skill or eval spec. The two axes are structurally separate per `.claude/rules/multi-provider-dispatch.md`; the factory kwargs are the highest-precedence layer of the operator-intent stack.

### Operator-intent precedence (highest → lowest)

The two axes have slightly different chains because the harness axis on `clauditor_runner` has no spec layer (the runner factory does not load an `EvalSpec`).

**Provider axis** (`clauditor_grader`, `clauditor_blind_compare`, `clauditor_triggers`):

1. **Factory kwarg** — e.g. `clauditor_grader(skill, output=..., provider="openai")`.
2. **Pytest CLI option** — `--clauditor-grading-provider=openai`.
3. **Env var** — `CLAUDITOR_GRADING_PROVIDER=openai`.
4. **Spec field** — `EvalSpec.grading_provider`.
5. **Default** — `"auto"` (model-prefix inference).

**Harness axis** (`clauditor_runner` factory only):

1. **Factory kwarg** — `clauditor_runner(harness="codex")`.
2. **Pytest CLI option** — `--clauditor-harness=codex`.
3. **Env var** — `CLAUDITOR_HARNESS=codex`.
4. **Default** — `"auto"` (PATH lookup: `claude` first, then `codex`).

`clauditor_spec` honors only `EvalSpec.harness` (author intent) and applies it via `harness_name_override` when wrapping `spec.run`; the operator-intent layers (CLI flag, env, factory kwarg) do not affect `clauditor_spec`'s harness selection. To pin a session-wide harness for skills loaded via `clauditor_spec`, set the `harness:` field in each skill's `eval.json`.

Each layer falls through to the next when `None` (or `"auto"`, for the auto-resolved fields). Mirrors the CLI seam exactly per `.claude/rules/spec-cli-precedence.md`.

### Worked example: `pytest.mark.parametrize` over `{harness, provider}`

```python
import pytest


@pytest.mark.parametrize(
    "harness,provider",
    [
        ("claude-code", "anthropic"),
        ("codex", "openai"),
    ],
)
@pytest.mark.clauditor_grade
def test_my_skill_across_stacks(
    clauditor_runner,
    clauditor_grader,
    harness,
    provider,
):
    runner = clauditor_runner(harness=harness)
    result = runner.run("my-skill")
    report = clauditor_grader(
        "skills/my-skill/SKILL.md",
        output=result.output,
        provider=provider,
    )
    assert report.pass_rate >= 0.8
```

The same matrix can be driven from the command line with no test changes — the pytest CLI options sit one precedence layer below the factory kwargs, so leaving the kwargs off lets the session-wide flags take over:

```bash
pytest --clauditor-harness=codex --clauditor-grading-provider=openai
```

### Cross-axis isolation (DEC-006)

`clauditor_runner` accepts only `harness=`. The grading fixtures (`clauditor_grader`, `clauditor_blind_compare`, `clauditor_triggers`) accept only `provider=` and `model=`. The two axes are independent — the runner has no grading concern, and the graders do not run the skill subprocess. Conflating them would re-introduce the "harness ≠ provider" bug DEC-010 of #151 explicitly avoided.

### Related commands (not covered by fixtures)

`clauditor lint` is a standalone CLI command for static agentskills.io spec conformance; it is not exposed as a pytest fixture. Invoke it directly (e.g. from a `subprocess.run` call or a release-gate script) rather than expecting a `clauditor_lint` fixture. See [`docs/cli-reference.md#lint`](cli-reference.md#lint).
