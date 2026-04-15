# Rule: Split pure compute from I/O, keep wrappers thin

When adding new logic that combines spec/config resolution with a
side-effectful call (JSON serialization, an LLM request, a subprocess,
an HTTP fetch), put the **resolution + composition** in a pure
function and let callers handle the I/O. Do NOT bundle "load +
resolve + call + serialize + write" inside one helper. The split is
small discipline with outsized payoff: the pure function is
unit-testable without fixtures, stays reusable from multiple call
sites, and moves each I/O concern to exactly one line at the call
site.

Originally codified for schema-versioned JSON sidecars (see first
canonical implementation), the pattern generalizes to any pure
resolve-and-compose helper that has more than one downstream caller.

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
- **Composability across dissimilar callers**: the same pure function
  can be consumed from a CLI (where the caller also does file I/O,
  stderr printing, and exit-code mapping) AND from a pytest fixture
  (where the caller wraps the async function in `asyncio.run` and
  returns the result to a sync test). Neither caller has to duplicate
  the resolution logic; both inherit future resolution changes for
  free.
- **One place enforces referential drift detection**: if a future
  ticket adds a new field to the spec (e.g. `EvalSpec.user_prompt`),
  only the pure helper needs to learn about it. Every caller picks it
  up automatically. A bundled "resolve in-line at each call site"
  approach invites divergence the next time resolution rules change.

## Canonical implementations

### First anchor (sidecar aggregation)

`src/clauditor/benchmark.py::compute_benchmark` + `Benchmark.to_json`
— pure aggregation, dataclass holds schema version. Caller:
`src/clauditor/cli.py::_cmd_grade_with_workspace` (search for
`compute_benchmark(`) — two lines inside the staging block, one for
compute and one for write, just before `workspace.finalize()`.

### Second anchor (shared resolution between CLI and pytest fixture)

`src/clauditor/quality_grader.py::blind_compare_from_spec` — pure
async helper that takes a `SkillSpec` + two pre-loaded output strings
and resolves `user_prompt`, `rubric_hint`, and the grading `model`
from the spec before awaiting `blind_compare`. Two callers:

- `src/clauditor/cli.py::_run_blind_compare` — file I/O, stderr
  progress print, `ValueError` → exit-2 + stderr `ERROR:` mapping,
  `_print_blind_report`. All the I/O concerns live in this one
  function.
- `src/clauditor/pytest_plugin.py::clauditor_blind_compare` — sync
  fixture factory. Loads the spec via `clauditor_spec`, calls
  `asyncio.run(blind_compare_from_spec(...))`, returns a `BlindReport`.
  No file I/O; the test provides the output strings directly.

Before the extraction (pre-#clauditor-0bo), the CLI had 15 lines of
inline `spec.eval_spec.test_args` / `grading_criteria` / `grading_model`
resolution that would have had to be duplicated verbatim in the
fixture — with no mechanism to keep them in lockstep. The extracted
helper eliminates that drift risk and makes the next ticket
(`clauditor-iag`, add `EvalSpec.user_prompt`) a single-file change
that both callers pick up automatically.

## Grandfathered counter-example

`src/clauditor/cli.py::_run_baseline_phase` does "run + grade + write
`baseline_*.json`" internally. That pattern predates this rule and is
grandfathered; do NOT copy it for new resolve-and-compose helpers.

## When this rule applies

Any new code that combines spec/config resolution with a
side-effectful call (JSON serialization, LLM request, subprocess,
HTTP fetch) AND either (a) will have more than one caller, (b)
produces a sidecar whose shape needs a schema version, or (c) has
non-trivial resolution logic that would otherwise be duplicated.

## When this rule does NOT apply

If the "computation" is really just a subprocess invocation whose
stdout you pipe straight to disk, there's no pure function to
extract — leave it as a single helper. If the code is a one-off CLI
command with no reusable resolution logic, inlining is fine; don't
invent a second caller just to satisfy the rule.
