# Super Plan: #21 — Capture timing + token usage per skill run

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/21
- **Branch:** `feature/21-timing-tokens`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/21-timing-tokens`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/30
- **Sessions:** 1
- **Last session:** 2026-04-13

---

## Discovery

### Ticket Summary

**What:** Wire timing and token-usage data through to `history.jsonl` so the `clauditor trend` command (from #18) can render real series for efficiency metrics.

**Why:** The agentskills.io eval spec treats timing/token capture as table-stakes. Clauditor's `history.jsonl` has a `metrics: {}` field that is always empty — this gap is what #18 identified when the command shipped. Without it, skill authors can't answer "did this skill get faster/cheaper?"

**Ticket acceptance criteria:**
- [ ] `runner.SkillResult` carries duration + token fields
- [ ] `cmd_grade` records timing + tokens into `history.append_record(metrics=...)`
- [ ] `clauditor trend <skill> --metric tokens` renders a real series
- [ ] `.grade.json` includes the same data (per-run detail)

### Codebase Findings

**Already in place:**
- `SkillResult.duration_seconds: float = 0.0` at `runner.py:22–36` — already a field, populated in `runner.run()` at lines 114–116 via `time.monotonic()`. **FileNotFoundError path misses it** (lines 144–151); would be fixed incidentally.
- `GradingReport.to_json()` at `quality_grader.py:70–93` already serializes `duration_seconds`. Easy to extend with a `metrics` dict.
- `history.append_record(metrics=...)` call site at `cli.py:284–289` — the target insertion point. `metrics={}` today.
- Mock patterns for both `subprocess.run` (test_runner.py:174–187) and `AsyncAnthropic` (test_quality_grader.py, test_grader.py) are established.

**Missing entirely:**
- Token capture anywhere. The three Anthropic SDK call sites (`grader.py:207`, `quality_grader.py:237`, `triggers.py:238`) all discard `response.usage`. These are Layer 2/3 calls made by clauditor itself — easy to capture.
- **Skill-side tokens.** `runner.py` invokes `subprocess.run([claude_bin, "-p", prompt])` with no `--output-format stream-json`. The default text mode returns no usage metadata. Getting skill-side tokens requires either parsing stream-json or accepting we don't have them.

**Relevant conventions:**
- 80% coverage gate, ruff clean, no TodoWrite (CLAUDE.md:57–79)
- `.claude/rules/` still doesn't exist (confirmed in #17/#18 plans)
- `workflow-project.md` doesn't exist
- `asyncio_mode = "strict"` — async tests need the marker
- Don't shadow plugin fixture names
- `history.append_record` is skipped when `--only-criterion` is set (cli.py:282) to prevent partial runs from corrupting longitudinal data

### Proposed Scope

Wire timing (already captured internally) and token usage (newly captured from SDK responses) through to both `.grade.json` and `history.jsonl`. Most plumbing is straightforward; the one real decision is **whether to add `--output-format stream-json` parsing to `runner.py`** to get skill-side tokens, or accept that we only record clauditor's own API calls (grader/quality_grader/triggers).

Ordering: SDK-level usage capture → SkillResult field additions → GradingReport serialization → history.append_record wiring → trend validation → Quality Gate → Patterns & Memory.

### Scoping Questions

**Q1 — Which tokens do we track?**
- **(A)** Layer 2/3 tokens only (grader + quality_grader + triggers — i.e. the API calls clauditor makes directly). Skill-side tokens stay zero / unavailable.
- **(B)** Layer 2/3 + skill-side, via new `--output-format stream-json` parsing in `runner.py`. Significant new code path.
- **(C)** (A) now, file (B) as a follow-up ticket once the core plumbing lands.

**Q2 — How are the different token buckets represented?**
- **(A)** One flat `total_tokens` field summed across all calls.
- **(B)** Structured: `{skill_tokens: int, grader_tokens: int, quality_tokens: int, triggers_tokens: int, total: int}`.
- **(C)** Two-level: `{input_tokens: int, output_tokens: int, total_tokens: int}` flat, buckets aggregated.
- **(D)** (B) but with `{input, output}` per bucket (the SDK's native shape).

**Q3 — Where does timing/tokens live in the persisted data?**
- **(A)** Only in `history.jsonl.metrics` (longitudinal view).
- **(B)** Only in `.grade.json` (per-run detail).
- **(C)** Both — history gets scalars, grade.json gets the full structured shape.
- **(D)** (C) plus a dedicated `timing.json` alongside each grade save (matches the agentskills.io spec's file layout precisely).

**Q4 — `cmd_validate` and `cmd_extract` — do they also record history?**
Today only `cmd_grade` writes to history.jsonl.
- **(A)** Keep status quo — only `cmd_grade` records history. `validate`/`extract` are free-form.
- **(B)** `cmd_extract` also records (since it uses a Layer 2 LLM call that consumes tokens).
- **(C)** All three record; `metrics` includes a `command: "grade"|"extract"|"validate"` discriminator so `trend` can filter.

**Q5 — Skill-run duration on the FileNotFoundError path**
The scout found that `runner.run()` doesn't set `duration_seconds` when the subprocess binary is missing. Fix?
- **(A)** Yes — wrap everything in the same `time.monotonic()` block; trivial fix.
- **(B)** No — out of scope; file a separate issue.

**Q6 — `clauditor trend` metric names**
Today `trend` accepts any metric name and looks it up in `metrics` dict (or top-level for `pass_rate`/`mean_score`).
- **(A)** Add `duration_seconds`, `total_tokens`, `input_tokens`, `output_tokens` as recognized metric names with friendly error if misspelled.
- **(B)** Keep trend generic — any key in `metrics` works, no special-case validation.
- **(C)** (B) plus a new `clauditor trend <skill> --list-metrics` subcommand that prints what's available for the skill.

---

## Decisions (from scoping)

- **DEC-001 — Skill-side tokens via stream-json (Q1=B):** `runner.run()` switches to `claude -p --output-format stream-json --verbose` (or equivalent; exact flag combo TBD in refinement). Parse the JSON line stream to extract final text + usage. Bigger surface than option A but delivers the full agentskills.io vision.
- **DEC-002 — Structured token buckets (Q2=D):** Persist as `{skill: {input, output}, grader: {input, output}, quality: {input, output}, triggers: {input, output}, total: {input, output, sum}}`. Matches the SDK's native shape per bucket; `total.sum` is the aggregate for trend line charts.
- **DEC-003 — Persistence locations (Q3=D):** Data lands in three places: (1) `history.jsonl` metrics dict (flat scalars for trend); (2) `.grade.json` (full structured shape for per-run detail); (3) a new `timing.json` sibling file alongside each grade save, matching the agentskills.io spec layout precisely.
- **DEC-004 — All three commands record history (Q4=C):** `cmd_grade`, `cmd_extract`, and `cmd_validate` all call `history.append_record(...)`. A new `command: Literal["grade", "extract", "validate"]` discriminator distinguishes them. `clauditor trend` grows a `--command` filter.
- **DEC-005 — Fix FileNotFoundError duration (Q5=A):** Wrap the full `runner.run()` body in a single `time.monotonic()` block so every exit path — success, timeout, binary-missing — records duration.
- **DEC-006 — Generic trend metrics + list (Q6=C):** Keep `clauditor trend --metric <name>` free-form (any key resolvable via dotted path into the history record). Add `--list-metrics` which inspects the last N records and prints the union of available metric keys.

## Architecture Review

Six review areas. The stream-json work (DEC-001) is the big one — everything else is plumbing.

| Area | Rating | Notes |
|---|---|---|
| **Security** | pass | stream-json input comes from the already-trusted `claude` binary. JSON parsing uses the stdlib `json` module on line-delimited input — no eval, no shell. Redaction concerns are out of scope (that's #24 transcripts). |
| **Performance** | pass | Reading `claude -p` output line-by-line vs buffering is a wash at typical skill-run sizes. No new network, no new disk churn beyond the small `timing.json` file per grade. History record grows from ~200B to ~800B — still negligible over tens of thousands of runs. |
| **Data Model** | concern | DEC-002's nested token shape means `history.jsonl` records are now nested. `clauditor trend` currently reads `record["metrics"][metric_name]` flat — need to support dotted paths like `metrics.grader.input` or `metrics.total.sum`. Backward compat for existing history.jsonl files: old rows have `metrics: {}` — trend should handle both shapes gracefully. |
| **API Design** | **blocker** | **DEC-001 reshapes `runner.run()` output.** Currently returns `SkillResult(output=<stdout>, exit_code, ...)`. With stream-json, `output` becomes the joined text extracted from `type: "assistant"` messages — not raw stdout. Any existing caller that reads raw stdout (the captured-output workflow from #17, maybe `cmd_run`) may see different output. Need to verify: (a) text extraction is lossless for the skill's final message; (b) `cmd_run` still produces expected user-visible output; (c) `tests/eval/captured/<skill>.txt` files committed to the repo are still reproducible. |
| **Observability** | concern | DEC-004 (all three commands record history) changes semantics: `trend --metric pass_rate` without `--command grade` now includes validate/extract runs where `pass_rate` may mean something different. Need a sensible default — probably `--command grade` is implicit unless `--command` is explicitly passed, to preserve the current mental model. |
| **Testing** | concern | stream-json changes the subprocess mock pattern. Current tests mock `subprocess.run` with a `stdout` string — new code reads the child's stdout stream line-by-line. Two options: (a) still call `subprocess.run` with `capture_output=True`, then split stdout on newlines and parse each — simpler, mock unchanged; (b) switch to `subprocess.Popen` for true streaming, which the existing mocks don't cover. Option (a) is easier; option (b) enables future work on long-running skills (progress bars, live transcripts for #24). |

### Blockers to resolve in refinement
1. **B1. stream-json output extraction correctness.** Decide how `SkillResult.output` is populated from stream-json — which message types are concatenated, whether tool-use blocks are included, whether system messages are filtered. Verify against a real `claude -p --output-format stream-json` run that captured-output files from #17 remain reproducible.

### Concerns to address in refinement
1. **C1. Dotted-path metric resolution in `cmd_trend`.** Do we introduce a small path walker (`metrics.grader.input`) or flatten the nested shape on write (`grader_input_tokens`)?
2. **C2. `--command` filter default.** Should `trend` default to `command=grade` implicitly, require explicit `--command`, or union across all commands?
3. **C3. subprocess.run vs Popen for stream-json.** Which subprocess API do we use?
4. **C4. `timing.json` file path convention.** #22 (per-iteration workspace) hasn't landed yet. Where does `timing.json` live today? Sibling of `.grade.json` in `.clauditor/`? Under `.clauditor/timing/<skill>-<ts>.json`? One file per grade run, or rolling?
5. **C5. Extract command token tracking.** `cmd_extract` ships the Layer 2 grader call but doesn't compute `pass_rate`/`mean_score` the same way `cmd_grade` does. What does its history record look like schema-wise?

### Follow-up / discussion items
- **F1. Usage field on Anthropic SDK responses.** Verify the SDK version pinned in `pyproject.toml` returns `response.usage.input_tokens` / `output_tokens`. Older SDK versions may not. (Should be trivially present on current versions.)

## Refinement Log

**Guiding principle** (user direction): clauditor is pre-production. No backward-compatibility constraints. Break existing behavior freely when it produces a cleaner tool that aligns more closely with agentskills.io. Previously-committed captured-output files can be regenerated.

- **DEC-007 — stream-json output semantics (B1):** `SkillResult.output` is populated from the concatenation of all `type == "assistant"` message text blocks joined by `\n`. Tool-use and tool-result blocks are NOT included in `output` (they are intermediate reasoning, not skill output). The final `type == "result"` message supplies `usage.input_tokens` and `usage.output_tokens`. Story US-001 begins with a schema-verification step against a real `claude -p --output-format stream-json --verbose` invocation before writing parser code.
- **DEC-008 — Captured-output regeneration (corollary to DEC-007):** After US-001 lands, any `tests/eval/captured/<skill>.txt` files committed to the repo must be regenerated via `clauditor capture` to match the new text-extraction path. This is a breaking change; acceptable per project direction.
- **DEC-009 — Path walker for metrics (C1=a):** `clauditor trend --metric <path>` resolves dotted paths like `grader.input_tokens` against `record["metrics"]` (and top-level for `pass_rate`/`mean_score`/`duration_seconds` as special cases for backwards readability). No flat aliases — the nested shape IS the canonical form. The resolver is a pure helper function, unit-testable in isolation.
- **DEC-010 — Implicit --command grade filter (C2=a):** `clauditor trend <skill> --metric X` implicitly filters to `command == "grade"` unless the user passes `--command <value>` explicitly (or `--command all` to union). Preserves the existing mental model and keeps the most common trend query one-liner.
- **DEC-011 — Popen for stream-json (C3=b):** `runner.run()` switches from `subprocess.run` to `subprocess.Popen` with `stdout=PIPE`, reading one line at a time. True streaming. This is prerequisite infrastructure for #24 (execution transcripts). Tests re-mock at the Popen level; a small `_FakePopen` helper in `tests/conftest.py` abstracts the pattern so future tests don't re-invent it.
- **DEC-012 — Drop separate timing.json for now (revises DEC-003 for C4):** The full structured timing+token shape lives in `.grade.json`. History records get scalar metrics (nested under `metrics.<bucket>.<kind>`). **Do NOT ship a separate `timing.json` file in this ticket.** The agentskills.io spec puts `timing.json` inside an iteration directory — that directory doesn't exist until #22 ships. Building a temporary path convention now would be churn. File a follow-up note in #22 to emit `timing.json` inside iteration dirs as part of that ticket.
- **DEC-013 — cmd_extract history shape (C5=a):** Extract runs record `pass_rate=None, mean_score=None, command="extract"` plus full `metrics` (timing + tokens). `clauditor trend` handles `None` values by filtering them out before sparkline computation, with a friendly message if the filtered set is empty.
- **DEC-014 — Token bucket keys:** Canonical bucket names: `skill`, `grader` (Layer 2 schema extraction), `quality` (Layer 3 rubric grading), `triggers` (trigger precision testing). Per-bucket shape: `{input_tokens: int, output_tokens: int}`. Total shape: `{input_tokens: int, output_tokens: int, total: int}`. `total` is `input + output` across all buckets present in that run. A bucket is omitted from `metrics` if that command didn't invoke it (e.g., `cmd_validate` only has `skill` + `total`; `cmd_extract` has `skill` + `grader` + `total`; `cmd_grade` has all four — skill + grader + quality + triggers + total).
- **DEC-015 — history.jsonl schema versioning:** Add a `schema_version: 2` field at the top level of each new record. Records without `schema_version` are treated as v1 (old `metrics: {}` rows) and skipped by `trend` when the requested metric is a new v2 key. No migration of old records — pre-production.
- **DEC-016 — cmd_validate token capture:** Layer 1 is deterministic; it makes no LLM calls. So `cmd_validate` records only `skill` bucket tokens (from the stream-json skill run) + `total`. The `grader`/`quality`/`triggers` buckets are absent. This still lets users trend skill token cost across validate runs.

## Detailed Breakdown

Stories are ordered by dependency. Each is sized for a single Ralph context window. Every story's acceptance includes `uv run ruff check src/ tests/` clean and `uv run pytest --cov=clauditor --cov-report=term-missing` passing with the 80% gate.

---

### US-001 — Stream-json runner foundation (Popen + parser + output extraction)

**Description:** Switch `runner.run()` from `subprocess.run(... -p prompt)` to `subprocess.Popen(... -p prompt --output-format stream-json --verbose)`. Parse the NDJSON stream line-by-line. Extract the concatenated assistant-message text as `SkillResult.output`. Extract token usage from the final result message into new `SkillResult` fields. Re-mock existing tests at the Popen layer via a shared `_FakePopen` helper.

**Traces to:** DEC-001, DEC-007, DEC-008, DEC-011, DEC-014

**Before writing code:**
1. Manually run `claude -p --output-format stream-json --verbose "/<any-skill> <any-arg>"` in a shell and capture 3-5 lines of the stream.
2. Document the exact message types and the shape of the final `result` message in a comment at the top of the new parser module or function.
3. Verify `usage.input_tokens` and `usage.output_tokens` are present on the terminal message.

**Files:**
- `src/clauditor/runner.py`:
  - Extend `SkillResult` dataclass with: `input_tokens: int = 0`, `output_tokens: int = 0`, `raw_messages: list[dict] = field(default_factory=list)` (future-proofing for #24 transcripts — unused by other modules for now, but captured).
  - Rewrite `run()` to use `subprocess.Popen(..., stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)`. Read `stdout` line by line in a loop, parse each as JSON, dispatch by `type`. Accumulate assistant-message text. Capture `usage` from the final `result` message. Handle the timeout path via a manual deadline check (time.monotonic() + timeout). Wrap everything in a single try/finally so `duration_seconds` is set on every exit path (DEC-005).
  - Keep the `run_raw()` helper for prompts that don't need stream-json — or remove it entirely if unused (grep first). If removed, note as a breaking change.
- `tests/conftest.py`:
  - New helper `_FakePopen` that takes a list of stream-json dicts and a final result dict, serializes them as newline-separated JSON, and presents as a Popen-compatible object with `.stdout`, `.wait()`, `.returncode`, `.pid`.
  - Helper: `make_fake_skill_stream(text: str, input_tokens: int = 100, output_tokens: int = 50)` builds a plausible assistant + result sequence.
- `tests/test_runner.py`:
  - Update every test that mocks `subprocess.run` to mock `subprocess.Popen` instead, using `_FakePopen`.
  - New tests: assistant-text concatenation across multiple messages; single assistant message; token extraction; malformed line handling (parse error on one line doesn't crash the whole run); missing final result message (fall back to zero tokens + a warning); timeout path still records duration; binary-not-found path still records duration (DEC-005).

**TDD:**
- `_FakePopen` with a single assistant message + result → `SkillResult.output == "hello"`, `input_tokens == 100`, `output_tokens == 50`.
- Two assistant messages → output is their text joined by `\n`.
- Tool-use block in an assistant message → NOT included in output (only the text blocks from that message).
- Missing final result → `input_tokens == 0`, `output_tokens == 0`, warning printed to stderr.
- Malformed JSON on one line → that line is skipped (warning to stderr), subsequent lines still parsed.
- Timeout: `duration_seconds > 0`, `error` field set to "timeout".
- `FileNotFoundError` for the claude binary: `duration_seconds > 0` (now fixed per DEC-005), `error` field indicates missing binary.

**Acceptance criteria:**
- `SkillResult` has the new fields with sensible defaults.
- All existing `tests/test_runner.py` tests pass with the new mock pattern.
- Coverage ≥ 80% on `runner.py`; line coverage on the new Popen code path ≥ 90%.
- Ruff clean.

**Done when:** A real (unmocked) `clauditor capture find-restaurants -- "near San Jose"` on a live setup returns a `SkillResult` with non-zero `input_tokens` + `output_tokens` and the assistant's user-visible text as `output`. (User verifies manually before merging; no automated end-to-end test in CI.)

**Depends on:** none.

---

### US-002 — Token bucket capture for clauditor's own Anthropic SDK calls

**Description:** Capture `response.usage.input_tokens` and `response.usage.output_tokens` at each of the three Anthropic SDK call sites in clauditor (`grader.py`, `quality_grader.py`, `triggers.py`). Return these through the existing return types so `cli.py` can aggregate them into the bucketed shape.

**Traces to:** DEC-002, DEC-014

**Files:**
- `src/clauditor/grader.py`:
  - At the `await client.messages.create(...)` call (~line 207), capture `response.usage.input_tokens` and `.output_tokens`. Add these as new fields on `ExtractedOutput` (or the equivalent return type): `grader_input_tokens: int`, `grader_output_tokens: int`.
- `src/clauditor/quality_grader.py`:
  - Same treatment at `await client.messages.create(...)` (~line 237). The function currently returns a `GradingReport`; extend it with `input_tokens: int`, `output_tokens: int`. Update `GradingReport.to_json` / `from_json` to round-trip these fields.
  - `measure_variance` calls `grade_quality` multiple times — aggregate tokens across all runs into the returned variance report.
- `src/clauditor/triggers.py`:
  - Same treatment at the SDK call (~line 238). Extend the trigger report type with `input_tokens: int`, `output_tokens: int`.
- `tests/test_grader.py`, `tests/test_quality_grader.py`, `tests/test_triggers.py`:
  - Update SDK mocks to return responses with a `usage` attribute (use `MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))`).
  - Assert the captured fields on the returned objects.

**TDD:**
- Each SDK call site: mock returns `usage.input_tokens=500, output_tokens=200` → returned object carries those values.
- `measure_variance` with 3 runs, each returning 100/50 tokens → aggregated report carries 300/150.
- `GradingReport.to_json` → `from_json` round-trip preserves `input_tokens`/`output_tokens`.

**Acceptance criteria:**
- All three SDK call sites capture usage.
- Return types carry the fields; serialization round-trips.
- Coverage ≥ 80%; ruff clean.

**Done when:** `clauditor grade` (mocked) produces a `GradingReport` whose `input_tokens`/`output_tokens` reflect the sum across Layer 3 grading calls.

**Depends on:** none (can run in parallel with US-001; US-003 integrates both).

---

### US-003 — Metrics aggregation helper + bucketed shape

**Description:** Add a new helper module `src/clauditor/metrics.py` that builds the canonical bucketed metrics dict from a `SkillResult` + optional grader/quality/triggers token counts. Pure function, no I/O. Used by US-004/US-005 to produce the `metrics=...` kwarg for `history.append_record` and the in-memory structure embedded in `.grade.json`.

**Traces to:** DEC-002, DEC-014, DEC-015

**Files:**
- `src/clauditor/metrics.py` (NEW):
  - `build_metrics(skill: SkillResult, grader_tokens: TokenUsage | None = None, quality_tokens: TokenUsage | None = None, triggers_tokens: TokenUsage | None = None, duration_seconds: float) -> dict` — returns `{skill: {input_tokens, output_tokens}, grader: {...}|None, quality: {...}|None, triggers: {...}|None, total: {input_tokens, output_tokens, total}, duration_seconds: float}`. Omits bucket keys (not sets to None; simply absent) when the corresponding TokenUsage arg is None.
  - Small `TokenUsage` typed dict or dataclass: `{input_tokens: int, output_tokens: int}`.
- `tests/test_metrics.py` (NEW):
  - Only skill tokens → `total` equals skill tokens, `grader`/`quality`/`triggers` absent.
  - All four buckets present → `total` is the sum.
  - Empty / zero-token skill → `total` is zero, shape is still correct.
  - Duration flows through unchanged.

**TDD:**
- All four cases above.

**Acceptance criteria:**
- `metrics.py` is pure (no I/O, no imports from `cli` / `runner` beyond types).
- `build_metrics` is typed and documented.
- Coverage ≥ 80%; ruff clean.

**Done when:** `build_metrics(...)` produces the exact shape specified in DEC-014 for each command's bucket combination.

**Depends on:** US-001 (needs `SkillResult` new fields), US-002 (needs the return types that carry token counts).

---

### US-004 — `cmd_grade` wires timing + tokens through to history and grade.json

**Description:** Update `cmd_grade` in `cli.py` to aggregate timing + tokens from the skill run, grader call, and quality grader call using `build_metrics()`, then pass the full structured metrics dict to both `history.append_record` and the `.grade.json` save path. Add `schema_version: 2` to history records.

**Traces to:** DEC-003, DEC-004, DEC-014, DEC-015

**Files:**
- `src/clauditor/cli.py`:
  - In `cmd_grade`, after the skill run + grading chain completes, call `build_metrics(skill=result, grader_tokens=..., quality_tokens=..., triggers_tokens=..., duration_seconds=...)`.
  - Pass the returned dict to `history.append_record(..., metrics=<dict>)` (replacing `metrics={}`).
  - Include the dict in the `.grade.json` saved under `--save`. Report file schema gets a new `metrics` top-level key.
  - Pass `command="grade"` to `history.append_record` (requires US-005 to add the param).
- `src/clauditor/history.py`:
  - `append_record` gains a required `command: Literal["grade", "extract", "validate"]` parameter.
  - Every record written includes `schema_version: 2`.
- `src/clauditor/quality_grader.py`:
  - `GradingReport.to_json()` includes a `metrics` key when the field is populated; `from_json()` round-trips it.
- `tests/test_cli.py`:
  - Update `TestCmdGrade` tests to assert the structured metrics appear in `history.append_record` mock calls.
  - Assert `.grade.json` saved under `--save` contains the new `metrics` key with the bucketed shape.
  - Assert `schema_version: 2` appears.

**TDD:**
- `cmd_grade` with a mocked skill run (100 input / 50 output) and mocked grader/quality/triggers (different token counts each) → history record has `metrics.skill`, `metrics.grader`, `metrics.quality`, `metrics.triggers`, `metrics.total.total` equals the sum.
- `--save` writes `.grade.json` with `metrics` top-level key matching.
- `history.append_record` is called with `command="grade"`.
- `schema_version: 2` is present.
- `--only-criterion` still skips history append (regression from #18).

**Acceptance criteria:**
- `cmd_grade` produces real, non-empty metrics on every run.
- `.grade.json` saved shape has the new `metrics` field.
- History records carry `command` + `schema_version`.
- Coverage ≥ 80%; ruff clean.

**Done when:** A mocked `cmd_grade` run produces a history record whose `metrics.total.total > 0`.

**Depends on:** US-001, US-002, US-003.

---

### US-005 — `cmd_extract` and `cmd_validate` record history with command discriminator

**Description:** Extend `cmd_extract` and `cmd_validate` to call `history.append_record(..., command="extract"|"validate", metrics=...)` using `build_metrics()`. `cmd_extract` aggregates skill + grader tokens; `cmd_validate` only aggregates skill tokens (Layer 1 is deterministic). Both pass `pass_rate=None`/`mean_score=None` where those concepts don't apply (DEC-013).

**Traces to:** DEC-004, DEC-013, DEC-016

**Files:**
- `src/clauditor/cli.py`:
  - `cmd_validate`: add `history.append_record(skill=spec.skill_name, pass_rate=pass_rate_or_none, mean_score=None, metrics=build_metrics(skill=result, duration_seconds=...), command="validate")` after the assertion run. `pass_rate_or_none` can use the Layer 1 pass rate when available.
  - `cmd_extract`: add the same call with `command="extract"`, passing `grader_tokens` from the Layer 2 extraction.
- `tests/test_cli.py`:
  - `TestCmdExtractHistory` (new class): assert `history.append_record` is called with `command="extract"` and metrics include the skill + grader buckets.
  - `TestCmdValidateHistory` (new class): assert the call with `command="validate"` and skill-only metrics.

**TDD:**
- extract run → history record has `command="extract"`, `metrics.grader` present, `metrics.quality` absent, `metrics.triggers` absent.
- validate run → history record has `command="validate"`, only `metrics.skill` present.
- Both commands honor the existing conftest isolation fixture (no `.clauditor/` pollution).

**Acceptance criteria:**
- All three commands record history with a discriminator.
- Each command's metrics include only the buckets it legitimately populates.
- Coverage ≥ 80%; ruff clean.

**Done when:** `clauditor trend <skill> --command extract --metric grader.input_tokens` (once US-006 lands) returns data after an extract run.

**Depends on:** US-003, US-004.

---

### US-006 — `cmd_trend` dotted-path resolution, `--command` filter, `--list-metrics`

**Description:** Extend `cmd_trend` to resolve dotted-path metric names against nested `metrics` dicts, filter by `command` (default `grade`), and support a new `--list-metrics` flag that prints the union of metric paths available for a skill in its history.

**Traces to:** DEC-006, DEC-009, DEC-010, DEC-015

**Files:**
- `src/clauditor/history.py`:
  - New helper `resolve_path(record: dict, path: str) -> float | int | None` that walks dotted paths. Treats `pass_rate`/`mean_score`/`duration_seconds` as top-level for readability. Returns `None` if the path is absent or the value isn't numeric.
- `src/clauditor/cli.py`:
  - `cmd_trend`: add `--command <name>` arg (default `"grade"`; `"all"` unions across commands). Add `--list-metrics` mutually exclusive with `--metric`. Filter records by command before resolving paths. Skip `schema_version: 1` records when the requested metric is a v2 key.
  - `cmd_trend` with `--list-metrics` walks the last N records and prints the union of resolvable numeric paths (using a small recursive walker), sorted alphabetically.
- `tests/test_history.py`: add `TestResolvePath` class.
- `tests/test_cli.py`: add `TestCmdTrendCommandFilter`, `TestCmdTrendListMetrics`, `TestCmdTrendDottedPath`.

**TDD:**
- `resolve_path({"metrics": {"grader": {"input_tokens": 500}}}, "grader.input_tokens")` → 500.
- `resolve_path(record, "pass_rate")` → top-level field.
- `resolve_path(record, "nonexistent.path")` → None.
- `trend --metric grader.input_tokens` across 5 grade runs renders a 5-point series.
- `trend --command extract --metric grader.input_tokens` filters to extract runs only.
- `trend --command all` unions.
- `trend --list-metrics` prints a sorted list including both top-level (`pass_rate`) and nested (`metrics.grader.input_tokens`).
- `trend --metric X` on a history with only v1 records prints a friendly empty-set error.

**Acceptance criteria:**
- Dotted-path resolver is pure and unit-tested.
- `--command` filter works with defaults + explicit + `all`.
- `--list-metrics` prints the union correctly.
- Coverage ≥ 80%; ruff clean.

**Done when:** After running `cmd_grade`, `cmd_extract`, `cmd_validate` each once, `clauditor trend <skill> --list-metrics` shows the union of available keys.

**Depends on:** US-005.

---

### US-007 — Quality Gate (code review × 4 + CodeRabbit)

**Description:** Run the `code-review` skill four times across the full changeset; fix all real bugs found each pass. Run CodeRabbit if available. Re-run `uv run ruff check` + `uv run pytest --cov` after fixes.

**Traces to:** All DEC-001 → DEC-016.

**Acceptance criteria:**
- 4 code-review passes complete.
- All real bugs found are fixed (stylistic deferrals documented).
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥ 80% coverage.
- All ticket acceptance-criteria checkboxes satisfied.

**Done when:** Four consecutive review passes produce no new real bugs.

**Depends on:** US-001 through US-006.

---

### US-008 — Patterns & Memory

**Description:** Update project conventions and memory with patterns learned. (a) Document the stream-json message schema clauditor relies on (in code comments + README); (b) document the canonical bucket naming + nested metrics shape; (c) document `schema_version` in history.jsonl; (d) `bd remember` non-obvious insights. Update README to reflect new `trend --command` / `--list-metrics` flags and the dotted-path metric resolution.

**Traces to:** DEC-007, DEC-009, DEC-014, DEC-015.

**Acceptance criteria:**
- README updated with the new trend flags + dotted-path syntax.
- At least three `bd remember` entries capturing non-obvious insights.
- A comment at the top of `runner.py` (or a new docs file) documents the stream-json schema contract clauditor depends on.

**Done when:** A fresh agent opening this repo would not re-ask any of the design questions resolved in DEC-007 through DEC-016.

**Depends on:** US-007.

---

## Beads Manifest

- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/21-timing-tokens`
- **Epic:** `clauditor-ar2` — #21: Capture timing + token usage per skill run
- **Tasks:**
  - US-001 → `clauditor-ar2.1` — Stream-json runner foundation
  - US-002 → `clauditor-ar2.2` — Token capture at Anthropic SDK call sites
  - US-003 → `clauditor-ar2.3` — build_metrics aggregation helper
  - US-004 → `clauditor-ar2.4` — cmd_grade metrics wiring
  - US-005 → `clauditor-ar2.5` — cmd_extract + cmd_validate history recording
  - US-006 → `clauditor-ar2.6` — cmd_trend dotted-path + --command + --list-metrics
  - US-007 → `clauditor-ar2.7` — Quality Gate
  - US-008 → `clauditor-ar2.8` — Patterns & Memory
- **Dependencies:** US-003←{001,002}; US-004←{001,002,003}; US-005←{003,004}; US-006←{005}; US-007←{001..006}; US-008←{007}.
