# Rule: LLM responses keyed by positional id zip must be validated

When parsing an LLM judge's response and pairing each result with a stable
`id` from the spec by **position**, validate both the length AND the
per-item text match before assigning ids. A positional zip looks cheap and
correct, but silently mislabels every subsequent result when the judge
reorders, drops, or synthesizes an item — producing audit history that
points at the wrong criterion, with no error anywhere.

## The problem

A typical L3 judge prompt enumerates criteria in a specific order and asks
the model to return a JSON list of per-criterion verdicts. The naive
implementation is:

```python
def parse_grading_response(text: str, criteria: list[dict]) -> list[Result]:
    data = json.loads(text)
    return [
        Result(id=criteria[i]["id"], **item)  # WRONG: trusts position
        for i, item in enumerate(data)
    ]
```

Failure modes that silently corrupt history:

- Model drops the second criterion → every id from index 1 onward is
  shifted and permanently mislabeled.
- Model swaps two criteria → those two rows get each other's ids.
- Model synthesizes a bonus "Overall" row → every id after it is wrong.
- Model returns `N+1` items in ambiguous order → `criteria[i]` raises
  `IndexError` OR silently truncates, depending on iteration shape.

## The pattern

```python
def parse_grading_response(text: str, criteria: list[dict]) -> list[Result]:
    data = json.loads(text)

    # Length check first — cheap, catches drop/insert.
    if len(data) != len(criteria):
        raise ValueError(
            f"judge returned {len(data)} results, expected {len(criteria)}: "
            f"expected={[criterion_text(c) for c in criteria]}, "
            f"got={[item.get('criterion') for item in data]}"
        )

    # Per-index text match — catches reordering.
    for i, item in enumerate(data):
        expected = criterion_text(criteria[i])
        actual = item.get("criterion", "")
        if actual.strip() != expected.strip():
            raise ValueError(
                f"judge result [{i}] criterion text mismatch: "
                f"expected={expected!r}, got={actual!r}"
            )

    # Safe to zip by position now.
    return [
        Result(id=criteria[i]["id"], **item)
        for i, item in enumerate(data)
    ]
```

## Why this shape

- **Length check before text check**: cheap early exit, names both counts
  in the error for quick diagnosis.
- **Per-item text comparison**: a len-equal-but-reordered response passes
  the length check. The text-match catches it.
- **Hard-fail with descriptive error**: the parser raises `ValueError` with
  both expected and actual lists. Callers can convert to a failed grading
  report so the failure surfaces in the user-facing output rather than as
  a crashed subprocess.
- **No fuzzy matching, no silent realignment**: fuzzy-matching on text
  reintroduces the exact drift the stable-id work was meant to eliminate.
  If the judge misbehaves, the right answer is to regenerate, not to
  guess.

## Canonical implementation

`src/clauditor/quality_grader.py::parse_grading_response` — length check,
per-index text check, then positional id assignment. `grade_quality`
catches the `ValueError` and produces a failed-parse `GradingReport` so the
hard-fail surfaces as a graceful report rather than a traceback.

## When this rule applies

Any future LLM judge or grader that pairs spec entries with parsed response
items by position. If the response carries explicit ids inline (e.g. the
prompt instructs the model to echo each id), match by id directly and this
rule does not apply — but verify every expected id is present.
