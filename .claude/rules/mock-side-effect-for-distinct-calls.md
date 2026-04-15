# Rule: Use side_effect (not return_value) when a mock is called more than once with distinct expected values

When a test mocks a function that the code under test calls twice
or more, and each call is supposed to yield a different value,
use side_effect=[v1, v2, ...] instead of return_value=v. A shared
return_value makes every call return the same object, which
silently zeroes out any arithmetic the renderer or aggregator is
supposed to perform on the difference between the calls. The test
still passes, but it fails to exercise the sign handling, format
specifiers, or delta logic it was written to protect.

## The problem

```python
# WRONG: same report for both the primary and baseline arm.
# grade_quality is called twice; both arms get the same pass_rate;
# delta arithmetic is trivially 0.0; any bug in the delta-block
# renderer's sign handling or %+.1f format specifier is masked.
with patch("clauditor.cli.grade_quality", return_value=primary_report):
    _cmd_grade_with_workspace(...)
```

## The pattern

```python
# RIGHT: distinct values per call so delta != 0 and the renderer
# actually touches the arithmetic path under test.
primary_report = make_report(pass_rate=0.80)
baseline_report = make_report(pass_rate=0.60)

with patch(
    "clauditor.cli.grade_quality",
    side_effect=[primary_report, baseline_report],
):
    _cmd_grade_with_workspace(...)
```

## Why this shape

- Distinct values exercise the delta path: a test whose purpose
  is to verify a with/without renderer MUST feed different inputs to
  the two arms. Otherwise the subtraction under test is x - x = 0,
  and every format-specifier and sign bug slides through green.
- side_effect is ordered: it documents "first call is primary,
  second call is baseline" at the test site, which also acts as a
  spec for the production call order. If the production code ever
  flips the order, the test breaks loudly instead of silently.
- side_effect with a list is bounded: if the production code
  accidentally starts calling the mock a third time, StopIteration
  fires immediately. return_value would happily keep returning the
  same object forever, masking a regression where an extra grading
  pass got added.

## Canonical implementation

tests/test_cli.py: the baseline delta-block rendering tests for
_cmd_grade_with_workspace. Pass 3 of #28 code review caught the
original return_value=report shape and converted it; the fix is
the canonical example.

## When this rule applies

Any test that mocks a function called more than once per test case
where distinct return values are semantically required:

- with-skill vs without-skill arms (this feature)
- variance reps that are supposed to produce different tokens or
  durations per call
- sequential LLM judges whose verdicts drive a downstream aggregate

It does NOT apply when the production code legitimately expects the
same value on every call (e.g. a pure constant lookup); a shared
return_value is fine there and cheaper than building a list.
