# Rule: Clauditor extension lives in a sibling file, not inside an external schema

When clauditor emits JSON that will be consumed by an external
system's own schema (shields.io endpoint JSON, a future GitHub
check-run annotation, a package-registry metadata blob, an OpenGraph
preview payload, ...), the output **MUST** be a pair of sibling
files — one shields.io- (or other-external-schema-) valid, one
carrying the clauditor extension. The extension **MUST NOT** be
embedded as a top-level key inside the external-schema payload.

**Why the sibling pattern, not embed.** The original rule (shipped
with #77) assumed external validators would silently ignore unknown
top-level keys and let us nest a `"clauditor": { ... }` block inside
the shields.io-required payload. Verified on 2026-04-22 against the
live shields.io `endpoint` badge renderer: **shields.io strictly
validates its schema and rejects unknown top-level keys** with an
`invalid properties: <key>` SVG response. The badge rendered grey
with the error text where the quality indicator should have been.
The fix is structural (two files), not cosmetic (rename the key).

This applies broadly. Any external schema worth its name validates.
Assume every external JSON contract will reject unknown fields until
proven otherwise.

## The pattern

Two files, two independent version lifecycles, same directory.

### File 1: `<name>.json` — shields.io / external-schema-only

Strictly the fields the external contract requires. Nothing else.
No clauditor metadata. Keys follow the external system's naming
convention (often camelCase).

```json
{
  "schemaVersion": 1,
  "label": "clauditor",
  "message": "3/3 · L3 100%",
  "color": "brightgreen"
}
```

Whitelisted passthrough keys (`style`, `logoSvg`, `cacheSeconds`,
`link`, etc. — documented by the external contract) land between
the required keys as the only permitted extension. Unknown keys
are rejected at the CLI boundary so the external validator never
sees them.

### File 2: `<name>.clauditor.json` — the clauditor extension

Standalone JSON with the full per-layer breakdown, thresholds,
iteration number, timestamp, and whatever other forensic data the
trend-audit / history consumer needs. First key is
`"schema_version": 1` per
`.claude/rules/json-schema-version.md`. Bumps independently of
the external schema's version.

```json
{
  "schema_version": 1,
  "skill_name": "review-pr",
  "generated_at": "2026-04-22T14:00:00Z",
  "iteration": 42,
  "layers": { "l1": {...}, "l3": {...} }
}
```

### Writer split (dataclass with two serializers)

```python
@dataclass
class Badge:
    label: str
    message: str
    color: str
    clauditor: ClauditorExtension
    style_overrides: dict[str, str | int] = field(default_factory=dict)
    schema_version: int = _SHIELDS_SCHEMA_VERSION  # == 1, camelCase on wire

    def to_endpoint_json(self) -> dict[str, Any]:
        """Shields.io-only payload. NO ``clauditor`` key."""
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "label": self.label,
            "message": self.message,
            "color": self.color,
        }
        for key in sorted(self.style_overrides):
            payload[key] = self.style_overrides[key]
        return payload

    def to_clauditor_extension_json(self) -> dict[str, Any]:
        """Clauditor extension payload. First key is ``schema_version``."""
        return _extension_to_dict(self.clauditor)
```

### Atomic pair write

```python
# cli/badge.py — the I/O side.
extension_target = target.with_suffix(".clauditor.json")

_atomic_write_json(target, badge.to_endpoint_json())
_atomic_write_json(extension_target, badge.to_clauditor_extension_json())
```

Both files are written via sibling-tempfile + `os.replace` (review
pass 2, C2-3). The two-file pair is not fully atomic across both
renames — the first `os.replace` can succeed and the second can
fail — but that is acceptable because the artifacts are fully
regenerable and the failure mode (one new file, one stale file)
is recoverable with `clauditor badge --force`. DEC-011's `--force`
policy applies to BOTH files as a set: either file existing without
`--force` fails the whole write.

## Why this shape

- **External validators reject unknown fields.** Shields.io is the
  specific anchor here, but every structured-JSON consumer worth
  using validates. OpenAPI servers reject unknown request fields
  (many reject on response too); package-registry APIs reject
  unknown top-level keys; webhook receivers typically strip or
  reject. Assuming the external validator is lenient is a gamble.
  The sibling-file pattern removes the gamble.
- **Two independent bump lifecycles.** Shields.io can change
  `schemaVersion` to 2 to reshape how they parse `message`; that
  has nothing to do with whether our aggregation algorithm or
  per-layer shape changed. The files carry their own version
  fields; bumping one does not force bumping the other.
- **One-glance audit separation.** A human reading
  `.clauditor/badges/demo.json` sees exactly what shields.io sees
  — no clauditor noise to squint past. A human reading
  `.clauditor/badges/demo.clauditor.json` sees clauditor telemetry
  without the external-schema envelope. Both files are small and
  self-contained.
- **Sidecar naming mirrors the reader's needs.** Shields.io hits
  `raw.githubusercontent.com/<user>/<repo>/<branch>/.clauditor/badges/<skill>.json`
  via the `endpoint?url=` pattern; it reads only the first file.
  A trend-audit consumer discovers `.clauditor/badges/*.clauditor.json`
  by glob and reads only the second. There is no cross-file
  dependency at read time.
- **`--force` policy applies to the pair, not one file.** A user
  who regenerated the shields JSON but forgot the extension (or
  vice versa) would end up with a mismatched pair where
  `shields.color == "brightgreen"` and
  `extension.iteration == 41` while the real latest iteration is
  42. Requiring `--force` for either file existing, and writing
  both together, prevents this drift.
- **`dict[str, str | int]` for style passthroughs.** Shields.io
  types some fields as integers (`cacheSeconds`) and others as
  strings (`style`, `logoSvg`); the CLI coerces at parse time
  (review pass 3, C3-1). The type annotation mirrors that.
- **Reserved-key rejection.** A user passing `--style
  schemaVersion=2` or `--style label=hijacked` would have silently
  overwritten the canonical shields.io fields via the
  sorted-alphabetical merge inside `to_endpoint_json`. The
  `_RESERVED_STYLE_KEYS` frozenset (Copilot PR review, 2026-04-22)
  rejects those at the CLI parse boundary with exit 2.

## What NOT to do

- Do NOT embed a top-level `"clauditor"` (or `"_meta"`, or
  `"_clauditor"`, or any prefix variant) key inside the shields.io
  payload. Shields.io rejects unknown top-level fields; we verified
  this against the live renderer. The error is a grey "invalid
  properties: <key>" SVG that silently replaces the quality badge.
- Do NOT fold the version fields together (e.g. reuse shields.io's
  `schemaVersion: 1` as our version too). The two lifecycles are
  distinct; coupling them means one bump forces the other.
- Do NOT use a shields.io-accepted field (like `link` or
  `logoSvg`) to smuggle clauditor data through. The external
  field's semantics are owned by the external schema;
  repurposing them for our own payload is both fragile (next
  shields.io release may sanitize) and user-hostile
  (a human inspecting the link / logo sees garbage).
- Do NOT write only the shields.io file and drop the extension.
  The per-layer breakdown, thresholds, and iteration number are
  real data that trend-audit / forensic consumers will want; the
  cost of the second file is trivial.
- Do NOT forget the `--force` check on BOTH files. A user who
  regenerated one file but forgot the other would otherwise end
  up with a mismatched pair. The CLI must require `--force` for
  either file existing.

## Canonical implementation

`src/clauditor/badge.py::Badge.to_endpoint_json` +
`Badge.to_clauditor_extension_json` — the two-method split. See
DEC-003, DEC-027, DEC-013 in `plans/super/77-clauditor-badge.md`
for the original embed-style decisions; the shift to sibling files
is the 2026-04-22 post-merge fix (branch
`fix/badge-shields-invalid-properties`).

`src/clauditor/cli/badge.py::_write_badge_sidecars` — the I/O
layer. Owns the `--force` collision check for the pair, the
`.clauditor.json` suffix derivation via `Path.with_suffix`, and
the per-file atomic tmp+rename publication.

Regression tests:

- `tests/test_badge.py::TestBadgeSerialization::test_top_level_key_order`
  — verifies `"clauditor" not in result` for the shields payload.
- `tests/test_badge.py::TestBadgeSerialization::test_clauditor_extension_schema_version_is_first_key`
  — verifies extension file's first-key invariant.
- `tests/test_badge.py::TestBadgeSerialization::test_payload_is_json_serializable`
  — round-trip check on both payloads.
- `tests/test_cli_badge.py::TestCmdBadgeHappyPath::test_writes_badge_json_default_path`
  — verifies BOTH sidecar files land on disk with correct shapes.

## When this rule applies

Any future clauditor feature that emits JSON for consumption by an
external system with its own schema. Plausible future callers:

- A `clauditor check-annotate` command producing GitHub check-run
  annotations (GitHub's schema + our per-assertion breakdown).
- A `clauditor export-opengraph` command producing OpenGraph
  preview metadata for skill catalog pages.
- A `clauditor publish` / `clauditor package` command emitting
  package-registry metadata (npm, PyPI, or a hypothetical
  agentskills.io registry) with a clauditor-provenance block.
- Any webhook / callback payload emitted to an external tracker
  (Linear, Jira) with a clauditor-sourced issue-annotation block.

Apply the full recipe: keep the external system's required fields
in one file with nothing extra; put the clauditor supplement in a
SIBLING file named `<stem>.clauditor.json` (or
`<stem>.clauditor.<ext>` if the external schema uses a non-JSON
format); carry the extension's own `schema_version` as its first
key; write both files atomically with shared `--force` policy.

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
