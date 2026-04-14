# Rule: Stable `id` fields on EvalSpec entries

Every per-entry-addressable field on an `EvalSpec` — L1 assertions, L2
`FieldRequirement`, L3 grading criteria — must carry an explicit `id` string
validated for uniqueness-within-skill at load time. Position-based matching
was the fragile prior state: inserting or reordering a single entry silently
invalidated every historical per-entry record keyed by position, corrupting
audit and trend data without any error.

## The pattern

```python
# schemas.py — load-time validation
def _require_id(entry: dict, seen: set[str], field: str, i: int) -> str:
    if "id" not in entry:
        raise ValueError(f"{field}[{i}]: missing 'id'")
    entry_id = entry["id"]
    if not isinstance(entry_id, str) or entry_id == "":
        raise ValueError(f"{field}[{i}]: 'id' must be a non-empty string")
    if entry_id in seen:
        raise ValueError(f"{field}[{i}]: duplicate id {entry_id!r}")
    seen.add(entry_id)
    return entry_id
```

Call `_require_id` on every assertion, field, and criterion in
`EvalSpec.from_file()`. Pass a single `seen` set that spans all three layers
so an assertion id clashing with a field id is rejected.

## Why this shape

- **Uniqueness spans all layers**: one shared `seen: set[str]` across L1/L2/L3
  means the same id cannot appear in both an assertion and a criterion,
  avoiding ambiguity when the audit aggregator groups by `(layer, id)`.
- **Load-time hard-fail**: any missing or duplicate id is a `ValueError` with
  a clear path like `"assertions[2]: missing 'id'"`. Catching at load time
  prevents silent data corruption downstream.
- **`id` is user-authored, not synthesized**: content-hash ids were
  considered and rejected. Authors need the ability to rename an entry
  without losing its audit history; an explicit id is the escape hatch.

## Canonical implementation

`src/clauditor/schemas.py` — `_require_id` helper and `EvalSpec.from_file()`
call sites. Apply the pattern to any new per-entry-addressable spec field.

## When this rule applies

Any new list-of-entries field on `EvalSpec` where a downstream caller may
want to key data by the entry's identity across multiple runs. If a field
is purely ephemeral (e.g. transient runner options with no historical
meaning), stable ids are not required.
