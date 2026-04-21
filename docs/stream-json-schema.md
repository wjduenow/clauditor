# Stream-JSON schema contract

clauditor invokes the Claude CLI with `--output-format stream-json --verbose`
and parses its NDJSON output line-by-line. This document is the authoritative
reference for which fields clauditor reads, which it tolerates missing, and
how it handles malformed lines. The parser lives in
`src/clauditor/runner.py::SkillRunner._invoke`.

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
{"type":"system","subtype":"init","session_id":"abc123","cwd":"/tmp/work"}
```

**Read by clauditor:** nothing beyond `type` — the message is appended to
`raw_messages` and `stream_events`, but no fields are extracted. System
events are forwarded to transcripts but do not affect `SkillResult.output`
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
| `result` message with `is_error: true` and `result` > 4 KB | Truncate at 4 KB with `" ... (truncated)"` suffix on `SkillResult.error`; classify from the prefix; full string retained in `stream_events` |
| No `result` message before EOF | Warn to stderr, return `SkillResult` with zero tokens |
| Subprocess times out | Kill child, return `SkillResult(exit_code=-1, error="timeout")` with whatever text was captured so far |
| `claude` binary not found | Return `SkillResult(exit_code=-1, error="Claude CLI not found: …")` |

Every exit path is wrapped in `try/finally` so that
`SkillResult.duration_seconds` is populated for success, timeout, missing
binary, and any other error path.

## Canonical parser

`src/clauditor/runner.py::SkillRunner._invoke` is the single source of truth
for all parsing logic. If you need to extend the schema (new message type,
new field), update that function *and* this document *and*
`.claude/rules/stream-json-schema.md` in the same commit.
