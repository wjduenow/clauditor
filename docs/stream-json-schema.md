# Stream-JSON schema contract

clauditor invokes the Claude CLI with `--output-format stream-json --verbose`
and parses its NDJSON output line-by-line. This document is the authoritative
reference for which fields clauditor reads, which it tolerates missing, and
how it handles malformed lines. The parser lives in
`src/clauditor/_harnesses/_claude_code.py::ClaudeCodeHarness.invoke`
(reached from `src/clauditor/runner.py::SkillRunner._invoke` via the
`Harness` protocol introduced in #148).

This schema reflects the Anthropic CLI streaming format as verified live
against `claude` 2.1.x. If a future CLI version changes the shape, update
this document and `.claude/rules/stream-json-schema.md` in the same commit.

## Transport

- Each line of `claude`'s stdout is one JSON object (NDJSON / JSON Lines).
- The parser reads lines until EOF, then calls `proc.wait()` to collect the
  exit code.
- Blank lines are silently skipped.
- Lines that fail `json.loads` are logged to stderr (prefix
  `clauditor.runner: skipping malformed stream-json line:`) and skipped —
  they never abort the run.
- Values that parse as JSON but are not objects (scalars, arrays) are
  defensively ignored.

## Message shapes clauditor consumes

Every message is a JSON object with a top-level `type` field. clauditor
dispatches on `type` and only reads the fields documented below. Any
unknown `type` is stored in `raw_messages` / `stream_events` for debugging
but otherwise ignored.

### `type: "system"`

Init / hook / misc events from the CLI.

```json
{"type":"system","subtype":"init","session_id":"abc123","cwd":"/tmp/work","apiKeySource":"ANTHROPIC_API_KEY"}
```

**Read by clauditor:** the `type: "system"` / `subtype: "init"` message
is parsed for its `apiKeySource` field (when present).

- `subtype` (string) — tolerated-if-missing. Only `"init"` messages
  trigger `apiKeySource` extraction; other subtypes (e.g. `"hook"`)
  are appended to `raw_messages` / `stream_events` but otherwise
  ignored.
- `apiKeySource` (string) — tolerated-if-missing / non-string
  (`SkillResult.api_key_source` stays `None` in that case). When a
  string is present on the FIRST `system/init` message, clauditor
  stores the value on `SkillResult.api_key_source` and emits one
  stderr info line of the form
  `clauditor.runner: apiKeySource=<value>`. Example values the
  Claude CLI emits today: `"ANTHROPIC_API_KEY"`, `"claude.ai"`,
  `"none"`. **The value is a label (identifying which auth path
  was used), not a secret** — it does not contain the API key
  itself, so printing it to stderr and persisting it on
  `SkillResult` is safe. Older CLI builds may omit this field; in
  that case `api_key_source` stays `None` and no stderr line is
  emitted (absence is the signal, per DEC-012 of
  `plans/super/64-runner-auth-timeout.md`). Subsequent `system/init`
  messages are ignored — first init wins, per DEC-015. When the
  caller threads a `subject` through `call_anthropic` (grader call
  sites — L2 extraction, L3 grading, L3 blind compare side1/side2,
  triggers judge, suggest proposer, propose-eval) the stderr line
  gains a ` (<subject>)` suffix so operators can attribute
  multi-subprocess runs (e.g. `grade --transport cli`) to specific
  internal LLM calls (issue #107).

All `system/*` messages (every subtype) are appended to
`raw_messages` and `stream_events` for downstream tooling
(transcripts, debug dumps) but do not affect `SkillResult.output`
or token counts.

### `type: "assistant"`

Assistant turn with one or more content blocks.

```json
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Here are 5 venues near you..."},{"type":"tool_use","id":"toolu_01","name":"WebSearch","input":{"query":"parks"}}]}}
```

**Required fields for text capture:**
- `message` (object) — tolerated-if-missing (treated as `{}`).
- `message.content` (list) — tolerated-if-missing / non-list
  (message is skipped for text capture).
- Each block in `message.content` must be a dict with `type == "text"` to
  contribute to `SkillResult.output`. The block's `text` field is read
  with `.get("text", "")`, so a text block missing its `text` field
  contributes an empty string rather than raising.

**Tolerated block types:** `tool_use`, `tool_result`, `thinking`, and any
other block whose `type` is not `"text"` are skipped for the purposes of
`SkillResult.output`. They are still present in `raw_messages` for
downstream tooling (transcripts, debug dumps).

Text chunks from every assistant message are joined with `\n` to form
`SkillResult.output`.

**`tool_use` block — `Task(run_in_background=true)` detection.** A
`tool_use` block with `name == "Task"` and
`input.run_in_background is True` (strict `is True` check, not a
truthy-match) is counted as a background-task launch for the
non-completion heuristic described below. Other `Task` calls
(foreground, or `run_in_background` absent / falsy) are not counted.
See `_count_background_task_launches` in
`src/clauditor/runner.py`.

### `type: "result"`

The terminal line of a run. Carries aggregate token usage and, on
failure, a user-facing error string.

```json
{"type":"result","subtype":"success","is_error":false,"usage":{"input_tokens":1423,"output_tokens":512}}
```

**Required fields:** none are hard-required — every field is read
defensively.
- `usage` (object) — tolerated-if-missing (token counts stay at 0).
- `usage.input_tokens` (int) — tolerated-if-missing / `None` / non-numeric
  (falls back to 0 via a `try/except (TypeError, ValueError)` wrapper).
- `usage.output_tokens` (int) — same defensive treatment.
- `is_error` (bool) — tolerated-if-missing (treated as `False`). When
  strictly `True` (Python `is True` check — the string `"true"`, int
  `1`, and other truthy non-bool values do NOT trigger the error
  branch), clauditor classifies the result as a failure and surfaces
  a user-facing error string via `SkillResult.error` /
  `SkillResult.error_category`. This strict check preserves back-
  compat with older CLI builds that may omit the field on success.
- `result` (string) — **present on result messages only**. When
  `is_error: true`, this is the human-readable error text, often the
  verbatim Anthropic API error including status codes (e.g. `"API
  Error: Request rejected (429) · Rate limit exceeded for your
  organization"`). Absent on success. Clauditor classifies the text
  by keyword (case-insensitive — the payload is lowercased before
  matching, so `"Rate Limit"` and `"rate limit"` classify identically
  and the `ANTHROPIC_API_KEY` hint matches regardless of casing):
  `"429"` / `"rate limit"` / `"rate-limit"` →
  `error_category = "rate_limit"`; `"401"` / `"403"` /
  `"unauthorized"` / `"authentication"` / `"auth error"` /
  `"ANTHROPIC_API_KEY"` → `error_category = "auth"`; otherwise
  `error_category = "api"`. The rate-limit match runs before the
  auth match so a string containing both is classified as
  `rate_limit`. Strings longer than 4 KB are truncated in
  `SkillResult.error` with a `" ... (truncated)"` suffix; the full
  string remains in `stream_events` for forensics.

Seeing a `result` message flips an internal `saw_result` flag. If the
stream ends without any `result` message, clauditor emits:

```
clauditor.runner: stream-json ended without a 'result' message; token usage unavailable
```

to stderr and still returns a `SkillResult` with `input_tokens = 0` and
`output_tokens = 0`. Missing token data is a warning, not a fatal error.

**Failure example — 429 rate limit.** When the underlying API returns
a 429 rate-limit, Claude CLI emits a terminal `result` message with
`is_error: true` and the user-facing text in `result`. Clauditor
surfaces this through `SkillResult.error` + `SkillResult.error_category
== "rate_limit"`.

```jsonl
{"type":"system","subtype":"init","session_id":"abc123","cwd":"/tmp/work"}
{"type":"assistant","message":{"id":"msg_01","role":"assistant","content":[],"stop_reason":null}}
{"type":"result","subtype":"error_max_turns","is_error":true,"result":"API Error: Request rejected (429). Your organization has exceeded the rate limit.","usage":{"input_tokens":1423,"output_tokens":0}}
```

## Heuristic classifications (advisory)

Two detectors run after the stream ends and a `result` message was
seen. Both are gated on `allow_hang_heuristic=True` (the default;
per-skill opt-out via `EvalSpec.allow_hang_heuristic`) AND on the
run not already carrying a stream-json `is_error: true`
classification. Each detector appends a prefixed warning to
`SkillResult.warnings` and sets `SkillResult.error_category`
without setting `SkillResult.error` — `output` and `exit_code` stay
as reported by the CLI. Both prefixes are load-bearing: they
down-classify `succeeded_cleanly` to `False`.

**Interactive-hang** (`error_category = "interactive"`, warning prefix
`interactive-hang:`): a single-turn run whose final assistant message
has `stop_reason == "end_turn"` and either ends with `?` or contains
an `AskUserQuestion` `tool_use` block. Catches skills that asked the
user for input and stalled. See `_detect_interactive_hang`.

**Background-task non-completion** (`error_category = "background-task"`,
warning prefix `background-task:`): runs only when interactive-hang
did NOT fire. Triggers when (a) at least one assistant `tool_use`
block has `name == "Task"` and `input.run_in_background is True`,
AND (b) either the concatenated final text matches the regex
`\b(waiting on|still waiting|continuing|in progress|in the background)\b`
(case-insensitive) OR the `result` message's `num_turns` is less
than `launches + 2`. `claude -p` does not poll background tasks,
so a skill that launches them and exits terminates with a valid
`result` message but truncated output; this detector catches that
silent failure. See `_detect_background_task_noncompletion`
(GitHub #97).

Precedence is strict: stream-json `is_error: true` wins over both
heuristics; interactive-hang wins over background-task. A run that
matches both heuristics is classified as interactive-hang only
(background-task does not also fire).

## Error handling summary

| Condition | Behavior |
|---|---|
| `json.JSONDecodeError` on a line | Log to stderr, skip the line, keep reading |
| Line parses as non-dict JSON | Silently skip |
| `assistant` message without `message.content` list | Skip text capture for that message |
| Text block missing `text` field | Contributes empty string |
| `result` message with missing/broken `usage` | Token counts default to 0 |
| `result` message with `is_error` absent | Treat as success (back-compat with older CLI versions) |
| `result` message with non-bool `is_error` (e.g. `"true"`, `1`) | Treat as absent — strict `is True` check only |
| `result` message with `is_error: true` and no `result` string | `SkillResult.error = "API error (no detail)"`, `error_category = "api"` |
| `system/init` message with non-string `apiKeySource` | Field ignored; `SkillResult.api_key_source` stays `None`; no stderr line emitted |
| Multiple `system/init` messages with `apiKeySource` | First wins; later init messages are ignored (DEC-015) |
| `result` message with `is_error: true` and `result` > 4 KB | Truncate at 4 KB with `" ... (truncated)"` suffix on `SkillResult.error`; classify from the prefix; full string retained in `stream_events` |
| No `result` message before EOF | Warn to stderr, return `SkillResult` with zero tokens |
| Subprocess times out | Kill child, return `SkillResult(exit_code=-1, error="timeout")` with whatever text was captured so far |
| `claude` binary not found | Return `SkillResult(exit_code=-1, error="Claude CLI not found: …")` |

Every exit path is wrapped in `try/finally` so that
`SkillResult.duration_seconds` is populated for success, timeout, missing
binary, and any other error path.

## Canonical parser

`src/clauditor/_harnesses/_claude_code.py::ClaudeCodeHarness.invoke` is the
single source of truth for all parsing logic (called from
`src/clauditor/runner.py::SkillRunner._invoke`). If you need to extend the
schema (new message type, new field), update that method *and* this document
*and* `.claude/rules/stream-json-schema.md` in the same commit.
