# Super Plan: clauditor-0bo — Expose `blind_compare` via pytest plugin

## Meta
- **Ticket:** `clauditor-0bo` (beads) — #24 follow-up
- **Branch:** `feature/pytest-blind-compare`
- **Worktree:** `worktrees/clauditor/feature/pytest-blind-compare`
- **Phase:** `implemented`
- **PR:** https://github.com/wjduenow/clauditor/pull/38
- **Priority:** P3
- **Sessions:** 1
- **Last session:** 2026-04-15

---

## Discovery

### Ticket Summary

**What:** Add a pytest fixture in `src/clauditor/pytest_plugin.py` that exposes
`blind_compare` (the blind A/B judge from #24) so eval tests can invoke it
inline on captured skill outputs. The fixture should use the same spec /
user_prompt resolution semantics as the existing CLI path (`clauditor compare
--blind`).

**Why:** Today the blind judge is reachable **only** from the CLI. Test
authors who want to assert "version B is preferred over version A" on
captured outputs have to shell out to the CLI or duplicate the CLI's
resolution logic in their conftest. A first-class fixture closes that
gap in ~80 LOC.

**Scope (narrow, P3):** 1 new fixture + 1 shared helper to avoid
duplicating the CLI's resolution logic + tests. No new JSON sidecars, no
new LLM prompts, no schema changes. The sibling bead `clauditor-iag`
(add `EvalSpec.user_prompt`) is **explicitly out of scope** — the fixture
inherits whatever resolution the CLI does today (read `test_args`) and
will pick up the future `user_prompt` field automatically when `iag`
lands.

### Codebase Findings

**`blind_compare` signature** (`src/clauditor/quality_grader.py:289-490`):
```python
async def blind_compare(
    user_prompt: str,
    output_a: str,
    output_b: str,
    rubric_hint: str | None = None,
    *,
    model: str = DEFAULT_GRADING_MODEL,
    rng: random.Random | None = None,
) -> BlindReport:
```
Pure string-in, `BlindReport` out. No file I/O, no SkillSpec awareness —
callers resolve everything and hand in pre-loaded strings. `BlindReport`
is defined at `quality_grader.py:170-187` with fields `preference`,
`confidence`, `score_a`, `score_b`, `reasoning`, `model`,
`position_agreement`, `input_tokens`, `output_tokens`,
`duration_seconds`.

**Prompt-injection hardening** (`quality_grader.py:207-257`):
`build_blind_prompt` already wraps untrusted outputs in XML-like fences
per `.claude/rules/llm-judge-prompt-injection.md`. The fixture reuses it
unchanged; nothing new to harden.

**`_monotonic` indirection** (`quality_grader.py:27`) is in place and
used at lines 348, 373, 377, 439, 477 per
`.claude/rules/monotonic-time-indirection.md`. Do not break it.

**CLI call site** (`src/clauditor/cli.py:1298-1379`,
`_run_blind_compare`). This is the canonical resolution path and is
load-bearing for what "same semantics" means:

- **user_prompt** is sourced from `skill_spec.eval_spec.test_args or ""`
  at `cli.py:1320`. There is no separate `--user-prompt` CLI flag today.
- **rubric_hint** is built from `grading_criteria` by joining criterion
  text with newlines at `cli.py:1329-1335`, passed as the `rubric_hint`
  param.
- **Call:** `asyncio.run(blind_compare(user_prompt, output_a, output_b,
  rubric_hint, model=model))` at `cli.py:1369-1377`.

The fixture must replicate this resolution byte-for-byte or extract a
shared helper and call it from both sites (recommended — see DEC-001
below).

**Existing plugin fixtures** (`src/clauditor/pytest_plugin.py`):
- `clauditor_runner` (line 86) — `SkillRunner` instance
- `clauditor_spec` (line 96) — factory `(skill_path, eval_path=None) -> SkillSpec`
- `clauditor_grader` (line 139) — **exact template for this bead**. Factory
  signature: `_factory(skill_path, eval_path=None, output=None) -> GradingReport`.
  Wraps `asyncio.run()` to present a sync surface. Lines 147-160 are the
  pattern to mirror.
- `clauditor_capture` (line 164) — factory returning paths to captured outputs
- `clauditor_triggers` (line 192) — factory for trigger testing

**Reserved fixture names** (`tests/conftest.py:5`): plugin-owned fixtures
that tests must not shadow. The new fixture name must be added to this
reserved list.

**Async pattern** (`pyproject.toml:62` — `asyncio_mode = "strict"`): plugin
fixtures present a sync surface by calling `asyncio.run(...)` internally.
The test function itself does NOT need `@pytest.mark.asyncio` to use the
new fixture. Existing async blind-compare tests
(`tests/test_quality_grader.py:1332+`) use `@pytest.mark.asyncio` and
`await blind_compare(...)` directly — those are for unit-testing
`blind_compare` itself, not for the new fixture.

**Test shape precedent** (`tests/test_pytest_plugin.py`): the existing
plugin tests patch `anthropic.AsyncAnthropic` with a mock client, mock
the Anthropic response content, and assert on the returned dataclass.
The new fixture tests follow the same shape.

**`EvalSpec.user_prompt`**: NOT present today. Grep on `schemas.py:128-150`
and `spec.py` confirms. The sibling bead `clauditor-iag` will add it
later. Resolution today is via `EvalSpec.test_args` (`schemas.py:136`).

### Rules Scan (applies to this feature)

- **monotonic-time-indirection** — MAYBE. `blind_compare` already uses
  the `_monotonic` alias. The new helper must not introduce a direct
  `time.monotonic()` call.
- **mock-side-effect-for-distinct-calls** — MAYBE. Fixture tests that
  exercise multiple comparison calls in one test function should use
  `side_effect=[...]` not `return_value`, per the rule added in #28
  US-007.
- **pure-compute-vs-io-split** — APPLIES. The new shared helper is a
  pure function (takes spec + two strings, returns a `BlindReport`);
  the CLI and the fixture are its only callers. Exactly the shape the
  rule describes.
- **llm-judge-prompt-injection** — N/A. Reuses `build_blind_prompt`
  unchanged.
- All other rules: N/A (no new JSON sidecars, no new subprocess
  wrappers, no new path validation, no schema changes).

No `plans/super/workflow-project.md`.

### Proposed Scope

Three options:

- **(A) Direct fixture** — New `clauditor_blind_compare` fixture that
  loads spec, extracts `test_args` + `grading_criteria` inline, calls
  `blind_compare` via `asyncio.run()`. Duplicates ~15 lines of CLI
  resolution logic. Fastest (~30 LOC) but introduces drift risk: if the
  CLI later changes resolution (e.g. `iag` adds `user_prompt`), the
  fixture must be kept in lockstep.
- **(B) Extract shared helper + new fixture** *(my rec)* — Extract a
  new `blind_compare_from_spec(spec, output_a, output_b, *, model=...)`
  function in `src/clauditor/quality_grader.py`. `_run_blind_compare`
  in `cli.py` becomes a thin wrapper over it; the fixture is another
  thin wrapper. Both call sites pick up future `user_prompt` field
  changes for free.
- **(C) Refactor `_run_blind_compare`** — Move resolution logic into
  the new helper and delete the duplication from cli.py. Same outcome
  as (B), but requires touching cli.py's existing function more
  aggressively.

(B) and (C) are almost identical. (B) leaves cli.py's `_run_blind_compare`
mostly intact as a backwards-compatible wrapper. (C) rewrites it. Either
way the fixture is ~30 LOC and the helper is ~40 LOC.

**Out of scope:**

- Adding `EvalSpec.user_prompt` (that's `clauditor-iag`).
- Exposing `grade_quality` or `compute_benchmark` as fixtures (separate
  follow-ups if anyone asks).
- Exposing the assertions-based diff (`diff_assertion_sets`) as a
  fixture (not requested, different use case).
- A CLI flag to print a single-file blind judgment (this ticket is
  strictly about the pytest surface).

### Scoping Questions

**Q1 — Shared helper vs direct fixture?**
- (A) Direct fixture, duplicated resolution (~30 LOC total)
- (B) New `blind_compare_from_spec` helper, CLI becomes thin wrapper,
  fixture calls helper *(my rec — matches `pure-compute-vs-io-split`
  rule)*
- (C) Aggressive refactor of `_run_blind_compare` to delete the
  duplication entirely

**Q2 — What does the fixture's factory signature look like?**
- (A) `clauditor_blind_compare(skill_path, output_a, output_b,
  eval_path=None, *, model=None) -> BlindReport` — caller provides the
  two output strings directly *(my rec — flexible, mirrors
  `clauditor_grader`)*
- (B) `clauditor_blind_compare(skill_path, before_path, after_path,
  eval_path=None) -> BlindReport` — caller provides file paths and the
  fixture reads them (mirrors the CLI)
- (C) Both: overload to accept strings OR paths (complex, unclear
  dispatch)

**Q3 — Should the fixture accept overrides beyond the spec?**
- (A) No — always resolve from spec. Tests that need a different user
  prompt or rubric mock `blind_compare` directly with
  `@patch`. *(my rec — keeps the fixture simple, matches
  `clauditor_grader`)*
- (B) Yes — expose `user_prompt_override` and `rubric_override` kwargs
  for tests that want to vary inputs without mocking

**Q4 — Where do the fixture tests live?**
- (A) `tests/test_pytest_plugin.py` (alongside existing plugin fixture
  tests) *(my rec)*
- (B) New `tests/test_pytest_plugin_blind_compare.py`

**Q5 — Does `_run_blind_compare` in cli.py get refactored, or left
alone as-is with duplicated resolution?**
- (A) **Refactor it to call the new shared helper** so CLI and fixture
  share one code path *(my rec)*
- (B) Leave it alone; both sites resolve independently and we accept
  the drift risk

---

## Architecture Review

### Ratings (collapsed — narrow P3 scope, ~80 LOC)

| Area | Rating | Headline |
|---|---|---|
| Security | pass | No new untrusted input path; `build_blind_prompt`'s XML-fence hardening is reused unchanged |
| Performance | pass | Fixture does one `blind_compare` call per test invocation (same as CLI); no N+1 or unbounded loops |
| Data model | pass | No new fields, no schema changes; consumes existing `EvalSpec.test_args` + `grading_criteria` + `grading_model` |
| API design | pass | `blind_compare_from_spec(spec, output_a, output_b, *, model=None, rng=None) -> BlindReport` matches `pure-compute-vs-io-split` rule |
| Observability | concern | CLI prints a `"Running blind A/B judge..."` progress line to stderr. Should the fixture echo it? Answer below. |
| Testing strategy | pass | `clauditor_grader` is the exact template; existing async-blind tests in `test_quality_grader.py:1332+` show the mocking pattern |
| Rule compliance | pass | `pure-compute-vs-io-split` APPLIES (helper is pure; CLI + fixture are thin wrappers); `monotonic-time-indirection` preserved; `mock-side-effect-for-distinct-calls` noted for test authoring |

### Decisions (Phase 3)

- **DEC-001 — Extract `blind_compare_from_spec(spec, output_a, output_b, *, model=None, rng=None) -> BlindReport`** in
  `src/clauditor/quality_grader.py`, alongside the existing
  `blind_compare`. The helper:
  1. Raises `ValueError` if `spec.eval_spec is None`.
  2. Raises `ValueError` if `spec.eval_spec.test_args` is empty or
     whitespace-only (mirrors the CLI's exit-2 check at `cli.py:1321-1327`).
  3. Extracts `user_prompt = spec.eval_spec.test_args`.
  4. Builds `rubric_hint` from `spec.eval_spec.grading_criteria` using
     the exact same `"\n".join(f"- {criterion_text(c)}" for c in criteria)`
     pattern as `cli.py:1333-1335`. `None` when no criteria.
  5. Resolves `effective_model = model or spec.eval_spec.grading_model`.
     (Allow the caller to override the spec's model — the fixture may
     want a different model for a given test.)
  6. Awaits `blind_compare(user_prompt, output_a, output_b, rubric_hint,
     model=effective_model, rng=rng)`.
  7. Returns the `BlindReport`.

  This is the authoritative resolution path. The CLI and the fixture
  are both thin wrappers over it. Future `EvalSpec.user_prompt` work
  (sibling bead `clauditor-iag`) will flow through this one function
  and both callers pick it up automatically.

- **DEC-002 — Refactor `cli.py::_run_blind_compare` to call the new
  helper** (Q5=A). The CLI keeps file existence/UTF-8 validation and
  the stderr progress print and the `_print_blind_report` call; the
  inner spec/test_args/rubric/model resolution is deleted and replaced
  with a single `asyncio.run(blind_compare_from_spec(...))`. `ValueError`
  from the helper maps to `exit 2 + stderr ERROR`. Preserves every
  existing exit-code path in the CLI's blind-compare flow.

- **DEC-003 — New fixture named `clauditor_blind_compare`** in
  `src/clauditor/pytest_plugin.py`. Factory signature (Q2=A):
  ```python
  def _factory(
      skill_path: str | Path,
      output_a: str,
      output_b: str,
      eval_path: str | Path | None = None,
      *,
      model: str | None = None,
  ) -> BlindReport: ...
  ```
  Inside: loads spec via `SkillSpec.from_file(str(skill_path),
  eval_path=eval_path)`, calls
  `asyncio.run(blind_compare_from_spec(spec, output_a, output_b,
  model=model))`, returns the `BlindReport`. No override kwargs beyond
  the spec (Q3=A). Caller passes strings directly — the fixture does
  NOT read files on the caller's behalf.

- **DEC-004 — Fixture name added to reserved list** in
  `tests/conftest.py:5`. Tests must not shadow `clauditor_blind_compare`.

- **DEC-005 — Fixture tests live in `tests/test_pytest_plugin.py`**
  (Q4=A) alongside the existing plugin fixture tests. Match the
  existing `TestClauditorGrader`-style class naming.

- **DEC-006 — Fixture does NOT echo the CLI's progress line to
  stderr.** The CLI's `"Running blind A/B judge..."` message is
  informational for interactive use; in tests it would pollute pytest
  output. The helper stays silent; only the CLI wrapper prints. The
  Observability concern resolves here.

### Blockers

None.

---

## Refinement Log

Decisions captured above in the Architecture Review section (DEC-001
through DEC-006). All scoping questions resolved with the user's
"B, A, A, A, A" choice.

### Session notes

- Scope is narrow and mechanical — the fix is mostly "move 15 lines
  from `cli.py:1312-1335` into a new helper in `quality_grader.py`"
  plus "add a ~20-line factory fixture that calls it".
- The parent #24 plan (`plans/super/24-blind-ab-judge.md`) is worth
  consulting for `blind_compare`'s position-swap randomization +
  `rng` parameter semantics.
- Existing CLI tests at `tests/test_cli.py:2923-3087` mock
  `clauditor.quality_grader.blind_compare` directly. After the
  refactor these still work because `blind_compare_from_spec` calls
  `blind_compare` under the hood — the mock target stays valid.

---

## Detailed Breakdown

### US-001 — Add `blind_compare_from_spec` helper

**Description:** New pure async helper in
`src/clauditor/quality_grader.py` that takes a `SkillSpec` and two
pre-loaded output strings, resolves `user_prompt` / `rubric_hint` /
`model` from the spec exactly as the CLI does today, and awaits
`blind_compare`. Pure logic — no file I/O, no stdout/stderr prints.

**Traces to:** DEC-001, DEC-006.

**TDD — write these in `tests/test_quality_grader.py` first** (match
the existing `TestBlindCompare` class style):

1. **Happy path** — build a `SkillSpec` with `test_args="What's the
   best sushi in Tokyo?"`, `grading_criteria=[c1, c2]`, and a stubbed
   `grading_model`. Mock `blind_compare` with `AsyncMock` and capture
   its call kwargs. Call `await blind_compare_from_spec(spec, "A",
   "B")`. Assert the mock was called with `user_prompt=<test_args>`,
   `output_a="A"`, `output_b="B"`, `rubric_hint` matching the exact
   `"- "`-prefixed join from `cli.py:1333-1335`, `model=<spec
   grading_model>`.
2. **`model` override** — same setup but call `blind_compare_from_spec(spec,
   "A", "B", model="claude-opus-4-6")`. Assert the mock saw
   `model="claude-opus-4-6"`, not the spec's model.
3. **No `eval_spec`** — build a `SkillSpec` with `eval_spec=None`.
   Assert `ValueError` with message naming the missing eval spec.
4. **Empty `test_args`** — `test_args=""`. Assert `ValueError` with
   message mentioning `test_args`.
5. **Whitespace-only `test_args`** — `test_args="   "`. Assert
   `ValueError` (mirrors CLI's `.strip()` check).
6. **No `grading_criteria`** — criteria list is empty. Assert the mock
   saw `rubric_hint=None`, not `""` or an empty join.
7. **`rng` pass-through** — pass `rng=random.Random(42)`, assert the
   mock saw that same object.

**Acceptance criteria:**
- `blind_compare_from_spec` exists in `src/clauditor/quality_grader.py`
  with the signature `(spec: SkillSpec, output_a: str, output_b: str,
  *, model: str | None = None, rng: random.Random | None = None) -> BlindReport`.
- Raises `ValueError` on missing `eval_spec`, empty `test_args`.
- Rubric hint computation matches `cli.py:1333-1335` byte-for-byte
  (uses `schemas.criterion_text`).
- Model resolution: explicit `model` kwarg wins; else
  `spec.eval_spec.grading_model`.
- All 7 TDD cases pass.
- `uv run ruff check src/ tests/` clean.

**Done when:** Tests green; helper importable; no caller has been
updated yet.

**Files:**
- MODIFY `src/clauditor/quality_grader.py` — add helper + import
- MODIFY `tests/test_quality_grader.py` — add `TestBlindCompareFromSpec` class

**Depends on:** none.

---

### US-002 — Refactor `_run_blind_compare` to use the helper

**Description:** In `src/clauditor/cli.py::_run_blind_compare`
(`cli.py:1298-1379`), delete the inline spec/test_args/rubric/model
resolution (lines 1312-1335 + 1364) and replace with a single
`asyncio.run(blind_compare_from_spec(...))` call. Keep everything else
(file existence check, UTF-8 decoding, stderr progress print,
`_print_blind_report`, exit-code mapping). Map `ValueError` from the
helper to `exit 2 + ERROR:` stderr.

**Traces to:** DEC-002.

**Acceptance criteria:**
- `_run_blind_compare` no longer reads `test_args`, builds
  `rubric_hint`, or reads `grading_model` directly — all of that
  happens inside the helper.
- `SkillSpec.from_file` load still happens in `_run_blind_compare`
  (the fixture loads its own spec separately).
- File existence + UTF-8 decoding error paths preserved exactly
  (still exit 2 with same stderr text).
- `ValueError` from the helper is caught and mapped to `exit 2` +
  stderr `ERROR: <ValueError message>`.
- Stderr progress print (`Running blind A/B judge ({model})...`)
  still emitted from the CLI wrapper, NOT the helper (DEC-006). The
  model value is read from `spec.eval_spec.grading_model` for the
  print line (the helper resolves it again internally — a tiny
  duplication, acceptable because the CLI wants the printed model
  and the helper wants the effective model).
- All existing `tests/test_cli.py::TestCompareBlind` (or wherever
  blind-compare CLI tests live, `test_cli.py:2923-3087`) tests still
  pass unchanged — they mock `clauditor.quality_grader.blind_compare`
  which is still called from the helper's body.
- Full gate clean.

**Done when:** `git diff cli.py` shows a net-negative line count in
`_run_blind_compare` with one new `asyncio.run(blind_compare_from_spec(...))`
call; all existing CLI tests still pass.

**Files:**
- MODIFY `src/clauditor/cli.py` — `_run_blind_compare` body

**Depends on:** US-001.

---

### US-003 — Add `clauditor_blind_compare` pytest fixture

**Description:** New factory fixture in
`src/clauditor/pytest_plugin.py`. Sync surface that wraps
`asyncio.run(blind_compare_from_spec(...))`. Loads spec via
`SkillSpec.from_file(str(skill_path), eval_path=eval_path)`. Caller
provides the two output strings directly.

**Traces to:** DEC-003, DEC-004, DEC-005.

**TDD — write these in `tests/test_pytest_plugin.py` first** (new
`TestClauditorBlindCompare` class):

1. **Happy path** — write a minimal skill + eval spec fixture with
   `test_args` and `grading_criteria` populated. Mock
   `clauditor.quality_grader.blind_compare` via `AsyncMock` returning
   a canned `BlindReport` with `preference="a"`, `confidence=0.8`.
   Call `clauditor_blind_compare(skill_path, "output A", "output B")`.
   Assert the returned `BlindReport` equals the canned one. Assert
   the mock was called once with `output_a="output A"`,
   `output_b="output B"`, rubric matching the spec's criteria.
2. **`eval_path` override** — supply a separate `eval_path` arg.
   Assert `SkillSpec.from_file` was called with that path.
3. **`model` override** — pass `model="claude-opus-4-6"`. Assert the
   mock saw that model.
4. **Missing `test_args` propagates `ValueError`** — build a spec
   with `test_args=""`. Assert `pytest.raises(ValueError)` when the
   factory is called.
5. **Reserved fixture name** — assert the new fixture name appears in
   `tests/conftest.py`'s reserved-names comment/list (if that's
   enforced structurally somewhere, otherwise this is a doc-level
   check only).

**Acceptance criteria:**
- Fixture `clauditor_blind_compare` exists in
  `src/clauditor/pytest_plugin.py`, pattern matches
  `clauditor_grader` at `pytest_plugin.py:139-160`.
- Fixture name added to the reserved list in `tests/conftest.py:5`.
- Factory signature:
  `(skill_path: str | Path, output_a: str, output_b: str, eval_path: str | Path | None = None, *, model: str | None = None) -> BlindReport`.
- Sync — caller does NOT need `@pytest.mark.asyncio` on the test
  function.
- All 5 TDD cases pass.
- Existing plugin fixtures still work unchanged; `pytest` full run
  green.
- Full gate clean.

**Done when:** `clauditor_blind_compare` is discoverable by pytest
and a new test uses it successfully to compare two strings.

**Files:**
- MODIFY `src/clauditor/pytest_plugin.py` — add fixture
- MODIFY `tests/conftest.py` — add to reserved list
- MODIFY `tests/test_pytest_plugin.py` — add `TestClauditorBlindCompare`

**Depends on:** US-001 (the helper must exist before the fixture can
call it). US-002 is independent and can land in either order, but
US-002 → US-003 is the cleanest sequence.

---

### US-004 — Quality Gate

**Description:** Run the code-reviewer subagent 4× across the
changeset, fix all real bugs each pass. Run CodeRabbit local review.
Run CodeRabbit via PR comment if/after the PR opens. Validate full
gate.

**Acceptance criteria:**
- 4 code-reviewer passes; each pass's findings fixed or documented as
  false positives.
- CodeRabbit local review clean (or findings addressed).
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80% coverage gate.
- Manual smoke test: write a tiny eval test that uses the new fixture,
  run it, verify the `BlindReport` shape.

**Depends on:** US-003.

---

### US-005 — Patterns & Memory

**Description:** Harvest anything new learned. Candidate: the
"refactor CLI inline resolution into a shared pure helper that both
CLI and plugin fixture call" pattern is already covered by
`.claude/rules/pure-compute-vs-io-split.md` (added in #28 US-007).
This ticket is a **canonical application** of that rule, so the rule
may benefit from a second `file:line` anchor pointing at
`blind_compare_from_spec` as another example.

**Acceptance criteria:**
- If the refactor produced a cleaner shape than the rule currently
  describes, update `.claude/rules/pure-compute-vs-io-split.md` with
  an additional anchor.
- Plan doc Meta.Phase → `implemented`; PR link filled in.
- Any surprising insight stored via `bd remember`.

**Depends on:** US-004.

---

### Estimate summary

| Story | LOC (approx) | Risk |
|---|---|---|
| US-001 helper + 7 tests | ~130 | low (pure logic) |
| US-002 refactor CLI | ~-20 net | low (existing tests guard the behavior) |
| US-003 fixture + 5 tests | ~100 | low |
| US-004 Quality Gate | 0 code | low |
| US-005 Patterns & Memory | ~20 | low |

**Total:** ~+230 LOC / ~20 deleted / net ~+210.

---

## Beads Manifest

- **Epic:** `clauditor-5x5` (all children closed)
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/pytest-blind-compare`
- **PR:** https://github.com/wjduenow/clauditor/pull/38

| Task | Title | Commit |
|---|---|---|
| `clauditor-5x5.1` | US-001: `blind_compare_from_spec` helper + 7 TDD tests | `4c3f5a8` |
| `clauditor-5x5.2` | US-002: refactor `_run_blind_compare` to call helper | `81c527e` |
| `clauditor-5x5.3` | US-003: `clauditor_blind_compare` pytest fixture | `3db4a65` |
| `clauditor-5x5.4` | Quality Gate: code review + CodeRabbit | `dc96c17`, `925fa63` |
| `clauditor-5x5.5` | Patterns & Memory | (this commit) |

## Session notes

- **Quality Gate segfault incident.** During the QG fix commit
  (`dc96c17`) a new integration test
  `test_clauditor_blind_compare_via_pytester_injection` was added to
  guard the fixture's pytest wiring via
  `pytester.runpytest_inprocess`. The inner test used
  `mock.patch("clauditor.quality_grader.blind_compare", ...)`. Under
  `pytest --cov=clauditor`, that combination triggered
  order-dependent segfaults in unrelated test files (argparse init,
  `unittest.mock` entry, `pkgutil.resolve_name`). The test was
  removed in commit `925fa63` after confirming the existing 5
  `__wrapped__`-based tests cover the factory body adequately. The
  lesson is codified in
  `.claude/rules/pytester-inprocess-coverage-hazard.md`.
- **`pure-compute-vs-io-split` second anchor.** This epic is the
  second canonical application of the rule (first was
  `compute_benchmark` in #28 US-007). The rule was augmented with a
  new "Second anchor" section citing `blind_compare_from_spec` +
  its two callers (`cli.py::_run_blind_compare` and
  `pytest_plugin.py::clauditor_blind_compare`) to demonstrate the
  "same pure function, dissimilar callers" compose-ability benefit
  that `compute_benchmark` alone did not surface.
- **Final numbers:** 1137 passing, 96.03% total coverage, ruff clean
  on `src/` and `tests/`.
