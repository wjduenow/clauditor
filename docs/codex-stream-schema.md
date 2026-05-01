# Codex stream-JSON schema contract

clauditor invokes the OpenAI Codex CLI with `codex exec --json
--output-last-message <tempfile>` and parses its NDJSON output
line-by-line. This is the authoritative reference for which Codex
events clauditor consumes, which fields it tolerates missing, and how
it classifies the failure surface. The parser lives in
`src/clauditor/_harnesses/_codex.py::CodexHarness.invoke` (reached
from `SkillRunner._invoke` via the `Harness` protocol from #148,
sibling to `ClaudeCodeHarness`).

> Returning from the [stream-json schema](stream-json-schema.md).
> This doc is Codex's NDJSON contract; the sibling covers Anthropic
> Claude's format. See also
> [Transport Architecture](transport-architecture.md).

Schema reflects `openai/codex@main` as of #149. On CLI version drift,
update this document and `.claude/rules/harness-protocol-shape.md` in
the same commit.

## Transport contract

Each line of `codex`'s stdout is one JSON object (NDJSON). The parser
reads lines until EOF, then `proc.wait()` collects the exit code
(Codex exits 0 or 1 only — no granular OS-level failure type). Blank
lines are silently skipped. Lines failing `json.loads` are logged to
stderr (prefix `clauditor.runner: skipping malformed codex stream-json
line:`) and added to `InvokeResult.warnings`; non-object JSON values
are defensively ignored. argv is fixed per DEC-001 / DEC-005:
`[codex_bin, "exec", "--json", "--output-last-message", <tempfile>,
"--skip-git-repo-check", "-s", "workspace-write", "-m", <model>, "-"]`.
The trailing `-` instructs Codex to read the prompt from stdin.
Sandbox is hardcoded to `workspace-write` for v1; configurability is
deferred to #151's `EvalSpec.harness` flag.

## Top-level event types

Every event is a JSON object with a top-level `type`. clauditor
dispatches on `type` and only reads the fields below; unknown types
pass through to `stream_events`. Each appended event has
`"harness": "codex"` added on append (see
[`harness` tag](#harness-tag-dec-010)). All event types below cite
`from openai/codex codex-rs/exec/src/exec_events.rs:11-39`.

### `type: "thread.started"`

```ts
type ThreadStarted = { type: "thread.started"; thread_id: string };
```

First event per process. `thread_id` lands in
`harness_metadata["thread_id"]` (first wins; non-string values
dropped).

### `type: "turn.started"`

```ts
type TurnStarted = { type: "turn.started" };
```

Increments `harness_metadata["turn_count"]`.

### `type: "turn.completed"`

```ts
type TurnCompleted = { type: "turn.completed"; usage: Usage };
```

Aggregate token usage. Fields read defensively via `_safe_int` —
`None`, non-numeric, or missing values fall back to `0`.

### `type: "turn.failed"`

```ts
type TurnFailed = { type: "turn.failed"; error: { message: string } };
```

Per-turn failure. Single string in `error.message`; no HTTP status,
error-type enum, or category discriminator. Classified via
`_classify_codex_failure`. First classification wins.

### `type: "error"`

```ts
type StreamError = { type: "error"; message: string };
```

Stream-level fatal error (serialization failure, dropped events,
auth failure on first request). Distinct from item-level `error`
items; classified the same way as `turn.failed`.

### `type: "item.{started,updated,completed}"`

```ts
type ItemEvent =
  | { type: "item.started"; item: ThreadItem }
  | { type: "item.updated"; item: ThreadItem }
  | { type: "item.completed"; item: ThreadItem };
```

clauditor branches only on `item.completed`; `started` / `updated`
pass through to `stream_events` for forensics.

## `Usage` field set

```ts
interface Usage {
  input_tokens: number; cached_input_tokens: number;
  output_tokens: number; reasoning_output_tokens: number;
}
// from openai/codex codex-rs/exec/src/exec_events.rs:11-39
```

`input_tokens` / `output_tokens` map to
`InvokeResult.{input,output}_tokens`; `cached_input_tokens` and
`reasoning_output_tokens` map to the same-named keys in
`harness_metadata`. Cache/reasoning fields populate unconditionally
once `turn.completed` lands (a `0` count is meaningful — distinct
from "no `turn.completed` seen").

## ThreadItem `item.type` matrix

`item.completed` events carry `item: ThreadItem` whose `item.type`
discriminates further. All shapes below cite
`from openai/codex codex-rs/exec/src/exec_events.rs:96-122`.

### `agent_message`

```ts
type AgentMessage = { id: string; type: "agent_message"; text: string };
```

Final assistant text. clauditor concatenates the `text` of every
`agent_message` (newline-joined) into `InvokeResult.output` — the
Codex analog of Claude's `assistant`-message text-block extraction.

### `reasoning`

```ts
type Reasoning = { id: string; type: "reasoning"; text: string };
```

Reasoning text. Surfaced in `stream_events` (with the
`harness="codex"` tag) but **not** appended to `InvokeResult.output`.
Per-item reasoning-token counts are NOT exposed by Codex; only the
aggregate `Usage.reasoning_output_tokens`.

### `command_execution`

```ts
type CommandExecution = {
  id: string; type: "command_execution"; command: string;
  aggregated_output: string; exit_code: number;
  status: "in_progress" | "completed" | "failed";
};
```

Sandbox shell exec result. `aggregated_output` soft-capped at 64 KB
per DEC-015 (see `_maybe_truncate_command_output` in
`src/clauditor/_harnesses/_codex.py`). Truncated text gets a
`...(truncated)` suffix; one warning fires per invoke (not per event).

### `file_change`

```ts
type FileChange = {
  id: string; type: "file_change";
  changes: Array<{ path: string; kind: "add" | "delete" | "update" }>;
  status: "in_progress" | "completed" | "failed";
};
```

Singular `file_change` (not `file_changes`). Pass-through into
`stream_events`.

### Other `item.type` values

`mcp_tool_call`, `collab_tool_call`, `web_search`, `todo_list`, and
item-level `error` (distinct from top-level `error`) pass through to
`stream_events`. Item-level `error` entries are advisory (e.g. "model
rerouted: gpt-5 → gpt-5-mini") and do NOT trigger
`_classify_codex_failure`. They also carry Lagged synthetic
dropped-event notifications (see
[Operational gotchas](#operational-gotchas)).

## Failure surface

Two classified shapes — `turn.failed.error.message` (per-turn) and
top-level `type: "error"` (stream-level fatal) — flow through
`_classify_codex_failure`. Item-level `error` items pass through
unclassified. First-classification-wins; timeout takes precedence
(watchdog firing sets `error="timeout"`, category `timeout`).

## `--output-last-message` semantics

clauditor always passes `--output-last-message <tempfile>` per
DEC-005. The tempfile lives inside a per-invocation
`tempfile.TemporaryDirectory` (DEC-016); cleanup is automatic on
every exit path. Codex writes the tempfile **only** on
`TurnStatus::Completed` (Failed/Interrupted do not write it).
Contents are raw text of the final agent message — no JSON wrapping.
`InvokeResult.output` is built primarily by joining `agent_message`
text; the tempfile is read **only** as a fallback when no
`agent_message` items appear, with the `last-message-empty:` warning
firing in that case. The tempfile **path** is recorded in
`harness_metadata["last_message_path"]` for #154's context-sidecar
consumer — the file itself is deleted by the time a caller reads
metadata.

## Auth source detection (clauditor's probe order)

`_detect_auth_source` (in `src/clauditor/_harnesses/_codex.py`)
probes auth sources in this deterministic order: (1)
`CODEX_API_KEY`, (2) `OPENAI_API_KEY`, (3) cached auth at
`$CODEX_HOME/auth.json` (default `~/.codex/auth.json`), (4)
`"unknown"` sentinel.

Codex's own runtime precedence is `cached $CODEX_HOME/auth.json →
CODEX_API_KEY → OPENAI_API_KEY → interactive`. Clauditor's probe
order inverts this because env-var probes are cheaper than the
cached-file probe — we resolve the first matching env var if set,
falling through to the file probe only when no env var is present.
For the headless `codex exec --json` invocation Codex never reaches
its interactive-login fallback, so the four-state probe-order set
(`{CODEX_API_KEY, OPENAI_API_KEY, cached, unknown}`) covers every
detectable case.

`_detect_auth_source` inspects the supplied `env` (or `os.environ`
when `env=None`) and resolves the first match. The result lands in
`harness_metadata["auth_source"]` and a one-shot stderr line per
invoke (DEC-017): `clauditor.runner: codex auth=<source>[ (subject)]`.
Subject sanitization (ANSI escape strip → CRLF → space → drop other
C0/DEL controls → strip → 200-char cap) hardens the label against
terminal-control-code injection in addition to the basic CRLF scrub.

`strip_auth_keys` (DEC-012) removes three vars from a spawned child
env: `CODEX_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` (the
base-URL strip prevents an attacker-controlled value routing traffic
to a malicious endpoint). Explicitly preserved: `CODEX_HOME`,
`SSL_CERT_FILE`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`,
`CODEX_CA_CERTIFICATE`, `OPENAI_ORG_ID`, `OPENAI_API_VERSION`.

## `harness` tag (DEC-010)

Every Codex event appended to `stream_events` is rebuilt into a NEW
dict (non-mutating per `.claude/rules/non-mutating-scrub.md`) with a
top-level `"harness": "codex"` added. Claude events stay unmarked —
asymmetry is intentional, no back-port. Consumers detect Codex
events by the presence of this key. Example:
`{"type":"thread.started","thread_id":"th_abc","harness":"codex"}`.

## Error category mapping (DEC-007)

The `error_category` Literal in `runner.py` stays closed. Codex
failures squash via case-insensitive substring match. Rate-limit
precedence runs first.

| Match | Category |
|---|---|
| `rate limit`, `rate-limit`, `quota`, `429` | `rate_limit` |
| `401`, `403`, `unauthorized`, `OPENAI_API_KEY`, `invalid api key` | `auth` |
| anything else | `api` |
| watchdog timeout | `timeout` (precedence over above) |

Empty / `None` / non-string messages return `"API error (no detail)"`
with category `api`. Messages over 4096 chars are truncated with a
`...(truncated)` suffix; classification runs against the
truncated text.

## `harness_metadata` key contract (DEC-008)

Absent keys are **omitted**, never `None`.

| Key | Type | Populated | Source |
|---|---|---|---|
| `sandbox_mode` | `str` | always (`"workspace-write"`) | DEC-001 |
| `auth_source` | `str` | always | `_detect_auth_source` |
| `last_message_path` | `str` | always | DEC-005 |
| `turn_count` | `int` | always | counter |
| `model` | `str` | always | resolved per-call / default |
| `thread_id` | `str` | when seen | first `thread.started` |
| `cached_input_tokens` | `int` | on `turn.completed` | `Usage` |
| `reasoning_output_tokens` | `int` | on `turn.completed` | `Usage` |
| `dropped_events_count` | `int` | non-zero only | `_detect_codex_dropped_events` |
| `stream_events_truncated` | `bool` | `True` only | DEC-015 envelope cap |

## Advisory warnings (DEC-018)

Three warning prefix constants live in `runner.py` next to Claude's.
All are **advisory**: `error_category` stays `None` and
`SkillResult.succeeded_cleanly` is NOT down-classified (distinct from
Claude's `interactive-hang:` and `background-task:` which DO
down-classify).

| Prefix | When | Source |
|---|---|---|
| `dropped-events:` | Lagged synthetic events present | `_detect_codex_dropped_events` |
| `last-message-empty:` | No `agent_message` + non-empty tempfile | `_detect_codex_truncated_output` |
| `codex-deprecation:` | Reserved for future deprecation stderr | (reserved) |

Plus `stream-events-truncated:` for the DEC-015 envelope-cap
overflow (lives in `_codex.py`, not `runner.py` — no cross-harness
consumer reads it).

## Operational gotchas

- **stderr is used in `--json` mode**: Codex emits tracing lines,
  "Reading prompt from stdin..." notices, and deprecation warnings
  even with `--json`. clauditor filters auth-leak patterns
  (`api_key`, `Authorization`, `OPENAI_API_KEY=`, `CODEX_API_KEY=`,
  `CODEX_HOME=` — case-insensitive), redacts offending lines with
  `<line redacted: matched auth-leak pattern>`, then caps at 8 KB
  (DEC-013).
- **Process-group cleanup (DEC-014)**: Codex spawns subprocesses for
  `command_execution`. POSIX uses `start_new_session=True` and
  escalates `os.killpg(os.getpgid(pid), SIGTERM)` then `SIGKILL`;
  Windows falls back to single-pid `proc.terminate()` / `proc.kill()`.
  All syscalls wrapped in `try / except OSError`.
- **Lagged synthetic events**: Codex emits a synthetic
  `item.completed` with `item.type == "error"` and `item.message`
  shaped `"<N> events were dropped..."` on channel overflow.
  `_detect_codex_dropped_events` sums counts; non-zero fires the
  `dropped-events:` warning.
- **Stream-events envelope cap (DEC-015)**: Running byte count is
  capped at 50 MB. On overflow, clauditor stops appending but keeps
  reading stdout so `turn.completed` still lands;
  `stream-events-truncated:` warning fires once and
  `harness_metadata["stream_events_truncated"] = True`.
- **No "stream end" sentinel**: EOF on stdout is the signal — Codex
  emits no terminal "done" event analog to Claude's `result`.
- **Exit code is 0 or 1 only**: Classification comes from event
  content, not exit code.

## Error handling summary

| Condition | Behavior |
|---|---|
| `json.JSONDecodeError` on a line | Log + warn, skip, keep reading |
| Line parses as non-dict JSON | Silently skip |
| `turn.completed` with missing/broken `usage` | Token counts default to 0 |
| `turn.failed.error.message` empty / non-string | `error="API error (no detail)"`, category `api` |
| `turn.failed.error.message` over 4 KB | Truncate with `...(truncated)` suffix |
| Multiple `turn.failed` / `error` events | First wins |
| Subprocess times out | Killpg-escalate (POSIX) or terminate+kill (Windows); `exit_code=-1`, `error="timeout"`, category `timeout` |
| `codex` binary not found | `InvokeResult(exit_code=-1, error="Codex CLI not found: …")`, category `None` |

Every exit path is wrapped in `try/finally` so
`InvokeResult.duration_seconds` is populated and the
`TemporaryDirectory` is cleaned up regardless of outcome.

## Canonical parser

`src/clauditor/_harnesses/_codex.py::CodexHarness.invoke` is the
single source of truth (called from `SkillRunner._invoke`). Pure
helpers (`_classify_codex_failure`, `_detect_codex_dropped_events`,
`_detect_codex_truncated_output`, `_filter_stderr`) live as
module-level functions per
`.claude/rules/pure-compute-vs-io-split.md`. To extend the schema,
update that method, this document, and
`.claude/rules/harness-protocol-shape.md` in the same commit.
