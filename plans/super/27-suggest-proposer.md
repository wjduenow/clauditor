# Super Plan: #27 — LLM-driven skill improvement proposer (`clauditor suggest`)

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/27
- **Branch:** `feature/27-suggest-proposer`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/27-suggest-proposer`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/36
- **Sessions:** 1
- **Last session:** 2026-04-15

---

## Discovery

### Ticket Summary

**What:** New CLI subcommand `clauditor suggest <skill>` that bundles the latest
grade run's signals (failing assertions, output slices, optionally execution
transcripts from #26, current SKILL.md) and asks Sonnet — using a proposer
prompt that incorporates the agentskills.io guidelines (generalize from
feedback, keep skill lean, explain the why, bundle repeated work) — to
propose edits. The output is a **reviewable diff**, never auto-applied, with
per-change rationale and confidence.

**Why:** Closes the agentskills.io eval loop. Today clauditor reports failures
and stops; humans translate signals to skill edits by hand, miss cross-failure
patterns, and skills drift as each fix is scoped narrowly. Gap #9 in the
agentskills.io alignment analysis.

**Done when:** `clauditor suggest find-restaurants` produces a reviewable diff
against the skill file based on the last grade run's failures.

**Philosophical note from ticket:** This shifts clauditor from "automated
verifier" toward "iteration assistant" — bigger scope shift than other gaps.
Worth confirming the direction fits the project before committing.

### Codebase Findings

**CLI plumbing** (`src/clauditor/cli.py:1965–2351`): subcommands registered via
`subparsers.add_parser(...)`, dispatched by `if parsed.command == "..."`
chain. New `suggest` subcommand slots in next to `grade`/`validate`/`compare`.

**Iteration workspace** (`src/clauditor/workspace.py`,
`src/clauditor/cli.py::_find_prior_grading_json`): `.clauditor/iteration-N/<skill>/`
contains `assertions.json`, `grading.json`, `extraction.json`, and
`run-K/output.jsonl` transcripts. "Latest grade run" = max iteration index
containing `<skill>/grading.json`. Pattern for finding it already exists.

**LLM client pattern** (`grader.py:440–511`, `quality_grader.py:630–715`):
`AsyncAnthropic().messages.create(model=..., max_tokens=4096, messages=[...])`,
defensive content-block extraction, JSON parsing strips ```json fences. Sonnet
ID: `claude-sonnet-4-6`. Each module catches `JSONDecodeError` → graceful
failure report.

**Failing-assertion shape** (`assertions.py:28–76`): `AssertionResult` carries
`id`, `name`, `passed`, `message`, `kind`, `evidence`, `transcript_path` (path
to `run-K/output.jsonl`). `assertions.json` keyed by stable id (DEC-001).
Already wired with the transcript path (#26 just landed).

**Grading shape** (`quality_grader.py::GradingReport`): per-criterion scores
with `id`, `criterion`, `score`, `rationale`, `verdict`. `extraction.json`
likewise carries per-field results.

**Transcripts** (`runner.py:1–11, 177–332`): NDJSON stream-json under
`run-K/output.jsonl`, schema documented in `docs/stream-json-schema.md`. One
file per primary + variance reps.

**SKILL.md location** (`spec.py:17–66`): `SkillSpec.from_file(skill_path,
eval_path=None)` — caller provides a `.md` path; eval spec auto-discovered as
sibling `.eval.json`. The skill name on the CLI typically maps via the spec
to a concrete file path.

**No existing diff helpers**: `comparator.py::diff_assertion_sets` is custom
flip detection, not unified diff. `_print_grade_diff` is a table. The
suggester would build diffs via stdlib `difflib.unified_diff`.

**Test patterns** (`tests/test_quality_grader.py`): `AsyncAnthropic` mocked
via `unittest.mock.patch`, `mock_response.content = [MagicMock(type="text",
text=json.dumps(...))]`, `@pytest.mark.asyncio`, class-based organization.
`_make_spec` helper builds `EvalSpec` inline.

**Scrubbing** (`transcripts.py::redact`): non-mutating recursive walk that
returns scrubbed copies; canonical path for any I/O-bound sanitization.

### Applicable `.claude/rules` Constraints

| Rule | How it constrains `suggest` |
|---|---|
| `llm-judge-prompt-injection.md` | XML-fence every untrusted block (SKILL.md, assertions, output slices, transcript snippets) sent to Sonnet; framing sentence outside the tags. |
| `json-schema-version.md` | Any sidecar `suggestion.json` carries `schema_version: 1` as first key; loader hard-fails on mismatch. |
| `non-mutating-scrub.md` | Transcript redaction before Sonnet upload uses `transcripts.redact()`-style copies; in-memory data untouched. |
| `path-validation.md` | If suggest accepts an explicit skill path, validate via `resolve(strict=True)` + `is_relative_to(spec_dir)`. |
| `positional-id-zip-validation.md` | If Sonnet returns a per-failure structured response, length + text-match validation before zipping with stable ids. |
| `monotonic-time-indirection.md` | Proposer module is async; alias `_monotonic = time.monotonic` at module top. |
| `sidecar-during-staging.md` | Any per-iteration suggest artifact written inside `workspace.tmp_path` before `finalize()`. |
| `subprocess-cwd.md` | Not directly applicable — suggest calls Anthropic SDK, not the Claude CLI. |
| `eval-spec-stable-ids.md` | Bundle assertions/criteria by their existing `id`; rely on load-time uniqueness. |
| `stream-json-schema.md` | When parsing existing `output.jsonl` to extract slices, defensive read (skip malformed lines). |

**CLAUDE.md gates:** 80% coverage gate enforced; async tests require
`@pytest.mark.asyncio`; class-based test organization; `bd` for tracking.

**No `workflow-project.md`** — use baseline ordering and review areas.

### Open Scoping Questions

(See "Phase 1 Questions for User" below — answers will be folded into
Discovery and drive Phase 2 architecture review.)

---

## Decisions (Phase 1)

- **DEC-001 — First-class subcommand.** `clauditor suggest` ships alongside
  `grade` with no experimental flag. Clauditor's direction expands to
  "iteration assistant" and the project commits to that scope.
- **DEC-002 — SKILL.md only.** Proposer may only propose edits to the skill
  file. Scripts/ and the eval spec are out of scope for v1. Tightest blast
  radius; matches the ticket's "done when" verbatim.
- **DEC-003 — Both representations.** Sonnet returns structured JSON edits
  (`{anchor, replacement, rationale, confidence}`); clauditor renders a
  unified diff for human review and persists both forms in the sidecar.
  Auditable + replayable + diffable.
- **DEC-004 — Default bundle: assertions + output slices + grading
  rationales.** Full execution transcripts only included when the user
  passes `--with-transcripts` (opt-in because of token cost).
- **DEC-005 — Persist outside iteration workspace.** Suggestions land at
  `.clauditor/suggestions/<skill>-<ts>.{json,diff}`. Suggest is a read-only
  consumer of grade output and should not contend with iteration staging.

---

## Phase 2 — Architecture Review

| Area | Rating | Finding |
|---|---|---|
| Prompt injection | concern | XML-fence assertions/output/transcripts; framing sentence outside tags. SKILL.md is trusted, untagged. Reuse `quality_grader.py:505–507` pattern. |
| Secret leakage | pass | `transcripts.redact()` covers OpenAI/Anthropic/GitHub/AWS/Slack tokens + `*_KEY/_TOKEN/_SECRET` keys. Acceptable for v1 — opt-in `--with-transcripts` is a layered guard. Gap noted: no Azure/GCP/Postgres URL coverage; out of scope. |
| Apply-by-mistake | concern | No `--apply` flag, ever. Apply is always a separate human action via `git apply` or manual edit. |
| **Anchor matching (ambiguous/missing)** | **blocker** | Sonnet may emit anchors that appear 0 or N>1 times in SKILL.md. Diff is undefined. Must validate every anchor exactly-once before rendering, and fail the whole run on any violation. |
| Path traversal | pass | Reuse `workspace.validate_skill_name(args.skill)` before constructing the suggestions path. |
| Sidecar JSON schema | pass | `schema_version: 1` first key. Envelope: `skill_name`, `source_iteration`, `source_grading_path` (relative), `model`, `generated_at`, `input_tokens`, `output_tokens`, `edit_proposals[]`, `summary_rationale`, `validation_errors[]`. |
| Edit proposal shape | pass | `{id, anchor, replacement, rationale, confidence: float 0–1, motivated_by: [stable_id…], applies_to_file}`. `motivated_by` is the audit-trail link back to L1/L3 signals. |
| Loader hard-fail | pass | Mirror `audit.py::_check_schema_version`. Future audit/trend will read these files. |
| Concurrent invocations | pass | Filename suffix `%Y%m%dT%H%M%S%fZ` (microseconds) — precedent at `cli.py:1903`. |
| Source-of-truth pointer | pass | Relative paths via `_relative_to_repo` pattern (`cli.py:979–990`). |
| Test patterns | pass | Mirror `test_quality_grader.py:442–490` AsyncAnthropic mock. New test classes: TestLoadLatestRun, TestBuildPrompt, TestParseResponse, TestValidateAnchors, TestRenderDiff, TestCmdSuggest. |
| Coverage gate (80%) | pass-conditional | Achievable once the anchor-validation contract is locked in. Pre-Sonnet validation is the cleanest path. |
| **CLI surface** | **blocker** | `--iteration N` is ambiguous (read or write?). DEC-005 makes this a *read* parameter. Rename `--from-iteration`. Drop `--output` (lock to `.clauditor/suggestions/`). `--json` prints sidecar JSON to stdout instead of unified diff. |
| Observability | pass | Stderr logging mirroring `cmd_grade`: bundle summary, model, tokens, duration, output paths. Stdout reserved for diff (or JSON when `--json`). |
| **Failure modes** | **blocker** | Need explicit policy for: (a) no prior grade run, (b) zero failures in latest run, (c) Anthropic API error, (d) unparseable Sonnet response, (e) any anchor invalid. |
| Async dispatch | pass | `asyncio.run(_cmd_suggest_impl(args))`. New module needs `_monotonic = time.monotonic` alias. |

### Blockers requiring decisions (Phase 3 refinement)

1. **Anchor strategy** — how to make Sonnet's edits unambiguous and how to fail when they're not.
2. **CLI surface** — finalize flag names and remove `--output`.
3. **Failure-mode exit codes** — formal table for the user-visible behaviors.

---

## Phase 3 — Refinement

### Decisions

- **DEC-006 — Anchor strategy: contract + hard validate.** The proposer
  prompt instructs Sonnet: *"Each `anchor` MUST be a verbatim substring
  appearing exactly once in the SKILL.md text shown above."* After parsing
  the response, clauditor counts occurrences of every `anchor` in the
  current SKILL.md. If **any** edit fails (count != 1), the entire run
  fails with exit code 2; stderr lists each bad edit with its
  `motivated_by` ids and the observed count. No sidecar is published on
  failure — the user re-runs. **Why:** smallest moving parts, reuses the
  existing "graceful failure → report" pattern from `grader.py`, avoids
  the ambiguity of occurrence-index schemes.

- **DEC-007 — CLI surface (final).**
  ```
  clauditor suggest SKILL
    [--from-iteration N]    # source grade run; default = latest with grading.json
    [--with-transcripts]    # opt-in transcript bundling
    [--model M]             # default claude-sonnet-4-6
    [--json]                # print sidecar JSON to stdout instead of unified diff
    [-v/--verbose]          # extra stderr logging
  ```
  No `--output` (always writes to `.clauditor/suggestions/`). No
  `--apply` (ever). `--from-iteration` is unambiguous read semantics.

- **DEC-008 — Failure-mode exit code table.**
  | Scenario | Stderr | Exit |
  |---|---|---|
  | No grading.json for skill in any iteration | "Run `clauditor grade SKILL` first" | 1 |
  | Zero failing assertions AND all grading scores ≥ pass threshold | "No improvement suggestions: all signals passed." — Sonnet NOT called | 0 |
  | Anthropic API error | exception summary | 3 |
  | Sonnet returns non-JSON / schema-invalid | "Proposer returned unparseable JSON: …" — no sidecar | 1 |
  | Any anchor fails validation (count != 1) | list bad edits + motivated_by + observed count — no sidecar | 2 |
  | Success | diff (or JSON when `--json`) to stdout; write sidecar pair | 0 |

---

## Phase 4 — Detailed Breakdown

Architecture ordering: new `suggest.py` module layered bottom-up (loader →
prompt → call+parse → validate+render+persist) → CLI wiring → end-to-end
integration → Quality Gate → Patterns & Memory.

Every story's Acceptance Criteria includes `uv run ruff check src/ tests/`
and `uv run pytest --cov=clauditor --cov-report=term-missing` passing with
the 80% coverage gate intact.

---

### US-001 — Latest-run loader for suggest

**Description:** Add `src/clauditor/suggest.py` with a module-level
`_monotonic = time.monotonic` alias (per `monotonic-time-indirection.md`)
and a synchronous helper that locates the latest iteration containing
`<skill>/grading.json` and loads the signals clauditor will bundle.

**Traces to:** DEC-001, DEC-004, DEC-005, DEC-008 (cases 1 & 2).

**Files:**
- `src/clauditor/suggest.py` (new) — `@dataclass SuggestInput` with
  `skill_name`, `source_iteration`, `source_grading_path` (repo-relative
  str), `skill_md_text`, `failing_assertions: list[AssertionResult]`,
  `failing_grading_criteria: list[GradingResult]`, `output_slices`
  (optional, from `run-0/output.txt`), `transcript_events` (optional, from
  `run-0/output.jsonl` — only when `--with-transcripts`).
- `src/clauditor/suggest.py::find_latest_grading(clauditor_dir, skill)` —
  mirrors `cli.py::_find_prior_grading_json` (`cli.py:993`); returns
  `(iteration_index, skill_dir)` or raises `NoPriorGradeError`.
- `src/clauditor/suggest.py::load_suggest_input(skill, clauditor_dir,
  with_transcripts, from_iteration)` — composes the above.
- `tests/test_suggest.py` (new).

**TDD:**
- `TestFindLatestGrading::test_picks_max_index_with_grading_json`
- `TestFindLatestGrading::test_skips_iterations_without_grading`
- `TestFindLatestGrading::test_raises_when_no_iteration_exists`
- `TestLoadSuggestInput::test_filters_to_failing_assertions_only`
- `TestLoadSuggestInput::test_filters_to_failing_grading_criteria_only`
- `TestLoadSuggestInput::test_with_transcripts_reads_output_jsonl`
- `TestLoadSuggestInput::test_without_transcripts_omits_events`
- `TestLoadSuggestInput::test_from_iteration_overrides_latest`
- `TestLoadSuggestInput::test_zero_failures_sets_empty_lists` (the
  DEC-008 "no-op" path — caller short-circuits before calling Sonnet)

**Done when:** Tests green, ruff clean, coverage stays ≥80%.

**Depends on:** none.

---

### US-002 — Proposer prompt builder

**Description:** Build the Sonnet prompt from a `SuggestInput`. Applies
`llm-judge-prompt-injection.md` fencing: framing sentence outside tags,
custom XML tags for each untrusted block. SKILL.md is trusted and placed
in its own `<skill_md>` block with no "untrusted" framing. Instructs the
model to return JSON edits where every `anchor` is a verbatim substring
appearing **exactly once** in the SKILL.md shown above (the DEC-006
contract). Embeds the agentskills.io guidelines (generalize from
feedback, keep the skill lean, explain the why, bundle repeated work).

**Traces to:** DEC-002, DEC-003, DEC-004, DEC-006; rule
`llm-judge-prompt-injection.md`; rule `non-mutating-scrub.md` (transcript
bundle path calls `transcripts.redact()` and sends only the scrubbed copy).

**Files:**
- `src/clauditor/suggest.py::build_suggest_prompt(input: SuggestInput)
  -> str`.
- `tests/test_suggest.py::TestBuildPrompt`.

**TDD:**
- `test_framing_sentence_appears_before_first_untrusted_tag`
- `test_skill_md_not_wrapped_as_untrusted`
- `test_assertions_fenced_per_item` (each assertion in its own
  `<failing_assertion id="...">` tag)
- `test_grading_criteria_fenced_per_item`
- `test_anchor_contract_instruction_present` (string assertion on the
  "exactly once" phrase)
- `test_agentskills_guidelines_present`
- `test_transcripts_omitted_when_none`
- `test_transcripts_redacted_before_inclusion` (fixture contains a
  secret; assert it's masked in the built prompt AND the input object
  is untouched afterward — non-mutating invariant)
- `test_response_schema_instruction_present` (tells model to return JSON
  list of `{anchor, replacement, rationale, confidence, motivated_by}`)

**Done when:** Tests green; ruff clean; coverage ≥80%.

**Depends on:** US-001.

---

### US-003 — Sonnet call, response parse, anchor validation

**Description:** Async entrypoint that takes a `SuggestInput`, builds the
prompt, calls `AsyncAnthropic().messages.create(model="claude-sonnet-4-6",
max_tokens=4096, messages=[...])` (override via `model` param), parses
the response as structured edit proposals, and validates every anchor
appears **exactly once** in the current SKILL.md. Returns a
`SuggestReport` dataclass carrying the proposals, parse errors, anchor
validation errors, token usage, duration, and the source pointers.

**Traces to:** DEC-003, DEC-006, DEC-008 (cases 3, 4, 5); rules
`llm-judge-prompt-injection.md`, `monotonic-time-indirection.md`,
`positional-id-zip-validation.md` (ids are assigned positionally within
the response but validated length-equal; each proposal's
`motivated_by` is cross-checked against the `SuggestInput` stable ids
before emission).

**Files:**
- `src/clauditor/suggest.py::SuggestReport` dataclass —
  `schema_version=1`, `skill_name`, `model`, `generated_at`,
  `source_iteration`, `source_grading_path`, `input_tokens`,
  `output_tokens`, `duration_seconds`, `edit_proposals:
  list[EditProposal]`, `summary_rationale`, `validation_errors:
  list[str]`, `parse_error: str | None`.
- `src/clauditor/suggest.py::EditProposal` dataclass — `id` (positional
  `edit-N`), `anchor`, `replacement`, `rationale`, `confidence` (float
  0–1, clamped), `motivated_by` (list of stable ids), `applies_to_file`
  (literal `"SKILL.md"` in v1).
- `src/clauditor/suggest.py::parse_suggest_response(text, input)` —
  strips ```json fences; `json.loads`; validates top-level shape;
  constructs `EditProposal`s; raises on malformed but catches one level
  up to a `parse_error` field.
- `src/clauditor/suggest.py::validate_anchors(proposals, skill_md_text)`
  — for each, `skill_md_text.count(anchor)`; appends human-readable
  errors to a returned list when count != 1 (includes the count and the
  proposal's `motivated_by`).
- `src/clauditor/suggest.py::propose_edits(input, model=None)` — async;
  builds prompt, calls Anthropic, measures duration via `_monotonic()`,
  parses, validates, returns `SuggestReport`. Catches `json.JSONDecodeError`
  and Anthropic exceptions into the appropriate report fields — **does
  not raise**; the CLI layer maps to exit codes.
- `SuggestReport.to_json(self) -> str` — `schema_version: 1` as first key.
- `_check_schema_version` module-level helper mirroring
  `audit.py::_check_schema_version` for future readers.

**TDD:**
- `TestParseSuggestResponse::test_parses_well_formed_edits`
- `test_strips_markdown_json_fence`
- `test_malformed_json_sets_parse_error`
- `test_length_and_shape_validation`
- `test_motivated_by_ids_must_exist_in_input` (positional zip rule —
  reject ids the model invented)
- `TestValidateAnchors::test_valid_when_exactly_one_occurrence`
- `test_missing_anchor_records_error`
- `test_duplicate_anchor_records_error_with_count`
- `TestProposeEdits::test_calls_sonnet_with_built_prompt` (mock
  `AsyncAnthropic` per `test_quality_grader.py:442–490`)
- `test_sets_duration_from_monotonic_alias` (patches
  `clauditor.suggest._monotonic` with a side_effect list — the
  canonical shape from `monotonic-time-indirection.md`)
- `test_api_exception_captured_not_raised`
- `test_to_json_first_key_is_schema_version`
- `test_schema_version_loader_rejects_mismatch`

**Done when:** Tests green; ruff clean; coverage ≥80%.

**Depends on:** US-002.

---

### US-004 — Unified diff renderer + sidecar writer

**Description:** Given a valid `SuggestReport` (no parse/validation
errors), render a unified diff against SKILL.md by applying each edit's
`anchor → replacement` substitution in a **copy** (non-mutating), feed
the before/after text to `difflib.unified_diff`, and write the
`.clauditor/suggestions/<skill>-<ts>.json` and `.diff` sidecar pair.
Timestamp format `%Y%m%dT%H%M%S%fZ` (microseconds; precedent at
`cli.py:1903`). Skill name validated via
`workspace.validate_skill_name`. The suggestions dir is created if
missing.

**Traces to:** DEC-003, DEC-005, DEC-006; rules `json-schema-version.md`,
`non-mutating-scrub.md` (skill text is copied, never mutated, so a
subsequent diff render against the same input yields the same output),
`path-validation.md` (skill name containment).

**Files:**
- `src/clauditor/suggest.py::render_unified_diff(report, skill_md_text)
  -> str` — applies all edits to a copy, returns the unified diff text
  with `fromfile="SKILL.md"`, `tofile="SKILL.md (proposed)"`.
- `src/clauditor/suggest.py::write_sidecar(report, diff_text,
  clauditor_dir) -> tuple[Path, Path]` — creates
  `.clauditor/suggestions/`, writes `{skill}-{ts}.json` and
  `{skill}-{ts}.diff`, returns the two absolute paths. Uses the
  `generated_at` timestamp already on the report.
- `tests/test_suggest.py::TestRenderUnifiedDiff`,
  `TestWriteSidecar`.

**TDD:**
- `test_single_edit_produces_expected_hunk`
- `test_multiple_edits_apply_in_declaration_order`
- `test_render_does_not_mutate_input_skill_text`
- `test_sidecar_writes_both_files`
- `test_json_sidecar_first_key_is_schema_version`
- `test_filename_uses_microsecond_timestamp`
- `test_creates_suggestions_dir_if_missing`
- `test_skill_name_validation_rejects_traversal` (e.g. `../../etc`)

**Done when:** Tests green; ruff clean; coverage ≥80%.

**Depends on:** US-003.

---

### US-005 — `cmd_suggest` CLI wiring + failure-mode dispatch

**Description:** Add the `suggest` subparser to `cli.py` and implement
`cmd_suggest` as the sync entrypoint delegating to
`asyncio.run(_cmd_suggest_impl(args))`. The impl function orchestrates
US-001→US-004 and implements the DEC-008 exit-code table. Stderr
logging mirrors `cmd_grade` (bundle summary → model → tokens → duration
→ output paths). Stdout carries the unified diff (or the sidecar JSON
when `--json`). Short-circuits the Sonnet call when the loader reports
zero failing signals (DEC-008 row 2).

**Traces to:** DEC-001, DEC-005, DEC-007, DEC-008.

**Files:**
- `src/clauditor/cli.py` — new `p_suggest = subparsers.add_parser("suggest",
  ...)` block next to `grade`; `cmd_suggest(args)` function; `elif
  parsed.command == "suggest":` dispatch branch (near line 2328).
- `tests/test_cli.py` (extend) — `TestCmdSuggest` class.

**TDD:**
- `test_no_prior_grade_exits_1_with_message`
- `test_zero_failures_exits_0_without_calling_sonnet` (assert
  `AsyncAnthropic` is never constructed)
- `test_success_prints_diff_to_stdout_and_writes_sidecar`
- `test_json_flag_prints_sidecar_json_to_stdout`
- `test_with_transcripts_flag_propagates_to_loader`
- `test_from_iteration_overrides_latest`
- `test_anthropic_error_exits_3`
- `test_parse_error_exits_1_and_no_sidecar` (assert
  `.clauditor/suggestions/` is still empty after failure)
- `test_anchor_validation_error_exits_2_and_no_sidecar`
- `test_verbose_emits_stderr_bundle_summary`

**Done when:** Tests green; ruff clean; coverage ≥80%. Manual smoke
test: run `clauditor suggest <some-real-skill>` against a local fixture
grade run and confirm the diff is sensible.

**Depends on:** US-004.

---

### US-006 — Quality Gate

**Description:** Run the code-reviewer agent four times across the full
changeset, fixing every real bug found in each pass. Run CodeRabbit
review on the PR. Ensure `uv run ruff check src/ tests/` and `uv run
pytest --cov=clauditor --cov-report=term-missing` both pass with the
80% gate intact after all fixes.

**Traces to:** All stories above.

**Files:** Whatever the reviewers surface.

**Done when:** Four clean code-reviewer passes in a row; CodeRabbit has
no outstanding actionable comments; CI green; coverage ≥80%.

**Depends on:** US-005.

---

### US-007 — Patterns & Memory

**Description:** Capture any new reusable patterns that emerged during
#27 into `.claude/rules/`. Likely candidates:
- A rule on the "pre-LLM contract + post-LLM hard-validate" shape
  (DEC-006 strategy — the prompt asserts an invariant and the parser
  enforces it; failing the whole run is the canonical response).
- Possibly an update to `llm-judge-prompt-injection.md` noting the
  trusted-vs-untrusted distinction (SKILL.md trusted, skill *output*
  untrusted — this is the first time the distinction matters in the
  codebase).
Update `docs/` or `CLAUDE.md` only if the suggest command changes the
advertised workflow.

**Traces to:** DEC-006; experience gathered across US-001 through US-005.

**Files:** `.claude/rules/*.md`, possibly `docs/`, `CLAUDE.md`.

**Done when:** New rules committed; existing rules updated if relevant;
no-op is an acceptable outcome if nothing genuinely reusable emerged.

**Depends on:** US-006.

---

## Phase 5 — Publish PR

- **Commit:** `be77775` on `feature/27-suggest-proposer`
- **PR:** https://github.com/wjduenow/clauditor/pull/36 (draft)
- **Base:** `dev`

---

## Phase 7 — Beads Manifest (devolved 2026-04-15)

| ID | Title | Depends on |
|---|---|---|
| `clauditor-dlb` | #27 epic: LLM-driven skill improvement proposer | — |
| `clauditor-dlb.1` | US-001 — Latest-run loader + SuggestInput dataclass | — |
| `clauditor-dlb.2` | US-002 — Proposer prompt builder (XML fence + anchor contract) | `.1` |
| `clauditor-dlb.3` | US-003 — Sonnet call, response parse, anchor validation | `.2` |
| `clauditor-dlb.4` | US-004 — Unified diff renderer + sidecar writer | `.3` |
| `clauditor-dlb.5` | US-005 — `cmd_suggest` CLI wiring + DEC-008 exit codes | `.4` |
| `clauditor-dlb.6` | US-006 — Quality Gate (code review x4 + CodeRabbit) | `.5` |
| `clauditor-dlb.7` | US-007 — Patterns & Memory | `.6` |

**Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/27-suggest-proposer`
**Branch:** `feature/27-suggest-proposer`


