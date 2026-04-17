# Super Plan: #52 — `clauditor propose-eval` LLM-assisted eval spec bootstrapping

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/52
- **Branch:** `feature/52-propose-eval`
- **Worktree:** _n/a (working on branch directly)_
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/53
- **Sessions:** 1
- **Last session:** 2026-04-17

---

## Discovery

### Ticket Summary

**What:** Add a new CLI subcommand `clauditor propose-eval <skill.md>` that
reads the skill's SKILL.md content and a captured runtime output, asks
Sonnet to propose a skill-specific eval.json (assertions + sections +
grading_criteria), and emits JSON the author reviews and commits.

**Why:** The current path — `clauditor init` emits a one-size-fits-all
boilerplate, leaving the author to hand-craft assertions/sections/criteria
against a captured output. For the 3 eval specs we just deleted from
`~/Projects/my_claude_agent` (`find-restaurants`, `find-events`,
`find-kid-activities`), re-authoring by hand is ~30 min each. A working
LLM-proposed first pass turns it into additive edits.

**Done when:** `clauditor propose-eval .claude/commands/<skill>.md`
emits a `<skill>.eval.json` that loads cleanly via `EvalSpec.from_file()`
and that `clauditor validate` passes on the assertions it proposed.

### Key Finding — `suggest.py` is the canonical parallel

The existing `src/clauditor/suggest.py` (920-line module backed by
`cli/suggest.py`, 2173 tests) is the reference implementation for
"LLM-driven structured output with hard validation." The scope of
`propose-eval` lines up almost exactly:

| Concern | `suggest.py` pattern | `propose-eval` equivalent |
|---|---|---|
| Public entry | `async def propose_edits(suggest_input, *, model, max_tokens) -> SuggestReport` | `async def propose_eval(propose_input, *, model, max_tokens) -> ProposeEvalReport` |
| Prompt builder | `build_suggest_prompt(suggest_input) -> str`; fences untrusted in XML tags | `build_propose_eval_prompt(propose_input) -> str`; same fencing |
| Anthropic call | `_anthropic.call_anthropic(prompt, model, max_tokens)` | same helper (rule: `centralized-sdk-call.md`) |
| Response parser | `parse_suggest_response(text, suggest_input)`; ValueError on any structural issue | `parse_propose_eval_response(text, propose_input)`; same shape |
| Hard validator | `validate_anchors(...)`; whole-run failure | `EvalSpec.from_file()` on the generated JSON — hard-fail on ValueError |
| Sidecar shape | `SuggestReport` dataclass with `schema_version`, `generated_at`, tokens, duration, proposals, validation_errors, parse_error, api_error | parallel `ProposeEvalReport` |
| CLI wiring | `cli/suggest.py::_cmd_suggest_impl` async; exit-code mapping per DEC-008 | `cli/propose_eval.py` same shape |
| Tests | `tests/test_suggest.py`: prompt-builder anchors, parser hard-validation, anchor validation, CLI integration | same shape at `tests/test_propose_eval.py` |

### Applicable `.claude/rules/`

- **`pre-llm-contract-hard-validate.md`** — prompt states invariants
  (every assertion has `id`, every section uses `tiers[]`, every
  grading_criterion is `{id, criterion}`); parser hard-rejects any
  proposal that would fail `EvalSpec.from_file()`; whole-run failure,
  no partial artifact.
- **`llm-judge-prompt-injection.md`** — fence skill SKILL.md as **trusted**
  (author wrote it), captured output as **untrusted** (skill runtime
  produced it). Framing sentence lists only untrusted tag names.
- **`eval-spec-stable-ids.md`** — the LLM must emit stable `id` fields on
  every assertion, tier field, and grading_criterion. The existing
  `_require_id` in `schemas.py` catches missing/duplicate ids at load
  time.
- **`positional-id-zip-validation.md`** — the parser must NOT trust
  positional correspondence. Every `{id, ...}` entry comes back
  self-identified; the loader validates uniqueness per
  `eval-spec-stable-ids.md`.
- **`pure-compute-vs-io-split.md`** — prompt builder + response parser
  + validator are pure; loaders (SKILL.md + capture) and file writes
  live in the CLI layer.
- **`centralized-sdk-call.md`** — must route through `_anthropic.call_anthropic`;
  no direct AsyncAnthropic construction.
- **`readme-promotion-recipe.md`** — add to `docs/cli-reference.md` as
  reference; README teaser follows the D2/D3 tiering already
  established. Likely D2 lean since propose-eval is a one-shot
  workflow, not a return-trip reference.
- **`plan-contradiction-stop.md`** — worker discipline for Ralph stage.
- `json-schema-version.md` — **N/A**: `EvalSpec` doesn't declare
  `schema_version` today; adding it to propose-eval output would be
  scope creep.
- `path-validation.md`, `non-mutating-scrub.md`, `stream-json-schema.md`,
  `sidecar-during-staging.md`, `monotonic-time-indirection.md` — **N/A**.

### Proposed Scope

1. New pure module `src/clauditor/propose_eval.py` with:
   - `ProposeEvalInput` dataclass (skill_name, skill_md_text, capture_text, description_from_frontmatter, argument_hint_from_frontmatter)
   - `load_propose_eval_input(skill_path, capture_path) -> ProposeEvalInput`
   - `build_propose_eval_prompt(input) -> str`
   - `parse_propose_eval_response(text) -> dict` (raw parsed JSON, hard-rejects on structural issues)
   - `validate_proposed_spec(json_dict, skill_name) -> None` — runs the JSON through a temporary-file `EvalSpec.from_file()` round-trip to surface the exact same ValueError the CLI would hit
   - `propose_eval(input, *, model, max_tokens) -> ProposeEvalReport`
   - `ProposeEvalReport` dataclass with generated_at, model, tokens, duration, proposed_spec (dict), validation_errors, parse_error, api_error

2. New CLI subcommand `src/clauditor/cli/propose_eval.py`:
   - `clauditor propose-eval <skill.md>` — default flow
   - `--from-capture <path>` — override capture-file discovery
   - `--from-iteration N` — alternative: read from `.clauditor/iteration-N/<skill>/run-0/output.txt`
   - `--dry-run` — print the prompt; no Anthropic call
   - `--force` — overwrite existing `<skill>.eval.json`
   - `--model <name>` — override default
   - `--json` — print the sidecar JSON to stdout instead of writing

3. Tests `tests/test_propose_eval.py` mirroring `tests/test_suggest.py` shape.

4. Documentation update: `docs/cli-reference.md` subcommand entry + README teaser.

### Scoping Questions

**Q1 — Capture discovery strategy.**
- **A.** Only `tests/eval/captured/<skill>.txt`; error clearly if absent. Matches `capture.py` default.
- **B.** `tests/eval/captured/<skill>.txt` first, then fall back to latest `.clauditor/iteration-N/<skill>/run-0/output.txt`. Two paths, more user-friendly.
- **C.** Require explicit `--from-capture` or `--from-iteration`; no default search.

**Q2 — Layers proposed by the LLM.**
- **A.** Full 3-layer (L1 assertions + L2 sections/fields + L3 grading_criteria) + `test_args` suggestion.
- **B.** L1 + L3 only; skip L2 (section extraction from a single capture is fragile — one output may show 3 venues, next may show 0).
- **C.** L1 only (most conservative; L2/L3 left to hand-authoring).

**Q3 — Existing eval.json handling.**
- **A.** Match `init.py` — error unless `--force`; with `--force`, silently overwrite. Author uses git diff to review.
- **B.** Emit to `<skill>.eval.proposed.json` alongside the existing. Author reviews, manually renames/merges.
- **C.** Stdout-only by default (`--json`); file write only with `--write` flag.

**Q4 — SKILL.md content passed to LLM.**
- **A.** Raw file contents (frontmatter + body).
- **B.** Parsed: just the `description` + `argument-hint` from frontmatter + the body. Cleaner signal but needs a tiny YAML parser (or reuse the one in `scripts/validate_skill_frontmatter.py`).
- **C.** Body only, no frontmatter (risk: LLM misses intent signal the description provides).

**Q5 — Capture size handling.**
- **A.** Pass as-is; hard error if > ~50k tokens.
- **B.** Head + tail truncation with `...elided N chars...` marker when > threshold.
- **C.** Accept any size; let `call_anthropic` raise a 400-too-long error and funnel to `api_error`.

**Q6 — Exit code mapping.**
- **A.** Mirror `suggest.py` DEC-008 exactly: `api_error → 3`, `parse_error → 1`, `validation_errors → 2`, success → 0.
- **B.** Simpler: any error → 1, success → 0.
- **C.** Custom mapping (specify).

---

## Architecture Review

### Resolved Scoping Choices (from Discovery)

| Q | Choice | Intent |
|---|---|---|
| Q1 | B | Capture discovery: `tests/eval/captured/<skill>.txt` → fallback to latest `.clauditor/iteration-N/<skill>/run-0/output.txt` |
| Q2 | A | Full 3-layer proposal (L1 + L2 + L3 + `test_args`) |
| Q3 | A | Match `init.py` semantics: error unless `--force` |
| Q4 | B | Parsed frontmatter: `description` + `argument-hint` + body, via a reused small YAML parser |
| Q5 | A | Pass capture as-is; hard error if > ~50k tokens (rough `len/4` heuristic pre-call) |
| Q6 | A | Mirror `suggest.py` DEC-008 exit codes: api→3, parse→1, validation→2, success→0 |

### Review Summary

| Area | Verdict | Notes |
|---|---|---|
| Security — prompt injection | pass | Plan already honors `llm-judge-prompt-injection.md`: SKILL.md is trusted (author wrote it), capture is untrusted (skill runtime produced it). Framing lists only untrusted tag names. |
| Security — API key handling | pass | Routes through `_anthropic.call_anthropic` per `centralized-sdk-call.md`; the helper owns `ANTHROPIC_API_KEY` lookup + error categorization. |
| Security — secrets in capture | concern | A captured skill output could contain an API key echoed in an error, a Bearer token in a header dump, etc. That would get sent to Anthropic in the prompt. We already have `transcripts.redact()` for exactly this. **Decision needed:** scrub the capture before fencing into the prompt? |
| Security — secrets in proposed spec | pass | LLM output is deterministic assertion patterns (e.g. `contains "Results"`). Unlikely path to echo an input secret into the proposed spec — but belt-and-suspenders: scrub the sidecar output too (`SuggestReport.to_json()` already does this for `api_error` post-bead 8l0; mirror the pattern). |
| Performance — token budget | concern | Per Q5=A: hard-error at ~50k tokens. Pre-call estimate via `len(prompt) / 4` heuristic. No token counter dep; accept ±20% slop on the threshold. Over-budget prompts fail fast, not mid-stream. |
| Performance — Anthropic latency | pass | Single API call, Sonnet default, `max_tokens=4096`. Same shape as `suggest.py` — no reason to expect different latency profile (~5-15s). |
| Data model — proposed JSON matches EvalSpec schema | **BLOCKER** | The validator must hard-fail on any malformed proposal. `EvalSpec.from_file()` takes a path, not a dict. We need either (a) a tempfile round-trip or (b) a new `EvalSpec.from_dict()` / `validate_dict()` entry point. The tempfile approach is ugly and has a cleanup dance; the `from_dict` extraction is the clean fix. |
| Data model — stable-ids invariant | pass | The prompt explicitly instructs the LLM to emit `id` on every assertion/field/criterion. The hard validator (`EvalSpec.from_file` via `_require_id`) catches any omissions. |
| Data model — tiered-sections invariant | pass | Prompt shows the `sections[].tiers[].fields[]` shape. Parser/validator catches the old flat shape via ValueError from `schemas.py:283-292`. |
| API design — CLI surface | pass | Mirrors `suggest.py`'s flag set: `--from-capture`, `--from-iteration`, `--dry-run`, `--force`, `--model`, `--json`. No new conventions. |
| Observability — progress reporting | pass | `suggest.py` has a `-v` verbose flag that logs bundle/token stats. Same pattern here. Stderr progress line ("Calling {model}…") for non-`--json` runs. |
| Observability — error messages | pass | Validation errors surface on stderr with the exact `ValueError` text from `EvalSpec.from_file()`; `--json` output keeps them in `validation_errors: []`. Matches `suggest.py` shape. |
| Testing — prompt-builder anchors | pass | `test_propose_eval.py` mirrors `test_suggest.py`: grep for "exactly once" analog phrase, verify framing sentence before first untrusted tag, assert SKILL.md content NOT in untrusted list. |
| Testing — parser hard-validation | pass | ValueError on missing keys, wrong types, duplicate ids; matches `TestParseSuggestResponse`. |
| Testing — CLI integration | pass | Mock `propose_eval` at the CLI boundary; verify DEC-008 exit codes, `--force` overwrite behavior, `--dry-run` prints prompt. Use `_mock_anthropic_result()` helper from `test_suggest.py`. |
| Frontmatter parsing reuse (Q4=B) | concern | `scripts/validate_skill_frontmatter.py` has a minimal YAML parser (~60 lines). Two options: (a) promote it to a shared `clauditor._frontmatter` module; (b) copy the logic inline in `propose_eval.py`. Scope decision. |

### Blocker detail

**BLOCKER — validator entry point.**
`EvalSpec.from_file()` reads JSON from disk. `propose-eval` has the
proposal as an in-memory dict. Calling through a tempfile works but is
awkward (temp dir, write, read-back, cleanup, `input_files` paths
relative to spec_dir break). The clean fix is to extract a
`from_dict()` classmethod on `EvalSpec` that takes `(spec_dict,
spec_dir)` and does the same validation, with `from_file()` becoming
a thin wrapper: `json.load` + `from_dict`.

Cost: ~30 lines of refactor in `schemas.py`, no behavior change to
existing callers, test coverage preserved by existing `test_schemas.py`.

This is a blocker only in the sense that it gates clean implementation;
the feature can technically ship with the tempfile workaround but the
resulting code would be fragile and hard to test. Decide now, not later.

### Concerns (to resolve in refinement)

1. **Scrub capture before fencing into prompt?** Captured outputs can
   contain secrets (Bearer tokens, API keys echoed in errors, etc.)
   that would otherwise be sent to Anthropic. `transcripts.redact()`
   already handles this for execution transcripts. Apply it here too?
2. **Scrub proposed spec before writing to disk?** Lower risk (the LLM
   generates assertion patterns, not raw input echo), but the
   post-`clauditor-8l0` pattern in `SuggestReport.to_json()` scrubs
   `api_error` anyway — mirror here for consistency.
3. **Frontmatter parser: promote or duplicate?** `scripts/validate_skill_frontmatter.py` has the logic. Share or copy.
4. **Token budget heuristic vs real counter?** `len(prompt) / 4` is
   rough. Good enough for a safety check, or worth pulling in a token
   counter dependency?

---

## Refinement Log

### Resolved (full ledger)

| Q | Choice | Intent |
|---|---|---|
| Q1 | B | Capture discovery: `tests/eval/captured/<skill>.txt` → fallback to latest iteration's `run-0/output.txt` |
| Q2 | A | Full 3-layer proposal (L1 + L2 + L3 + `test_args`) |
| Q3 | A | Match `init.py`: error unless `--force` |
| Q4 | B | Parsed frontmatter: `description` + `argument-hint` + body |
| Q5 | A | Pass capture as-is; hard error if > 50k tokens (via `len/4` heuristic) |
| Q6 | A | Mirror `suggest.py` DEC-008 exit codes |
| R-A | A1 | Extract `EvalSpec.from_dict()` now as part of this ticket |
| R-B | B1 | Scrub capture via `transcripts.redact()` before fencing into prompt |
| R-C | C1 | Scrub proposed sidecar on write (mirror `SuggestReport.to_json()`) |
| R-D | D1 | Promote frontmatter parser to `src/clauditor/_frontmatter.py` shared module |
| R-E | E1 | `len(prompt)/4` heuristic for 50k token cap |

### Decisions

**DEC-001 — Capture discovery: primary + iteration fallback.**
`propose_eval` looks for `tests/eval/captured/<skill>.txt` first; if
absent, searches `.clauditor/iteration-N/<skill>/run-0/output.txt` for
the highest N. `--from-capture` and `--from-iteration` override. If
neither location has a capture, exit 2 with a clear error pointing at
`clauditor capture <skill>`.

**DEC-002 — Full 3-layer proposal.**
The LLM is asked to propose `test_args`, `assertions[]`, `sections[].tiers[].fields[]`,
`grading_criteria[]`, `grading_model`. Not proposed: `input_files`,
`output_file/output_files`, `trigger_tests`, `variance` — those are
explicit domain choices, not inferable from a single capture.

**DEC-003 — Collision semantics match `init.py`.**
`<skill>.eval.json` already exists → exit 1 with stderr `ERROR:
<path> already exists. Use --force to overwrite.`. With `--force`,
silently overwrite. Author reviews the diff via git.

**DEC-004 — Parsed frontmatter input.**
The LLM receives, as separate fenced sections:
- `<skill_description>` — `description` from SKILL.md frontmatter
- `<skill_argument_hint>` — `argument-hint` from frontmatter (if present)
- `<skill_body>` — the markdown body (frontmatter stripped)

All three are trusted content (SKILL.md author wrote it); no
"untrusted data" framing for these tags.

**DEC-005 — Token-budget safety valve.**
Before calling Anthropic, compute `estimated_tokens = len(prompt) / 4`.
If `> 50_000`, emit `ERROR: estimated prompt size {N} tokens exceeds
50000-token safety cap. Reduce capture size or slice before running.`
and exit 2. No truncation; the user picks which content to cut.

**DEC-006 — Exit codes mirror `suggest.py` DEC-008.**
- 0 = success (sidecar written or `--json` printed)
- 1 = `parse_error` or load-time business error (existing file without `--force`, parser ValueError)
- 2 = `validation_errors` (proposed spec fails `EvalSpec.from_dict()`) OR pre-call input errors (capture not found, oversize token budget, missing model)
- 3 = `api_error` (Anthropic failure surfaced through `AnthropicHelperError`)

**DEC-007 — Extract `EvalSpec.from_dict(spec_dict, spec_dir)`.**
Refactor `EvalSpec.from_file()` into `json.load` + `from_dict()`. The
classmethod `from_dict(cls, data: dict, spec_dir: Path) -> EvalSpec`
holds all validation logic currently in `from_file`. Existing
`from_file()` becomes: `with path.open() as f: data = json.load(f);
return cls.from_dict(data, path.parent)`. No behavior change for
existing callers. New in-memory validator path for `propose_eval`
catches the same `ValueError`.

**DEC-008 — Scrub capture before prompt.**
`load_propose_eval_input(skill_path, capture_path)` calls
`transcripts.redact(capture_text)` and uses the scrubbed copy for
the prompt. In-memory `ProposeEvalInput.capture_text` stores the
scrubbed copy (non-mutating: the source file on disk is untouched).
Count of redactions is included in verbose output.

**DEC-009 — Scrub sidecar on write.**
`ProposeEvalReport.to_json()` runs the full payload through
`transcripts.redact()` before emitting. Mirrors `SuggestReport.to_json()`
scrubbing `api_error` post-clauditor-8l0. In-memory report stays
full-fidelity.

**DEC-010 — Promote frontmatter parser to `src/clauditor/_frontmatter.py`.**
Move the YAML-ish parser from `scripts/validate_skill_frontmatter.py`
into a new pure module `_frontmatter.py` exposing:
- `parse_frontmatter(text: str) -> tuple[dict | None, str]` — returns
  (parsed_frontmatter_dict, body_text)
- Raises `ValueError` on malformed frontmatter (missing delimiters,
  YAML parse errors on the restricted grammar we support)

Update `scripts/validate_skill_frontmatter.py` to import from the
new module. `propose_eval.py` imports too. New tests in
`tests/test_frontmatter.py` cover the parser directly; existing
`tests/test_skill_validator.py` still passes end-to-end.

**DEC-011 — `len/4` token estimate.**
Pre-call check: `estimated_tokens = (len(prompt) + 3) // 4`. This
overshoots Claude's tokenizer by ~20% on English prose (Claude tends
to pack tokens tighter than GPT-family). Acceptable slop for a
safety-valve threshold. No new dep.

---

## Detailed Breakdown

Natural ordering: foundational refactors first (`EvalSpec.from_dict`
+ shared `_frontmatter`), then the pure compute module, then CLI
glue, then docs, then QG + patterns.

**Validation command** for every story: `uv run ruff check src/ tests/
scripts/ && uv run pytest --cov=clauditor --cov-report=term-missing`
(80% global gate).

---

### US-001 — Extract `EvalSpec.from_dict()` + tests

**Description:** Refactor `EvalSpec.from_file()` in `src/clauditor/schemas.py`
into two layers: `from_dict(cls, data: dict, spec_dir: Path) -> EvalSpec`
holds the full validation logic, and `from_file()` becomes a thin
wrapper (`json.load` + `from_dict`). This unblocks `propose_eval`
from doing tempfile acrobatics for hard-validation.

**Traces to:** DEC-007

**Acceptance:**
- `EvalSpec.from_dict(data, spec_dir)` is a public classmethod that
  runs every validation currently in `from_file()` (id uniqueness,
  tiered sections shape, `input_files` path resolution, etc.) and
  raises identical `ValueError` messages.
- `EvalSpec.from_file(path)` delegates to `from_dict` after loading
  JSON. Existing callers (`suggest.py`, `cli/grade.py`, tests) still
  work without modification.
- `tests/test_schemas.py` gains direct tests for `from_dict` covering
  the full error matrix already tested against `from_file`; existing
  `from_file` tests still pass.
- Coverage on `schemas.py` ≥ existing baseline (shouldn't drop).

**Done when:** all existing tests pass; new `from_dict` tests cover
every ValueError branch directly.

**Files:**
- `src/clauditor/schemas.py` (refactor)
- `tests/test_schemas.py` (new tests for `from_dict`)

**Depends on:** none

**TDD:** yes — test-first is natural here. Write `from_dict` tests
that fail, extract the method to make them pass, confirm `from_file`
tests still pass.

---

### US-002 — Promote frontmatter parser to `src/clauditor/_frontmatter.py`

**Description:** Extract the YAML-ish frontmatter parser from
`scripts/validate_skill_frontmatter.py` into a new pure module
`src/clauditor/_frontmatter.py`. Update the validator script to
import from the new module. Positions the parser for reuse by
`propose_eval` and any future skill-linter.

**Traces to:** DEC-010

**Acceptance:**
- New module `src/clauditor/_frontmatter.py` with:
  - `parse_frontmatter(text: str) -> tuple[dict | None, str]` returning
    `(parsed_frontmatter, body_text)`. `None` for frontmatter when the
    file has no `---` delimiter block.
  - Raises `ValueError` on malformed frontmatter (mismatched delimiters,
    invalid YAML-subset entries).
  - Supports the subset used by real SKILL.md files: top-level scalars
    (strings), nested mapping under `metadata:`, inline lists for
    `allowed-tools` values. No PyYAML dep — keep the hand-rolled
    grammar limited to what current skills use.
- `scripts/validate_skill_frontmatter.py` imports from the new module.
  Its existing CLI behavior is preserved (CI workflow keeps working).
- New `tests/test_frontmatter.py` with parser-direct tests covering:
  - No frontmatter → returns `(None, text)`.
  - Valid frontmatter + body → returns `({...parsed}, body)`.
  - Missing closing delimiter → `ValueError`.
  - Malformed YAML line → `ValueError`.
  - Nested `metadata:` block parses into a dict.
- Existing `tests/test_skill_validator.py` still passes end-to-end
  (imports chain now goes through the new module).

**Done when:** parser lives in `src/clauditor/_frontmatter.py`,
validator script imports from it, both test files are green.

**Files:**
- `src/clauditor/_frontmatter.py` (new)
- `scripts/validate_skill_frontmatter.py` (refactor imports)
- `tests/test_frontmatter.py` (new)

**Depends on:** none

**TDD:** yes — port tests one at a time from the validator script's
inline parser, then lift the implementation.

---

### US-003 — Pure `propose_eval` module

**Description:** Core feature logic. A new pure module
`src/clauditor/propose_eval.py` exposing the full pipeline: input
loading, prompt building, Anthropic call, response parsing, hard
validation against `EvalSpec.from_dict` (US-001), sidecar scrub.
No file I/O in the public-facing functions — caller owns writes.

**Traces to:** DEC-001, DEC-002, DEC-004, DEC-005, DEC-008, DEC-009, DEC-011

**Acceptance:**
- Module exposes:
  - `ProposeEvalInput` dataclass: `skill_name`, `skill_md_body`,
    `frontmatter` (dict), `capture_text` (scrubbed), `capture_source`
    (path or iteration ref string), `redaction_count` (int from
    the scrub).
  - `load_propose_eval_input(skill_path, *, from_capture=None,
    from_iteration=None) -> ProposeEvalInput` — reads SKILL.md,
    splits frontmatter via `_frontmatter.parse_frontmatter`, discovers
    capture per DEC-001, scrubs capture via `transcripts.redact`.
  - `build_propose_eval_prompt(input) -> str` — XML-fenced prompt
    following `llm-judge-prompt-injection.md`: trusted
    `<skill_description>`, `<skill_argument_hint>`, `<skill_body>`;
    untrusted `<skill_output>` (the scrubbed capture). Framing
    sentence lists only `<skill_output>` as untrusted. Prompt
    includes a load-bearing "every entry must have a unique `id`
    string" invariant phrase so the prompt-builder test can anchor
    on it (per `pre-llm-contract-hard-validate.md`).
  - `parse_propose_eval_response(text) -> dict` — strips markdown
    fence if present, `json.loads`, returns the raw proposed-spec
    dict. Raises `ValueError` on non-dict top level or missing
    required top-level keys (`assertions`, `sections`,
    `grading_criteria`).
  - `validate_proposed_spec(spec_dict, spec_dir) -> EvalSpec` —
    thin wrapper around `EvalSpec.from_dict` that tags any
    `ValueError` with `"proposed spec invalid: "` prefix so CLI
    error messages are clear.
  - `ProposeEvalReport` dataclass — `generated_at`, `model`,
    `skill_name`, `capture_source`, `redaction_count`,
    `input_tokens`, `output_tokens`, `duration_seconds`,
    `proposed_spec` (dict), `validation_errors` (list[str]),
    `parse_error` (str | None), `api_error` (str | None).
  - `ProposeEvalReport.to_json() -> str` — runs the full payload
    through `transcripts.redact()` before `json.dumps`.
  - `async def propose_eval(input, *, model, max_tokens=4096) ->
    ProposeEvalReport` — the async orchestrator. Never raises.
    Failures become report fields (`api_error`, `parse_error`,
    `validation_errors`).
  - Token-budget check (DEC-005/DEC-011): `estimated_tokens =
    (len(prompt) + 3) // 4`. If > 50000, return a report with
    `parse_error = "estimated prompt size {N} tokens exceeds 50000"`
    before calling Anthropic.
- All module-level I/O-looking reads (`Path.read_text`, glob) live
  in `load_propose_eval_input`; the rest is pure.
- Uses `_anthropic.call_anthropic` (DEC-007's rule:
  `centralized-sdk-call.md`).
- New `tests/test_propose_eval.py` with classes mirroring
  `test_suggest.py`:
  - `TestLoadProposeEvalInput` — primary + fallback capture
    discovery (DEC-001), missing-both error, frontmatter parse,
    scrub count reporting.
  - `TestBuildProposeEvalPrompt` — framing sentence before first
    untrusted tag; `<skill_body>` not in untrusted list;
    "every entry must have a unique `id`" phrase present; tiered
    sections shape explained.
  - `TestParseProposeEvalResponse` — markdown-fence strip,
    well-formed envelope parses, ValueError on non-dict top level,
    ValueError on missing keys.
  - `TestValidateProposedSpec` — passes-through valid spec, raises
    with "proposed spec invalid:" prefix on id-missing/tier-missing.
  - `TestProposeEval` — mocks `call_anthropic`; full-pipeline
    happy path; api_error path; oversize token-budget path.
  - `TestProposeEvalReportToJson` — scrubs sensitive content;
    schema_version absence (matches EvalSpec); `api_error` scrubbed.

**Done when:** the module ships with ≥95% coverage; all test classes
above pass; `propose_eval.propose_eval(...)` returns a valid report
end-to-end under a mocked `call_anthropic`.

**Files:**
- `src/clauditor/propose_eval.py` (new)
- `tests/test_propose_eval.py` (new)

**Depends on:** US-001 (needs `EvalSpec.from_dict`), US-002 (needs
`_frontmatter.parse_frontmatter`)

**TDD:** yes — prompt-builder invariants + parser hard-validation
tests first; then `propose_eval()` orchestrator with a mocked call.

---

### US-004 — CLI subcommand `cli/propose_eval.py`

**Description:** Wire the pure module into a new CLI subcommand
`clauditor propose-eval <skill.md>` following the same shape as
`cli/suggest.py`. Handles flag parsing, file I/O, exit-code mapping
per DEC-006, stderr progress reporting, `--force` collision semantics
per DEC-003, `--dry-run` prompt-print behavior, `--json` stdout vs
sidecar-write routing.

**Traces to:** DEC-003, DEC-006

**Acceptance:**
- `src/clauditor/cli/propose_eval.py` exposes `cmd_propose_eval(args)`
  and `add_parser(subparsers)`.
- Flags: `skill` (positional), `--from-capture`, `--from-iteration`,
  `--force`, `--dry-run`, `--model`, `--json`, `-v/--verbose`.
- Default writes `<skill>.eval.json` next to the skill file. With
  `--json`, prints the sidecar content to stdout. With `--dry-run`,
  prints the built prompt and exits 0 without calling Anthropic.
- Collision: existing target file + no `--force` → exit 1, stderr
  message matching `cli/init.py`.
- Exit code mapping per DEC-006. Every branch has a test.
- Subparser registered in `src/clauditor/cli/__init__.py` dispatcher
  (mirrors the pattern of every other subcommand).
- Verbose mode prints: capture source, redaction count, model name,
  estimated tokens, actual input/output tokens from the Anthropic
  response.
- Tests in `tests/test_cli.py`: happy path (mock `propose_eval`
  returns success, verify file written), `--dry-run` (no call, prompt
  printed), `--force` (overwrites), no-force-collision (exit 1),
  `api_error` branch (exit 3), `parse_error` branch (exit 1),
  `validation_errors` branch (exit 2), `--json` (stdout), `--from-capture`
  override, `--from-iteration` override.

**Done when:** `uv run clauditor propose-eval --help` shows the new
subcommand; running it end-to-end against a real skill with a
captured output produces a file the `EvalSpec.from_file` loader
accepts.

**Files:**
- `src/clauditor/cli/propose_eval.py` (new)
- `src/clauditor/cli/__init__.py` (register subparser + dispatch)
- `tests/test_cli.py` (new `TestCmdProposeEval` class)

**Depends on:** US-003

---

### US-005 — Documentation

**Description:** Document the new subcommand in the existing docs
surface per `readme-promotion-recipe.md`. D2 lean teaser in the
root README's CLI section; full reference in `docs/cli-reference.md`.

**Traces to:** DEC-001..DEC-011 (all user-visible)

**Acceptance:**
- `README.md` CLI Reference section gets one `clauditor propose-eval
  <skill.md>` line added to the command list.
- `docs/cli-reference.md` gains a `## propose-eval` subsection
  covering: purpose, required inputs (skill path + capture), flag
  list with examples, exit codes, relationship to `init` and
  `capture`. Token-budget note. Security note on scrubbing.
- `CHANGELOG.md` gains an "Added" entry under `[Unreleased]`.
- Root README line budget (DEC-011 of #47) stays ≤165.
- Ruff + pytest pass (docs-only smoke check).

**Done when:** `uv run clauditor propose-eval --help` matches the
README entry; docs render correctly on GitHub.

**Files:**
- `README.md` (one line added to the CLI teaser)
- `docs/cli-reference.md` (new subsection)
- `CHANGELOG.md` (Added entry)

**Depends on:** US-004

---

### US-006 — Quality Gate

**Description:** 4 code-reviewer passes, CodeRabbit run,
validation-gate confirmation across the full changeset.

**Traces to:** all implementation DECs

**Acceptance:**
- 4 reviewer passes; every real finding fixed.
- CodeRabbit `--plain --base dev --type committed` run; findings
  addressed (or rate limit acknowledged).
- `uv run ruff check src/ tests/ scripts/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` green
  with ≥80% global coverage; propose_eval.py and frontmatter.py
  individually at ≥95%.
- Manual dogfood: run `propose-eval` against a real
  `my_claude_agent` skill capture (if available) and confirm the
  produced JSON loads via `EvalSpec.from_file`.

**Done when:** reviewer passes clean, validation gate green,
CodeRabbit threads resolved on PR.

**Depends on:** US-001..US-005

---

### US-007 — Patterns & Memory

**Description:** Distill any reusable patterns from this
implementation into `.claude/rules/` or `bd remember`.

**Traces to:** novel patterns surfaced during implementation

**Acceptance:**
- Candidate rule: "LLM proposes JSON that must load through an
  existing dataclass validator — route through the dataclass's
  `from_dict` path, don't tempfile-roundtrip." Worthy of a new rule?
  Judge during implementation; the `pre-llm-contract-hard-validate`
  rule may already cover it sufficiently.
- Candidate rule: "Subset-YAML parser as a shared module, not a
  script-local helper." Likely `bd remember` insight rather than a
  rule (small pattern, one-line learning).
- `bd remember` any ephemeral insights.

**Done when:** rule updates committed (if warranted), memory recorded.

**Depends on:** US-006

---

### Dependency graph

```
US-001 (EvalSpec.from_dict) ─┐
                              │
US-002 (_frontmatter module) ─┤
                              ├─► US-003 (pure propose_eval) ─► US-004 (CLI) ─► US-005 (docs)
                                                                                    │
                                                                                    ▼
                                                                             US-006 (QG) ─► US-007 (P&M)
```

US-001 and US-002 run in parallel. US-003 is the integration point.

---

## Beads Manifest

- **Epic:** `clauditor-2ri`
- **Branch:** `feature/52-propose-eval`
- **PR:** https://github.com/wjduenow/clauditor/pull/53

| Story | Bead ID | Depends on | Ready |
|---|---|---|---|
| US-001 `EvalSpec.from_dict` extraction | `clauditor-2ri.1` | — | ✅ |
| US-002 `_frontmatter` shared module | `clauditor-2ri.2` | — | ✅ |
| US-003 pure `propose_eval` | `clauditor-2ri.3` | US-001, US-002 | |
| US-004 CLI subcommand | `clauditor-2ri.4` | US-003 | |
| US-005 documentation | `clauditor-2ri.5` | US-004 | |
| US-006 Quality Gate | `clauditor-2ri.6` | US-001..US-005 | |
| US-007 Patterns & Memory | `clauditor-2ri.7` (P4) | US-006 | |

10 dependency edges wired. Kickoff set:
`{clauditor-2ri.1, clauditor-2ri.2}` run in parallel.

---

## Session Notes

### Session 1 — 2026-04-17

Discovery complete. Scouts surfaced:
- `suggest.py` + `cli/suggest.py` + `tests/test_suggest.py` as a
  near-perfect parallel for this feature. The full LLM-structured-output
  pipeline (prompt builder → centralized Anthropic call → response
  parser → hard validator → sidecar) ships today; reuse the shape.
- `_anthropic.call_anthropic` is the mandatory SDK entry per the rule
  `centralized-sdk-call.md`.
- `EvalSpec.from_file()` is the ground-truth validator; hard-failing
  on its ValueError satisfies `pre-llm-contract-hard-validate.md`.
- 8 rules apply, 5 are N/A. No `workflow-project.md` exists.

Awaiting user answers on Q1-Q6.
