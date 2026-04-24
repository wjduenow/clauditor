# Transport Architecture

clauditor's LLM-mediated commands (`grade`, `extract`, `propose-eval`, `suggest`, `triggers`, `compare --blind`) route their single Anthropic call through one of two transports: an HTTP SDK path (the `anthropic` Python SDK's `AsyncAnthropic` client) or a subprocess path that shells out to the `claude` CLI. Read this doc when you need to understand which transport a given invocation will pick, what auth state each path requires, how precedence resolves across CLI flags / env / spec, or what failure categories each transport can surface.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a one-sentence summary under [Authentication and API Keys](../docs/cli-reference.md#authentication-and-api-keys).

## Why two transports

The Python `anthropic` SDK is API-only: it reads `ANTHROPIC_API_KEY` from the environment and cannot pick up the Claude Pro/Max subscription credentials cached under `~/.claude/` by the `claude` CLI. Before #86, the six LLM-mediated commands hard-required `ANTHROPIC_API_KEY` for that reason — a Pro/Max subscriber who had not also bought API credits could not run `clauditor grade` at all (tracked in #83, which shipped the exit-2 pre-flight guard).

Routing the same single-turn prompt through `claude -p` sidesteps that limitation: the CLI reads its own cached subscription auth, so any Pro/Max subscriber with `claude` on PATH can drive L3 grading + the other five LLM-mediated commands without an API key. The trade-off is latency (a subprocess spawn plus stream-json parse per call) and observability surface (no SDK `Message` object, so `raw_message` is `None` on the CLI path — see [Known limitations](#known-limitations)).

The two transports are designed to be **interchangeable** at the call-site level: both flow through `clauditor._anthropic.call_anthropic`, both return an `AnthropicResult` with the same field shape, and both surface non-retriable failures as `AnthropicHelperError` subclasses. Callers that want to branch on transport can `except ClaudeCLIError:` (CLI-only subclass); callers that do not care stay transport-blind via `except AnthropicHelperError:`.

## Auth-state matrix

The effective transport depends on which auth prerequisites are satisfied at invocation time. Two independent axes: whether `ANTHROPIC_API_KEY` is exported, and whether the `claude` CLI is on PATH and authenticated. The `auto` default (no flag, no env, no spec field) picks **CLI when available** per DEC-001 subscription-first; explicit `--transport api` / `--transport cli` short-circuits the check.

| API key set? | `claude` CLI on PATH? | `auto` default | `--transport cli` | `--transport api` |
| :----------: | :-------------------: | -------------- | ----------------- | ----------------- |
| ✓ | ✓ | CLI (subscription auth wins) | CLI | API (subscription auth bypassed) |
| ✓ | ✗ | API | fails: `ClaudeCLIError(category=transport)` (binary missing) | API |
| ✗ | ✓ | CLI | CLI | fails: exit 2 pre-flight or `AnthropicHelperError` mid-call |
| ✗ | ✗ | fails: exit 2 pre-flight (no auth path available) | fails: exit 2 pre-flight | fails: exit 2 pre-flight |

Notes on the edge cells:

- **Row 1 (both present, auto default)**: CLI wins because DEC-001 is subscription-first. Operators with an API key who want the SDK path for logging / observability / latency reasons can pass `--transport api` explicitly or set `CLAUDITOR_TRANSPORT=api`.
- **Row 2 (API key only, `--transport cli`)**: the subprocess attempts to spawn `claude` and fails at binary resolution. The helper raises `ClaudeCLIError(category="transport")` with the sanitized DEC-014 message; the CLI routes to exit 3 per `.claude/rules/llm-cli-exit-code-taxonomy.md`.
- **Row 3 (CLI only, `--transport api`)**: the explicit `api` override bypasses the CLI branch. The pre-flight guard (`check_any_auth_available`) passes because the CLI *is* available, but the actual SDK call then raises `AnthropicHelperError` wrapping the SDK's auth failure. Operators who forced `api` here are expected to know what they asked for.
- **Row 4 (neither)**: `check_any_auth_available` fails at exit 2 with the DEC-015 actionable message ("ANTHROPIC_API_KEY not set AND claude CLI not on PATH").

The pre-flight guard is **relaxed** under #86: it passes when *either* auth path is available, matching the `auto` default's behavior. The three pytest fixtures (`clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare`) stay **strict** by default (DEC-009): they require `ANTHROPIC_API_KEY` so a CI run under subscription-only auth surfaces a config regression instead of silently falling back to the CLI. Opt in to the relaxed fixture guard with `CLAUDITOR_FIXTURE_ALLOW_CLI=1`.

The `--no-api-key` flag (a separate, older mechanism targeted at the `claude -p` subprocess the **skill runner** spawns) is distinct from the `--transport` flag this doc covers. `--no-api-key` strips the key from the child env so skill runs land under subscription auth; `--transport` picks which path clauditor's own LLM calls take. Both can be combined — e.g. `clauditor grade ... --no-api-key --transport cli` runs both the skill *and* the grader under subscription auth.

## Precedence

The transport for any LLM-mediated call resolves through four layers, highest wins. The resolution happens inside `SkillSpec.run` (for skill-runner-mediated calls) and at the CLI entry point (for the six LLM-mediated commands), so every caller — CLI, pytest fixture, future batch runner — inherits the same precedence with no per-caller drift.

| Priority | Layer | How it is set | When to use |
| :------: | ----- | ------------- | ----------- |
| 1 (highest) | `--transport` CLI flag | Per-invocation override on `grade`, `extract`, `propose-eval`, `suggest`, `triggers`, `compare --blind`. Values: `api`, `cli`, `auto`. | Operator knows exactly which transport they want for this one run (CI pipeline forcing API path for logging consistency; one-off debug run forcing CLI path). |
| 2 | `CLAUDITOR_TRANSPORT` env var | Exported in shell / CI config. Same three values. | Per-shell / per-CI-job default that overrides any spec field but yields to an explicit CLI flag. |
| 3 | `EvalSpec.transport` field | Declared in the skill's `eval.json`. Same three values. Defaults to `auto` when the field is absent; explicit `null` is invalid. | Per-skill author preference — e.g. a research skill that routinely exhausts the API tier can declare `"transport": "cli"` so every run uses subscription auth by default. |
| 4 (lowest) | Built-in default `"auto"` | Hardcoded in `clauditor._anthropic.resolve_transport`. Picks CLI when the `claude` binary is on PATH, else API. | Fallback when none of the above is set. Most operators see this path. |

The precedence direction is **load-bearing**: operator intent (CLI flag) wins over CI / shell intent (env) wins over author intent (spec) wins over library intent (default). Flipping any pair would silently override a more-specific signal with a less-specific one — for example, letting the spec field win over the CLI flag would defeat a pipeline that forced `--transport api` for debugging. This direction matches `.claude/rules/spec-cli-precedence.md`, which codifies the same shape for the `timeout` knob.

The `auto` resolution itself happens at call time via `shutil.which("claude")`: if the binary is on PATH, CLI wins; otherwise API wins. Per DEC-019, the **first** `auto → cli` resolution per Python process emits a one-shot stderr line:

```
clauditor: using Claude CLI transport (subscription auth); pass --transport api to opt out
```

Explicit `--transport cli` never announces (no surprise — the operator picked it). Explicit `--transport api` never announces. Subsequent auto-resolutions in the same process are silent (flag `_announced_cli_transport` is flipped on the first emission).

## Error categories

Both transports surface non-retriable failures as `AnthropicHelperError` instances. The CLI path's subclass `ClaudeCLIError` carries an additional `category` attribute (`"rate_limit"`, `"auth"`, `"api"`, `"transport"`) so downstream tooling can branch without substring-matching error text. The category drives the retry ladder (`_compute_retry_decision`) so both transports apply the same retry budget for the same category of failure (DEC-005 retry parity).

The four DEC-014 templates are committed verbatim in `src/clauditor/_anthropic.py::_CLI_ERROR_TEMPLATES`; any phrasing drift surfaces as a red test. The machine-readable suffix `(transport=cli, category=<cat>)` is parseable by log scrapers and future `clauditor audit --by-category` segmentation without substring-matching exception text.

| Category | Meaning | Retry budget | Exit code |
| -------- | ------- | ------------ | --------- |
| `rate_limit` | Anthropic throttled the request (HTTP 429 on SDK; stream-json `is_error` with rate-limit keywords on CLI). | Up to 3 retries with `2^i` exponential backoff + ±25% jitter. | 3 after exhaustion |
| `auth` | Authentication failed (401 / 403 on SDK; stream-json `is_error` with auth keywords on CLI). | No retry — fix the credential and re-run. | 3 immediately |
| `api` | API-side failure (HTTP 5xx on SDK; stream-json `is_error` with API keywords on CLI). | 1 retry then raise. | 3 after exhaustion |
| `transport` | **CLI-only**. Subprocess-level failure: binary missing (`exit_code=-1`), watchdog timeout, malformed stream-json, empty output. | 1 retry then raise. | 3 after exhaustion |

The CLI path's `transport` category has no SDK analog — the SDK's equivalent is `APIConnectionError` (same 1-retry budget), but the failure modes are different (the CLI can fail to spawn; the SDK can fail to reach the API endpoint). Both ladder decisions go through the shared `_compute_retry_decision` helper so the retry budget is the same number of attempts regardless of which transport produced the failure.

Every `ClaudeCLIError` message is **sanitized**: no `str(exc)`, no `invoke.error` text, no stream-json `result` content. The fixed template + category suffix is the whole user-facing surface. The original `RuntimeError` carrying the raw invoke result is preserved on `__cause__` via `raise ... from cause` so debuggers can still find the root failure without the user-facing stderr leaking anything about the skill's runtime state (defense-in-depth per `.claude/rules/non-mutating-scrub.md` spirit, applied to error envelopes rather than transcripts).

## Spawn-overhead benchmark

The CLI transport pays a subprocess-spawn cost plus a stream-json parse cost per call; the SDK transport pays a TLS handshake + httpx-client construction cost on the first call in a process. The script `scripts/bench_cli_transport.py` measures both with a trivial prompt so operators can decide when the CLI path's convenience is worth the latency.

```bash
# Run on a dedicated machine (live ``claude`` + valid API key).
uv run python scripts/bench_cli_transport.py
uv run python scripts/bench_cli_transport.py --runs 20 --model claude-haiku-4-5
```

Per DEC-016: single machine, ≥10 runs per transport, report mean + p95 + stddev, distinguish cold (first call in process) vs warm (steady state).

**Representative numbers** — see `scripts/bench_cli_transport.py`. Run it on a dedicated machine to generate actual numbers for your hardware; the script reports mean, median, p95, and stddev for both transports across cold (first call in process) and warm (steady state) phases.

The relevant derived metric is the **CLI warm overhead vs SDK warm** — the mean-delta and p95-delta an operator should expect when routing many calls through the CLI path. A small positive overhead (CLI > API by a fraction of a second on warm calls) is the expected shape; any larger gap is worth investigating (network issues, stale subscription auth, an unusually slow `claude` binary path).

Rerun the script when any of the following change: the `claude` binary version, the clauditor version, the Anthropic model, or the local machine (kernel / CPU / network). Raw output from the script is the canonical source; the table above is a human-readable summary.

## Migration from pre-#86

Before #86 landed, the six LLM-mediated commands hard-required `ANTHROPIC_API_KEY` and all LLM calls went through the SDK. After #86, the default `auto` transport picks the CLI path whenever `claude` is on PATH. This can surprise operators who had both prerequisites available — they previously always got the API path; now they get the CLI path by default.

**I had both, now it's CLI — how do I opt out?**

Three escape hatches, in descending order of scope:

1. **Per-invocation**: pass `--transport api` on the command line. Scoped to this one run; nothing persists.
2. **Per-shell / per-CI-job**: export `CLAUDITOR_TRANSPORT=api`. Scoped to the shell or job; clears when the env is reset.
3. **Per-skill**: add `"transport": "api"` to the skill's `eval.json`. Scoped to this skill; every invocation uses the API path until the field is removed or overridden.

**I want the old exit-2 auth guard back (strict API-key-only).**

For pytest fixtures, the strict guard is the default — fixtures keep requiring `ANTHROPIC_API_KEY` unless you opt in with `CLAUDITOR_FIXTURE_ALLOW_CLI=1`. For the CLI commands, the relaxed guard is the new default and there is no global "strict mode" flag; if you need strictness at the command level (e.g. a CI job that must refuse to run under subscription auth), combine `--transport api` with an ambient check that `ANTHROPIC_API_KEY` is set before invoking clauditor.

**My tests started silently passing with no API key.**

Only possible if you set `CLAUDITOR_FIXTURE_ALLOW_CLI=1` *and* have `claude` on PATH. Unset the env var to restore the strict fixture guard. Without the env var, the three grading fixtures still raise `AnthropicAuthMissingError` when `ANTHROPIC_API_KEY` is absent, surfacing the config regression.

**My `raw_message` assertions broke.**

On the CLI transport, `AnthropicResult.raw_message` is always `None` (there is no SDK `Message` object to attach — the subprocess output is the whole response). Tests that inspect `raw_message` for tool-use blocks or refusal content need to either (a) force `--transport api` in the test env, or (b) fall back to `text_blocks` for the content they were fishing out of `raw_message`. See [Known limitations](#known-limitations) below.

## Known limitations

The two transports have intentional observability gaps that callers should know about:

- **`raw_message` is `None` on the CLI transport.** The subprocess output carries no SDK `Message` object, so callers that introspected `raw_message.content` (for tool-use blocks, refusal handling, model-specific metadata) must fall through to `text_blocks` on the CLI path. Documented on `AnthropicResult.raw_message` per DEC-007. The `text_blocks` field is populated on both transports — a single-element list containing `invoke.output` on CLI, per-content-block text on SDK.
- **No cache-token accounting under the CLI.** The SDK's `response.usage` surfaces prompt-caching breakdowns (`cache_creation_input_tokens`, `cache_read_input_tokens`); the CLI's stream-json `result` event carries only the two top-level counts (`input_tokens`, `output_tokens`). Callers that rely on cache-hit rate metrics should force `--transport api` until the CLI's stream-json schema grows the cache fields.
- **`api_key_source` is CLI-only.** The `SkillResult.api_key_source` field (populated from the stream-json `system/init` event's `apiKeySource`) is only meaningful on the CLI path — the SDK transport does not emit that signal. Under the SDK path, `api_key_source` stays at `None`. This means a test that asserts `result.api_key_source == "claude.ai"` will fail under any code path that forces the SDK transport, even if the user is actually authenticated through their subscription.
- **CLI transport ignores `max_tokens`, but does forward `model`.** `call_anthropic(prompt, model=..., max_tokens=..., transport="cli")` accepts the same signature for parity, but only `model` is forwarded to the `claude -p` subprocess (as `--model <value>`); `max_tokens` is not currently passed through — the CLI binary retains its own response-length handling. Callers that need a specific model for a specific call can use the CLI transport; callers that need per-call `max_tokens` control must force `--transport api`.
- **No streaming on either transport surface.** Both branches return a fully-assembled `AnthropicResult` once the response (or subprocess) completes; neither yields chunks. Streaming is out of scope for `call_anthropic` — the clauditor callers that consume the result all need the full text before doing anything useful.
- **Subprocess timeout is fixed at 180 s for the CLI transport.** Unlike `SkillRunner.run`, which threads the resolved timeout through `_invoke_claude_cli`, the `call_anthropic` CLI branch uses a hardcoded 180 s default. (Note: `SkillRunner`'s own default is 300 s as of #104 — the CLI-transport grader budget is intentionally tighter because a single grading call should not legitimately take that long.) A per-call override is not currently wired. Callers that routinely exceed 180 s on a grading call should force `--transport api` or, if that is also slow, split the prompt across multiple calls.
- **`Task(run_in_background=true)` is not awaited under `claude -p`.** The print-mode parent agent emits its `result` message before background sub-agents finish, truncating the captured transcript. Detected and warned-about by the [#97](https://github.com/wjduenow/clauditor/issues/97) heuristic; force-syncable via the `--sync-tasks` flag (sets `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in the subprocess env). The flag closes the *capture* gap for parallel-fanout skills but does NOT evaluate async semantics — the skill's sync execution is a different model than what ships. True async-fidelity evaluation is blocked on upstream Claude Code gaining headless background-task polling (filed at [anthropics/claude-code#52917](https://github.com/anthropics/claude-code/issues/52917); decision record at [`docs/adr/transport-research-103.md`](adr/transport-research-103.md)).
