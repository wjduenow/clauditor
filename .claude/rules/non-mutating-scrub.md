# Rule: Scrubs applied just-before-I/O must be non-mutating

When you add a transformation that cleans, redacts, or otherwise
sanitizes a value right before writing it to disk / stderr / the network,
the transformation function must return a **new** structure and leave
the input untouched. The caller is then free to keep using the
full-fidelity in-memory copy for anything else (debugging, further
assertions, downstream graders) without worrying that the scrub already
clobbered the data.

## The pattern

```python
def redact(obj: Any) -> tuple[Any, int]:
    """Return ``(scrubbed_copy, count)`` for a JSON-compatible value.

    The input is never mutated; nested containers are rebuilt.
    """
    if isinstance(obj, dict):
        new_dict = {}
        total = 0
        for key, value in obj.items():
            if _is_sensitive_key(key):
                new_dict[key] = _REDACTED
                total += 1
                continue
            scrubbed, n = redact(value)
            new_dict[key] = scrubbed
            total += n
        return new_dict, total
    if isinstance(obj, (list, tuple)):
        # ... rebuild list, never mutate the input sequence ...
```

At the call site:

```python
# Disk write gets scrubbed copy; in-memory SkillResult keeps the original.
scrubbed_events, n = transcripts.redact(skill_result.stream_events)
(run_dir / "output.jsonl").write_text(
    "\n".join(json.dumps(ev) for ev in scrubbed_events) + "\n"
)
# skill_result.stream_events is untouched — downstream consumers can still
# read the full-fidelity sequence.
```

## Why this shape

- **Disk/memory separation**: the motivating requirement is DEC-010 —
  "on disk scrubbed, in memory untouched". The non-mutating contract is
  the single mechanism that makes this possible without duplicating the
  serialization path. Callers get to pick which copy they want.
- **Testability**: a non-mutating function has no hidden state. Tests can
  pass in a fixture, assert the return value, AND `assert fixture ==
  original` to prove the scrub did not mutate. The companion test in
  `tests/test_transcripts.py::TestCombined::test_returns_new_object` is
  the canonical shape.
- **Recursive safety**: when the function is recursive (as redaction is,
  walking nested JSON), mutation would need to be opt-out at every level
  — easy to miss a branch. Build-new-structure is a single uniform
  rule.
- **Multiple call sites, no order coupling**: the slice printer
  (`_print_failing_transcript_slice`) and the disk writer
  (`_write_run_dir`) both apply `redact()` independently. If either
  mutated, the other would silently get the already-scrubbed view. With a
  non-mutating contract, they each see the full-fidelity data and
  produce their own scrubbed copy.

## What NOT to do

- Do NOT implement an in-place variant "for performance" unless you have
  measured evidence of a hotspot. The allocation overhead is trivial
  against the JSONL serialization step that follows.
- Do NOT return the input unchanged when nothing matched — return a new
  structure anyway, so the non-mutating invariant holds for every
  caller regardless of data. (An `_scrub_string` fast-path that returns
  the same string when no regex matched is fine — strings are immutable,
  so there is no mutation risk.)

## Canonical implementation

`src/clauditor/transcripts.py::redact` — recursive walk over
JSON-compatible values, rebuilds every nested container. Callers in
`src/clauditor/cli.py`:

- `_write_run_dir` — scrubs `stream_events` and `output_text` before
  writing `output.jsonl` / `output.txt`.
- `_print_failing_transcript_slice` — scrubs a per-run slice before
  printing to stderr; the caller's in-memory `SkillResult.stream_events`
  stays untouched.

## When this rule applies

Any future transformation function whose purpose is "clean this value
before it leaves the process" — redactors, normalizers, anonymizers,
PII strippers, error-message sanitizers. Pure in-place mutations that
never cross an I/O boundary (e.g. building up a result list inside a
single function) are not covered.
