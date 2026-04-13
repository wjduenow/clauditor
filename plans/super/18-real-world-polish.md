# Super Plan: #18 — Real-world P2/P3 polish from my_claude_agent eval run

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/18
- **Branch:** `feature/18-real-world-polish`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/18-real-world-polish`
- **Phase:** `published`
- **PR:** https://github.com/wjduenow/clauditor/pull/20
- **Sessions:** 1
- **Last session:** 2026-04-13

---

## Discovery

### Ticket Summary

**What:** Nine independent polish items (4× P2, 5× P3) surfaced by the same `my_claude_agent` eval run that drove #17. Quality-of-life and structural improvements: comparator workflow, assertion metadata, docs, strict/loose format patterns, metric history, grader debugging, pytest fixture, ASCII mode, and single-criterion Layer 3 re-runs.

**Why:** Reduce friction for adopters and tighten the feedback loop. None are blockers individually, but together they close the observability and ergonomics gaps found in real-world usage.

**Ticket acceptance criteria:**
- [ ] `clauditor compare` command implemented
- [ ] `AssertionResult.kind` field added
- [ ] README documents `pattern` vs `format` decision (now: `format` dual-mode semantics, since #17 removed `pattern`)
- [ ] All format registry entries have correct strict/extract patterns
- [ ] `.clauditor/history.jsonl` persists metrics
- [ ] Raw Haiku JSON surfaced on Layer 2 failure
- [ ] `clauditor_capture` pytest fixture added
- [ ] ASCII mode for assertion names
- [ ] `--only-criterion` flag for Layer 3

### Codebase Findings (from scout)

**P2.1 `clauditor compare`** → `comparator.py` (already has `compare_ab(spec, model)` → `ABReport`, lines 62–126) + `cli.py:596–779` argparse subparser pattern. `cmd_grade()` already has a `--compare` flag (cli.py:131–134). A new `compare` subcommand needs to load *two captured outputs* against one spec and diff `AssertionResult.passed` flips (pass↔fail).

**P2.2 `AssertionResult.kind`** → `assertions.py:17–27` dataclass. Name-to-kind mapping currently implicit in suffixes: `:format`, `:count`, `:count_max`, `has_format:{name}`, bare = presence, `≥N` inside name = count threshold. Naming call sites: assertions.py lines 113, 123, 145, 155, 176, 307, 323, 331; grader.py lines 96, 110, 169.

**P2.3 README docs — `format` dual-mode** → `schemas.py:15–46` already has an accurate docstring (DEC-007 from #17). README lines 147–150 don't surface it. Add a "Field validation" section under Layer 2 with a decision tree.

**P2.4 Format registry strict vs extract** → `formats.py:14–36`. `FormatDef` has separate `pattern` (strict, fullmatch) and `extract_pattern` (loose, findall). Only `zip_uk` (109–113) and `uuid` (125–129) currently set a distinct `extract=`. All others reuse the strict pattern for extraction, so `has_format(...)` undercounts. `validate_value()` (formats.py:164) uses strict, `assert_has_format()` (assertions.py:314–335) uses extract.

**P3.5 Persistent metric history** → No `.jsonl` writers exist. `.clauditor/` convention exists (`cli.py:202–255` writes `{skill}.grade.json` on `--save`). Natural home: extend `--save` to also append a line to `.clauditor/history.jsonl`.

**P3.6 Raw Haiku JSON on failure** → `grader.py:29–33` — `ExtractedOutput.raw_json` populated at line 239 but discarded on failure (lines 226–236). Parse-failure path emits one `grader:parse` assertion with only first 200 chars of response text.

**P3.7 `clauditor_capture` fixture** → `pytest_plugin.py:85–112` has `clauditor_runner`/`clauditor_spec`/`clauditor_grader`/`clauditor_triggers`. Capture convention: `tests/eval/captured/<skill>.txt` (from #17, DEC-001). New fixture slots in next to the existing four.

**P3.8 ASCII assertion names** → `≥`/`≤` literals in 7 locations in `assertions.py` (145, 155, 165, 176, 188, 307, 331). Rendered verbatim via `cli.py:64, 193`.

**P3.9 `--only-criterion` for Layer 3** → `quality_grader.py:215–260` iterates `eval_spec.grading_criteria` with no filter. `cli.py:625–658` `p_grade` subparser is the natural home for the flag.

### Conventions (from CLAUDE.md, confirmed by scout)
- Test file per source module, class-based organization.
- 80% coverage gate enforced.
- Don't shadow plugin fixture names (`clauditor_runner/spec/grader/triggers`).
- Beads for task tracking; no TodoWrite/markdown TODOs.
- No `.claude/rules/*.md` files and no `workflow-project.md` — general CLAUDE.md rules apply.

### Proposed Scope

Ship all nine items in one plan. They're independent and share the same review surfaces as #17 (schemas/assertions/grader/cli/pytest_plugin). Ordering follows dependency: kind/format foundations first, then the features that build on them, then docs/history. Quality Gate + Patterns & Memory close it out.

### Scoping Questions

**Q1 — `clauditor compare` input shape**
How does `compare` consume its inputs?

- **(A)** `clauditor compare <before.txt> <after.txt> --spec x.eval.json` — two captured stdout files + one spec. Outputs a diff of assertion results.
- **(B)** `clauditor compare <before.grade.json> <after.grade.json>` — diff two saved grade reports from `.clauditor/` (no re-grading, pure diff).
- **(C)** Both: positional args auto-detect `.txt` vs `.grade.json`; require `--spec` only for `.txt`.
- **(D)** Skip — `cmd_grade --compare` already exists; ship a README doc pointing at it instead.

**Q2 — `AssertionResult.kind` population strategy**
How does `kind` get set?

- **(A)** New required enum field on `AssertionResult`; every construction site passes it explicitly. Most principled, touches ~10 call sites.
- **(B)** Derived property that parses `self.name` on access. Zero call-site churn but fragile to name format drift.
- **(C)** Optional field defaulting to `"custom"`; internal builders pass it, external callers (there are none currently) get the fallback.
- **(D)** Same as (A) but also remove the suffix from `name` (e.g. `website` instead of `website:format`) once `kind` carries it — big refactor, breaks grouped_summary from #17.

**Q3 — Format registry strict-vs-extract rollout**
Which entries get distinct `extract_pattern`?

- **(A)** All phone/email/url/domain — the high-traffic ones. Leave `uuid`/`zip_uk` as-is. Document the convention in a formats.py module docstring.
- **(B)** Every entry, audited one at a time. Biggest correctness win, highest review load.
- **(C)** (A) plus a unit-test matrix that asserts, for every registry entry, that `validate_value("X")==True → extract_pattern.findall("prefix X suffix")` finds ≥1 match. Enforces invariant going forward.

**Q4 — Metric history format**
What schema does `.clauditor/history.jsonl` use?

- **(A)** One line per run, flat: `{ts, skill, pass_rate, mean_score, <metric>: <value>, ...}`. Simple but mixes dimensions.
- **(B)** One line per (run, metric): `{ts, skill, metric_name, value}`. Easier to query, more lines.
- **(C)** (A) plus a `clauditor trend <skill> --metric <name>` read command in the same story (ticket asks for it).
- **(D)** (B) plus `clauditor trend`.

**Q5 — Raw Haiku JSON surfacing mechanism**
Where does `raw_json` go on failure?

- **(A)** Attach as `evidence` on the failing `grader:parse` / `grader:shape` `AssertionResult` (string, truncated to ~2KB).
- **(B)** Write to `.clauditor/last_grader_response.json` automatically on every grade (success or failure), overwriting each time.
- **(C)** Both: evidence carries a reference string (`"see .clauditor/last_grader_response.json"`) and the file is always written.
- **(D)** New optional `AssertionResult.raw_data: dict | None` field; CLI pretty-prints it when `-v`.

**Q6 — `clauditor_capture` fixture behavior**
What does the fixture do when the capture file is missing?

- **(A)** Return the `Path` only; missing file is the test's problem (`.read_text()` will raise `FileNotFoundError`).
- **(B)** Return the text, skip the test with `pytest.skip("no capture for {skill}")` if missing.
- **(C)** Return the text, auto-run `clauditor capture <skill>` to populate if missing (slow, costs tokens, surprising).
- **(D)** (A) with a helper: `clauditor_capture("skill").read_text()` returns text and `clauditor_capture("skill").path` returns the Path — small wrapper object.

**Q7 — ASCII mode scope**
How wide is the ASCII fix?

- **(A)** Replace `≥`/`≤` at the source (in `assertions.py`). Permanent ASCII everywhere. Simplest, breaks anyone grepping for `≥`.
- **(B)** Keep Unicode in name construction; add `--ascii` flag on relevant CLI subcommands that rewrites names at render time.
- **(C)** New `AssertionSet.ascii_summary()` / `ascii_grouped_summary()` methods mirroring the existing ones. CLI flag toggles which one is called.
- **(D)** Environment variable `CLAUDITOR_ASCII=1` toggles at render time — no CLI surface change.

**Q8 — Layer 3 `--only-criterion` matching**
How does the filter match criterion names?

- **(A)** Exact substring match on the criterion name/description.
- **(B)** Exact name match only; name must match a criterion identifier field.
- **(C)** Repeatable flag: `--only-criterion a --only-criterion b` runs multiple.
- **(D)** (C) + substring matching.

**Q9 — Bundling vs splitting**
All nine items in one PR, or split?

- **(A)** One PR, one plan (matches #17 precedent).
- **(B)** Two PRs: P2 items (compare, kind, docs, strict/loose) land first; P3 items (history, raw json, fixture, ascii, only-criterion) follow. Smaller review surfaces.
- **(C)** (A) but ordered so P2 items land as the first N commits for easy partial-review.

---

## Decisions (from scoping)

- **DEC-001 — `compare` input shape (Q1=C):** `clauditor compare <before> <after> [--spec x.eval.json]` auto-detects file type by extension. Two `.txt` files require `--spec` and re-grade both. Two `.grade.json` files diff saved reports with no re-grading. Mixed inputs error out.
- **DEC-002 — `AssertionResult.kind` population (Q2=A):** Add a required `kind` field to `AssertionResult` (enum-like `Literal` type). Every construction site passes it explicitly. Kinds: `presence`, `format`, `pattern`, `count`, `count_max`, `custom`. Touches ~10 call sites across `assertions.py` and `grader.py`.
- **DEC-003 — Format registry strict/extract audit (Q3=B):** Audit every entry in `FORMAT_REGISTRY`. For each, decide whether `extract_pattern` should differ from `pattern` (the strict validator). Document per-entry rationale in code comments.
- **DEC-004 — `history.jsonl` schema + `clauditor trend` (Q4=C):** One line per run, flat shape: `{ts, skill, pass_rate, mean_score, metrics: {metric_name: value, ...}}`. Append-only to `.clauditor/history.jsonl`. Ship `clauditor trend <skill> --metric <name>` read command in the same plan.
- **DEC-005 — Raw Haiku JSON surfacing (Q5=D):** Add optional `raw_data: dict | None = None` field to `AssertionResult`. Populate on Layer 2 parse/shape failure with the full `ExtractedOutput.raw_json`. CLI pretty-prints when `-v`/`--verbose` is set.
- **DEC-006 — `clauditor_capture` fixture shape (Q6=A):** Fixture returns a `Path` for `tests/eval/captured/<skill>.txt`. Missing file is the test's problem — `.read_text()` raises `FileNotFoundError`, which is the correct signal.
- **DEC-007 — ASCII assertion names (Q7=A):** Replace `≥`/`≤` with `>=`/`<=` at every source location in `assertions.py`. Permanent ASCII — no flag, no mode. Acknowledged cost: any downstream user grepping for `≥` breaks.
- **DEC-008 — `--only-criterion` matching (Q8=D):** Repeatable flag with substring matching: `--only-criterion foo --only-criterion bar` keeps criteria whose name contains `foo` OR `bar` (case-insensitive).
- **DEC-009 — Bundling (Q9=C):** One PR, one plan. Order commits so all P2 stories (US-001 through US-004) land before P3 stories. This gives a natural partial-review cut line.

## Architecture Review

Single-pass review (matching #17 precedent) — items are small, additive, and touch well-scoped internals. No auth, no data migration, no new external API surface.

| Area | Rating | Notes |
|---|---|---|
| **Security** | pass | Read-only changes except `history.jsonl` append (local file, no user input echoed). Raw Haiku JSON (DEC-005) is already trusted content; surfacing it doesn't introduce new attack surface. |
| **Performance** | pass | `history.jsonl` append is O(1) per run. `grouped_summary` already computed lazily. `compare` on `.grade.json` is pure deserialize + set diff. `clauditor trend` reads jsonl sequentially — no index needed until history grows past ~10k entries. |
| **Data Model** | concern | **DEC-002 adds a required field to `AssertionResult`.** Constructors without `kind` will break at runtime. Every call site in `assertions.py` (lines 113, 123, 145, 155, 165, 176, 188, 307, 323, 331) + `grader.py` (lines 96, 110, 169) must be updated in one commit. Tests that construct `AssertionResult` directly (grep `AssertionResult(` in `tests/`) must also be migrated. **DEC-005 adds an optional field** — safer, backward compatible. |
| **API Design** | concern | `clauditor compare` (DEC-001) overlaps with existing `cmd_grade --compare` flag. Need to clarify in `--help` and README: `cmd_grade --compare` = A/B within one grade run; `cmd compare` = diff two captures/reports. Risk of user confusion. Also: `clauditor trend` is a new read surface that needs to tolerate missing/corrupt history files gracefully. |
| **Observability** | pass | DEC-005 (raw_json) and DEC-004 (history) are themselves observability improvements. No new logging needed. |
| **Testing** | concern | DEC-003 (audit every format) is the biggest test surface change. Each registry entry needs: strict validation positive + negative cases, extract positive + negative cases, and the invariant `validate_value(X)==True → extract.findall("prefix X suffix").count ≥ 1`. That's ~15 entries × ~4 cases = ~60 new assertions. 80% coverage gate must stay green. |
| **Migration / Breaking changes** | **blocker** | **DEC-007 (hard ASCII replacement)** breaks any `.eval.json` spec or test that asserts on `name=="has_urls≥3"` exactly. Grep for `≥` across `tests/` and `my_claude_agent` before committing. **DEC-002** is also a breaking API change for any external caller constructing `AssertionResult` — there are none in this repo, but worth confirming. |

### Blockers to resolve in refinement
1. **DEC-007 grep sweep** — need to enumerate every literal `≥`/`≤` reference in tests and specs before flipping the source. If my_claude_agent has eval specs referencing `has_urls≥3` by exact name, those will regress silently.

### Concerns to address in refinement
1. **DEC-002 rollout sequencing** — do we add `kind` as a required positional arg (breaks in one commit) or stage it as `kind: str = "custom"` first, migrate all call sites, then tighten to required? The two-step path is safer but adds a second commit. One-shot is cleaner but needs a thorough grep first.
2. **`compare` vs `grade --compare` naming** — should we rename the existing `--compare` flag to avoid clash? Or document the distinction and move on?
3. **DEC-003 test matrix size** — is a per-entry parametrized test acceptable, or do we need one test method per format? Affects readability and coverage numbers.
4. **`clauditor trend` scope** — minimum viable: print last N values for a metric. Stretch: sparkline, regression detection. Where's the line?
5. **`history.jsonl` flush timing** — append on every `cmd_grade` run, only on `--save`, or new `--history` flag?

## Refinement Log

- **DEC-010 — B1 downgraded (no release yet):** clauditor is unreleased. DEC-007 (hard ASCII replacement) proceeds as written; update any broken clauditor tests in the same story. my_claude_agent is out of scope. No alias, no CHANGELOG entry, no deprecation window.
- **DEC-011 — `kind` field rollout shape (C1=one-shot):** Add `kind` as a required field on `AssertionResult` in a single commit. Update all ~13 construction call sites (`assertions.py` lines 113, 123, 145, 155, 165, 176, 188, 307, 323, 331 + `grader.py` lines 96, 110, 169 + any test constructors) in the same commit. No two-step migration.
- **DEC-012 — `compare` entry point consolidation (C2=c):** Remove `grade --compare` entirely. `clauditor compare` becomes the sole entry point for A/B and diff comparison workflows. The new subcommand absorbs the existing `compare_ab()` machinery from `comparator.py`.
- **DEC-013 — DEC-003 test matrix shape (C3=b):** One test class per format entry (e.g. `TestPhoneUsFormat`, `TestEmailFormat`). Each class has methods for strict positive, strict negative, extract positive, extract negative, and the invariant (`validate_value(X) ⇒ findall("prefix X suffix") ≥ 1`). Verbose but readable; easier to track regressions per format.
- **DEC-014 — `clauditor trend` scope (C4=b):** v1 ships last-N-values tab-separated plus an ASCII sparkline (using `▁▂▃▄▅▆▇█` or `- . : | #` — pick the simpler set). No regression detection in v1. **Wait — DEC-007 requires ASCII.** Sparkline must use ASCII characters only (`_.-=#` or similar) to stay consistent. Update: use ASCII-only sparkline glyphs.
- **DEC-015 — `history.jsonl` flush timing (C5=a):** Append a record to `.clauditor/history.jsonl` on every `cmd_grade` run, unconditionally. No `--save` gate, no opt-in flag. If `.clauditor/` doesn't exist, create it. If the file is corrupt, `clauditor trend` reports the corrupt line and continues.
- **DEC-016 — Commit ordering within the PR (DEC-009=C):** Commits land in this order for easy partial review: US-001 (kind) → US-002 (formats) → US-003 (compare) → US-004 (README) → US-005 (raw_data) → US-006 (history+trend) → US-007 (fixture) → US-008 (ascii) → US-009 (only-criterion) → US-010 (Quality Gate) → US-011 (Patterns & Memory). P2 items (US-001..US-004) form the first review cut.
- **DEC-017 — `clauditor compare` file type detection:** Extension-based. `.txt` → captured stdout, requires `--spec`. `.grade.json` → saved grade report, no `--spec` needed. Other extensions → error with a clear message. Mixed types (one `.txt` + one `.grade.json`) → error. Both files must resolve to the same spec.
- **DEC-018 — `AssertionResult.kind` values:** `Literal["presence", "format", "pattern", "count", "count_max", "reachability", "custom"]`. `pattern` is retained even though DEC-007 of #17 removed `FieldRequirement.pattern` — it still describes the *kind* of check (regex pattern match), which is distinct from a registered format. Bare `presence` is the default for fields without a format. `reachability` is a dedicated kind for URL-reachability assertions (assertions.py:307). `custom` is the escape hatch for top-level assertions that don't fit a structured category.
- **DEC-019 — `--only-criterion` CLI flag shape (from DEC-008):** `--only-criterion` is repeatable (`action="append"`), substring match, case-insensitive. If no `--only-criterion` is passed, all criteria run. If any are passed, only criteria whose name/description contains at least one substring run. Empty filtered set → exit with a clear error naming the available criteria.

## Detailed Breakdown

Stories ordered by commit sequence (DEC-016). Each is sized for a single Ralph context window. Every story's acceptance includes `uv run pytest --cov=clauditor --cov-report=term-missing` passing with the 80% gate and `uv run ruff check src/ tests/` clean.

---

### US-001 — Add `AssertionResult.kind` field (required)

**Description:** Add a required `kind: Literal[...]` field to `AssertionResult`. Update every construction site in `assertions.py`, `grader.py`, and tests to pass an explicit kind. No suffix parsing, no fallback — the field is the source of truth going forward.

**Traces to:** DEC-002, DEC-011, DEC-018

**Files:**
- `src/clauditor/assertions.py` — extend dataclass (line ~17) with `kind: Literal["presence", "format", "pattern", "count", "count_max", "reachability", "custom"]`. Update call sites at lines 113 (`contains:` → `kind="presence"`), 123 (`not_contains:` → `"presence"`), 145 (`min_count:` → `"count"`), 155 (`min_length` → `"count"`), 165 (`max_length` → `"count"`), 176 (`has_urls` → `"count"`), 188 (URL reachability count → `"count"`), 307 (`urls_reachable` → `"reachability"`), 323 (`has_format` bare → `"format"`), 331 (`has_format...≥N` → `"count"`).
- `src/clauditor/grader.py` — update call sites at 96 (`:count/{tier}` → `"count"`), 110 (`:count_max` → `"count_max"`), 169 (`:format` suffix → `"format"`), and any presence check (bare `section:...` construction) → `"presence"`.
- `tests/test_assertions.py` — update any direct `AssertionResult(...)` constructors to pass `kind`.
- `tests/test_grader.py` — same.
- `tests/test_schemas.py` — same.

**TDD:**
- Every kind value is produced by at least one call site (parametrized test asserting coverage of the enum).
- `AssertionResult(...)` without `kind` raises `TypeError` at construction.
- `AssertionSet.grouped_summary()` from #17 still works — grouping key continues to use the name; kind is supplementary.
- Existing `summary()` output unchanged (regression test).

**Acceptance criteria:**
- Every `AssertionResult` construction in `src/` and `tests/` passes an explicit `kind`.
- Grep for `AssertionResult(` returns zero matches without a `kind=` kwarg in the same call.
- Coverage ≥ 80%; ruff clean.

**Done when:** Running `uv run pytest` is green and every failing/passing assertion in the test suite has a non-`custom` kind (unless explicitly a custom check).

**Depends on:** none

---

### US-002 — Audit every FORMAT_REGISTRY entry for strict/extract correctness

**Description:** Walk every entry in `FORMAT_REGISTRY` and decide whether `extract_pattern` must differ from `pattern`. For each entry, add inline comments explaining the choice. Add a per-entry test class asserting the strict/extract invariant.

**Traces to:** DEC-003, DEC-013

**Files:**
- `src/clauditor/formats.py` — audit every entry. For entries like `phone_us`, `email`, `url`, `domain`, `zip_us`, `uuid`, `ipv4`, `iso_date`, etc., separate strict (fullmatch) from extract (findall-in-prose). Add a comment above each `_def(...)` call explaining: "strict = <intent>; extract = <intent>." For entries where strict === extract, say so explicitly.
- `tests/test_formats.py` — add one `Test<Format>Format` class per registry entry (DEC-013). Each class has: `test_strict_accepts_canonical`, `test_strict_rejects_malformed`, `test_extract_finds_in_prose`, `test_extract_rejects_pure_noise`, and `test_invariant_validate_implies_extract` (if `validate_value(X)` is True, then `extract_pattern.findall(f"prefix {X} suffix")` returns at least one match containing X).

**TDD:**
- For each of the ~15 entries: 5 test methods × 15 classes = ~75 new test cases. List the entries to audit before writing code so none are missed.
- Existing `test_formats.py` cases pass unchanged (regression).
- `FORMAT_REGISTRY` key set unchanged (no additions/removals in this story).

**Acceptance criteria:**
- Every current registry entry has a test class.
- Every entry has an inline comment justifying its strict/extract shape.
- The invariant test passes for every entry.
- Coverage ≥ 80%; ruff clean.

**Done when:** `uv run pytest tests/test_formats.py -v` lists one class per format entry, all green.

**Depends on:** none

---

### US-003 — Add `clauditor compare` subcommand; remove `grade --compare`

**Description:** New `compare` subparser that takes two positional file args, auto-detects `.txt` vs `.grade.json`, loads and diffs assertion results, prints the flipped assertions (regressions and improvements). Remove the existing `grade --compare` flag entirely (DEC-012).

**Traces to:** DEC-001, DEC-012, DEC-017

**Files:**
- `src/clauditor/cli.py` — add `p_compare = subparsers.add_parser("compare", ...)` with positional `before`, `after`, optional `--spec`. Add `cmd_compare()` that:
  1. Detects file type by extension; errors on mismatch.
  2. For `.txt`: requires `--spec`; runs `SkillSpec.from_file(...).grade()` on each, extracts `AssertionSet` from each.
  3. For `.grade.json`: deserializes saved reports into `AssertionSet`.
  4. Diffs the two `AssertionSet`s by assertion `name`: regressions (was pass, now fail), improvements (was fail, now pass), unchanged (skipped in output).
  5. Prints a human-readable diff with `[REGRESSION]` / `[IMPROVEMENT]` prefixes. Exit code 1 if any regressions, 0 otherwise.
- `src/clauditor/cli.py` — remove `p_grade.add_argument("--compare", ...)` (line ~634) and the branch in `cmd_grade()` that calls `compare_ab()` (lines 131–134).
- `src/clauditor/comparator.py` — `compare_ab()` stays (future use) but is no longer wired into `cmd_grade`. Add a new `diff_assertion_sets(before: AssertionSet, after: AssertionSet) -> list[Flip]` helper that the new subcommand uses.
- `tests/test_cli.py` — add `TestCmdCompare` class with cases: (a) two `.txt` files + spec, (b) two `.grade.json` files, (c) mixed extensions errors, (d) `.txt` without `--spec` errors, (e) regression detection returns exit 1, (f) no flips returns exit 0.
- `tests/test_comparator.py` — add test class for `diff_assertion_sets()`.

**TDD:**
- All 6 CLI cases above.
- `diff_assertion_sets` handles: name only in before, name only in after, name in both with same passed, name in both with flipped passed.

**Acceptance criteria:**
- `clauditor compare --help` shows the new subcommand.
- `clauditor grade --compare` errors (flag removed).
- All prior `cmd_grade --compare` test cases migrated or deleted.
- Coverage ≥ 80%; ruff clean.

**Done when:** `clauditor compare before.grade.json after.grade.json` prints a diff and exits with the right code.

**Depends on:** US-001 (kind field is referenced by the diff output to categorize flips).

---

### US-004 — README: document `format` dual-mode semantics

**Description:** Add a "Field validation" section to README under Layer 2 explaining that `FieldRequirement.format` accepts either a registered format name or an inline regex, with a decision tree and examples. Link to `FORMAT_REGISTRY` entries.

**Traces to:** DEC-007 (from #17), DEC-011 (from #17)

**Files:**
- `README.md` — add a subsection under Layer 2 (after line ~150). Include: (a) a one-sentence overview, (b) a decision tree (registered name? use it. custom? inline regex.), (c) two examples: `{"name": "phone", "format": "phone_us"}` and `{"name": "slug", "format": r"^[a-z0-9-]+$"}`, (d) a pointer to `FORMAT_REGISTRY` in `formats.py` and the `clauditor formats list` command from #17 (if that command shipped — verify before linking).

**Acceptance criteria:**
- README renders (manual spot check via `grip` or similar, not required).
- `uv run ruff check` clean (no code changes to lint).
- Links work (verify `formats.py` path).

**Done when:** A new user can read README and know when to use a registry name vs inline regex without opening source.

**Depends on:** none (docs only).

---

### US-005 — Add `AssertionResult.raw_data` + surface raw Haiku JSON on Layer 2 failure

**Description:** Add optional `raw_data: dict | None = None` field to `AssertionResult`. On Layer 2 parse/shape failure, attach `ExtractedOutput.raw_json` to the failing assertion's `raw_data`. CLI pretty-prints `raw_data` when `-v`/`--verbose` is set.

**Traces to:** DEC-005

**Files:**
- `src/clauditor/assertions.py` — extend dataclass with `raw_data: dict | None = None` (optional, default None). Do not require it anywhere.
- `src/clauditor/grader.py` — at the parse-failure path (lines 226–236), populate `raw_data=response_json_if_available` on the `grader:parse` assertion (set to the parsed-but-malformed dict if the shape check failed, or `None` if JSON parse itself failed). At the shape-failure path (find via grep for `grader:shape` or equivalent), same treatment.
- `src/clauditor/cli.py` — in the assertion-rendering code (cli.py:64, 193), when `args.verbose` is set and `result.raw_data is not None`, pretty-print the JSON under the failing assertion with a 4-space indent.
- `tests/test_grader.py` — case: grading a malformed-but-parseable response attaches `raw_data` to the shape failure. Case: grading an unparseable response leaves `raw_data=None` but still attaches response text as evidence.
- `tests/test_cli.py` — case: `cmd_grade` with `-v` prints raw_data for failing assertions that have it.

**TDD:**
- All 4 cases above.

**Acceptance criteria:**
- `raw_data` is optional and defaults to None everywhere.
- Non-failure assertions never carry raw_data (verify via a negative test).
- Coverage ≥ 80%; ruff clean.

**Done when:** A Haiku response that fails shape validation shows the full JSON in `clauditor grade -v` output.

**Depends on:** US-001 (both modify `AssertionResult`; US-001 goes first for a cleaner diff).

---

### US-006 — Persistent metric history + `clauditor trend` command

**Description:** On every `cmd_grade` run, append a flat record to `.clauditor/history.jsonl`: `{ts, skill, pass_rate, mean_score, metrics: {metric_name: value, ...}}`. Add a `clauditor trend <skill> --metric <name>` subcommand that reads the history file, prints the last N values tab-separated, and renders an ASCII sparkline (DEC-014).

**Traces to:** DEC-004, DEC-014, DEC-015

**Files:**
- `src/clauditor/history.py` (new) — functions `append_record(skill, pass_rate, mean_score, metrics, path=".clauditor/history.jsonl")` and `read_records(skill, metric_name, path=...)`. Handles missing dir (creates), missing file (treats as empty), corrupt line (skips with a warning to stderr).
- `src/clauditor/cli.py` — in `cmd_grade`, after grading completes, call `append_record(...)`. Extract numeric metrics from the `AssertionSet` (any result with a numeric evidence string that looks like a count, plus `pass_rate` and `mean_score` from the existing summary).
- `src/clauditor/cli.py` — add `p_trend = subparsers.add_parser("trend", ...)` with positional `skill_name`, required `--metric <name>`, optional `--last N` (default 20). Add `cmd_trend()` that calls `history.read_records(...)`, prints tab-separated values, and an ASCII sparkline using only ASCII characters (DEC-014 note — e.g. `_.-=#` mapped to normalized value ranges).
- `tests/test_history.py` (new) — round-trip: append then read. Corrupt line handling. Missing file → empty list. Multiple skills interleaved → filter works.
- `tests/test_cli.py` — `TestCmdTrend` class: happy path, missing metric, no history yet, `--last` truncation, sparkline rendering (snapshot test on a known value sequence).

**TDD:**
- All cases above before implementation.
- Sparkline function is a pure helper, unit-tested separately.

**Acceptance criteria:**
- `.clauditor/history.jsonl` written on every grade (verify via tmp_path).
- `clauditor trend` reads and renders correctly for a seeded history file.
- Sparkline uses only ASCII characters (DEC-014).
- Coverage ≥ 80%; ruff clean.

**Done when:** A sequence of five grade runs produces a trendable series visible in `clauditor trend`.

**Depends on:** none (independent of US-001..US-005; uses existing `AssertionSet` read API).

---

### US-007 — Add `clauditor_capture` pytest fixture

**Description:** Add a `clauditor_capture` fixture to `pytest_plugin.py` that returns a factory: `clauditor_capture("skill-name")` → `Path("tests/eval/captured/skill-name.txt")`. Missing files raise `FileNotFoundError` on `.read_text()` (DEC-006).

**Traces to:** DEC-006

**Files:**
- `src/clauditor/pytest_plugin.py` — add the fixture alongside `clauditor_runner`/`clauditor_spec`/`clauditor_grader`/`clauditor_triggers` (after line ~112). Fixture scope: `session`. The factory accepts an optional `base_dir` kwarg (default `tests/eval/captured`).
- `tests/test_pytest_plugin.py` — add test cases: (a) fixture returns a Path, (b) Path points to the conventional location, (c) custom `base_dir` works, (d) missing file still returns a Path (error deferred to read). Use pytester for integration-style tests if needed.
- Document the fixture in README under the pytest plugin section (if that section exists; add a one-liner if not).

**TDD:**
- All 4 cases above.

**Acceptance criteria:**
- Fixture registered, discoverable by `pytest --fixtures`.
- Does not shadow existing fixture names (CLAUDE.md rule).
- Coverage ≥ 80%; ruff clean.

**Done when:** `def test_x(clauditor_capture): assert clauditor_capture("find-restaurants").name == "find-restaurants.txt"` passes.

**Depends on:** none.

---

### US-008 — Replace `≥`/`≤` with ASCII `>=`/`<=` at source

**Description:** Hard-replace all `≥` and `≤` literals in `src/clauditor/assertions.py` (and anywhere else they appear in source) with `>=`/`<=`. Update any test that asserts on exact assertion names.

**Traces to:** DEC-007, DEC-010

**Files:**
- `src/clauditor/assertions.py` — replace `≥` → `>=` at lines 145, 155, 165, 176, 188, 307, 331 (and any missed). Replace `≤` similarly if present.
- Grep the rest of `src/` for any stray `≥`/`≤` and fix.
- Grep `tests/` for any test asserting on exact names containing `≥`/`≤`; update the expected strings.
- Grep `plans/super/` — don't modify historical plan docs, but note in the commit message that names have changed.

**TDD:**
- After replacement, grep `src/clauditor/` for `≥`/`≤` returns zero matches.
- `uv run pytest` still green.
- Existing `test_assertions.py` tests that referenced `≥`-containing names now reference `>=`-containing names.

**Acceptance criteria:**
- Zero Unicode `≥`/`≤` in `src/clauditor/`.
- All tests pass.
- Coverage ≥ 80%; ruff clean.

**Done when:** `grep -r '≥\|≤' src/clauditor/ tests/` returns no matches.

**Depends on:** US-001 (both touch assertions.py name-construction lines; US-001 first avoids merge conflict churn).

---

### US-009 — Add `--only-criterion` to Layer 3 grading

**Description:** Add a repeatable `--only-criterion <substring>` flag to `cmd_grade` (or wherever Layer 3 grading is triggered). Filter `eval_spec.grading_criteria` before calling `grade_quality()` — keep criteria whose name/description contains any provided substring (case-insensitive). Empty filtered set → clear error naming the available criteria.

**Traces to:** DEC-008, DEC-019

**Files:**
- `src/clauditor/cli.py` — at `p_grade` (line ~625), add `p_grade.add_argument("--only-criterion", action="append", default=None, help="...")`. In `cmd_grade()` (or `_run_grade`), before calling `grade_quality()`, if `args.only_criterion` is set, filter `spec.grading_criteria` (case-insensitive substring match against name and description). If result is empty, print an error listing available criteria and exit 2.
- `src/clauditor/quality_grader.py` — no changes needed; filtering happens at the CLI layer before the grader is invoked. Keeps the grader API pure.
- `tests/test_cli.py` — `TestOnlyCriterion` class: (a) single substring matches subset, (b) multiple substrings match union, (c) no match → exit 2 with criterion list in stderr, (d) no flag → all criteria run (regression), (e) case insensitivity.

**TDD:**
- All 5 cases above.

**Acceptance criteria:**
- Flag documented in `--help`.
- Filtering happens before LLM call (saves tokens — verify via mock call count).
- Coverage ≥ 80%; ruff clean.

**Done when:** `clauditor grade <skill> --only-criterion clarity --only-criterion accuracy` runs only the clarity and accuracy criteria.

**Depends on:** none.

---

### US-010 — Quality Gate (code review × 4 + CodeRabbit)

**Description:** Run the `code-review` skill four times across the full changeset; fix all real bugs found each pass. Run CodeRabbit if available. Re-run project validation (`uv run ruff check`, `uv run pytest --cov`) after fixes. Depends on **all implementation stories** being complete.

**Traces to:** All DEC-001 → DEC-019.

**Acceptance criteria:**
- 4 code-review passes complete.
- All real bugs fixed (stylistic deferrals documented).
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥ 80% coverage.
- All ticket acceptance-criteria checkboxes satisfied.

**Done when:** Four consecutive review passes produce no new real bugs.

**Depends on:** US-001 through US-009.

---

### US-011 — Patterns & Memory

**Description:** Update project conventions and memory with patterns learned. Specifically: (a) document `AssertionResult.kind` enum values and the "kind is source of truth; name is for humans" convention; (b) record the "strict fullmatch / extract findall" format registry invariant; (c) record the `.clauditor/history.jsonl` schema shape for future additions; (d) `bd remember` any non-obvious insights (e.g. why `pattern` is still a valid kind despite `FieldRequirement.pattern` being removed in #17).

**Traces to:** DEC-002, DEC-003, DEC-004, DEC-018.

**Acceptance criteria:**
- README updated with `compare`, `trend`, and `--only-criterion` docs.
- At least two `bd remember` entries capturing non-obvious insights.
- No MEMORY.md files created (beads is the project's memory store).

**Done when:** A fresh agent opening this repo would not re-ask any of the design questions resolved in DEC-001 through DEC-019.

**Depends on:** US-010.

---

## Beads Manifest

_pending_
