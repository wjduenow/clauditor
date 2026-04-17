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
helper eliminated that drift risk, and when #39 / `clauditor-iag`
added `EvalSpec.user_prompt`, the corresponding resolution-logic
change in `blind_compare_from_spec` (switching from `test_args` to
`user_prompt`) was isolated to `quality_grader.py`: both the CLI
caller and the pytest fixture picked up the new resolution
automatically, validating the rule's prediction.

### Third anchor (baseline phase split)

`src/clauditor/baseline.py::compute_baseline` — pure function that
takes an already-run `SkillResult` + `EvalSpec` and returns a
`BaselineReports` dataclass containing L1 assertions, L2 extraction
(when sections declared), and L3 grading. The dataclass provides
`to_json_map()` returning `{filename: json_str}` for all baseline
sidecars, with `schema_version` as the first key in each payload.

Single caller: `src/clauditor/cli.py::_run_baseline_phase` — thin
wrapper that handles subprocess invocation (`run_raw`), input-file
staging, stderr progress, and `write_text()` for each sidecar.

Before the extraction (pre-#41), `_run_baseline_phase` bundled
subprocess invocation, grading, assertion evaluation, extraction,
JSON serialization, and file writes in a single 98-line function —
the grandfathered counter-example previously cited by this rule. The
refactored split makes the grading logic unit-testable without
`tmp_path` or subprocess mocks, and positions the pure helper for
reuse from a future pytest fixture.

### Fourth anchor (decision function for setup)

`src/clauditor/setup.py::plan_setup` — pure function that takes a
`cwd`, a resolved `pkg_skill_root`, and the `force` / `unlink` flags
and returns a `SetupAction` enum member describing what the I/O
layer should do next. First anchor for a *decision function*
(returns an enum discriminator) rather than a data-aggregation or
resolve-and-compose helper: the pure compute is "inspect the
filesystem, classify the situation, pick the branch"; the I/O layer
then dispatches on the enum to run `os.symlink`, `os.unlink`,
`shutil.rmtree`, or print a refusal. Traces to DEC-014 in
`plans/super/43-setup-slash-command.md`.

Two callers:

- `src/clauditor/cli/setup.py::cmd_setup` — side-effect layer.
  Translates each `SetupAction` into filesystem operations,
  stdout/stderr messages, and exit codes (DEC-008 / DEC-009 /
  DEC-016). Also runs the "plan + dispatch, retry once on
  `FileExistsError`" loop for the atomic create-or-fail path.
- `tests/test_setup.py` — pure consumer. 23 tests, one per enum
  branch plus home-exclusion guards for `find_project_root`. Each
  test constructs a `cwd` + `pkg_skill_root` under `tmp_path`, calls
  `plan_setup`, and asserts on the returned enum member — no
  subprocess mocks, no stdout capture, no exit-code assertions.

The split makes the home-directory exclusion in `find_project_root`
directly unit-testable (see also
`.claude/rules/project-root-home-exclusion.md`): a bundled "classify
and execute" helper would have hidden that guard behind a subprocess
mock and an assertion on the absence of a symlink, instead of a
direct `plan_setup(cwd=home_like_dir, ...)` returning the refusal
enum.

### Fifth anchor (LLM grader pure split)

The four async LLM-grader entry points in `grader.py` and
`quality_grader.py` — `extract_and_grade`, `extract_and_report`,
`grade_quality`, `blind_compare` — were refactored into thin
`build_prompt → await call_anthropic → parse_response → return`
wrappers, with all verdict logic extracted into pure helpers. Each
async wrapper is now under ~50 body lines and does zero parsing,
JSON decoding, or assertion construction: it builds a prompt, awaits
a single Anthropic call (via the centralized helper — see
`.claude/rules/centralized-sdk-call.md`), and hands the response
text to a pure builder.

The pure helpers extracted (all side-effect-free, unit-testable
without `AsyncMock` or any SDK patch):

- `src/clauditor/grader.py`:
  - `build_extraction_prompt(eval_spec, output_text=None)` — two-arg
    form returns the full prompt with a fenced `<skill_output>`
    block; one-arg form keeps the header-template tests working.
  - `parse_extraction_response(text, eval_spec) → ExtractionParseResult`
    — strips markdown fences, parses JSON, normalizes into
    `ExtractedOutput`, surfaces flat-list failures as structured
    `ExtractionParseError` entries so both callers translate them
    into the appropriate output shape.
  - `build_extraction_assertion_set(...)` — pure core of
    `extract_and_grade`.
  - `build_extraction_report_from_text(...)` — pure core of
    `extract_and_report`.
  - `_strip_markdown_fence(text)` — shared fence stripper.
- `src/clauditor/quality_grader.py`:
  - `build_grading_prompt(eval_spec, output_text=None)` — parallel
    two-form shape.
  - `build_grading_report(response_text, eval_spec, ...)` — pure
    core of `grade_quality`; dispatches on empty text / alignment
    failure / unparseable JSON / happy path.
  - `parse_blind_response(text)` — promoted to a public name; the
    legacy `_parse_blind_response` is kept as an alias for back-
    compat callers.
  - `combine_blind_results(parsed1, parsed2, ...)` — pure core of
    `blind_compare`; handles both-fail / only-one-parsed / agreement
    / disagreement branches and the verdict arithmetic.
  - `build_blind_prompt(...)` — retained; verified to stay inside
    the pure layer after the extraction.
  - `_translate_blind_result`, `_validate_blind_inputs`,
    `_pick_blind_mappings`, `_slots_for_mapping`,
    `_build_blind_prompt_for_mapping` — private pure sub-helpers
    that partition the blind-compare protocol (input validation,
    mapping selection, slot assignment, per-mapping prompt build,
    result translation) so each step is testable in isolation.

The wrappers ended up this small:

- `grade_quality` — ~36 body lines: build prompt, `_monotonic` /
  `call_anthropic` / `_monotonic` for duration, first-text-block
  extraction, delegate to `build_grading_report`.
- `blind_compare` — ~47 body lines: validate, pick mappings, build
  two prompts, `asyncio.gather(call_anthropic, call_anthropic)`,
  parse each response, delegate to `combine_blind_results`.
- `extract_and_grade` — ~28 body lines: build prompt, single
  `call_anthropic`, delegate to `build_extraction_assertion_set`.
- `extract_and_report` — ~33 body lines: build prompt, single
  `call_anthropic`, delegate to `build_extraction_report_from_text`.

Why the split matters here, beyond the usual testability payoff:

- **No SDK mocks in the pure-helper tests**: `TestBuildGradingReport`,
  `TestCombineBlindResults`, `TestParseExtractionResponse`, and
  `TestBuildExtractionAssertionSet` feed canned response strings
  directly to the builders and assert on the returned
  `GradingReport` / `BlindReport` / `AssertionSet`. No `patch`, no
  `AsyncMock`, no `anthropic` SDK import. Tests that verify the
  Anthropic call itself live separately and mock
  `clauditor._anthropic.call_anthropic` at a single seam per module.
- **Error-branch coverage is cheap**: the empty-text, unparseable-
  JSON, alignment-failure, and disagreement branches each get a
  direct unit test passing the specific bad string to the pure
  builder. Previously those branches could only be reached through
  an `AsyncMock` with `side_effect` wiring that hid bugs behind
  multi-layer setup.
- **The async wrappers are now trivially reviewable**: a reviewer
  reading `grade_quality` sees the shape (build → await → parse →
  return) at a glance and can check the five pure helpers
  independently. Before the split, verdict logic was interleaved
  with SDK exception handling, token accounting, and duration
  tracking in one ~140-line function.

Traces to bead `clauditor-24h.5` (US-005) of
`plans/super/audit-quality-2026-04.md`. Companion rule:
`.claude/rules/centralized-sdk-call.md` codifies the shared
`call_anthropic` seam the thin wrappers all target.

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
