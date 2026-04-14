# Super Plan: Issue #24 — Blind A/B Comparison Judge (Layer 3 holistic scoring)

## Meta

| Field         | Value                                      |
|---------------|--------------------------------------------|
| Ticket        | wjduenow/clauditor#24                      |
| Branch        | `feature/24-blind-ab-judge` (created in-place on dev checkout) |
| Worktree      | none — in-place feature branch (DEC-005)   |
| Phase         | detailing                                  |
| Created       | 2026-04-14                                 |
| Sessions      | 1                                          |

---

## Discovery

### Ticket Summary

**What:** Add a blind A/B comparison judge to Layer 3. A new function `quality_grader.blind_compare(output_a, output_b, prompt, rubric_hint)` presents two skill outputs side-by-side to a Sonnet judge *without revealing which is which*, and returns a holistic preference verdict (a / b / tie), confidence, per-output scores, and reasoning. Wired into `clauditor compare` as an optional `--blind` flag.

**Why:** Rubric-only grading (existing `quality_grader.grade_quality`) misses holistic regressions — two outputs can both pass every criterion but one visibly "feels worse." The agentskills.io eval spec calls out blind comparison as complementary to rubric grading. Gap #4 in the alignment analysis.

**Done when:** `clauditor compare before.txt after.txt --spec <skill.md> --blind` prints a preference verdict with reasoning, with position-bias mitigated by running the comparison twice with swapped A/B positions.

**Key constraints from ticket:**
- Randomize A/B position to eliminate position bias; internally track the mapping
- Run twice with swapped positions; check agreement between the two runs
- Return: preference (a/b/tie), confidence, holistic score per output, reasoning

### Codebase Findings

**Layer 3 module landscape** (`src/clauditor/`):
- `quality_grader.py` — `grade_quality()` (line 225) grades ONE output against rubric criteria, returns `GradingReport`. Uses `AsyncAnthropic()` lazily; JSON-mode prompts; `parse_grading_response()` (line 178) handles markdown-wrapped JSON fallback. Token tracking via `response.usage.{input,output}_tokens`. Default model `claude-sonnet-4-6`.
- `comparator.py` — `compare_ab()` (line 132) already does skill-vs-baseline comparison by running `grade_quality()` twice and zipping by criterion index. `ABResult` / `ABReport` dataclasses (lines 86, 96).
- `triggers.py` — parallel Sonnet judge for trigger precision. **Same client pattern** as quality_grader (lazy `AsyncAnthropic()`, JSON response parsing, per-call token usage). No shared abstraction — each module instantiates its own client.
- `cli.py` — `cmd_compare()` (line 706) + `p_compare` parser (line 1343). Accepts positional `before`/`after` (files or iteration dirs) and `--spec`, `--eval`, `--skill`, `--from`, `--to`. Currently diffs assertion sets. `--blind` flag slots in after line 1390; dispatch added to `cmd_compare()`.
- `schemas.py` — **dataclasses only, no pydantic**. `GradeThresholds` (line 77), `VarianceConfig` (line 85), `EvalSpec.from_file()` is the large composite. `BlindReport` should follow `@dataclass` style with `to_json()` serializer.
- `tests/test_quality_grader.py::TestGradeQuality` (line 416+) — canonical mock pattern: `mock_response.content = [MagicMock(text=json.dumps(...))]`, `mock_client.messages.create = AsyncMock(return_value=mock_response)`, `patch("clauditor.quality_grader.AsyncAnthropic", return_value=mock_client)`. New `TestBlindCompare` must match this.

**Relationship to existing `comparator.compare_ab`:** The existing comparator is *skill-vs-baseline rubric regression* (same skill output, grades both). The new blind judge is *holistic side-by-side preference* — a different question. They should coexist, not merge.

**Prior related work:** `plans/super/3-llm-judge-grading.md` (Phase: devolved) is the parent Layer 3 plan. This ticket extends it.

### Convention Constraints

**Rules (`.claude/rules/`):**
- `path-validation.md` — applies only if `--blind` ever accepts path args. **YES** — `clauditor compare before.txt after.txt` already takes path args, so any new path handling in `cmd_compare --blind` path must validate per recipe. (Reuse existing `_load_assertion_set` plumbing — it already handles path resolution.)
- `subprocess-cwd.md` — **does not apply**; blind_compare calls Anthropic API, not subprocess.

**Build / test gate:**
- `uv run ruff check src/ tests/` — lint, `line-length=88`, `target-version=py311`
- `uv run pytest --cov=clauditor --cov-fail-under=80` — **80% coverage enforced**
- Test file: `tests/test_quality_grader.py` — add `TestBlindCompare` class
- Async tests need `@pytest.mark.asyncio` (`asyncio_mode = "strict"`)
- Mock `AsyncAnthropic` via `patch("clauditor.quality_grader.AsyncAnthropic", ...)`
- `tests/conftest.py` fixtures must NOT shadow: `clauditor_runner`, `clauditor_spec`, `clauditor_grader`, `clauditor_triggers`
- Use `tmp_path`, not `tempfile` directly
- Beads is the task tracker — **no TodoWrite, no markdown TODO lists**

**No `workflow-project.md`** — no project-specific scoping/review/chunking overrides.

### Scope (proposed)

**In scope:**
1. New dataclass `BlindReport` in `quality_grader.py` (or new file — see DEC-001 below)
2. New `async def blind_compare(output_a, output_b, prompt, rubric_hint=None, model="claude-sonnet-4-6") -> BlindReport`
3. Position randomization + two-call swap-and-check protocol
4. New prompt builder that presents `<output_1>` / `<output_2>` without revealing which is before/after
5. JSON-mode response parsing (reuse pattern from `parse_grading_response`)
6. CLI: `--blind` flag on `compare` subcommand, dispatched when both inputs are `.txt` files
7. Tests: unit tests with mocked client covering randomization, swap, agreement, tie handling
8. Token/duration tracking in `BlindReport` matching `GradingReport` style

**Out of scope (for this ticket):**
- Integrating blind compare into `compare_ab()` (skill-vs-baseline workflow)
- Variance integration (multi-run blind compare across N reps)
- Exposing via `pytest_plugin.py`
- History/persistence of blind reports to `.clauditor/history.jsonl`

---

## Scoping Questions

1. **Q1 — Module placement.** Where does `blind_compare` live?
   - **A)** New function in existing `quality_grader.py` (co-located with `grade_quality`). Simplest.
   - **B)** New module `src/clauditor/blind_judge.py` (parallel to `triggers.py`). Keeps `quality_grader.py` focused on rubric grading.
   - **C)** New module inside `comparator.py` since it's comparison-shaped. Risks blurring rubric/blind boundaries.

2. **Q2 — Return shape.** The ticket spec says `BlindReport` with `preference`, `confidence`, per-output score, reasoning. The convention scan suggested reusing `GradingReport` — I think that's wrong because the shapes don't match. Confirm:
   - **A)** New `BlindReport` dataclass with fields: `preference: Literal["a","b","tie"]`, `confidence: float`, `score_a: float`, `score_b: float`, `reasoning: str`, `position_agreement: bool`, `model`, `input_tokens`, `output_tokens`, `duration_seconds`, `to_json()`. **Recommended.**
   - **B)** Reuse `GradingReport` by shoehorning blind results into rubric criteria. Loses holistic signal.

3. **Q3 — Swap-and-check disagreement handling.** When the two runs disagree (e.g. run 1 says "A", run 2 with swapped positions says "A" in the new ordering = "B" originally):
   - **A)** Return `preference="tie"` with `confidence` lowered and `position_agreement=False`. User sees "judge was position-biased, verdict unreliable."
   - **B)** Run a third tiebreaker call. More cost.
   - **C)** Return both verdicts as a structured `List[BlindVerdict]` and let caller decide.

4. **Q4 — `rubric_hint` semantics.** The ticket signature has an optional `rubric_hint: str | None`. What does it do?
   - **A)** Free-text injected into the judge prompt as "Pay extra attention to: {hint}". No structured grading.
   - **B)** If provided, the judge scores against hint criteria too and returns per-criterion results alongside the holistic verdict. Larger scope.
   - **C)** Drop it from this ticket; add in a follow-up if users ask.

5. **Q5 — Worktree / branch workflow.** The standard `/super-plan` flow creates a worktree. Looking at the repo, current state is `dev` branch with unrelated uncommitted changes (settings.json, docs/temp/, plans/super/3-llm-judge-grading.md).
   - **A)** Create worktree `../worktrees/clauditor/24-blind-ab-judge` on a new `feature/24-blind-ab-judge` branch from `dev`. Isolates the work, standard pattern.
   - **B)** Work directly on `dev` in the current checkout. Simpler, but mixes with those untracked files.
   - **C)** Create the branch in-place (no worktree) and stash untracked noise.

6. **Q6 — CLI scope for `--blind`.** The ticket says `--blind` applies to `.txt` file pairs. What about iteration-based refs (`--from 3 --to 4`)?
   - **A)** `.txt` pairs only for this ticket; iteration/dir refs return a clear error. Smaller scope, ships faster.
   - **B)** Support both `.txt` pairs AND iteration refs (resolve `clauditor.txt` inside each iteration dir). Matches existing `cmd_compare` shape.

---

## Decisions

### DEC-001: Module placement
**Decision:** Add `blind_compare()` and `BlindReport` to existing `quality_grader.py`.
**Rationale:** Co-located with the other Sonnet judge; avoids a new module for ~150 LOC. Scoping Q1, option A.

### DEC-002: Return shape
**Decision:** New `BlindReport` dataclass — fields: `preference: Literal["a","b","tie"]`, `confidence: float`, `score_a: float`, `score_b: float`, `reasoning: str`, `position_agreement: bool`, `model: str`, `input_tokens: int`, `output_tokens: int`, `duration_seconds: float`. Method `to_json()`.
**Rationale:** Rubric `GradingReport` shape does not match holistic verdict. Ticket spec is explicit. Scoping Q2, option A.

### DEC-003: Swap disagreement handling
**Decision:** When run-1 and run-2 (swapped positions) disagree on the winner, set `preference="tie"`, lower `confidence` (take min of the two), and set `position_agreement=False`. No tiebreaker call.
**Rationale:** Disagreement *is* the signal — the judge is position-biased on this pair. Surfacing it as tie+flag gives the user actionable info without doubling cost. Scoping Q3, option A.

### DEC-004: `rubric_hint` semantics
**Decision:** Free-text prompt injection. If non-None, inject `"Pay extra attention to: {hint}"` into the judge prompt. No structured per-criterion output.
**Rationale:** Matches ticket's optional-hint wording. Structured per-criterion (option B) is a separate feature and out of scope. Scoping Q4, option A.

### DEC-005: Branch / worktree
**Decision:** Feature branch `feature/24-blind-ab-judge` in-place on the existing dev checkout — no worktree. Pre-existing untracked/modified files (`.claude/settings.json`, `docs/temp/`, `plans/super/3-llm-judge-grading.md`) are left untouched.
**Rationale:** Solo project, small surface, no need to isolate. Worktree overhead not worth it here. Scoping Q5, option C.

### DEC-006: CLI scope for `--blind`
**Decision:** `.txt` file pairs only for this ticket. If `--blind` is combined with `--from/--to` iteration refs, return a clear error message pointing at the file-pair form.
**Rationale:** Ticket "Done when" is explicit about the `.txt` pair form. Iteration-ref support is a follow-up if users ask. Scoping Q6, option A.

---

## Architecture Review

Given the small surface (one async function calling Anthropic API, one dataclass, one CLI flag, unit tests), the review is condensed. No new subprocesses, no DB, no routes, no UI.

| Area           | Rating  | Notes |
|----------------|---------|-------|
| Security       | pass    | API key from env (existing pattern). User-supplied `.txt` file contents go into the prompt — same trust model as `quality_grader.grade_quality`. Path validation reused from existing `cmd_compare` file loading (per `path-validation.md` rule). No new attack surface. |
| Performance    | pass    | Two Anthropic calls per invocation (run-1 + position-swapped run-2). No loops, no N+1. Duration tracked. No caching optimization in scope — can add later if cost becomes a concern. |
| Data Model     | pass    | New `BlindReport` dataclass is additive. No migrations, no schema changes to `EvalSpec`. `to_json()` for optional persistence follows `GradingReport` pattern. |
| API Design     | concern | **`blind_compare` signature has a naming question:** the ticket says `(output_a, output_b, prompt, rubric_hint)` but "prompt" is ambiguous — is it the original user prompt that produced the outputs? Or the judge's meta-prompt? Need to clarify before implementation. See concern C-001. |
| Observability  | pass    | Print header line with model name; print verdict + reasoning on stdout; `BlindReport.to_json()` available for programmatic use. Token + duration tracked in report. |
| Testing        | pass    | Mock pattern from `test_quality_grader.py::TestGradeQuality` applies directly. Edge cases enumerated in stories: position randomization determinism (seeded), swap agreement, swap disagreement→tie, malformed JSON response, missing response content, `rubric_hint` prompt injection. Coverage gate 80% already in place. |
| Rules compliance | pass  | `path-validation.md` reused via existing `cmd_compare` path handling; `subprocess-cwd.md` N/A. |

**Blockers:** none.

**Concerns to resolve in Phase 3 (Refinement):**
- **C-001** — Disambiguate the `prompt` parameter in `blind_compare(output_a, output_b, prompt, ...)`. Is it the user's original query that produced the two outputs (gives the judge context), or the skill task description, or something else?

---

---

## Refinement

### DEC-007: `prompt` parameter meaning (resolves C-001)
**Decision:** `prompt` is the **original user query** that produced `output_a` and `output_b`. The judge sees it as context in the form: *"The user asked: `<prompt>`. Here are two responses. Evaluate holistically and pick the better one."*
**Rationale:** Matches the agentskills.io eval spec language and how LLM-judge blind evals work in the literature. Gives the judge the semantic grounding it needs to render a meaningful verdict. Option A from C-001.

### DEC-008: Position randomization determinism
**Decision:** Accept an optional `rng: random.Random | None = None` parameter. When `None`, use `random.Random()` (non-deterministic). Tests pass a seeded `Random(42)` for repeatability. The chosen mapping (which output went into slot_1 for run-1) is stored in `BlindReport` under a private-ish field `_run1_mapping` so tests can assert it.
**Rationale:** Determinism for tests without polluting the public API default. Matches the project's preference for keyword-only optional params (cf. `subprocess-cwd.md` rule shape).

### DEC-009: Empty / degenerate inputs
**Decision:** If either output is empty (`""` or whitespace-only), raise `ValueError("blind_compare: output_a and output_b must be non-empty")`. Identical non-empty outputs are a valid input and should produce `preference="tie"` naturally from the judge.
**Rationale:** Empty input is almost certainly a caller bug (reading the wrong file); surfacing it early is better than sending it to the API.

### DEC-010: JSON response schema from the judge
**Decision:** Judge is prompted to return:
```json
{"preference": "1" | "2" | "tie", "confidence": 0.0-1.0, "score_1": 0.0-1.0, "score_2": 0.0-1.0, "reasoning": "..."}
```
Note the judge sees labels `1`/`2` (randomized), not `a`/`b`. `blind_compare` translates back using the stored mapping. Malformed/missing JSON → return `BlindReport` with `preference="tie"`, `confidence=0.0`, `position_agreement=False`, reasoning set to the parse error. Mirror the graceful-failure pattern from `parse_grading_response`.
**Rationale:** 1/2 labeling keeps the judge from anchoring on a/b conventions in its training data. Graceful failure matches the rest of `quality_grader.py`.

### DEC-011: Output formatting on CLI
**Decision:** `clauditor compare before.txt after.txt --spec <skill.md> --blind` prints:
```
Blind A/B comparison (model: claude-sonnet-4-6)
  before.txt: score 0.72
  after.txt:  score 0.85
  preference: AFTER (confidence 0.80)
  position agreement: yes
  reasoning: <text>
```
Plus exit code 0 regardless of winner (this is an information tool, not a pass/fail gate). If `--blind` is combined with `--from/--to`, print `error: --blind currently only supports file-pair form (before.txt after.txt)` and exit 2.
**Rationale:** Human-readable first; JSON available via `BlindReport.to_json()` for programmatic callers. Exit code 0 because a verdict was rendered.

### DEC-012: Model default and override
**Decision:** Default model `claude-sonnet-4-6` (matches `quality_grader.grade_quality`). CLI respects existing `--clauditor-model` / env overrides if already wired; otherwise add `--model` flag on `compare` subcommand only if absent. (Check during implementation.)
**Rationale:** Consistency with existing graders; avoids scope creep on model-selection plumbing.

---

## Detailed Breakdown

Stories ordered for Ralph execution. Each completable in a single context window.

### US-001 — BlindReport dataclass + build_blind_prompt helper

**Description:** Add the `BlindReport` dataclass and a pure `build_blind_prompt()` function to `quality_grader.py`. No LLM calls yet. This is the TDD-friendly foundation.

**Traces to:** DEC-002, DEC-007, DEC-010, DEC-011

**Files:**
- `src/clauditor/quality_grader.py` — add `BlindReport` dataclass + `build_blind_prompt(user_prompt, output_1, output_2, rubric_hint)` near other prompt builders
- `tests/test_quality_grader.py` — new class `TestBlindReport` + `TestBuildBlindPrompt`

**TDD (write failing first):**
- `test_blind_report_to_json_roundtrip` — create, serialize, parse back
- `test_blind_report_defaults` — position_agreement defaults True, etc.
- `test_build_blind_prompt_includes_user_prompt` — user query text appears verbatim
- `test_build_blind_prompt_labels_outputs_1_and_2` — outputs labeled `1` and `2`, never `a`/`b`
- `test_build_blind_prompt_no_rubric_hint_when_none` — absence of hint keeps prompt clean
- `test_build_blind_prompt_injects_rubric_hint_when_given` — hint appears in prompt
- `test_build_blind_prompt_requests_json_schema` — prompt mentions `preference`, `confidence`, `score_1`, `score_2`, `reasoning`

**Done when:** Tests green, `ruff check src/ tests/` clean, coverage of new code 100%.

**Depends on:** none

---

### US-002 — blind_compare async function with swap-and-check

**Description:** Implement `async def blind_compare(user_prompt, output_a, output_b, rubric_hint=None, *, model="claude-sonnet-4-6", rng=None) -> BlindReport`. Calls Anthropic twice: run-1 with a randomized mapping of (a,b)→(1,2), run-2 with the swap. Parses both responses, checks agreement on the original-space winner, aggregates into `BlindReport`. Tracks tokens/duration across both calls.

**Traces to:** DEC-001, DEC-002, DEC-003, DEC-008, DEC-009, DEC-010, DEC-012

**Files:**
- `src/clauditor/quality_grader.py` — add `blind_compare()` + a private `_parse_blind_response(text) -> dict | None` helper (mirrors `parse_grading_response` style)
- `tests/test_quality_grader.py` — new class `TestBlindCompare`

**TDD (write failing first):**
- `test_blind_compare_rejects_empty_output_a` — ValueError
- `test_blind_compare_rejects_whitespace_output_b` — ValueError
- `test_blind_compare_agreement_picks_winner` — run-1 says "1", run-2 (swapped) says "2" → both point to output_a → preference="a", position_agreement=True
- `test_blind_compare_disagreement_returns_tie` — judge picks the same slot in both runs (position-biased) → preference="tie", position_agreement=False
- `test_blind_compare_explicit_tie_verdict` — both runs return "tie" → preference="tie", position_agreement=True
- `test_blind_compare_tracks_tokens_across_both_calls` — input/output tokens summed
- `test_blind_compare_tracks_duration` — duration > 0
- `test_blind_compare_malformed_json_returns_graceful_tie` — bad JSON → preference="tie", confidence=0.0, reasoning contains parse error
- `test_blind_compare_seeded_rng_is_deterministic` — two calls with Random(42) produce identical run-1 mapping
- `test_blind_compare_uses_custom_model_arg` — model name passed to client
- `test_blind_compare_score_fields_translated_to_original_space` — score_1 in judge output correctly maps to score_a when mapping was (a→1)

**Mock pattern:** `patch("clauditor.quality_grader.AsyncAnthropic", return_value=mock_client)`, `mock_client.messages.create = AsyncMock(side_effect=[response1, response2])` for the two calls.

**Done when:** Tests green, `ruff check` clean, coverage of `blind_compare` ≥ 90%, overall gate ≥ 80%.

**Depends on:** US-001

---

### US-003 — CLI --blind flag and output formatting

**Description:** Add `--blind` flag to `compare` subcommand. When set and both positional inputs are `.txt` files, call `blind_compare()` and print the verdict block per DEC-011. When `--blind` is combined with iteration refs (`--from`/`--to`), print error and exit 2. Requires `--spec` to resolve the user prompt / skill context.

**Traces to:** DEC-006, DEC-007, DEC-011, DEC-012

**Files:**
- `src/clauditor/cli.py` — add `--blind` flag to `p_compare` (after line 1390); dispatch in `cmd_compare()` (around line 706) when flag is set; add `_print_blind_report(report, before_path, after_path)` formatter
- `tests/test_cli.py` (or appropriate CLI test file — discover at implementation time) — new `TestCompareBlind` class

**TDD:**
- `test_compare_blind_happy_path` — mocks `blind_compare`, asserts output contains preference + reasoning + both filenames
- `test_compare_blind_with_iteration_refs_errors` — `--blind --from 3 --to 4` → exit 2, error message mentions file-pair form
- `test_compare_blind_requires_spec` — omitting `--spec` raises a clear error
- `test_compare_blind_tie_output` — preference="tie" renders cleanly
- `test_compare_blind_disagreement_surfaces_position_bias` — `position_agreement=False` appears in output

**Open question for implementation:** How does the CLI obtain the "user prompt" for DEC-007? Options: (a) parse it from the eval spec's `prompt` field if present, (b) require a new `--prompt` CLI arg, (c) derive from skill `.md` description. **Resolve by reading `EvalSpec` at implementation time — prefer (a) if the field exists, else add `--prompt`.**

**Done when:** CLI invocation matches ticket "Done when" literally: `clauditor compare before.txt after.txt --spec <skill.md> --blind` prints a preference verdict with reasoning. Tests green, ruff clean, coverage ≥ 80%.

**Depends on:** US-002

---

### US-004 — Quality Gate

**Description:** Run code reviewer 4 times across the full changeset, fixing all real bugs each pass. Run CodeRabbit if available. Project validation (`ruff check` + `pytest --cov=clauditor --cov-fail-under=80`) must pass after all fixes.

**Files:** any touched by US-001..003 — review iteratively.

**Done when:** 4 clean reviewer passes, CodeRabbit clean (or N/A), all tests pass, coverage gate met.

**Depends on:** US-003

---

### US-005 — Patterns & Memory

**Description:** If new patterns emerged during implementation worth preserving, update `.claude/rules/`, `docs/`, or project memory. Candidates: a rule for "optional-seed rng for determinism in LLM-judge tests", a rule for "1/2 labeling over a/b in blind prompts to avoid training-data anchoring", or a README section on the blind compare feature.

**Files:** `.claude/rules/<new-rule>.md` if applicable; `README.md` compare-section update.

**Done when:** Any new conventions documented; no-op if nothing surprising emerged.

**Depends on:** US-004

---

**Phase status:** detailing complete. Ready for Phase 5 (publish PR) on your approval.


