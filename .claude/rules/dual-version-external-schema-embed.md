# Rule: Embedding a clauditor extension inside an external schema

When clauditor emits JSON that will be consumed by an external
system's own schema (shields.io endpoint JSON, a future GitHub
check-run annotation, a package-registry metadata blob, an OpenGraph
preview payload, ...), the payload carries **two independent
schema-version fields**:

1. The external system's required schema-version field (e.g.
   shields.io's top-level `"schemaVersion": 1`) — using **their**
   name and convention (which is often camelCase even when our
   codebase uses snake_case).
2. A clauditor extension block under a dedicated namespace key (e.g.
   `"clauditor": {...}`), whose **first** key is our internal
   `"schema_version": 1` per
   `.claude/rules/json-schema-version.md`.

The two versions bump independently. A future shields.io bump to
`schemaVersion: 2` does not force a `clauditor.schema_version` bump,
and vice versa. The external consumer reads their own top-level
version; a future clauditor-side loader (e.g. a trend-audit consumer)
reads the nested one.

## The pattern

```python
# badge.py — canonical dataclass shape
@dataclass
class Badge:
    """Serializable shields.io endpoint-JSON payload."""

    # Shields.io's required fields (their schema):
    label: str
    message: str
    color: str
    clauditor: ClauditorExtension  # our extension block
    style_overrides: dict[str, str | int] = field(default_factory=dict)

    # Shields.io's top-level schemaVersion (camelCase per their docs).
    schema_version: int = _SHIELDS_SCHEMA_VERSION  # == 1

    def to_endpoint_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            # 1. External system's required field first.
            "schemaVersion": self.schema_version,
            "label": self.label,
            "message": self.message,
            "color": self.color,
        }
        # Style passthroughs alphabetized between the external keys
        # and our nested namespace.
        for key in sorted(self.style_overrides):
            payload[key] = self.style_overrides[key]
        # 2. Our extension block — first key inside is
        # ``schema_version`` (snake_case, our convention).
        payload["clauditor"] = self.clauditor.to_dict()
        return payload


@dataclass
class ClauditorExtension:
    skill_name: str
    generated_at: str
    iteration: int | None
    l1: L1Summary | None
    l3: L3Summary | None
    variance: VarianceSummary | None
    # Our internal version (snake_case), FIRST key in the nested
    # dict per .claude/rules/json-schema-version.md.
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema_version": self.schema_version}
        result["skill_name"] = self.skill_name
        result["generated_at"] = self.generated_at
        if self.iteration is not None:
            result["iteration"] = self.iteration
        layers: dict[str, Any] = {}
        if self.l1 is not None:
            layers["l1"] = self.l1.to_dict()
        # ... l3, variance conditional ...
        result["layers"] = layers
        return result
```

Resulting JSON:

```json
{
  "schemaVersion": 1,
  "label": "clauditor",
  "message": "8/8 · L3 92%",
  "color": "brightgreen",
  "clauditor": {
    "schema_version": 1,
    "skill_name": "review-pr",
    "generated_at": "2026-04-21T14:00:00Z",
    "iteration": 42,
    "layers": { "l1": {...}, "l3": {...} }
  }
}
```

## Why this shape

- **Two independent lifecycles.** Shields.io can bump their
  `schemaVersion` to 2 to change how they parse `message` / `color`;
  that has nothing to do with whether our aggregation algorithm
  changed. If we wired the two versions together, every shields.io
  bump would force a clauditor-side bump even when our block's shape
  did not change — and vice versa, a clauditor-side rename of a
  `layers.*` field would confusingly force us to bump their
  `schemaVersion` too. Separate fields keep the bump signal clean.
- **Naming follows the OWNER of each field.** The external system
  defines their field's name, type, and casing. Use theirs literally
  (`schemaVersion` camelCase for shields.io). Our nested block is
  ours, so it uses our convention (`schema_version` snake_case per
  `.claude/rules/json-schema-version.md`). Mixing the two casings in
  the same payload is not inconsistency — it is fidelity to each
  schema's ownership.
- **Clauditor's `schema_version` is FIRST inside the nested block.**
  Per `.claude/rules/json-schema-version.md`: a human reading the
  JSON diff sees the version bump immediately. The nested block's
  first-key discipline survives even though the block itself is not
  top-level — we own what is inside the namespace.
- **Any future loader checks the NESTED version, not the
  external-top-level one.** Clauditor does not read shields.io's
  `schemaVersion` field when consuming the payload (that version
  belongs to them). A trend-audit consumer that ever reads badge
  JSON back in validates `payload["clauditor"]["schema_version"]`
  against `_BADGE_CLAUDITOR_SCHEMA_VERSION = 1` following the
  `_check_schema_version` pattern from `src/clauditor/audit.py`.
  Keep the two checks separate in code.
- **The extension block goes last in the top-level key order.**
  Sorted style passthroughs land between the external-required keys
  and the nested extension, so the nested block is always at the
  visual bottom of the payload — the "clauditor supplemental"
  position is unambiguous to someone reading the JSON in a GitHub
  raw-content view.

## What NOT to do

- Do NOT inline our fields at the top level alongside the external
  system's fields. A top-level `"clauditor_skill_name"` or
  `"_clauditor_iteration"` fragments the namespace; the external
  validator (shields.io in this case) may warn on unknown fields;
  and future clauditor-side readers must grep for every
  `"clauditor_*"` prefix. Use ONE namespace key.
- Do NOT use only one version field. "Just the external one" loses
  the clauditor-side bump signal — a trend-audit consumer has no
  way to detect a shape change inside our block. "Just our nested
  one" means the external validator cannot see the version it
  requires.
- Do NOT alias our version to theirs (e.g. copy their
  `schemaVersion: 1` into `clauditor.schema_version: 1`). The two
  will silently drift the first time one bumps. Emit them from
  independent constants (`_SHIELDS_SCHEMA_VERSION = 1` and
  `_BADGE_CLAUDITOR_SCHEMA_VERSION = 1`, or the dataclass default
  field on `ClauditorExtension`).
- Do NOT translate their casing to ours or vice versa. Shields.io's
  docs say `schemaVersion`; emit that literal key. Do not emit
  `"schema_version"` at the top level and hope shields.io is
  lenient — their schema is their contract.
- Do NOT sort the top-level key order alphabetically. Python 3.7+
  preserves insertion order, and the order we emit is load-bearing:
  external-required keys first, our namespace key last (with any
  passthroughs in between). Alphabetical ordering would put
  `clauditor` between `color` and `label`, interleaving our
  namespace with theirs and breaking the visual "supplemental
  section" convention.

## Canonical implementation

`src/clauditor/badge.py` — the `Badge` dataclass and
`ClauditorExtension.to_dict`. See DEC-003, DEC-027, DEC-013 in
`plans/super/77-clauditor-badge.md`. Regression tests:

- `tests/test_badge.py::TestBadgeSerialization::test_top_level_key_order`
  — verifies shields.io-owned keys precede our namespace.
- `tests/test_badge.py::TestBadgeSerialization::test_shields_schema_version_is_camelcase`
  — defends the case-fidelity invariant.
- `tests/test_badge.py::TestBadgeSerialization::test_clauditor_schema_version_is_first_key`
  — defends the nested-first-key invariant via
  `list(result["clauditor"].keys())[0]`.

## When this rule applies

Any future clauditor feature that emits JSON for consumption by an
external system with its own schema. Plausible future callers:

- A `clauditor check-annotate` command producing GitHub check-run
  annotations (GitHub's schema + our per-assertion breakdown).
- A `clauditor export-opengraph` command producing OpenGraph preview
  metadata for skill catalog pages.
- A `clauditor publish` / `clauditor package` command emitting
  package-registry metadata (npm, PyPI, or a hypothetical
  agentskills.io registry) with a clauditor-provenance block.
- Any webhook / callback payload we might emit to an external
  tracker (Linear, Jira) with a clauditor-sourced issue-annotation
  block.

Apply the full recipe: keep the external system's required fields
at the top with their own naming, gate our supplement under a
dedicated namespace key (`"clauditor"` is fine and consistent with
the badge precedent), carry our nested `schema_version` as the
first key of that namespace, and emit the two version constants
from independent sources.

## When this rule does NOT apply

- JSON artifacts that are **clauditor-only** (both writer and
  reader are clauditor itself) — those use the flat
  `"schema_version": 1` as first top-level key per
  `.claude/rules/json-schema-version.md`. Sidecars like
  `assertions.json`, `grading.json`, `extraction.json`,
  `variance.json`, `baseline_*.json`, and `benchmark.json` all
  follow the flat shape; there is no external schema to embed
  within.
- JSON artifacts consumed by a **generic JSON parser** (no specific
  schema at all) — e.g. a debug dump, a REPL-inspection payload.
  No external top-level version is required; use the flat shape.
- NDJSON / streaming formats where there is no top-level object to
  carry a `schemaVersion` on. Each line is its own message; per-line
  versioning is the responsibility of that format's own design.
  See `.claude/rules/stream-json-schema.md` for the defensive
  parser-side pattern in that case.
