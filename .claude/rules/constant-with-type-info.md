# Rule: Single-source-of-truth constants carry per-key type info

When a loader's key-presence constant (the `dict[type, KeySpec]`
that declares "type X requires these keys, optionally accepts
these") governs payload fields that are not all strings — some
ints, some bools, some floats — extend the `KeySpec` dataclass with
a `field_types: dict[str, type]` member and enforce `isinstance` at
load time from the same validator that checks presence. One
constant, one validator, one error path. Do NOT split type-checking
off into a second pass that a future caller might forget to run, and
do NOT rely on handler-side runtime coercion (`int(a.get(key, ""))`)
to reject wrong types — by then the bad value has already been
accepted, and downstream callers have already built partial state
around it.

## The pattern

```python
# schemas.py — the spec dataclass carries presence AND type info.
@dataclass(frozen=True)
class AssertionKeySpec:
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    field_types: dict[str, type] = field(default_factory=dict)


# Single source of truth: every type's required/optional keys AND
# the expected native JSON type for each payload key.
ASSERTION_TYPE_REQUIRED_KEYS: dict[str, AssertionKeySpec] = {
    "contains": AssertionKeySpec(
        required=frozenset({"needle"}),
        field_types={"needle": str},
    ),
    "min_length": AssertionKeySpec(
        required=frozenset({"length"}),
        field_types={"length": int},
    ),
    "has_urls": AssertionKeySpec(
        optional=frozenset({"count"}),
        field_types={"count": int},
    ),
    "has_format": AssertionKeySpec(
        required=frozenset({"format"}),
        optional=frozenset({"count"}),
        field_types={"format": str, "count": int},
    ),
    # ...
}


def _require_assertion_keys(entry: dict, ctx: str) -> None:
    spec = ASSERTION_TYPE_REQUIRED_KEYS[entry["type"]]
    # ... presence checks (unknown key, missing required) ...

    # Type-check every declared field_types entry that is present.
    for key, expected in spec.field_types.items():
        if key not in entry:
            continue
        val = entry[key]
        if val is None:
            raise ValueError(
                f"{ctx}: key {key!r} must be {expected.__name__}, "
                f"not null (omit the key to use the default)"
            )
        # bool is a subclass of int in Python — guard explicitly,
        # otherwise {"count": True} would silently pass isinstance(val, int).
        ok = isinstance(val, expected) and not (
            expected is int and isinstance(val, bool)
        )
        if not ok:
            raise ValueError(
                f"{ctx}: key {key!r} must be {expected.__name__}, "
                f"got {type(val).__name__} {val!r}"
            )
```

## Why this shape

- **One constant, one validator, one error path.** Splitting
  type-checking off into a separate `_validate_types(entry)` pass
  means a future caller might forget to run it, or run it in the
  wrong order relative to the presence-check. Co-locating type
  info on the same `KeySpec` and looping through `spec.field_types`
  inside `_require_assertion_keys` guarantees every load goes
  through both checks together.
- **Reject string-typed ints at load time, not runtime.** The
  pre-#67 loader accepted `{"value": "500"}` (a JSON string) and
  relied on `int(a.get("value", ""))` inside the handler to coerce.
  That shifted the error from "bad spec" (surface at load, exit 2)
  to "runtime crash in the handler" (surface mid-run, opaque
  traceback). With `field_types` declared and enforced in the
  loader, `{"length": "500"}` rejects with a crisp
  `"key 'length' must be int, got str '500'"` before any grading
  run spins up.
- **Explicit `bool is not int` guard.** Python's `isinstance(True,
  int)` returns `True`. A naive `isinstance(val, int)` silently
  accepts `{"count": True}` as a legal count, which then flows
  through arithmetic as `1` without complaint. The explicit
  `not (expected is int and isinstance(val, bool))` branch rejects
  bool-for-int with the same "got bool" error message an author
  would actually recognize.
- **`None` gets its own branch with an actionable message.** A
  `val is None` check ahead of the `isinstance` call produces
  `"must be int, not null (omit the key to use the default)"`
  instead of the technically-correct-but-confusing `"got NoneType
  None"`. Hand-authors default-write `null` when unsure; the
  friendlier error tells them to delete the key instead.
- **Skip keys the author omitted.** `if key not in entry: continue`
  means optional-with-default keys stay optional — the `count` key
  on `has_urls` can be absent, and the handler's `a.get("count", 1)`
  supplies the default. Type-checking only fires on keys the author
  actually wrote.
- **Natural extension of presence info, not a parallel universe.**
  Every required-or-optional key has a type; every field_types
  key must also appear in required or optional. A test-side
  drift guard (`test_field_types_match`) can assert that
  invariant at load time, so a future contributor who adds a
  field_types entry without updating required/optional gets a
  red test rather than silent skew.

## What NOT to do

- Do NOT declare `field_types` as a `dict[str, tuple[type, ...]]`
  to support "int OR string" permissive reads. That is the
  pre-#67 behavior this rule explicitly tightens against. If you
  need a permissive read, do it with two distinct `type` values
  (one for int, one for string) rather than a union-typed field.
- Do NOT put the type-check pass before the presence check. The
  error-path order matters: unknown type → unknown key → missing
  required → wrong type. A user who typed `pattern` instead of
  `needle` on a `contains` assertion should see the `"did you mean
  'needle'?"` hint (unknown-key path), not a `"missing required
  key 'needle'"` confusing side-effect of a presence-first order.
- Do NOT `isinstance(val, int)` without the `bool` guard. Bool is
  an int subclass in Python; silently accepting `True` / `False`
  for a numeric field is a specific Python foot-gun, not a rare
  edge case. The guard is two lines and anchors every `int` type.
- Do NOT skip the `test_field_types_match` drift guard. Without
  it, a future contributor can add a `field_types` entry without
  a matching `required`/`optional` entry (or vice versa), and the
  constant quietly drifts out of lockstep with the validator's
  iteration order.

## Canonical implementation

`src/clauditor/schemas.py::AssertionKeySpec.field_types` + the
per-key `isinstance` loop inside `_require_assertion_keys`. The
`field_types` field was added in #67 (DEC-012 of
`plans/super/67-per-type-assertion-keys.md`) as a natural
consequence of switching from stringly-typed ints on disk
(`{"value": "500"}`) to native JSON ints (`{"length": 500}`).
Before this split, types were enforced implicitly by handler-side
coercion (`int(a.get(...))`), which accepted strings, rejected
them at runtime, and produced opaque errors.

Tests:

- `tests/test_schemas.py::TestAssertionKeySpec::test_field_types_match`
  — drift guard asserting every `required ∪ optional` key has a
  matching `field_types` entry and vice versa, for every type
  in `ASSERTION_TYPE_REQUIRED_KEYS`.
- `tests/test_schemas.py::TestRequireAssertionKeys` — parametrized
  wrong-type cases (`{"type": "min_length", "length": "500"}`,
  `{"type": "contains", "needle": 123}`, `{"type": "has_urls",
  "count": True}`) verify each branch of the type-check loop.

Companion rules:

- `.claude/rules/per-type-drift-hints.md` — the sibling hint
  table keyed on the same discriminator. A `KeySpec` extension
  that adds type info often needs a hint-table extension in the
  same change.
- `.claude/rules/eval-spec-stable-ids.md` — the `id` uniqueness
  validator lives in the same `from_dict` context and runs
  alongside the key/type validator.
- `.claude/rules/pre-llm-contract-hard-validate.md` — the broader
  "fail loudly at load, never silently accept a bad spec" shape.

## When this rule applies

When a new `dict[type, KeySpec]` (or `list[KeySpec]`) constant
governs payload fields of mixed primitive types — ints alongside
strings, floats alongside bools — AND the loader already has (or is
gaining) per-type key-presence validation. Plausible future callers:

- A grading-criteria constant with a `score_type` discriminator
  (`numeric` takes `float` fields, `tiered` takes `str` fields,
  `binary` takes `bool`).
- A section-field constant where `required: bool` lives next to
  `format: str`, `name: str`, and a future `min_length: int`.
- A trigger-test constant with mixed-type slots (`pattern: str`,
  `max_tokens: int`, `strict: bool`).

The rule also applies retroactively: any existing
`{required, optional}`-only constant that governs mixed-type payload
fields is a latent foot-gun. Add `field_types` during the next
touch that adds or renames a key.

## When this rule does NOT apply

- Monotyped constants where every payload field is a string (or
  every field is an int). A single-pass `isinstance(val, str)`
  sweep at the validator level is enough; a per-key map is
  over-engineering.
- Dataclass-wrapped structured fields where the loader builds a
  nested dataclass from the dict. The dataclass's `__init__` type
  annotations already carry the type info; re-declaring in
  `field_types` would drift. Validate by constructing the
  dataclass instead.
- Constants consumed only by generated code (e.g. JSON Schema
  exported to an external validator). The external validator owns
  type-checking; a second in-process copy is redundant and
  drift-prone. Emit the types from the constant into the external
  schema rather than re-enforcing in-process.
- One-off scripts or diagnostic tools that load a spec for
  display only and do not hand the result to the grading pipeline.
  Those can tolerate loose typing.
