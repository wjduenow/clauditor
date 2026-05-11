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

### Sixth anchor (runner classification + CLI render seams)

The #63 runner-error-surfacing work introduced three new pure
helpers that each sit at a natural "plain types at the boundary"
seam, testable in isolation without subprocess or SDK mocks:

- `src/clauditor/_harnesses/_claude_code.py::_classify_result_message(msg: dict)
  -> tuple[str | None, str | None]` — consumes a stream-json
  `result` message dict, returns `(error_text, error_category)`.
  Strict `is True` check on `is_error`; 4 KB soft cap on the
  `result` string; keyword-priority classification (`rate_limit`
  before `auth` before `api`). `ClaudeCodeHarness.invoke` calls it once per run.
- `src/clauditor/_harnesses/_claude_code.py::_detect_interactive_hang(stream_events:
  list[dict], final_text: str) -> bool` — gated on
  `num_turns==1 + stop_reason=="end_turn"` AND either a trailing
  `?` in the final text OR an `AskUserQuestion` `tool_use` block
  in assistant content. Every malformed/missing-field branch
  degrades to `False` without raising.
- `src/clauditor/cli/__init__.py::_render_skill_error(result:
  SkillResult, *, unknown_fallback: str = "Unknown error") -> str`
  — returns the error *tail* (callers keep their own
  `"ERROR: <prefix>: "` framing). Contains the `CATEGORY_HINTS`
  lookup table, 1000-char truncation logic, and the
  `(stderr: ...)` warnings trailer. Five CLI commands
  (`validate`, `run`, `capture`, `grade`, `extract`) call it.

All three tested directly via dedicated test classes
(`TestClassifyResultMessage`, `TestDetectInteractiveHang`,
`TestRenderSkillError`) that pass plain dicts / dataclasses /
strings — no `patch("subprocess.Popen")`, no `AsyncMock`, no
SDK stubs. The "Test quality" payoff this rule promises shows
up cleanly: every keyword-priority edge case, every defensive
fallback branch, every truncation-boundary check is a one-line
construction away.

Why the split mattered specifically here:

- **Two parsers + one renderer, one seam each**: the parser-side
  pure helpers (`_classify_result_message`,
  `_detect_interactive_hang`) are called from deep inside
  `ClaudeCodeHarness.invoke`'s streaming loop; embedding their logic inline would
  have required subprocess-level mocking to reach the error
  branches. With the extraction, the branches are reachable
  from in-memory tests and the `ClaudeCodeHarness.invoke` wiring is narrow
  enough to review at a glance.
- **Render-precedence has exactly one audit site**: the
  stream-json-wins-over-stderr ordering from DEC-001, the
  1000-char truncation from DEC-003, the category hint line
  from DEC-004, and the `(stderr: ...)` trailer from DEC-002
  all land in `_render_skill_error`. Five CLI commands share
  the one authoritative implementation; a future precedence
  change edits one file, not six.
- **Defensive guards stay reviewable**: `_classify_result_message`
  has eight distinct `isinstance` / `is True` / `.get(... , default)`
  guards against malformed stream-json dicts. Having them live
  in a pure helper means a reader can scan them in ~30 lines
  without chasing through the surrounding streaming loop.

Traces to bead `clauditor-cha.11` (US-011) of
`plans/super/63-runner-error-surfacing.md`. Companion rule:
`.claude/rules/stream-json-schema.md` codifies the defensive
stream-json-parsing contract the two parser-side helpers
honor.

### Seventh anchor (Codex helper composition)

The #149 CodexHarness work introduced four pure helpers that
collectively drive the harness's failure-classification, advisory-
detection, and stderr-redaction surface — each at a natural "plain
types at the boundary" seam, testable without `subprocess.Popen`
mocks or live `codex exec` invocations:

- `src/clauditor/_harnesses/_codex.py::_classify_codex_failure(message:
  str | None) -> tuple[str, Literal["rate_limit", "auth", "api"]]`
  — consumes a raw failure string (Codex surfaces text on
  `turn.failed.error.message` per-turn and on top-level `error.message`
  for stream-fatal errors, NOT one stream-event dict like Claude).
  Returns `(truncated_text, category)`; closed Literal stays closed
  per DEC-007 of `plans/super/149-codex-harness.md`. 4 KB truncation
  with classification preserved across the cap. `CodexHarness.invoke`
  calls it from both the `turn.failed` and top-level `error` arms.
- `src/clauditor/_harnesses/_codex.py::_detect_codex_dropped_events(
  stream_events: list[dict]) -> int` — sums the leading integer on
  Lagged-synthetic `item.completed` events (`item.type == "error"`,
  message matches `<N> events were dropped...`). Defensive read posture
  per `.claude/rules/stream-json-schema.md`: every malformed/missing-
  field branch degrades to 0 without raising. Wired post-parse-loop
  to surface a `dropped-events:` advisory warning (DEC-018) without
  setting `error_category`.
- `src/clauditor/_harnesses/_codex.py::_detect_codex_truncated_output(
  stream_events: list[dict], last_message_text: str) -> bool` —
  returns True when no `agent_message` items appeared but the
  `--output-last-message` tempfile is non-empty. Surfaces the
  `last-message-empty:` advisory warning per DEC-018 and triggers
  the DEC-005 fallback that promotes the tempfile content into
  `InvokeResult.output`.
- `src/clauditor/_harnesses/_codex.py::_filter_stderr(stderr_text:
  str) -> str` — hybrid redact-then-cap pipeline per DEC-013:
  per-line regex redaction of bare `sk-...` tokens and `Bearer ...`
  prefixes, then per-line substring redaction against
  `_AUTH_LEAK_PATTERNS`, then 8 KB byte cap (cap last so a redacted
  line that lands inside the cap is still safely redacted). Pure +
  non-mutating per `.claude/rules/non-mutating-scrub.md`.

All four tested directly via dedicated test classes
(`TestClassifyCodexFailure`, `TestDetectCodexDroppedEvents`,
`TestDetectCodexTruncatedOutput`, `TestFilterStderr`) that pass
plain strings/dicts and assert on the return value — no
`patch("subprocess.Popen")`, no `_FakeCodexPopen`, no Codex CLI on
PATH. Every keyword-priority edge case (rate_limit before auth
before api per DEC-007), every Lagged-event count parse branch,
every redaction pattern, every truncation-boundary check is a
one-line construction away.

The async `CodexHarness.invoke` wrapper that wires these four
helpers is the canonical thin-orchestrator shape: argv assembly,
TemporaryDirectory + drainer-thread + watchdog setup, NDJSON parse
loop, post-parse advisory pass, then `InvokeResult` construction.
None of the verdict logic, classification logic, or redaction
logic lives inside `invoke`; all four pure helpers stay at the
boundary.

Why the split mattered specifically here:

- **Two distinct error surfaces, one classifier**: Codex emits
  errors via two different shapes (per-turn `turn.failed.error` and
  stream-fatal top-level `error`). A single
  `_classify_codex_failure` taking the raw message string keeps
  the dispatch site simple — `invoke` reads the message from
  whichever event arrived and hands it to one classifier. An inline
  classifier-per-arm would have produced two near-identical keyword
  ladders that drift over time.
- **Advisory vs failure separation enforced structurally**: the
  two `_detect_codex_*` advisory helpers return primitive types
  (int, bool); they cannot accidentally set `error_category` or
  down-classify `succeeded_cleanly`. The orchestrator translates
  their returns into `warnings: list[str]` entries with the DEC-018
  prefixes (`dropped-events:`, `last-message-empty:`). A future
  contributor extending the advisory surface gets a structurally-
  enforced "advisory means warning, not failure" guarantee.
- **Stderr redaction is the load-bearing security boundary**:
  `_filter_stderr` is the last line of defense before stderr text
  is appended to `SkillResult.warnings` and persisted to sidecars.
  Keeping it pure + tested independently lets the redaction-pattern
  set evolve (e.g. adding new key shapes) without re-validating the
  surrounding subprocess plumbing.

Traces to bead `clauditor-cif.1` (US-001) of
`plans/super/149-codex-harness.md`. Convention rules: C2 (this
rule) and C4 (`monotonic-time-indirection.md` for the `_monotonic`
alias). Companion rules: `.claude/rules/non-mutating-scrub.md`
(the `_filter_stderr` non-mutating contract),
`.claude/rules/stream-json-schema.md` (the defensive parse-loop
shape the orchestrator uses around the four helpers),
`.claude/rules/harness-protocol-shape.md` (lists CodexHarness as
the second non-mock canonical implementation).

### Eighth anchor (cross-axis mixed-dimension detection)

`src/clauditor/audit.py::detect_mixed_dimension` — pure helper
introduced in #153 to drive the cross-axis comparability refusal
on both `trend` and `compare`. Signature: `(records: list[dict],
*, dimension: Literal["harness", "provider"]) -> tuple[bool,
list[str]]`. Sibling of the per-axis coercers
`_provider_or_default` (audit.py:91) and `_harness_or_default`
(audit.py:108); reuses them as the dispatch table so coercion
semantics for non-string / blank / `None` records cannot drift
between caller commands. No I/O, never raises.

Two callers, both thin orchestrators:

- `src/clauditor/cli/trend.py::cmd_trend` — calls
  `detect_mixed_dimension` once per axis on the full filtered
  set BEFORE the `--last` slice, collects refusal messages from
  both axes into a list, and prints them together at exit 2 if
  any axis is mixed without an opt-in (DEC-011 multi-axis
  refusal). Per-axis WARNINGs for opt-in flags fire only after
  every axis has cleared its refusal check.
- `src/clauditor/cli/compare.py::cmd_compare` — calls
  `detect_mixed_dimension` on a 2-element list (the two
  compared inputs' metadata dicts) inside the `before_kind ==
  "grade.json" and after_kind == "grade.json"` gate. Per
  DEC-003, `.txt` capture pairs silent-skip — the helper is
  never called when metadata is unavailable.

Tests: `tests/test_audit.py::TestDetectMixedDimension` (line
2277) — six unit tests on the pure helper. No `tmp_path`, no
subprocess mocks, no fixtures beyond inline list literals. Each
edge case (single-value, mixed, missing key, non-string,
harness mirror, empty input) is one assertion away.

Why the split mattered specifically here:

- **One seam, two commands**: `trend` averages over many
  records; `compare` deltas exactly two. Both must reject mixed-
  axis aggregation with byte-identical message lead-ins
  (`"Mixed <plural> detected"`) and identical coercion
  semantics. Routing both through the same pure helper makes
  this structural — a future change to coercion (e.g. adding
  whitespace-stripping or case normalization) propagates
  uniformly.
- **Tests trivially cover the malformed-record paths**: the
  fourth test case (whitespace-only / `None` / non-string
  values default safely) is impossible to write cleanly when
  the detection logic is inlined inside `cmd_trend` or
  `cmd_compare` — those entry points require iteration-dir
  fixtures and full history files. The pure helper accepts
  `[{"provider": "  "}, {"provider": None}, {"provider": 42}]`
  as a single inline literal.
- **Future axes plug in without orchestrator churn**: when
  `transport_source` (or any other stack-identity dimension)
  joins as a third axis, the pure helper gains one Literal
  member and one dispatch-table entry; the orchestrator code
  in `cmd_trend` / `cmd_compare` extends by one
  `detect_mixed_dimension(records, dimension="transport_source")`
  call. The `.claude/rules/cross-axis-comparability-refusal.md`
  rule codifies the full extension recipe.

Traces to DEC-010 of `plans/super/153-cross-axis-comparability.md`.
Companion rule: `.claude/rules/cross-axis-comparability-refusal.md`
(the per-axis refusal+filter+opt-in shape this helper drives).

### Ninth anchor (pricing-table cost estimation)

`src/clauditor/_providers/_pricing.py` — pure-compute module
introduced in #169 to back the `cost_usd` field on
`IterationContext`. Sibling shape to `_providers/_retry.py`: a
small pure-compute module living inside the `_providers` package
without being a `call_model` consumer. Two helpers, both pure:

- **`estimate_cost(provider, model, input_tokens, output_tokens,
  reasoning_tokens=None) -> float | None`** — pure dict-lookup
  + arithmetic. Validates input types up front (raises
  `ValueError` on contract violation: non-string provider/model,
  bool / non-`int` / negative tokens), looks up the rate card in
  `_PRICING_TABLE`, and computes
  `(input * input_per_mtok + (output + reasoning) * output_per_mtok)
  / 1_000_000`. Returns `None` cleanly on unknown
  `(provider, model)` pairs so callers can write `cost_usd: null`
  without raising. Reasoning tokens are folded into the effective
  output count and billed at the model's output rate per the
  provider research notes (see the module docstring's
  reasoning-tokens contract).
- **`compute_iteration_cost_usd(grading_report, extraction_report,
  provider) -> float | None`** — pure composition helper. Sums
  Layer 2 + Layer 3 grader-call cost from the already-computed
  report dataclasses (`GradingReport`, `ExtractionReport`). Per
  DEC-002 of #169 the helper is **all-or-nothing**: any internal
  `estimate_cost` returning `None` → composition returns `None`
  rather than partial cost. A "roughly right" partial estimate
  is silently wrong for budgeting and trend reads, so
  null-on-any-miss is the safe default. `extraction_report=None`
  contributes `0.0` to the sum (that is NOT a lookup miss — it
  is a genuine "no Layer-2 call happened" signal).

Both helpers satisfy the rule's pure-compute contract at the
**return-value layer**: their returned cost is a deterministic
function of the inputs, with no network I/O, no file I/O, and no
subprocess. The one documented exception is the announcement
family — `estimate_cost` may emit one-shot
`print(..., file=sys.stderr)` notices via
`announce_pricing_table_stale_if_old` and `announce_unknown_model`,
each of which mutates a module-level flag/set on first fire and
no-ops on subsequent calls. The notices belong to the
"implicit-coupling announcements" family documented in
`.claude/rules/centralized-sdk-call.md` and never affect the
returned cost. Tests reset the module-level flags via autouse
fixtures, so the deterministic-return-value contract is observable
in isolation while the announcement side-effects are exercised by
their own dedicated test classes.

Two callers today:

- `src/clauditor/cli/grade.py::_write_context_sidecar` — the
  production seam. Uses `compute_iteration_cost_usd` to populate
  `IterationContext.cost_usd` during workspace staging per
  `.claude/rules/sidecar-during-staging.md`. The CLI seam owns
  provider resolution and the surrounding I/O (sidecar
  serialization, atomic publication); the pure helper just sums.
- `tests/test_providers_pricing.py` (52 tests) and
  `tests/test_cli.py::TestCmdGradeContextCostUsd` — exercise
  both functions directly with plain-dataclass fixtures, no
  `tmp_path`, no subprocess mocks. Every contract-violation
  branch (non-string provider, bool token, negative count, both
  L2 / L3 lookup-miss permutations) is reachable via inline
  arguments.

Why the split mattered specifically here:

- **Two helpers, one pure module**:
  `compute_iteration_cost_usd` concentrates Layer 2 + Layer 3
  dispatch and all-or-nothing aggregation logic in one testable
  seam — per-layer cost dispatch, lookup-miss propagation, and
  the null-on-any-miss rule that defends `cost_usd`'s sidecar
  contract. Inlining at the call site
  (`_write_context_sidecar`) would have spread the per-layer
  dispatch + the all-or-nothing rule across the I/O layer, where
  future refactors are likely to drift them. The pure helper
  keeps the policy in one place even though there is only one
  production caller today.
- **Sidecar-shape contract**: `cost_usd` is a persisted field on
  `context.json` whose shape is part of the always-v1 contract
  (see `.claude/rules/json-schema-version.md` "New sidecar
  family — `context.json` (#154)"). The pure-helper boundary
  defends that contract: bug fixes to the cost arithmetic (e.g.
  if a future ticket adds cache-token rates) land inside
  `estimate_cost` and propagate to every caller, with no
  per-call-site duplication that could ship a divergent shape on
  disk.
- **Borderline-case threshold**: the rule's existing one-caller
  threshold says "don't extract a pure helper just to satisfy
  the rule." The pricing module qualifies for extraction
  because it (a) produces a sidecar field whose shape is part of
  `context.json`'s contract, and (b) has non-trivial resolution
  logic (provider/model dispatch + reasoning-tokens-at-output-
  rate folding) that would otherwise duplicate at the call site.
  A simpler "compute one number from one number" helper would
  not earn extraction.

Traces to bead `clauditor-60x.8` (US-008) of
`plans/super/169-pricing-cost-estimator.md`. Companion rules:
`.claude/rules/multi-provider-dispatch.md` "Provider-dispatch
shape extends to non-auth lookups (#169)" (the structural-
routing invariant `estimate_cost` preserves —
lookup-miss→`None` vs contract-violation→`ValueError`),
`.claude/rules/centralized-sdk-call.md` "Implicit-coupling
announcements — an emerging family" (the two pricing-coupled
announcement members `_announced_pricing_table_stale` and
`_announced_unknown_models` that fire from inside
`estimate_cost`), `.claude/rules/json-schema-version.md` "New
sidecar family — `context.json` (#154)" (the always-v1 sidecar
field this module populates).

### Tenth anchor (defensive SDK extraction + nullable-aware aggregation)

The #170 `reasoning_tokens` work introduced two pure helpers that
together codify the **defensive-extract → nullable-aware-sum**
pipeline shape: a per-provider extractor that maps a raw SDK
shape to `int | None` with a load-bearing `bool`-guard, and a
chain-level aggregator that distinguishes "no source surfaced a
count" (all-None → None) from "sources surfaced zero" (mixed or
all-int → real sum). The two helpers live in different modules
(provider-side vs report-side) but solve a coupled concern; both
are unit-testable in isolation without `AsyncMock`, subprocess
patches, or any SDK import:

- `src/clauditor/_providers/_openai.py::_extract_reasoning_tokens(usage:
  Any) -> int | None` — defensive read of
  `usage.output_tokens_details.reasoning_tokens`. Returns the
  integer (including `0` — preserved as a real "model didn't
  reason" signal per DEC-002 of `#170`); returns `None` for
  `usage is None`, missing nested attribute, missing field,
  non-int value, `bool` value (the guard), or any
  attribute-access exception. The bool-vs-int guard precedes
  the `isinstance(value, int)` check per
  `.claude/rules/constant-with-type-info.md` because Python's
  `isinstance(True, int)` returns `True` — without the explicit
  `isinstance(value, bool) → return None` arm, a future SDK
  surfacing `reasoning_tokens=True` would silently coerce to
  `1`. Anthropic does NOT get an analogous helper: per DEC-001
  the SDK has no separately-billed reasoning-token field, so
  the construction site at
  `src/clauditor/_providers/_anthropic.py::_extract_result`
  hardcodes `reasoning_tokens=None` with an inline comment
  citing the rationale. The asymmetry is structural ("when the
  SDK doesn't expose it, return None — don't approximate"),
  not an oversight.
- `src/clauditor/quality_grader.py::_sum_optional_reasoning_tokens(values:
  list[int | None]) -> int | None` — chain-level aggregator
  that filters out `None`s and returns `sum(present) if present
  else None`. The semantic teaching is novel: a single `None`
  component does NOT poison a sum that has at least one real
  value (`[None, 42]` → `42`, NOT `0` and NOT `None`), and an
  all-`None` chain stays `None` rather than collapsing to `0`.
  This preserves the provider-attribution distinction across
  the multi-call grader chain — variance reps, parse-retry
  attempts, blind-compare's two parallel calls, mixed
  Anthropic+OpenAI grader pairs all aggregate cleanly. The
  `or None` shorthand on an empty `present` list collapses the
  empty-sum-equals-zero foot-gun. Per DEC-003 of #170.

Both tested directly via dedicated test classes
(`TestExtractReasoningTokens` in `tests/test_providers_openai.py`,
`TestSumOptionalReasoningTokens` in
`tests/test_quality_grader.py`) that pass plain `MagicMock` /
list literals and assert on the return value — no `AsyncMock`,
no `patch("subprocess.Popen")`, no Responses-API stubs. Every
edge case (missing attribute, `0`-vs-`None`, bool guard, mixed
all-None / mixed-non-None / all-int chains) is one inline
assertion away.

The orchestrator-side wiring is the canonical thin shape:
`grader.py::extract_and_grade` and `quality_grader.py::grade_quality`
each collect per-attempt `ModelResult.reasoning_tokens` into a
list (`reasoning_attempts.append(api_result.reasoning_tokens)`),
then hand the list to `_sum_optional_reasoning_tokens(...)` for
the final report field. None of the verdict logic, coercion
discipline, or aggregation semantics live inside the
orchestrator; both helpers stay at the boundary.

Why the split mattered specifically here:

- **One bool-guard, one place per provider**: had the bool guard
  been inlined at every `ModelResult` construction site (one per
  provider × one per code path × one per retry branch), it
  would have drifted within weeks. Centralizing it inside
  `_extract_reasoning_tokens` means a future SDK shape change
  edits one helper, and every call path inherits the fix.
- **The `0`-vs-`None` distinction survives all aggregation
  layers**: the per-call extractor preserves `0`, and the
  chain-level aggregator preserves the all-None→None semantic.
  Together they thread an honest signal from the SDK boundary
  through to `IterationContext.reasoning_tokens` without any
  layer collapsing the distinction. A future audit/trend
  consumer can compare "no reasoning-capable call ran" against
  "reasoning-capable calls ran but model used zero tokens"
  cleanly.
- **Symmetric reader-side bool-guard in
  `from_json`**: the same defensive pattern applies on the
  on-disk read side. Both `GradingReport.from_json` and
  `ExtractionReport.from_json` carry a parallel
  `if raw_reasoning is None or isinstance(raw_reasoning, bool):
  reasoning_tokens = None` branch per the
  `.claude/rules/json-schema-version.md` "Schema version bumps
  for #170" subsection. The on-disk JSON boundary is a
  serialize-then-parse roundtrip; a malformed sidecar with
  `"reasoning_tokens": true` would otherwise coerce to `1` on
  the way back in. Symmetric guards on both sides of the I/O
  boundary keep the discipline structural.
- **Future nullable-token-style fields plug in trivially**:
  `#169` (`cost_usd: float | None`) shipped using the same shape —
  per-provider defensive extractor (with a parallel `bool`/`int`
  guard, since Python's `bool` is a subclass of `int` — NOT of
  `float` — and `True`/`False` therefore pass any broad numeric
  check like `isinstance(x, (int, float))` without an explicit
  bool guard ahead of the int/float check), chain-level
  `_sum_optional_cost_usd` aggregator with the same
  all-None-stays-None semantic. The Ninth anchor (pricing-table
  cost estimation) above documents how `#169` applied this
  recipe; this Tenth anchor codifies the recipe itself so future
  nullable-token fields inherit it without rediscovering.

Traces to DEC-001, DEC-002, DEC-003, DEC-006 of
`plans/super/170-reasoning-tokens-capture.md`. Companion rules:
`.claude/rules/centralized-sdk-call.md` (the asymmetric per-
provider population that `_extract_reasoning_tokens` lives
inside),
`.claude/rules/json-schema-version.md` (the schema-bump and
loader-side-bool-guard contract that mirrors the writer-side
discipline this anchor codifies),
`.claude/rules/constant-with-type-info.md` (the bool-vs-int
discipline this helper enforces at the SDK boundary).

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
