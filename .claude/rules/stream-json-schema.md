# Rule: Defensive parsing of the `claude` stream-json NDJSON stream

clauditor invokes `claude -p … --output-format stream-json --verbose` and
reads the child's stdout line-by-line. The parser must treat every field
as tolerated-if-missing, skip-and-warn on malformed lines, and never let a
single bad message abort the run. The CLI's streaming format is a moving
target — hard-failing on a missing field or an unknown `type` would turn
every future CLI upgrade into a clauditor outage.

## The pattern

```python
for raw_line in proc.stdout:
    line = raw_line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        print(
            f"clauditor.runner: skipping malformed stream-json line: {exc}",
            file=sys.stderr,
        )
        continue
    if not isinstance(msg, dict):
        continue  # scalar/array JSON is not a valid stream-json message

    raw_messages.append(msg)
    mtype = msg.get("type")

    if mtype == "assistant":
        message = msg.get("message") or {}
        content = message.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_chunks.append(block.get("text", ""))

    elif mtype == "result":
        saw_result = True
        usage = msg.get("usage") or {}
        if isinstance(usage, dict):
            try:
                input_tokens = int(usage.get("input_tokens", 0) or 0)
            except (TypeError, ValueError):
                input_tokens = 0
            try:
                output_tokens = int(usage.get("output_tokens", 0) or 0)
            except (TypeError, ValueError):
                output_tokens = 0

if not saw_result:
    print(
        "clauditor.runner: stream-json ended without a 'result' message; "
        "token usage unavailable",
        file=sys.stderr,
    )
```

## Why this shape

- **Skip + log, do not crash**: a single malformed line (truncated JSON,
  partial flush, CLI bug) must not abort the run. The line is reported to
  stderr so operators can triage, and the loop keeps reading.
- **`msg.get(...) or {}` / `or []` guards**: every nested dict/list access
  uses `.get` with a falsy-safe default. An `assistant` message without
  `message.content`, or a `result` message without `usage`, degrades to
  "no text captured" or "zero tokens" — not an exception.
- **`isinstance` before recursion**: `content` might be a string, a dict,
  or `None` in a broken build of the CLI. The `isinstance(content, list)`
  guard is what keeps the parser from raising `TypeError` mid-stream.
- **Defensive `int()` on token counts**: if the CLI ever emits
  `"input_tokens": null` or a string, the `try/except (TypeError,
  ValueError)` falls back to 0 instead of aborting. Token counts are
  observability data, not correctness data — losing them should be a
  warning, not a crash.
- **`saw_result` flag + stderr warning on EOF**: a stream that ends
  without a `result` message is suspicious but not fatal — the run still
  produced output. Warn, return zero tokens, let the caller decide.
- **Unknown `type` values pass through**: new message types added to the
  CLI land in `raw_messages` / `stream_events` unchanged. They do not
  contribute to `SkillResult.output`, but they also do not break anything.
  Extending the parser for a new type is an additive change.

## Canonical implementation

`src/clauditor/runner.py::SkillRunner._invoke` — the `for raw_line in
proc.stdout` loop. The human-readable schema reference lives at
`docs/stream-json-schema.md` and enumerates every field clauditor reads,
with concrete JSONL examples and an error-handling table.

## When this rule applies

Any future parser of an external streaming JSON format produced by a tool
clauditor does not control. The defensive-read + skip-and-warn shape is
not appropriate for internal sidecars clauditor *writes* and reads back —
those use `schema_version` hard checks (see
`.claude/rules/json-schema-version.md`). The distinction is trust: our
own artifacts are versioned and validated; a third-party streaming
format is parsed permissively.
