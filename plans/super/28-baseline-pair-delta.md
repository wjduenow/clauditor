# Super Plan: #28 — Baseline pair runs (delta block)

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/28
- **Branch:** `feature/28-baseline-pair`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/28-baseline-pair`
- **Phase:** `detailing`
- **PR:** (not yet opened)
- **Sessions:** 1
- **Last session:** 2026-04-15

---

## Discovery

### Ticket Summary

**What:** Re-expose "with-skill vs without-skill" baseline pair runs as a
first-class CLI workflow that emits the agentskills.io `delta` block
(`{pass_rate, time_seconds, tokens}`) and gives CI a knob to fail the run
when the skill does not beat raw Claude by a configurable margin.

**Why:** agentskills.io treats pair runs as the *core* eval pattern. The
previous `cmd_grade --compare` flag was removed in #18 (DEC-012) in favor
of a file-diff `clauditor compare` subcommand, and `compare_ab()` was
orphaned as a library function. At the same time, `_run_baseline_phase`
was added to `clauditor grade` under a `--baseline` flag that already
executes the second (without-skill) arm and writes four sidecars. What
is still missing is the **delta block** — the artifact agentskills.io
actually recommends — and the CI exit-code semantics.

**Done when:** `clauditor grade <skill> --baseline` runs with- and
without-skill, writes a `benchmark.json` sidecar inside the iteration
workspace using the literal agentskills.io field names, prints a
human-readable delta block on stdout, and exits non-zero when a
configurable `--min-baseline-delta` is violated.

### Key Finding (Reshapes the Ticket)

The ticket's "Option A / B / C" framing predates the delivery of #21 and
#22. Both have landed, and — crucially — **`clauditor grade --baseline`
has *already* been wired** via `_run_baseline_phase`
(`src/clauditor/cli.py:525-616`, registered at `cli.py:2240`, dispatched
at `cli.py:877-880`). It currently writes:

- `baseline.json` — `{output, exit_code, input_tokens, output_tokens, duration_seconds}`
- `baseline_assertions.json` — L1 against baseline output
- `baseline_extraction.json` — L2 (only if spec has sections)
- `baseline_grading.json` — L3 `GradingReport`

All are written to `workspace.tmp_path` before `workspace.finalize()`,
honoring the sidecar-during-staging rule. The pair *runs* and its
*sidecars* are done. **The ticket collapses to: compute, persist, print,
and gate on a delta block derived from sidecars that already exist.**

### Codebase Findings

**Pair runs already wired.** `_run_baseline_phase` at
`src/clauditor/cli.py:525-616` is called from
`_cmd_grade_with_workspace` at `cli.py:877-880` behind
`if getattr(args, "baseline", False)`. The flag is registered at
`cli.py:2240-2247`. `spec.runner.run_raw(test_args, cwd=effective_cwd)`
(`runner.py:141-163`) is the skill-less invocation — no skill prefix,
same stream-json plumbing, same `{input_tokens, output_tokens,
duration_seconds}` fields as `run()`. FIX-15 even mirrors input-file
staging into a sibling `baseline-run/` dir so `input_files` specs work.

**Metrics already available to compute the delta.**
- `SkillResult.duration_seconds` / `input_tokens` / `output_tokens`
  (`runner.py:44-48`).
- `GradingReport.duration_seconds` / `input_tokens` / `output_tokens`
  (`quality_grader.py:58-61`).
- `GradingReport.pass_rate` property
  (`quality_grader.py:78-82`): `sum(1 for r in self.results if r.passed) / len(self.results)`.
- `AssertionSet` also has a passed/total shape — the plan needs to pick
  which layer owns `pass_rate` (see DEC-001 below).

**Orphaned library function.** `comparator.compare_ab()`
(`comparator.py:132-196`, tested in `tests/test_comparator.py:285-410`)
still runs skill + `run_raw`, grades both with `grade_quality`, and
returns an `ABReport` with per-criterion regression flags. Since #18 it
has no CLI caller. Its value overlap with the now-existing
`_run_baseline_phase` is ~90%: both run both arms and grade both. The
library function's one unique contribution is the *per-criterion
regression zip*. This is not what agentskills.io asks for (they ask for
an aggregate delta, not a criterion-level regression flag). Decision:
see DEC-002 below.

**`clauditor compare` subcommand** (`cli.py:1250-1330+`): file-pair
diff only — loads two saved outputs or two saved `.grade.json` files
and diffs assertion sets. Does NOT run fresh pairs. Unrelated to #28;
we should not touch it.

**Iteration workspace** (`src/clauditor/workspace.py:65-240`): already
supports the atomic-staging pattern. `iteration-N/<skill>/` is the
target dir; `benchmark.json` as a new sibling sidecar slots in cleanly.

**No existing `benchmark.json`, no existing delta computation, no
existing `--min-baseline-delta` flag on `grade`.** Grep confirmed:
the only `min-baseline-delta` hit is on the unrelated `audit`
subcommand (`cli.py:2476`) and refers to a discrimination metric, not
this feature.

### agentskills.io Canonical Shapes (literal field names)

```json
{
  "schema_version": 1,
  "run_summary": {
    "with_skill":    {"pass_rate": {"mean": 0.83, "stddev": 0.06},
                      "time_seconds": {"mean": 45.0, "stddev": 12.0},
                      "tokens": {"mean": 3800, "stddev": 400}},
    "without_skill": {"pass_rate": {"mean": 0.33, "stddev": 0.10},
                      "time_seconds": {"mean": 32.0, "stddev": 8.0},
                      "tokens": {"mean": 2100, "stddev": 300}},
    "delta": {"pass_rate": 0.50, "time_seconds": 13.0, "tokens": 1700}
  }
}
```

Literal field names: `with_skill`, `without_skill`, `delta`,
`pass_rate`, `time_seconds`, `tokens`, `run_summary`, `mean`, `stddev`.
`time_seconds` is a float (not `duration_ms`). agentskills.io provides
no CI-threshold guidance — Phase 3 must invent exit-code semantics.

### DEC-012 Distinction (what we are NOT reintroducing)

From `plans/super/18-real-world-polish.md`:

> **DEC-012 — `compare` entry point consolidation (C2=c):** Remove
> `grade --compare` entirely. `clauditor compare` becomes the sole
> entry point for A/B and diff comparison workflows.

The removed `--compare` ran skill-A vs skill-B (two skill versions
against each other). #28 runs skill vs *no skill*. Different artifact,
different question. The distinction matters because `--baseline` is
already the right name in the code and does not collide with the
removed flag's surface.

### Rules Compliance Scan (applies to this feature)

Convention Checker returned 11 rules. Applicable:

- **json-schema-version** (`.claude/rules/json-schema-version.md`) —
  APPLIES. `benchmark.json` must have `"schema_version": 1` as the
  first key and any loader must hard-check it.
- **sidecar-during-staging** (`.claude/rules/sidecar-during-staging.md`)
  — APPLIES. `benchmark.json` must be written to `workspace.tmp_path`
  before `workspace.finalize()`, next to the existing baseline sidecars.
- **subprocess-cwd** (`.claude/rules/subprocess-cwd.md`) — MAYBE.
  `run_raw` already supports `cwd=`. No new subprocess wrapper is
  expected. If delta-computation lives in a helper that shells out to
  anything, it must honor this. Likely moot.
- **monotonic-time-indirection**
  (`.claude/rules/monotonic-time-indirection.md`) — MAYBE. `time_seconds`
  values come from existing `GradingReport.duration_seconds` /
  `SkillResult.duration_seconds`, both of which already live in async
  modules with the alias in place. The delta aggregator does not need
  new timing of its own. Likely moot.
- **non-mutating-scrub** (`.claude/rules/non-mutating-scrub.md`) — N/A.
  No new redaction pass; existing redact() already runs on the two
  arms' transcripts.

Not applicable: `eval-spec-stable-ids`, `positional-id-zip-validation`,
`pre-llm-contract-hard-validate`, `llm-judge-prompt-injection`,
`stream-json-schema`, `path-validation`.

No `workflow-project.md` exists in the repo.

### Proposed Scope

Narrow, surgical:

1. Add a `compute_benchmark()` function (likely in a new
   `src/clauditor/benchmark.py` or appended to `comparator.py`) that
   reads the existing `baseline.json` + `baseline_grading.json` +
   primary `grading.json` (plus per-rep `run-K/` data if #22's
   variance reps exist) and returns a dataclass with the literal
   agentskills.io field names plus `schema_version: 1`.
2. Have `_run_baseline_phase` (or its caller) write `benchmark.json`
   into `skill_dir` before `workspace.finalize()`.
3. Print a human-readable delta block on stdout at the end of
   `_cmd_grade_with_workspace` when `--baseline` is set.
4. Add a `--min-baseline-delta` flag on `grade` (float, default off)
   that exits non-zero when `delta.pass_rate <
   min_baseline_delta`. Document that the threshold is on `pass_rate`
   (not tokens or time) because a skill can legitimately be slower or
   token-heavier; the core contract is "the skill helps the model
   pass more criteria."
5. Decide the fate of the orphaned `compare_ab()` library function
   (see DEC-002 below).

Out of scope:

- Touching `clauditor compare` (file-pair diff subcommand).
- A new `clauditor pair` top-level command (Option B from the ticket).
- Changing how `_run_baseline_phase` runs the two arms — it already
  works and this plan builds strictly on top of it.
- Reintroducing any skill-vs-skill comparison (that was DEC-012's
  removed surface).
- Auto-proposing exit-code thresholds for `time_seconds` or `tokens`.

### Open Questions for the User

**Q1 — Which layer owns `pass_rate` in the delta?** (Load-bearing —
all downstream shapes depend on this.)

The feature needs a single number per arm for `pass_rate`. Today there
are three candidates on the `with_skill` side: Layer 1 (assertions
pass rate), Layer 2 (extraction field pass rate), Layer 3
(`GradingReport.pass_rate` — fraction of criteria passed). The
baseline side has the same three. Pick one source of truth:

- **(A)** **L3 only** — `GradingReport.pass_rate` from
  `baseline_grading.json` vs `grading.json`. Pro: one number, matches
  agentskills.io's "quality" framing, works for specs without L1 or
  L2. Con: silent when L1/L2 regress but L3 stays flat.
- **(B)** **L1 + L3 blended** — average the two, or surface both in
  the delta as `pass_rate_assertions` / `pass_rate_quality`. Pro:
  catches regressions agentskills.io's single-number shape would
  miss. Con: deviates from the literal `delta.pass_rate` field name
  the spec uses.
- **(C)** **Weighted blend across all three layers**, with weights
  derived from how many entries each layer contributes. Pro: one
  number, covers all layers. Con: the weighting is opinion and we
  would have to document/defend it.
- **(D)** **User picks at the flag level** —
  `--baseline-metric=l1|l2|l3|all` (default `l3`). Pro: explicit.
  Con: more surface area for a flag most users will never touch.

Recommendation: **(A)**. L3 is the broadest-applicable signal, the
only one that is guaranteed to exist whenever `--baseline` is useful
(no L1 assertions ⇒ no `baseline_assertions.json`; no L2 sections ⇒
no `baseline_extraction.json`), and its field name literally matches
`pass_rate`. L1/L2 deltas can still be exposed as *extra* sidecar
data without being the gating number.

**Q2 — What happens to the orphaned `comparator.compare_ab()` and
its 125 lines of tests?**

- **(A)** **Delete it** — `_run_baseline_phase` has fully absorbed
  the pair-run contract, and its per-criterion regression zip is not
  what agentskills.io calls for. Pro: removes dead code, shrinks
  maintenance surface. Con: loses the `ABReport.regressions`
  per-criterion flagging, which could be useful in the delta block
  later.
- **(B)** **Keep it, reuse it** — have `_run_baseline_phase`
  delegate the dual-run+grade step to `compare_ab()` and build
  `benchmark.json` on top of the returned `ABReport`. Pro: single
  execution path, preserves per-criterion regression signal for
  free. Con: `_run_baseline_phase` is already written and works;
  swapping it for `compare_ab()` is larger than this plan wants.
- **(C)** **Repurpose it as a pure helper** — rename to
  `build_ab_report(skill_grading, baseline_grading)` that takes two
  already-existing `GradingReport` objects and returns the
  per-criterion regression zip. No longer runs anything. Pro:
  preserves the criterion-level signal without duplicating the
  run+grade path. Con: a new function shape, new tests.
- **(D)** **Leave it as-is** — orphaned library function, no CLI,
  no callers. Pro: zero work. Con: strictly worse than (A).

Recommendation: **(A)** — delete. If per-criterion regression flags
turn out to matter later, (C) is a ~20-line follow-up against
`GradingReport`.

**Q3 — What should the stdout delta block look like?**

- **(A)** **Compact one-liner** —
  `delta: pass_rate +0.50, time_seconds +13.0, tokens +1700`.
- **(B)** **Three-line block** — one line per field with mean/stddev
  when variance reps exist.
- **(C)** **Full `run_summary` table** — with_skill / without_skill /
  delta as three rows, one column per metric.

Recommendation: **(C)** when stdout is a TTY, **(A)** for
non-TTY/piped output. Matches how grade output already formats.

**Q4 — Where does `benchmark.json` live?**

- **(A)** **`iteration-N/<skill>/benchmark.json`** — sibling of
  `grading.json`, `baseline.json`, etc. One benchmark per skill per
  iteration.
- **(B)** **`iteration-N/benchmark.json`** — iteration-wide
  aggregate. Matches agentskills.io's literal layout where
  `benchmark.json` sits at the iteration root.
- **(C)** **Both** — per-skill file plus an iteration-wide rollup.

Recommendation: **(A)**. Clauditor's iteration dirs are keyed per
*skill*, not per *eval slug*, so the per-skill home is the natural
fit. The agentskills.io layout assumes one skill per iteration; a
rollup can be a follow-up if the workspace shape ever changes.

**Q5 — Exit-code semantics for `--min-baseline-delta`.**

- **(A)** **Gate on `delta.pass_rate >= threshold`** — if the skill
  beats raw Claude by at least `threshold` fraction of criteria,
  pass; else exit 2. Default off.
- **(B)** **Gate on `delta.pass_rate > 0`** — any positive delta
  passes; no threshold needed. Simpler flag.
- **(C)** **Gate on multiple fields** — fail if `pass_rate` drops OR
  if `tokens` blows past a threshold. Spec punts on thresholds so
  this is opinion.

Recommendation: **(A)** — `--min-baseline-delta FLOAT`, default off
(`None`), applied to `delta.pass_rate`. Simpler than (C), more useful
for CI than (B).

---

## Architecture Review

### Ratings

| Area | Rating | Headline |
|---|---|---|
| Schema-version compliance (`benchmark.json`) | pass | First-key placement + hard loader check fits `.claude/rules/json-schema-version.md` verbatim |
| Sidecar-staging compliance | pass | Correct slot is `cli.py:883–896` (after `_run_baseline_phase`, before `workspace.finalize()`) |
| Variance-reps shape (`stddev`) | concern | Baseline runs exactly once — `without_skill.*.stddev` is better as `null` than `0.0` |
| Baseline single-run vs primary variance reps | concern | Asymmetric: primary may have N reps, baseline always 1; affects `delta` interpretation |
| Field-name collision check | pass | `with_skill` lives as an `IterationRecord` field in `audit.py:85-90` but dict-key vs struct-field is a different namespace |
| Back-compat of `benchmark.json` filename | pass | Grep confirms no existing loader for `benchmark.json` |
| `--min-baseline-delta` flag placement + naming | pass | Insert after `--baseline` at `cli.py:2247`; `audit --min-discrimination` is orthogonal |
| `_unit_float` validator | pass | Right range `[0.0, 1.0]`; edge case `0.0` needs explicit semantics (see concern below) |
| Dependency on `--baseline` | concern | Project convention is runtime validation, not argparse mutually-required — match `compare/blind` pattern at `cli.py:1293-1299` |
| Exit code choice | concern | Exit `2` is currently reserved for input/validation errors in `cmd_grade` (`cli.py:466, 495, 498`); delta-violation is a grading outcome → use `1`, not `2` |
| TTY-aware stdout block | blocker-for-DEC-003 | No existing `isatty` pattern anywhere in repo or tests; DEC-003's "TTY-aware table" would introduce a new cross-cutting pattern |
| Rendering slot in `_cmd_grade_with_workspace` | pass | After `_print_grade_diff()` (~`cli.py:953`), before `history.append_record()` (~`cli.py:967`) |
| `clauditor compare` cross-check | pass | `compare` subparser has no `--min-*` flags (`cli.py:2276-2331`) |
| `_run_baseline_phase` return shape | concern | Currently writes and returns `None`; delta block needs `baseline_grading` in scope → change it to return `baseline_grading` (or capture via tmp file reload) |
| `compare_ab()` deletion scope | pass | Surgical: `compare_ab`, `ABResult`, `ABReport`, `Flip`, `FlipKind` are unique to the pair-run path; also trim `__init__.py` `__all__` / `_lazy_imports` and `tests/test_init.py::test_lazy_import_ab_result`. `diff_assertion_sets` + `AssertionSet` must remain — used by `cli.py:1257, 1369` |
| Existing test patterns | pass | `tests/test_cli.py:1748-1811` (`test_grade_with_baseline_flag_writes_all_baseline_sidecars`) is the template: mock `SkillSpec.from_file`, `grade_quality` via `AsyncMock`, assert `spec.runner.run_raw.assert_called_once()` |
| Exit-code test pattern | pass | `tests/test_audit.py:727-739` — `pytest.raises(SystemExit) as exc; assert exc.value.code == N` |
| Delta-computation unit test home | pass | Matches CLAUDE.md "one test file per source module" — new `src/clauditor/benchmark.py` + `tests/test_benchmark.py` |
| Structured telemetry | pass | Repo has no OT/Prometheus; stdout + sidecar JSON is the whole observability story |

### Concerns to resolve in Refinement (Phase 3)

1. **`without_skill.*.stddev` shape** — `null` (no variance data available) or `0.0` (a single observation has zero sample variance)?
2. **Variance rep symmetry** — should `--baseline` also variance-rep the baseline arm when `--variance=N` is passed on the primary, at roughly 2x baseline LLM cost? Or is an asymmetric (`N` primary, `1` baseline) shape acceptable?
3. **Exit code** — this is the reviewer pushing back on my Phase 1 handwave. Exit `2` is spoken for (input errors). Delta-violation is a grading outcome. Use `1` instead?
4. **`0.0` edge case for `--min-baseline-delta`** — is `--min-baseline-delta 0.0` (a) "any non-negative delta passes, strict no-regression gate" or (b) functionally off? Reviewer recommended treating `0.0` as off; I think (a) is more useful ("don't regress" is a valid CI gate).
5. **DEC-003 walkback — TTY-aware output** — the codebase has *zero* existing TTY detection. DEC-003 would introduce a cross-cutting pattern (patched `sys.stdout.isatty()` in tests, branching in the printer) for one block. Reviewer recommends plain unconditional output.
6. **`_run_baseline_phase` return shape** — today returns `None`. The delta block needs both reports in scope. Cleanest: change signature to `-> GradingReport` (returning `baseline_grading`) so the caller can diff against `primary_report` without reloading from disk.

### Blockers

None — all items above are concerns (resolvable by picking an option) rather than architectural blockers.

---

## Refinement Log

### Decisions (architecture-review round)

- **DEC-006 — `without_skill.*.stddev` is emitted as JSON `null`.**
  Baseline runs exactly once via `spec.runner.run_raw()`; a single
  observation has no meaningful sample stddev. `null` correctly
  signals "no variance data available" to downstream consumers;
  `0.0` would falsely imply observed zero variance.
- **DEC-007 — Asymmetric variance shape: N primary reps, 1 baseline
  run.** The primary arm honors `--variance=N` as today. The
  baseline arm stays single-shot. Rationale: doubling baseline LLM
  cost is not justified for the "sanity-check raw Claude can't
  already do this" framing agentskills.io uses. If users later need
  symmetric variance, add `--baseline-variance=M` as a follow-up.
- **DEC-008 — `delta.pass_rate < min_baseline_delta` exits with
  code 1, not 2.** Exit 2 is already reserved in `cmd_grade` for
  input/validation errors (`cli.py:466, 495, 498`). Delta-violation
  is a grading outcome, matching the existing exit-1 "grading
  failure" semantics. The failure message on stderr must name the
  observed delta and the threshold.
- **DEC-009 — `--min-baseline-delta 0.0` is a strict no-regression
  gate.** Explicit `0.0` means `delta.pass_rate >= 0.0` (the skill
  must be at least as good as raw Claude). Flag-not-passed (`None`)
  means no gate at all. This is the most useful CI semantics:
  "0.0 = don't regress" is a common and reasonable minimum bar.
- **DEC-010 — Walk back DEC-003. The stdout delta block is plain
  unconditional output.** No TTY detection. The codebase has zero
  existing `isatty` usage; introducing a cross-cutting pattern
  (patched `sys.stdout.isatty()` in tests, branching in the
  printer) for a single block is out of proportion to the value.
  Format is a short 4-line block printed after the diff block and
  before `history.append_record()` at ~`cli.py:960`. Example:
  ```
  baseline delta:
    pass_rate    +0.50  (with_skill 0.83, without_skill 0.33)
    time_seconds +13.0  (with_skill 45.0, without_skill 32.0)
    tokens       +1700  (with_skill 3800, without_skill 2100)
  ```
- **DEC-011 — `_run_baseline_phase` return signature changes from
  `None` to `GradingReport`.** Callers need `baseline_grading` in
  scope for delta computation and `benchmark.json` writing. Reload-
  from-disk would work but adds a pointless read. The signature
  change is a one-line caller update at `cli.py:878-883`.

### Session notes

- Pair-runs plumbing is already done (`--baseline` exists and
  writes four sidecars). This ticket is a narrow extension on top,
  not a from-scratch command. Estimated surface: ~300 LOC
  including tests.
- `comparator.compare_ab()` is being removed as part of this
  ticket, not kept around "just in case." The per-criterion
  regression zip it provided is not what agentskills.io asks for.

### Decisions

- **DEC-001 — L3 owns `delta.pass_rate`.** The delta block's
  `pass_rate` field is sourced from `GradingReport.pass_rate` on
  `grading.json` vs `baseline_grading.json`. L1 and L2 results remain
  visible in their own sidecars but do not contribute to the gated
  number. Rationale: broadest-applicable signal; matches the literal
  agentskills.io field name; works for specs without L1 or L2.
- **DEC-002 — Delete `comparator.compare_ab()` and its tests.**
  `_run_baseline_phase` has fully absorbed the dual-run+grade contract.
  The per-criterion regression zip is not what agentskills.io asks for,
  and if it turns out to matter later it is a ~20-line follow-up
  against `GradingReport`. Removing the dead code now is strictly
  simpler than keeping it around "just in case."
- **DEC-003 — Stdout delta block: TTY-aware format.** When stdout is a
  TTY, print a 3-row `run_summary` table (`with_skill` / `without_skill`
  / `delta`) with one column per metric. When stdout is piped/non-TTY,
  print a compact one-liner: `delta: pass_rate +0.50, time_seconds
  +13.0, tokens +1700`. Matches how other grade output already adapts to
  TTY.
- **DEC-004 — `benchmark.json` lives at
  `iteration-N/<skill>/benchmark.json`.** Sibling of `grading.json`,
  `baseline.json`, etc. One benchmark per skill per iteration.
  Clauditor iteration dirs are keyed per-skill, so this is the
  natural home. An iteration-wide rollup is a follow-up if the
  workspace shape ever changes to eval-slug keying.
- **DEC-005 — `--min-baseline-delta FLOAT`, default off, gates on
  `delta.pass_rate`.** When unset, `--baseline` just reports. When set,
  `grade` exits with code 1 (per DEC-008) if `delta.pass_rate < min_baseline_delta`.
  Only `pass_rate` is gated — a skill can legitimately be slower or
  token-heavier; the core contract is "the skill helps the model pass
  more criteria." The failure message must name the observed delta and
  the threshold so CI logs are self-explanatory.

---

## Detailed Breakdown

### Story ordering rationale

Project convention (from CLAUDE.md + past plans): one source module
per file, one test module per source module, failing tests first for
pure logic, thin CLI glue last. The delta computation is pure — it
belongs in its own module with its own test file, unit-tested before
any CLI wiring. Then the sidecar write, then the stdout block, then
the exit-code gate, then the dead-code removal, then Quality Gate
and Patterns & Memory.

---

### US-001 — Create `benchmark` module with pure delta computation

**Description:** New module `src/clauditor/benchmark.py` with a pure
`compute_benchmark()` function that takes the already-computed
primary and baseline `GradingReport` objects plus the primary
`VarianceReport` (or `None` when no variance reps) plus the
baseline `SkillResult`, and returns a `Benchmark` dataclass with the
literal agentskills.io field names. No file I/O, no LLM calls.

**Traces to:** DEC-001, DEC-006, DEC-007, DEC-010.

**TDD — write these tests first in `tests/test_benchmark.py`:**
1. Single-rep primary, single-shot baseline → `with_skill.stddev`
   is `0.0` (single observation), `without_skill.stddev` is `None`.
2. Multi-rep primary (N=3) → `with_skill.pass_rate.mean` is the
   average of the three `GradingReport.pass_rate` values,
   `stddev` is the sample stddev, `without_skill.stddev` is still
   `None`.
3. `delta.pass_rate` equals `with_skill.pass_rate.mean -
   without_skill.pass_rate.mean`; same for `time_seconds`, `tokens`.
4. `schema_version: 1` appears as the first key of `Benchmark.to_json()`.
5. `Benchmark.to_json()` round-trips through `json.loads` without
   losing precision on pass_rate (float), time_seconds (float), or
   tokens (int).
6. Empty `results` in either `GradingReport` → raises a clear
   `ValueError` naming which arm had no criteria.
7. Primary reports list is empty → raises `ValueError`.

**Acceptance Criteria:**
- `src/clauditor/benchmark.py` exists with `compute_benchmark()` +
  `Benchmark` dataclass.
- `schema_version: 1` is the first key in `Benchmark.to_json()`
  (rule: `.claude/rules/json-schema-version.md`).
- A `_check_schema_version()` loader exists in the same module,
  following the canonical pattern in `audit.py::_check_schema_version`.
- `without_skill.*.stddev` is `None` in the Python dataclass and
  `null` in the emitted JSON.
- `tests/test_benchmark.py` covers all seven TDD cases.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest tests/test_benchmark.py -v` passes.

**Done when:** All tests green; module is importable; no other
source file has been touched.

**Files:**
- NEW `src/clauditor/benchmark.py`
- NEW `tests/test_benchmark.py`

**Depends on:** none.

---

### US-002 — Wire `benchmark.json` sidecar into grade workflow

**Description:** Thread `compute_benchmark()` into
`_cmd_grade_with_workspace` so that when `--baseline` is set, a
`benchmark.json` file lands in `skill_dir / "benchmark.json"` inside
the staging workspace, written **before** `workspace.finalize()`.
This story also changes `_run_baseline_phase` to return the
`GradingReport` it currently throws away.

**Traces to:** DEC-004, DEC-011, `.claude/rules/sidecar-during-staging.md`.

**Acceptance Criteria:**
- `_run_baseline_phase` signature becomes
  `-> GradingReport` (instead of `-> None`).
- In `_cmd_grade_with_workspace`, after the `_run_baseline_phase`
  call (~`cli.py:878-883`), the primary variance reports list and
  the returned `baseline_grading` are passed into
  `compute_benchmark()`; the result is written to
  `skill_dir / "benchmark.json"` **before** `workspace.finalize()`
  (per the sidecar-during-staging rule).
- When `--baseline` is NOT set, `benchmark.json` is not written
  and no benchmark computation runs.
- `benchmark.json` passes `json.loads` and contains the literal
  field names `schema_version`, `run_summary`, `with_skill`,
  `without_skill`, `delta`, `pass_rate`, `time_seconds`, `tokens`.
- New test in `tests/test_cli.py` follows the
  `test_grade_with_baseline_flag_writes_all_baseline_sidecars`
  pattern (file:1748-1811): mocks `SkillSpec.from_file` and
  `grade_quality`/`extract_and_report` via `AsyncMock`, verifies
  `benchmark.json` exists, contents parse, and field names match.
- `uv run pytest` passes.

**Done when:** Running `clauditor grade <skill> --baseline` on a
fresh iteration produces `iteration-N/<skill>/benchmark.json`
alongside the existing `baseline_*.json` sidecars, and the test
above asserts its presence and shape.

**Files:**
- MODIFY `src/clauditor/cli.py` — `_run_baseline_phase` return type,
  `_cmd_grade_with_workspace` call site + write slot.
- MODIFY `tests/test_cli.py` — new sidecar presence/shape test.

**Depends on:** US-001.

---

### US-003 — Print delta block on stdout when `--baseline` is set

**Description:** After `workspace.finalize()` (and after
`_print_grade_diff` but before `history.append_record()`, ~`cli.py:960`),
when `--baseline` was passed, print the plain 4-line delta block
(DEC-010 format) to stdout. Unconditional — no TTY branching.

**Traces to:** DEC-003 (walked back by DEC-010), DEC-010.

**Acceptance Criteria:**
- New helper `_print_baseline_delta_block(benchmark: Benchmark,
  out=sys.stdout) -> None` in `cli.py`, styled like the existing
  `_print_grade_diff` helper at `cli.py:1032`.
- Output is the 4-line format from DEC-010. Sign characters (`+`/`-`)
  are explicit for delta rows.
- Called from `_cmd_grade_with_workspace` only when `args.baseline`
  is truthy AND a benchmark was computed.
- Not called when `--baseline` is absent.
- Test in `tests/test_cli.py` uses `capsys` to capture stdout and
  asserts the four literal substrings (`baseline delta:`,
  `pass_rate`, `time_seconds`, `tokens`) appear in order.
- `uv run pytest` passes.

**Done when:** Running `clauditor grade <skill> --baseline` on a
fresh iteration prints the delta block as the last user-facing
line before the exit-code decision.

**Files:**
- MODIFY `src/clauditor/cli.py` — helper + call site.
- MODIFY `tests/test_cli.py` — `capsys`-based output assertion.

**Depends on:** US-002.

---

### US-004 — Add `--min-baseline-delta` flag with exit-code gate

**Description:** Register `--min-baseline-delta FLOAT` on the
`grade` subparser (after `--baseline` at ~`cli.py:2247`), validated
by `_unit_float`. When set and `delta.pass_rate < threshold`,
`_cmd_grade_with_workspace` returns exit code **1** (DEC-008) and
prints a stderr message naming both the observed delta and the
threshold. When `--min-baseline-delta` is set but `--baseline` is
not, the command exits with a clear error at runtime (per DEC-
architecture-concern R5-convention in-code check).

**Traces to:** DEC-005, DEC-008, DEC-009.

**TDD — write these tests first:**
1. `--min-baseline-delta 0.4` with observed `delta.pass_rate=0.50`
   → exit 0 (passes the gate).
2. `--min-baseline-delta 0.4` with observed `delta.pass_rate=0.30`
   → exit 1, stderr contains both `0.30` and `0.40`.
3. `--min-baseline-delta 0.0` with observed `delta.pass_rate=0.0`
   → exit 0 (DEC-009: strict no-regression gate, equality passes).
4. `--min-baseline-delta 0.0` with observed `delta.pass_rate=-0.05`
   → exit 1 (regression blocked).
5. `--min-baseline-delta 0.5` without `--baseline` → exit 2 with
   stderr `--min-baseline-delta requires --baseline` (input error,
   code 2 is correct here because this IS an input error).
6. No `--min-baseline-delta` flag, `--baseline` set, any delta →
   flag not exercised, exit determined by normal grade outcome.
7. `_unit_float` rejects `--min-baseline-delta 1.5` at argparse
   time with exit 2.

**Acceptance Criteria:**
- Flag registered after `--baseline` with `_unit_float` validator.
- Runtime check in `_cmd_grade_with_workspace`: if
  `args.min_baseline_delta is not None and not args.baseline`,
  print stderr error and return 2.
- Gate check: after `compute_benchmark`, if
  `args.min_baseline_delta is not None and benchmark.run_summary.delta.pass_rate < args.min_baseline_delta`,
  print stderr error naming observed delta + threshold, and route
  the command to exit 1.
- All seven TDD cases pass.
- `uv run pytest` passes.

**Done when:** All TDD cases pass and manual run of
`clauditor grade <skill> --baseline --min-baseline-delta 0.99` on
a realistic skill exits 1 with a readable error.

**Files:**
- MODIFY `src/clauditor/cli.py` — flag registration, runtime
  validation, gate check.
- MODIFY `tests/test_cli.py` — seven TDD cases.

**Depends on:** US-003.

---

### US-005 — Delete orphaned `compare_ab()` and its unique types

**Description:** Surgical removal of dead code. `_run_baseline_phase`
now fully subsumes `compare_ab()`'s contract, and the per-criterion
`ABResult.regression` zip is not consumed anywhere.

**Traces to:** DEC-002.

**Delete (exact list from architecture review):**
- `src/clauditor/comparator.py`: `compare_ab()`, `ABResult`,
  `ABReport`, `Flip`, `FlipKind`.
- `src/clauditor/__init__.py`: entries for `ABReport`, `ABResult`
  in `__all__` and `_lazy_imports`.
- `tests/test_comparator.py`: only the `compare_ab`-related tests
  (~lines 285-410 per review).
- `tests/test_init.py`: `test_lazy_import_ab_result` test.

**Preserve (do NOT delete):**
- `diff_assertion_sets()` — used by
  `clauditor compare` subcommand at `cli.py:1257, 1369`.
- `AssertionSet` — used across the codebase.
- All other `tests/test_comparator.py` tests covering
  `diff_assertion_sets`.

**Acceptance Criteria:**
- `rg "compare_ab|ABResult|ABReport|FlipKind" src/ tests/` returns
  zero matches.
- `rg "from .comparator import Flip|from clauditor.comparator import Flip"`
  returns zero matches.
- `clauditor compare` subcommand still works
  (`tests/test_cli.py::test_compare_*` still passes).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80% coverage gate.

**Done when:** All the above, and the diff contains only deletions
in the files named above (no edits anywhere else).

**Files:**
- MODIFY `src/clauditor/comparator.py` (delete unique symbols)
- MODIFY `src/clauditor/__init__.py`
- MODIFY `tests/test_comparator.py`
- MODIFY `tests/test_init.py`

**Depends on:** US-004 (keep deletion last so the rest of the
feature is green before we rip out dead code — easier bisect if
something goes sideways).

---

### US-006 — Quality Gate

**Description:** Run the code-reviewer subagent four times across
the full US-001 through US-005 changeset, fixing every real bug
found in each pass. Run CodeRabbit review on the PR after the code-
reviewer passes are clean. Validate the full test + lint + coverage
gate at the end.

**Acceptance Criteria:**
- Four code-reviewer passes executed; each pass's findings either
  fixed in a commit or documented as a false positive in the
  plan's session notes.
- CodeRabbit review run on the PR; all non-nit findings resolved
  or explicitly dismissed.
- `uv run ruff check src/ tests/` passes clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80% coverage gate (the project gate from CLAUDE.md).
- `benchmark.json` writer/loader roundtrip verified on a real
  grade run end-to-end.

**Done when:** All review passes clean, CI green on the PR.

**Depends on:** US-005.

---

### US-007 — Patterns & Memory

**Description:** Harvest any new conventions that emerged while
implementing this feature and promote them to persistent knowledge.

**Acceptance Criteria:**
- If `compute_benchmark()` introduced a new "pure helper that takes
  already-computed reports, returns a schema-versioned dataclass,
  written by the caller" pattern that differs from the existing
  `_run_baseline_phase`-style "does it all" helper, write a new
  rule under `.claude/rules/` or update an existing one.
- If the delta-block stdout printer pattern would benefit future
  printers, document it (probably as a note in an existing rule,
  not a new rule — one-off printers don't justify their own file).
- Update `plans/super/28-baseline-pair-delta.md` Meta.Phase to
  `implemented` and the PR link.
- `bd remember` any session-level insight that would help a future
  run on a similar ticket.

**Done when:** Conventions updated, plan doc finalized, any new
rules cross-link the canonical implementation file with
`file:line` anchors.

**Depends on:** US-006.

---

### Estimate summary

| Story | LOC (approx) | Risk |
|---|---|---|
| US-001 benchmark module + tests | ~250 | low (pure logic) |
| US-002 sidecar wiring + test | ~80 | low (staging slot is clear) |
| US-003 stdout delta block + test | ~60 | low |
| US-004 flag + gate + 7 tests | ~150 | medium (exit-code semantics) |
| US-005 delete dead code | -300 net | low |
| US-006 Quality Gate | 0 code | medium |
| US-007 Patterns & Memory | ~30 | low |

Total additive LOC: ~500 LOC added, ~300 LOC deleted, net ~+200.

---

## Beads Manifest

*(Phase 7.)*
