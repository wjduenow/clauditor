# Rule: Per-type drift hints for polymorphic-dict loaders

When a loader validates polymorphic dicts — entries discriminated by
a `type` (or other discriminator) field where each type value accepts
a different set of payload keys — the "unknown key" error path must
consult a **per-type** hint table, NOT a global one. A global hint
table is correct until the schema is renamed and some of the
"wrong" keys become valid for a subset of types; after that point a
global hint silently mis-suggests. The per-type table carries
`dict[type, dict[wrong-key, right-key]]` and is keyed by the
discriminator so the same "wrong" key (e.g. `pattern`) can be hinted
differently depending on which type the author wrote.

## The pattern

```python
# schemas.py — sibling constant to the required-keys table.
_ASSERTION_DRIFT_HINTS: dict[str, dict[str, str]] = {
    "contains":       {"value": "needle", "pattern": "needle"},
    "not_contains":   {"value": "needle", "pattern": "needle"},
    "regex":          {"value": "pattern"},
    # `pattern` intentionally absent here — it's a VALID key on
    # `regex` and `min_count`, so hinting on it would be wrong.
    "min_count":      {"value": "pattern", "minimum": "count",
                       "min_count": "count", "threshold": "count"},
    "min_length":     {"value": "length", "min": "length"},
    "max_length":     {"value": "length", "max": "length"},
    "has_urls":       {"value": "count", "minimum": "count",
                       "min_count": "count", "threshold": "count"},
    # ... per-type entries continue ...
}


def _require_assertion_keys(entry: dict, ctx: str) -> None:
    type_val = entry["type"]
    spec = ASSERTION_TYPE_REQUIRED_KEYS[type_val]
    allowed = {"id", "type", "name"} | spec.required | spec.optional
    for key in entry:
        if key in allowed:
            continue
        # Per-type lookup — no global fallback.
        suggestion = _ASSERTION_DRIFT_HINTS.get(type_val, {}).get(key)
        hint = (
            f" — did you mean {suggestion!r}?"
            if suggestion is not None
            else ""
        )
        raise ValueError(
            f"{ctx} (type={type_val!r}): unknown key {key!r}{hint}"
        )
```

## Why this shape

- **Per-type keying survives renames.** The motivating bug: after
  #67 renamed `value` to per-type semantic keys (`needle`, `pattern`,
  `length`, `count`), `pattern` became VALID for `regex` and
  `min_count` but remained WRONG for `contains`. A global
  `{"pattern": "value"}` hint would mis-suggest in both directions —
  telling a `regex` author to rename a valid key back to `value`,
  and giving no hint to a `contains` author who typed `pattern`.
  The per-type table carries exactly the right asymmetry: `pattern`
  appears under `contains`/`not_contains` (suggest `needle`) and is
  absent under `regex`/`min_count` (where it is already valid).
- **Sibling constant, not inlined map.** The hint table lives next
  to the required-keys table so reviewers diffing one notice the
  other. Inlining the hints into the validator function body hides
  them from a `git blame` on the constant and makes the per-type
  asymmetry easy to miss during a rename.
- **`.get(type, {}).get(key)` double-default.** Both levels fall
  through to `None` cleanly: an unknown type (already rejected by
  the type-validation branch, but the validator runs unknown-key
  BEFORE missing-required — see below) yields no hint rather than
  a `KeyError`, and an unknown key not in the hint table yields no
  hint rather than a spurious suggestion.
- **Unknown-key fires BEFORE missing-required.** The error-path
  order is: (a) unknown/missing `type` → (b) unknown key →
  (c) missing required → (d) wrong type. A user who wrote an old
  alias (`value` on `contains`) gets the actionable `"did you mean
  'needle'?"` hint instead of the opaque `"missing required key
  'needle'"` that would hide the rename from them.
- **Drift-hint coverage includes ALL common legacy aliases, not
  just the most recent rename.** The `min_count` entry hints
  `minimum`, `min_count`, AND `threshold` — three keys a hand-
  author might reach for when they've seen any of those in adjacent
  code or tool docs. The goal is migration UX, not a minimal table.

## What NOT to do

- Do NOT use a global `dict[wrong-key, right-key]` table that
  applies across all types. After the first rename where some of
  the "wrong" keys become valid for a subset of types, the global
  table silently mis-suggests. Start per-type from day one.
- Do NOT hint on a key that is VALID for the current type. The
  `pattern` key is intentionally absent from
  `_ASSERTION_DRIFT_HINTS["regex"]` and
  `_ASSERTION_DRIFT_HINTS["min_count"]` — hinting would be factually
  wrong and confusing.
- Do NOT add a "default" entry (e.g. `_ASSERTION_DRIFT_HINTS["*"]`
  as a fallback). The whole point of per-type keying is that a
  key's meaning depends on context; a default re-introduces the
  global problem under a different name.
- Do NOT emit hints as stderr warnings or logging. The hint is part
  of the `ValueError` message so it reaches the CLI's single error-
  rendering seam (see `.claude/rules/llm-cli-exit-code-taxonomy.md`)
  and is never silently swallowed.

## Canonical implementation

`src/clauditor/schemas.py::_ASSERTION_DRIFT_HINTS` + the consultation
inside `_require_assertion_keys`. Sibling to
`ASSERTION_TYPE_REQUIRED_KEYS` (the single source of truth for which
keys each type accepts). The hint table was introduced alongside the
#67 per-type key redesign (DEC-009 of
`plans/super/67-per-type-assertion-keys.md`) specifically to handle
the rename asymmetry — where the global `{"pattern","min","max"} →
"value"` hint from #61's first-pass validator became incorrect after
`pattern` gained legitimate per-type meaning.

Tests: `tests/test_schemas.py::TestRequireAssertionKeys` — one hint
test per `(type, wrong-key, expected-right-key)` triple in
`_ASSERTION_DRIFT_HINTS`. Coverage should walk the table.

Companion rules:

- `.claude/rules/pre-llm-contract-hard-validate.md` — the broader
  "assert in the prompt, enforce in the parser" shape. This rule
  refines the UX layer of the parser-side enforcement.
- `.claude/rules/constant-with-type-info.md` — the required-keys
  constant this table sits alongside carries per-key type info
  that the same validator also enforces.

## When this rule applies

Any loader validating a polymorphic dict (a discriminator field +
per-discriminator-value payload-key sets). Plausible future callers:

- A grading-criteria validator growing per-scale-type keys
  (`numeric` scale uses `min`/`max`, `tiered` uses `levels`,
  `binary` uses neither).
- A section-field validator growing per-format keys (`regex`
  format uses `pattern`, `registered` format uses `name`, `range`
  format uses `low`/`high`).
- A trigger-test validator with `should_trigger` /
  `should_not_trigger` sub-types that each accept different slot
  shapes.
- Any future DSL-style JSON/YAML config with discriminated variants.

The rule also generalizes to **any** discriminated-union dict loader
that has to reject unknown keys with author-friendly hints, even
outside clauditor — the shape is language-agnostic and table-driven.

## When this rule does NOT apply

- Monomorphic dict loaders where every entry accepts the same
  keys. A single global hint table is fine there — there is no
  per-type asymmetry to preserve.
- Loaders that silently ignore unknown keys (permissive passthrough).
  Those have no error path to attach a hint to. A future tightening
  from "permissive" to "strict unknown-key rejection" would trigger
  this rule's applicability.
- Validators where all hints are always correct regardless of type
  (e.g. pure case-correction: `"ID" → "id"`, `"Type" → "type"`). A
  global table suffices — the hints are not type-dependent.
- Loaders where the discriminator itself is optional or inferred.
  The per-type lookup has no anchor without a reliable type value;
  resolve the type-required question first (see
  `.claude/rules/pre-llm-contract-hard-validate.md`).
