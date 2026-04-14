# Rule: Indirection for `time.monotonic` in async modules

When a module uses `time.monotonic()` for duration tracking AND runs inside
`asyncio`, define a module-level alias and call that alias everywhere instead
of `time.monotonic` directly. This lets tests patch only your module's clock
without breaking the asyncio event loop, which *also* calls `time.monotonic`
internally for scheduling.

## The problem

Patching `clauditor.quality_grader.time.monotonic` with a `side_effect` list
clobbers the module-level `time.monotonic` attribute. The asyncio event loop
then calls that patched function on every tick, exhausts the `side_effect`
iterator, and raises `StopIteration` from deep inside `asyncio.gather`. The
test fails with an unrelated traceback that looks like a race condition.

## The pattern

```python
import time

# Module-level alias lets tests patch this without clobbering the asyncio
# event loop's own time.monotonic calls.
_monotonic = time.monotonic


async def blind_compare(...) -> BlindReport:
    start = _monotonic()
    ...
    duration = _monotonic() - start
```

In tests:

```python
with patch("clauditor.quality_grader._monotonic", side_effect=[0.0, 1.25]):
    report = await blind_compare(...)
assert report.duration_seconds == pytest.approx(1.25)
```

## Why this shape

- **Module-level alias captures the function reference at import time** so
  the asyncio loop still resolves `time.monotonic` through its own binding.
- **Tests patch the alias, not `time.monotonic`**, so only your code sees the
  mocked sequence. Everything else (including the event loop) keeps working.
- **Grep for `time.monotonic` should only find the alias line** and the
  comment above it. Any direct call in the module body is a latent bug — the
  next test that tries to assert on duration will hit StopIteration.

## Canonical implementation

`src/clauditor/quality_grader.py` — the `_monotonic = time.monotonic` alias
at the top of the module, used by `blind_compare` and `grade_quality`.
`tests/test_quality_grader.py::test_blind_compare_tracks_duration` is the
canonical test shape.

## When this rule does not apply

Sync modules that never run inside an asyncio context can use `time.monotonic`
directly — the event-loop collision is the whole reason the indirection
exists. If you add a new async judge or grader, apply the pattern. If you add
a new sync helper, don't bother.
