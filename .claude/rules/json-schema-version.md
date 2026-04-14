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
`src/clauditor/audit.py::render_json`, and the sidecar envelopes in
`src/clauditor/cli.py::_cmd_grade_with_workspace` /
`_run_baseline_phase`.

Loaders: `src/clauditor/audit.py::_check_schema_version` and its call sites
in `_records_from_assertions`, `_records_from_extraction`,
`_records_from_grading`.

## When this rule applies

Any new persisted JSON file whose shape may evolve. Internal-only debug
dumps and transient files the codebase does not read back do not need a
version field.
