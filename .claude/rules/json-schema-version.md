# Rule: Every new JSON artifact declares and validates `schema_version`

Any new persisted JSON file (sidecar, report, history record) must include a
`schema_version: 1` field as the first top-level key, and every loader that
reads the file must verify the version before consuming it. Forward-compat
silence — where a v1 loader silently misparses a v2 file — is a recipe for
subtle history corruption that only surfaces weeks later.

## The pattern

### Writer

```python
def to_json(self) -> str:
    payload = {
        "schema_version": 1,  # first key — canonical diff stability
        "skill_name": self.skill_name,
        ...
    }
    return json.dumps(payload, indent=2) + "\n"
```

### Loader

```python
_SCHEMA_VERSION = 1

def _check_schema_version(data: dict, source: Path) -> bool:
    version = data.get("schema_version")
    if version != _SCHEMA_VERSION:
        print(
            f"warning: {source} has schema_version={version!r}, "
            f"expected {_SCHEMA_VERSION} — skipping",
            file=sys.stderr,
        )
        return False
    return True


def _records_from_sidecar(data: dict, source: Path) -> list[Record]:
    if not _check_schema_version(data, source):
        return []
    # ... parse records ...
```

## Why this shape

- **`schema_version` as first key**: canonical diff stability. Humans and
  tools reading the file see the version immediately; a bump is visually
  obvious in a diff.
- **Hard numeric comparison, not substring**: `version != 1` catches
  missing version, wrong type (string `"1"`), or any future bump. No
  accidental soft-match on `"v1.0"`.
- **Skip + log, do not crash**: mismatched-version files are skipped with a
  stderr warning. The caller (e.g. `load_iterations`) tolerates empty
  results, so one bad file does not take down an entire audit run.
- **Pre-release, so version 1 is the starting point**: when a future bump
  ships, writers emit `schema_version: 2` and loaders are updated to accept
  `{1, 2}` with a per-version parser. This pattern makes the bump explicit
  and auditable.

## Canonical implementation

Writers: `src/clauditor/quality_grader.py::GradingReport.to_json`,
`src/clauditor/grader.py::ExtractionReport.to_json`,
`src/clauditor/audit.py::render_json`,
`src/clauditor/baseline.py::BaselineReports.to_json_map`, and the
sidecar envelopes in `src/clauditor/cli.py::_cmd_grade_with_workspace`.

Loaders: `src/clauditor/audit.py::_check_schema_version` and its call sites
in `_records_from_assertions`, `_records_from_extraction`,
`_records_from_grading`.

### Schema version bumps for #86 (`transport_source` field)

#86 added `transport_source` to `GradingReport` and `ExtractionReport` to
record which Anthropic backend (`"api"` or `"cli"`) handled each grader call.
Rather than creating a new sidecar format, the existing `grading.json` and
`extraction.json` sidecars were bumped to `schema_version: 2`. The audit
loader (`_check_schema_version`) was updated to accept `{1, 2}` and defaults
`transport_source` to `"api"` when reading v1 sidecars so pre-#86 history
stays readable without reprocessing.

`assertions.json` sidecars were NOT bumped — L1 assertions make no Anthropic
call and have no transport to record.

### Schema version bumps for #147 (`provider_source` field)

`#147` added `provider_source` to `GradingReport` and `ExtractionReport` to
record which model provider's SDK (`"anthropic"` or `"openai"`) served each
grader call — the provider-axis sibling of `transport_source`. The field
name is deliberately parallel to `transport_source` per DEC-001 of
`plans/super/147-sidecar-provider-field.md`. The existing `grading.json`
and `extraction.json` sidecars bumped from `schema_version: 2` to
`schema_version: 3`; the audit loader accepts `{1, 2, 3}` and defaults
`provider_source` to `"anthropic"` when reading legacy v1/v2 sidecars so
pre-#147 history stays readable without reprocessing.

In parallel, `history.jsonl` (the per-iteration JSONL stream
`clauditor trend` reads) bumped from `schema_version: 1` to
`schema_version: 2` per DEC-012, adding a top-level `provider` field to
each record. `history.append_record` is now keyword-only on `provider=`
(both call sites — `cli/grade.py` and `cli/extract.py` — pass the
resolved provider). `read_records` defaults missing `provider` to
`"anthropic"` for legacy v1 lines so the trend mixed-provider refusal
(DEC-011) works without rewriting old history.

`assertions.json` was deliberately NOT bumped per DEC-002 — L1 assertions
make no LLM call and have no provider to record. The honest harness-axis
bump for `assertions.json` (and the next `grading.json`/`extraction.json`
revision adding a `harness` field) lives in #152, which is strictly
separable from #147 per DEC-006. `IterationRecord` carries a
`provider: str = "anthropic"` placeholder so audit groups L1 records under
`("anthropic", "L1", id)` regardless of the underlying skill harness — a
small lie in mixed-harness scenarios that #152 will resolve.

DEC-008 refactored the audit loader's accepted-version surface from a
per-filename `frozenset` (the #86-vintage shape) to a `MAX_SCHEMA_VERSION:
dict[str, int]` map plus a pure helper `_is_accepted_version(filename,
version)` that enforces `1 <= version <= MAX_SCHEMA_VERSION[base]`
(stripping the `baseline_` prefix). Future bumps (e.g. #152's `harness`
field) become a one-number-per-file edit instead of re-listing the
accepted set. The "version-and-up" check assumes monotonic forward
compatibility within a sidecar family — per-version shape differences
remain the responsibility of the per-version `_records_from_*` helpers.
File anchors: `src/clauditor/audit.py::MAX_SCHEMA_VERSION` (the canonical
map) + `src/clauditor/audit.py::_is_accepted_version` (the pure helper)
+ `src/clauditor/audit.py::_check_schema_version` (the loader-side
caller that emits the stderr warning).

`history.py` keeps its own narrow accept-list
`_ACCEPTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1, 2})` rather
than reusing the audit `MAX_SCHEMA_VERSION` map — the JSONL stream is a
different artifact family with its own version lifecycle, and
duplicating two integers is cheaper than coupling the two surfaces.
File anchor: `src/clauditor/history.py::SCHEMA_VERSION` +
`_ACCEPTED_SCHEMA_VERSIONS`. If a third sidecar family adopts the same
"max version per filename" shape, extract a shared helper module per
DEC-008's pattern; today two call sites is below the
extraction threshold.

Writers and readers (post-#147):

- `src/clauditor/quality_grader.py::GradingReport.to_json` /
  `GradingReport.from_json` — emits `schema_version: 3`; reader
  defaults missing `provider_source` to `"anthropic"`.
- `src/clauditor/grader.py::ExtractionReport.to_json` /
  `ExtractionReport.from_json` — same shape.
- `src/clauditor/history.py::append_record` (keyword-only `provider=`) /
  `read_records` (defaults missing `provider` to `"anthropic"`).

## When this rule applies

Any new persisted JSON file whose shape may evolve. Internal-only debug
dumps and transient files the codebase does not read back do not need a
version field.
