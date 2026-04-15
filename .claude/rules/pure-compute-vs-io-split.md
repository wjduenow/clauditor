# Rule: Split pure compute from I/O for schema-versioned sidecars

When adding a new per-iteration JSON sidecar, put the aggregation logic
in a **pure function** that returns a dataclass, and let the CLI caller
handle `to_json()` + `write_text()`. Do NOT bundle "run + grade +
serialize + write" inside one helper. The split is small discipline
with outsized payoff: it makes the compute unit-testable without a
tmp_path, keeps staging-dir ownership at one call site, and puts
`schema_version` in exactly one place.

## The pattern

```python
# benchmark.py — pure: no file I/O, no subprocess, no LLM.
def compute_benchmark(
    *,
    skill_name: str,
    primary_reports: list[GradingReport],
    baseline_report: GradingReport,
    primary_results: list[SkillResult],
    baseline_result: SkillResult,
) -> Benchmark:
    # ... aggregate into dataclass ...
    return Benchmark(schema_version=1, ...)


@dataclass
class Benchmark:
    schema_version: int  # first key, per .claude/rules/json-schema-version.md
    ...
    def to_json(self) -> str:
        return json.dumps({"schema_version": self.schema_version, ...}) + "\n"
```

At the CLI call site, inside the staging block:

```python
benchmark = compute_benchmark(...)                              # pure
(skill_dir / "benchmark.json").write_text(benchmark.to_json())  # I/O
# ... workspace.finalize() runs below, per sidecar-during-staging rule.
```

## Why this shape

- **Unit-testable without tmp_path**: `compute_benchmark` takes plain
  dataclasses and returns a plain dataclass. Tests construct fixtures,
  call the function, and assert on return-value fields — no
  `tmp_path`, no monkeypatching `Path.write_text`, no `json.loads` of
  a roundtripped file. See `tests/test_benchmark.py`.
- **One place enforces sidecar-during-staging**: the caller owns the
  `skill_dir` path and is the only code that writes the file. The
  existing `.claude/rules/sidecar-during-staging.md` contract applies
  at exactly one line. A bundled "run + grade + write" helper scatters
  the I/O across internal branches and makes the rule hard to audit.
- **`schema_version` in one place**: the dataclass's `to_json()` is
  the only writer. A bundled helper tempts the author to emit the
  version inline at each call site, where drift goes unnoticed.
- **Composability**: downstream code (audit, compare, future
  dashboards) can call `compute_benchmark` directly against objects
  already loaded into memory, without re-reading files.

## Canonical implementation

`src/clauditor/benchmark.py::compute_benchmark` + `Benchmark.to_json`
— pure aggregation, dataclass holds schema version. Caller:
`src/clauditor/cli.py::_cmd_grade_with_workspace` (search for
`compute_benchmark(`) — two lines inside the staging block, one for
compute and one for write, just before `workspace.finalize()`.

Contrast with the older `_run_baseline_phase` shape in the same file,
which does "run + grade + write `baseline_*.json`" internally. That
pattern predates this rule and is grandfathered; do NOT copy it for
new sidecars.

## When this rule applies

Any new per-iteration JSON sidecar whose shape is computed from
already-collected run data (reports, results, assertion counts,
trigger verdicts). If the "computation" is really just a subprocess
invocation whose stdout you pipe to disk, this split doesn't apply —
there's no pure function to extract.
