# Super Plan: Issue #12 — Tiered Section Validation

## Meta

| Field         | Value                                      |
|---------------|--------------------------------------------|
| Ticket        | wjduenow/clauditor#12                      |
| Branch        | `feature/12-tiered-section-validation`     |
| Worktree      | TBD                                        |
| Phase         | detailing                                  |
| Created       | 2026-04-10                                 |
| Sessions      | 1                                          |

---

## Discovery

### Ticket Summary

Support primary and secondary entry tiers within a section schema, with different required field sets per tier. Originated from `/find-restaurants` eval where 3 honorable mentions (abbreviated one-liners) failed 7 field checks, dropping score to 84% despite perfect main results. Current workaround: making fields globally optional, which weakens validation for main entries.

### User Concern

**The ticket may be too specific to the restaurant test case.** The proposed JSON schema (with `"main"` and `"honorable_mentions"` tier labels, LLM classification, per-tier field requirements) is heavy machinery for what may be a narrow problem.

### Codebase Findings

**Current schema flow:**
- `SectionRequirement` has a flat `fields: list[FieldRequirement]` — all entries validated identically
- `FieldRequirement` has `required: bool` — binary, no nuance
- `grade_extraction()` iterates entries uniformly, checking required fields on each
- `build_extraction_prompt()` generates a single JSON schema per section, no tier awareness
- `min_entries` is per-section, not per-tier

**Key files:**
- `src/clauditor/schemas.py` — `SectionRequirement`, `FieldRequirement`, `EvalSpec`
- `src/clauditor/grader.py` — `grade_extraction()`, `build_extraction_prompt()`, `extract_and_grade()`
- `tests/test_grader.py` — Grader tests (267 lines)
- `tests/test_schemas.py` — Schema roundtrip tests (583 lines)

**The core tension:** Entries beyond `min_entries` are "bonus" results the LLM found, but they're validated with the same strictness as the primary entries. This creates a perverse incentive: the more results the LLM finds, the more likely it is to fail.

---

## Scoping Answers

- **Q1 (B) Emerging pattern** — Other evals will produce mixed-quality entries. Worth a general solution.
- **Q2 (B) Full tiers** — Named tiers with independent field sets and `min_entries`. LLM classifies entries into tiers.
- **Q3 (A) Critical** — Eval report must clearly distinguish "main entry passed" from "bonus entry passed" in output.
- **Q4 (A) Build first** — The weakened schema (globally optional fields) is unacceptable. Build the real solution before shipping.

---

## Architecture Review

| Area                   | Rating  | Notes |
|------------------------|---------|-------|
| Security               | pass    | No new user input vectors. Tier labels come from eval spec (author-controlled), not user input. |
| Performance            | pass    | Same single Haiku call. Slightly more complex prompt but no additional API calls. |
| Data Model             | concern | `to_dict()` must emit either `tiers` or `fields`, never both. Round-trip needs care. Tier label uniqueness should be validated. |
| API Design             | pass    | CLI commands unchanged. Tier info appears in grading output/assertion names. |
| Observability          | pass    | Assertion names will include tier: `section:Venues/main[0].address`. Clear in reports. |
| Testing                | concern | Need assertion name format tests, unknown-tier handling, zero-min_entries tier edge case. |
| LLM Classification     | concern | Haiku must interpret prose to classify entries into tiers — contradicts current "do not interpret" prompt rule. Accuracy risk with varied phrasing. |
| Extraction Parsing     | blocker | Current parser (`isinstance(entries_data, list)`) silently drops dict values. Tiered output is `{"Section": {"tier": [...]}}` — would produce zero entries with no error. Must handle both shapes. |
| Extraction Prompt      | blocker | `build_extraction_prompt()` generates flat JSON schema. Must be redesigned for tiered output. Conflicting instructions (extract raw vs classify into tiers). |
| Backward Compatibility | concern | `fields` without `tiers` must still work. `from_file()` must detect and normalize. `to_dict()` must match source shape. |

### Blocker Resolution Required

**B1: Extraction parsing** — Parser must handle both flat (`[entries]`) and tiered (`{"tier": [entries]}`) shapes. Flat = legacy/no-tiers sections. Tiered = sections with tiers defined.

**B2: Extraction prompt** — Tiered sections need a different prompt structure. The "do not interpret" rule must be relaxed for tier classification while keeping it for field values. Prompt must include tier definitions and expected JSON shape.

---

## Refinement Log

### Decisions

**DEC-001: No backward compatibility required**
Module is not in production. All changes can be breaking. No dual-path serialization, no legacy format preservation.

**DEC-002: `fields` is sugar for a single default tier**
If a section has `fields` but no `tiers`, `from_file()` normalizes it into a single tier with label `"default"`, inheriting the section's `min_entries` and `fields`. Internally, `SectionRequirement` always has `tiers: list[TierRequirement]`. `to_dict()` always emits the `tiers` form.
*Rationale:* One code path in the grader, not two. Breaking changes are acceptable.

**DEC-003: Assertion names always include tier**
Format: `section:{Section}/{tier}[{i}].{field}` and `section:{Section}:count/{tier}`. Legacy sections use tier label `"default"`. No branching on tier presence.
*Rationale:* One format everywhere. Clean, predictable, parseable.

**DEC-004: Fail on shape mismatch, no silent fallback**
If the section defines tiers but the LLM returns a flat list, emit `grader:parse:{SectionName}` failure. Don't silently degrade — the eval spec author needs to know their tier descriptions aren't working.
*Rationale:* Silent fallback hides broken prompts. Explicit failure is debuggable.

**DEC-005: Tier `description` field for extraction hints**
Each tier gets an optional `description` injected into the extraction prompt. The label is the machine name, the description is the human instruction to the LLM. If omitted, the label is used as-is.
*Rationale:* Makes Haiku's job pattern-matching rather than interpretation. Addresses LLM classification accuracy concern.

**DEC-006: Extraction JSON shape is nested by tier**
```json
{
  "Restaurants": {
    "main": [{"name": "...", "phone": "..."}],
    "honorable_mentions": [{"name": "..."}]
  }
}
```
*Rationale:* Clean separation. Parser checks `isinstance(entries_data, dict)` for tiered sections.

### Blocker Resolutions

**B1 (Extraction parsing):** Resolved by DEC-006. Parser expects dict-of-lists for all sections (since all sections are tiered internally via DEC-002). Flat list = parse error per DEC-004.

**B2 (Extraction prompt):** Resolved by DEC-005. Tier descriptions guide the LLM. Prompt redesign includes tier-aware JSON schema example and per-tier extraction instructions.

---

## Detailed Breakdown

### US-001: Schema — Add TierRequirement and refactor SectionRequirement

**Description:** Add a `TierRequirement` dataclass and refactor `SectionRequirement` so that `tiers: list[TierRequirement]` is the canonical representation. `from_file()` normalizes legacy `fields`-style sections into a single `"default"` tier. `to_dict()` always emits the `tiers` form.

**Traces to:** DEC-001, DEC-002

**Acceptance criteria:**
- `TierRequirement` has `label: str`, `description: str = ""`, `min_entries: int = 0`, `fields: list[FieldRequirement]`
- `SectionRequirement` has `name: str`, `tiers: list[TierRequirement]` (no more top-level `fields` or `min_entries`)
- `from_file()` with a `tiers`-style JSON loads correctly
- `from_file()` with a legacy `fields`-style JSON normalizes to single `"default"` tier
- `to_dict()` always emits `tiers` array (never `fields` at section level)
- Roundtrip: `from_file() -> to_dict() -> from_file()` produces equivalent objects for both styles
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80% coverage

**Done when:** `EvalSpec` loads both legacy and tiered JSON, serializes consistently, and all schema tests pass.

**Files:**
- `src/clauditor/schemas.py` — Add `TierRequirement`, refactor `SectionRequirement`, update `from_file()` and `to_dict()`
- `tests/test_schemas.py` — Update `SAMPLE_EVAL` to use tiers, add tier-specific tests, update existing assertions for new structure

**Depends on:** none

**TDD:**
1. Test `from_file()` loads a tiered JSON with two tiers, correct labels/fields/min_entries
2. Test `from_file()` normalizes legacy `fields`-style section into `tiers: [{label: "default", ...}]`
3. Test `to_dict()` always emits `tiers` (never top-level `fields`)
4. Roundtrip test for tiered JSON
5. Roundtrip test for legacy JSON (loads as tiers, serializes as tiers)
6. Test tier `description` field is preserved through roundtrip

---

### US-002: Grader — Tier-aware extraction prompt, parsing, and validation

**Description:** Update `build_extraction_prompt()` to generate a tier-nested JSON schema with tier descriptions. Update `grade_extraction()` to validate per-tier field requirements and entry counts with new assertion name format. Update the `extract_and_grade()` parser to handle dict-of-lists (tiered) JSON shape and fail on flat-list responses.

**Traces to:** DEC-003, DEC-004, DEC-005, DEC-006

**Acceptance criteria:**
- `build_extraction_prompt()` generates nested JSON schema: `{"Section": {"tier_label": [{fields}]}}`
- Prompt includes tier descriptions when provided
- `grade_extraction()` validates per-tier: entry count as `section:{Name}:count/{tier}`, fields as `section:{Name}/{tier}[{i}].{field}`
- `extract_and_grade()` parser expects `dict` values per section (not `list`), maps entries by tier label
- Flat list response for a tiered section produces `grader:parse:{SectionName}` failure
- All existing `extract_and_grade()` async tests updated for new JSON shape
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80% coverage

**Done when:** Extraction prompt asks for tiered JSON, parser handles it, grading validates per-tier, and all grader tests pass.

**Files:**
- `src/clauditor/grader.py` — Update `build_extraction_prompt()`, `grade_extraction()`, and the parser in `extract_and_grade()`
- `tests/test_grader.py` — Update `_make_spec()` for tier structure, update all test data to tiered JSON shape, add tier-specific tests

**Depends on:** US-001

**TDD:**
1. Test `grade_extraction()` with two tiers, all fields present — passes
2. Test `grade_extraction()` with missing required field in main tier — fails with `section:Venues/main[0].address`
3. Test `grade_extraction()` with optional field missing in secondary tier — passes
4. Test `grade_extraction()` with too few entries in a tier — fails with `section:Venues:count/main`
5. Test `grade_extraction()` with zero-min_entries tier having no entries — passes
6. Test `grade_extraction()` with missing section — fails
7. Test `build_extraction_prompt()` includes tier labels and descriptions in output
8. Test `extract_and_grade()` with tiered JSON response — parses correctly
9. Test `extract_and_grade()` with flat list response — fails with `grader:parse:SectionName`
10. Test `extract_and_grade()` with unknown tier label in response — entries ignored (only defined tiers validated)

---

### US-003: CLI and example — Update init template and example spec

**Description:** Update `cmd_init()` starter JSON to use `tiers` format. Update the example eval spec to demonstrate tiered sections.

**Traces to:** DEC-002

**Acceptance criteria:**
- `cmd_init()` generates a starter spec with `tiers` (single default tier) instead of top-level `fields`
- Example `example-skill.eval.json` updated to use `tiers` format
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80% coverage

**Done when:** Init and example both demonstrate the new tiers format.

**Files:**
- `src/clauditor/cli.py` — Update `cmd_init()` starter JSON (lines 411-444)
- `examples/.claude/commands/example-skill.eval.json` — Convert to `tiers` format

**Depends on:** US-001

---

### US-004: Quality Gate — code review x4

**Description:** Run code reviewer 4 times across the full changeset, fixing all real bugs found each pass. Lint and tests must pass after all fixes.

**Traces to:** all decisions

**Acceptance criteria:**
- 4 review passes completed
- All real bugs fixed
- `uv run ruff check src/ tests/` passes
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with >=80% coverage

**Done when:** All review passes clean, all tests green.

**Files:** Any files touched by US-001 through US-003

**Depends on:** US-001, US-002, US-003

---

### US-005: Patterns & Memory — update conventions and docs

**Description:** Update CLAUDE.md architecture overview to mention tiered validation. Record any new patterns learned during implementation.

**Traces to:** all decisions

**Acceptance criteria:**
- CLAUDE.md architecture section updated if tier pattern warrants it
- Any useful memories captured via `bd remember`

**Done when:** Documentation reflects the new tier capability.

**Files:** `CLAUDE.md` (if applicable)

**Depends on:** US-004

---

## Beads Manifest

| Field       | Value |
|-------------|-------|
| Epic        | TBD   |
| Worktree    | TBD   |

| Story  | Bead ID | Status |
|--------|---------|--------|
| US-001 | TBD     | —      |
| US-002 | TBD     | —      |
| US-003 | TBD     | —      |
| US-004 | TBD     | —      |
| US-005 | TBD     | —      |

