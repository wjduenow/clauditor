# 80 — Tighten SKILL.md frontmatter validation to catch YAML bugs

## Meta

- **Ticket:** [#80](https://github.com/wjduenow/clauditor/issues/80)
- **Phase:** detailing
- **Sessions:** 1
- **Last session:** 2026-04-22
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/80-strict-frontmatter-yaml`
- **Branch:** `feature/80-strict-frontmatter-yaml`
- **Base:** `dev`

## Ticket summary

PR #79 fixed a symptom: `.claude/skills/review-agentskills-spec/SKILL.md`
had `compatibility: Requires ... Optional: the ...` — an unquoted
`space-colon-space` sequence inside a plain scalar. GitHub's strict
YAML parser rejected it (`mapping values are not allowed in this
context at line 3 column 95`); clauditor's own `_frontmatter.py`
parser accepted it silently.

This ticket tightens `check_conformance` so that class of bug is
caught by `clauditor lint` / the `SkillSpec.from_file` soft-warn hook
before it lands in a file that won't render on GitHub.

## Discovery

### Committed-state findings (Codebase Scout)

**`src/clauditor/_frontmatter.py`** — deliberately permissive YAML
subset parser. `_split_key_value` uses `line.partition(":")` — it
splits on the **first** colon only. So
`compatibility: Requires ... Optional: the ...` is stored as a raw
string; the embedded `: ` is never examined. Module docstring
explicitly scopes it to "the shape real SKILL.md files use today,
not full YAML".

**`src/clauditor/conformance.py`** — 24+ existing `AGENTSKILLS_*`
codes, all declared **inline** in `ConformanceIssue(code=..., severity=..., message=...)`
constructor calls (not a central constant table).
`AGENTSKILLS_FRONTMATTER_INVALID_YAML` already exists at ~lines
219-233 and catches `ValueError` from `parse_frontmatter`, with
newline sanitization in the message. After permissive-parse
succeeds, other `_check_<field>` functions run per top-level key.

**PyYAML dep status** — **NOT a runtime or dev dep.** `pyproject.toml`
has `dependencies = []` and no PyYAML in dev deps or `uv.lock`. The
"minimal hand-rolled YAML reader to avoid adding PyYAML as a runtime
dependency" is an **explicit project convention** documented in
`tests/test_bundled_skill.py`'s module docstring.

**Test seams:**
- `tests/test_frontmatter.py` — 18 cases; **no test for unquoted
  space-colon-space plain scalars**.
- `tests/test_conformance.py` — extensive coverage of existing
  AGENTSKILLS_* codes; has negative tests for `INVALID_YAML` (missing
  delimiter, colon-less line) but not for the unquoted-colon case.
- `tests/test_bundled_{skill,review_skill}.py` — exercise
  `check_conformance` indirectly via `SkillSpec.from_file`; 18 tests
  for the review skill, 2 for the clauditor skill.

**CLI surface** (`src/clauditor/cli/lint.py`):
- exit 0 — no issues or warnings-only without `--strict`
- exit 1 — parse/load failure OR `AGENTSKILLS_FRONTMATTER_INVALID_YAML`
  (special case; overrides `--strict`)
- exit 2 — any error-severity issue OR warnings with `--strict`

**Soft-warn hook** (`SkillSpec.from_file`): only WARNINGS surface to
stderr with the `clauditor.conformance: <CODE>: <message>` prefix.
Errors stay silent at this seam — they surface when the user runs
`clauditor lint`.

**Regression fixture (pre-#79 file at commit `6b858b8`):**

```yaml
---
name: review-agentskills-spec
description: Review the current agentskills.io specification and ...
compatibility: Requires network access to fetch https://agentskills.io/specification. Optional: the gh CLI for issue creation.
metadata:
  clauditor-version: "0.0.0-dev"
disable-model-invocation: true
allowed-tools: WebFetch, Read, Grep, Glob, Bash(gh issue create:*)
---
```

The bug is the unquoted `Optional: ` on the `compatibility:` line.

### Applicable rules (Convention Checker)

| Rule | Applies | Constraint |
| --- | --- | --- |
| `pure-compute-vs-io-split.md` | yes | `check_conformance` stays pure: no I/O, no raise, returns `list[ConformanceIssue]`. |
| `pre-llm-contract-hard-validate.md` | yes | Fail loudly at the parse boundary: any YAML that strict parsers reject must produce a user-visible issue. |
| `llm-cli-exit-code-taxonomy.md` | yes | New error-severity issue routes to exit 2 (input validation), not 1 or 3. |
| `plan-contradiction-stop.md` | yes | Standard Ralph hygiene; escalate if plan preconditions turn out false. |
| `constant-with-type-info.md` | **no** — conformance.py uses inline code declarations, not a central table. Existing convention overrides. |

### Key convention flip

Initial ticket ranked Option A (PyYAML re-parse) as the strongest
backstop. Discovery surfaces that **PyYAML is explicitly avoided by
project convention** — the whole reason `_frontmatter.py` exists is
to keep PyYAML off the dependency list. This flips the recommendation
toward Option B (targeted regex, no new dep).

Option C (both) would violate the convention for the PyYAML half
with no marginal benefit Option B doesn't already provide for this
bug class.

## Scoping questions

### Q1 — Option A/B/C after the dep-convention discovery?

Ticket originally leaned toward A or C. Discovery makes B the
aligned choice.

- **A. PyYAML strict re-parse** — broad coverage of every strict-vs-
  permissive divergence, but violates the "no PyYAML" convention.
  Adds a runtime dep the project has explicitly avoided.
- **B. Targeted check for unquoted `: ` inside plain scalars** —
  no new dep, aligned with convention, catches the exact bug class
  the ticket names, narrow coverage.
- **C. Both** — all of A's cost for B's benefit.

**Recommend B.** If future strict-vs-permissive bugs surface, we can
add more targeted checks case-by-case. A PyYAML dep is a one-way
door.

### Q2 — Where does the new check live?

- **A. In `conformance.py::check_conformance` as a new `_check_*()` function** — runs after the permissive parse succeeds, inspects the parsed dict + raw text, appends a new `AGENTSKILLS_FRONTMATTER_*` code if any top-level scalar value contains unquoted `: `.
- **B. In `_frontmatter.py::parse_frontmatter` — parser becomes stricter** — the parser itself raises `ValueError`, which flows through the existing `AGENTSKILLS_FRONTMATTER_INVALID_YAML` code.

**Recommend A.** Keeps the parser's permissive contract intact
(documented by its module docstring); new behavior lives under a
new conformance code so error messages can be specific and
debuggable. Option B changes parser semantics, may break existing
direct `parse_frontmatter` callers/tests, and blurs the "what does
this code detect" mapping.

### Q3 — New error code name and severity?

- **A. `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR` / `error`** — specific, debuggable, exit 2 per `llm-cli-exit-code-taxonomy.md`.
- **B. `AGENTSKILLS_FRONTMATTER_YAML_AMBIGUOUS_SCALAR` / `error`** — slightly broader name; might fit future related checks.
- **C. Warning severity** — skill still loads for Claude Code, but file fails on GitHub. Warning would mean `clauditor lint` still exits 0.

**Recommend A** for name (matches the existing specific-code
convention — e.g. `NAME_CONSECUTIVE_HYPHENS`). **Recommend error**
for severity — the file is de facto broken on GitHub; silent-
warning would defeat the ticket's motivation.

### Q4 — How strict should the regex pattern be?

The check needs to reject `" Optional: the"` but accept `"https://foo"`
(colon-slash, no space after colon). Draft pattern: for each top-
level scalar value that came from an **unquoted** line, reject if
`: ` (colon followed by space) appears anywhere in the value.

Edge cases:
- **Quoted values (`"..."` or `'...'`)**: the parser's `_strip_quotes`
  runs before the check sees the value. Need to pass the check
  ONLY unquoted values — requires a small change to
  `_frontmatter.py` to expose "was this value quoted?" OR track
  quoting in the new check by re-inspecting the raw text.
- **Nested `metadata:` values**: same rule should apply. Less
  common in practice but worth the consistency.
- **The actual URL case `https://`**: `:/` not `: ` — passes the
  check naturally.

Options:

- **A. Re-inspect raw text in the new check** — the check function
  walks the raw frontmatter text directly (regex-match each
  `key: value` line where the value isn't quote-wrapped), no
  changes to `_frontmatter.py`.
- **B. Extend `_frontmatter.py` to record quoted-vs-unquoted per value** — cleaner data flow but widens the parser's public surface.

**Recommend A.** Zero changes to `_frontmatter.py`; the new check is
self-contained. A one-line regex in conformance.py is simpler than
a parser-level contract change.

### Q5 — Scope the check to top-level scalars only, or include nested `metadata:` values?

- **A. Top-level only** — matches where the #79 bug occurred; simpler.
- **B. Top-level + nested `metadata:`** — consistency; trivial extra coverage.

**Recommend B.** The regex is the same; the walk just visits one
more level. Future nested-block bugs get caught for free.

## Architecture review

Targeted review for a small validator extension. Most axes n/a (no
auth surface, no runtime-path change, no schema evolution). Material
axes:

| Area | Rating | Finding |
| --- | --- | --- |
| Regex correctness | concern → resolved in DEC-007 | False-positive / false-negative edges: URLs, quoted values, nested `metadata:`, comments. Algorithm codified below. |
| Testing strategy | concern → resolved in DEC-006 | Three test classes enumerated: regression, quote awareness, nested scope. |
| Existing skills | pass | Verified: `src/clauditor/skills/clauditor/SKILL.md` and post-#79 `.claude/skills/review-agentskills-spec/SKILL.md` both pass the new check. No breakage. |

No blockers.

## Refinement log

### Decisions

**DEC-001 — Option B (targeted regex, no PyYAML dep).**
Discovery surfaced an explicit project convention ("minimal hand-
rolled YAML reader to avoid adding PyYAML as a runtime dependency",
documented in `tests/test_bundled_skill.py`). Adding PyYAML is a
one-way door; a targeted check catches the exact bug class the
ticket names without importing a heavy dep. Future strict-vs-
permissive divergences can be handled one-at-a-time with additional
targeted checks.

**DEC-002 — New check lives in `conformance.py`, not `_frontmatter.py`.**
`_frontmatter.py`'s module docstring explicitly scopes it to a
permissive YAML subset; that contract is load-bearing for error-
message stability and for downstream direct callers. The new
behavior is a conformance concern (does this file render on
GitHub?), not a parsing concern (can we read the dict?). Adding it
to `conformance.py` keeps both contracts clean.

**DEC-003 — Code name `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR`, severity `error`.**
Name matches existing specific-code convention
(e.g. `AGENTSKILLS_NAME_CONSECUTIVE_HYPHENS`). Severity `error`
because the file fails to render on GitHub — a warning would let
`clauditor lint` exit 0 on a de facto broken file, defeating the
ticket's motivation. CLI exit routes to 2 per
`.claude/rules/llm-cli-exit-code-taxonomy.md` (input validation
failure).

**DEC-004 — Raw-text inspection, no `_frontmatter.py` API widening.**
The check walks the raw frontmatter text line-by-line rather than
requiring `parse_frontmatter` to report quoted-vs-unquoted. Zero
parser changes; the check is fully self-contained in
`conformance.py`.

**DEC-005 — Scope: top-level scalars + nested `metadata:` values.**
The line-by-line walker naturally visits every key:value line
regardless of nesting. A nested unquoted `: ` is exactly as
problematic as a top-level one; the walk is free.

**DEC-006 — Test coverage: three classes, explicit cases.**

Each class lives in `tests/test_conformance.py` and uses
`check_conformance(text, path)` directly (pure-compute, no
`tmp_path` needed for most cases).

- **`TestUnquotedColonInScalarDetection`**:
  - `test_pre_79_fixture_flagged` — verbatim pre-#79 SKILL.md
    content → expects one `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR`
    error at the `compatibility:` line.
  - `test_post_79_fixture_passes` — quoted compatibility value →
    expects zero issues of the new code.
  - `test_clauditor_bundled_skill_passes` — current
    `src/clauditor/skills/clauditor/SKILL.md` → expects zero issues
    of the new code.

- **`TestQuoteAwareness`**:
  - `test_double_quoted_value_with_colon_space_passes` —
    `description: "Note: when X happens"` → no issue.
  - `test_single_quoted_value_with_colon_space_passes` —
    `description: 'Note: when X happens'` → no issue.
  - `test_unquoted_value_with_colon_space_flagged` —
    `description: Note: when X happens` → one issue.
  - `test_url_in_unquoted_value_passes` —
    `link: See https://example.com/page for details` → no issue
    (colon-slash, no space after).
  - `test_allowed_tools_colon_star_passes` —
    `allowed-tools: Bash(gh issue create:*)` → no issue (colon-
    star, no space after).

- **`TestNestedMetadataScope`**:
  - `test_nested_unquoted_value_with_colon_space_flagged` — a
    `metadata:` child line with unquoted `: ` → one issue.
  - `test_nested_quoted_value_with_colon_space_passes` — the
    same child, quoted → no issue.

**DEC-007 — Algorithm (raw-text walker).**

```python
def _check_unquoted_colon_in_scalar(
    skill_md_text: str, issues: list[ConformanceIssue]
) -> None:
    """Reject unquoted ``: `` inside scalar values.

    Walks the raw frontmatter text line-by-line. For each line that
    looks like ``key: value``, extract the value portion, skip it if
    empty or wrapped in matching quotes, otherwise flag the line if
    ``": "`` appears anywhere in it.

    Runs AFTER ``parse_frontmatter`` has already succeeded; the
    existing ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` short-circuit
    handles files that fail permissive parsing first.
    """
    # Extract the frontmatter block (between the two ``---`` markers).
    # If no frontmatter, no-op.
    lines = skill_md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return
    try:
        end_idx = next(
            i for i, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        return  # malformed — already caught by INVALID_YAML

    for lineno, line in enumerate(lines[1:end_idx], start=2):
        # 1-based line numbers in error messages (YAML convention,
        # matches GitHub's "line 3 column 95" style).
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.lstrip()
        if not value:
            continue  # ``metadata:`` block header, no scalar to check
        # Quote detection: first char determines quoting. The
        # permissive parser already stripped matching quotes, but
        # here we're inspecting raw text, so this is our own check.
        if (value[0] == '"' and value.rstrip().endswith('"')) or \
           (value[0] == "'" and value.rstrip().endswith("'")):
            continue
        if ": " in value:
            issues.append(
                ConformanceIssue(
                    code="AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR",
                    severity="error",
                    message=(
                        f"Frontmatter line {lineno} has ': ' inside "
                        f"an unquoted value for key {key.strip()!r}: "
                        f"strict YAML parsers (including GitHub's) "
                        f"treat this as a nested mapping. Wrap the "
                        f"value in double quotes."
                    ),
                )
            )
```

Design notes:

- **Runs after existing parse succeeds** — if `parse_frontmatter`
  raises, the existing `AGENTSKILLS_FRONTMATTER_INVALID_YAML` fires
  and `check_conformance` short-circuits before reaching this
  check. No double-fire risk.
- **Line numbers 1-based from the first YAML content line** — matches
  YAML convention and the GitHub error format ("line 3 column 95")
  so users can cross-reference.
- **Quote detection is first-and-last char match** — avoids false
  positives when a quote appears mid-string (e.g. `"foo" bar baz`
  isn't a valid quoted scalar; we fall through to the `: ` check,
  which is the conservative choice).
- **Message names the key** — debuggability; user can Cmd-F to find
  the offending line.
- **Message recommends the fix** ("wrap in double quotes") — makes
  the error self-serve.

**DEC-008 — No CLI code changes.**
The existing `clauditor lint` CLI already routes error-severity
issues to exit code 2. The new code slots into that path with no
special-casing. The only file that changes is `conformance.py`
(and its tests).

**DEC-009 — Documentation note in `docs/cli-reference.md`.**
`docs/cli-reference.md` lists the `AGENTSKILLS_FRONTMATTER_INVALID_YAML`
exit-1 special case. The new code does NOT need a special case
(routes normally through error → exit 2). But the conformance
section should mention the new code alongside existing codes for
completeness. Small prose addition; no new table required.

**DEC-010 — Scope guard.**
No changes to:
- `src/clauditor/_frontmatter.py` (public contract preserved).
- `pyproject.toml` (no PyYAML, no other dep).
- `src/clauditor/cli/lint.py` (existing exit code flow handles it).
- `src/clauditor/spec.py::SkillSpec.from_file` (soft-warn hook
  surfaces warnings only — errors stay silent at that seam, same
  as all other error-severity codes).
- Existing bundled skills' `SKILL.md` content (verified no breakage).

### Session notes

- **Dep convention discovery was load-bearing.** The ticket's initial
  preference for Option A (PyYAML) would have violated an explicit,
  documented project constraint. Discovery caught it. Codify in
  US-004 if this generalizes to a "check the project's stated
  convention before adopting a 'standard' approach" rule.
- **Raw-text inspection vs parser-extension tradeoff (DEC-004).**
  Keeping the walker self-contained in `conformance.py` is cheaper
  this round. If a future ticket needs quote-awareness for a
  different check, the parser-extension refactor becomes worth it;
  not now.

## Detailed breakdown

### US-001 — Implement `_check_unquoted_colon_in_scalar` + tests + doc note

**Description.** Add the new conformance check to `conformance.py`
with TDD: write all 9 test cases from DEC-006 first, watch them fail
against an empty `_check_unquoted_colon_in_scalar` stub, then
implement the algorithm from DEC-007 and watch them pass. Also add
a short doc note to `docs/cli-reference.md` listing the new code
alongside the existing conformance codes. One atomic commit.

**Traces to:** DEC-001 through DEC-010 (all).

**Files.**

- `src/clauditor/conformance.py` —
  - Add `_check_unquoted_colon_in_scalar(skill_md_text, issues)` per
    DEC-007. Place it alongside the other `_check_*` functions.
  - Call it from `check_conformance` AFTER
    `parse_frontmatter` succeeds (so the existing
    `AGENTSKILLS_FRONTMATTER_INVALID_YAML` short-circuit wins for
    files that fail permissive parsing). Exact call site: right
    after `parsed, body = parse_frontmatter(skill_md_text)` and
    before any other `_check_*` that inspects `parsed`. The new
    check doesn't need `parsed` — it walks the raw text.
- `tests/test_conformance.py` —
  - `TestUnquotedColonInScalarDetection` (3 cases per DEC-006).
    Pre-#79 fixture content is inline in the test (DEC-006 lists
    it verbatim); don't read a file. Use the clauditor-skill
    content from `git show HEAD:src/clauditor/skills/clauditor/SKILL.md`
    inline, NOT via file I/O.
  - `TestQuoteAwareness` (5 cases).
  - `TestNestedMetadataScope` (2 cases).
- `docs/cli-reference.md` — append a 1-2-sentence note to the
  conformance-codes section mentioning
  `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR` and what
  triggers it. No new table or subsection — consistency with how
  other codes are referenced.

**Depends on:** none (first story).

**Acceptance criteria.**

- All 9 new test cases pass (see DEC-006 for the list).
- Existing `tests/test_conformance.py` suite remains green.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80% coverage.
- `tests/test_bundled_review_skill.py` and
  `tests/test_bundled_skill.py` still pass — both bundled skills
  do not trigger the new check (DEC-006 has explicit positive
  tests for this; they should validate in live pytest too).
- `docs/cli-reference.md` has a grep-able mention of
  `AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR`.
- Scope guard (DEC-010): `git diff` shows NO changes to
  `src/clauditor/_frontmatter.py`, `pyproject.toml`,
  `src/clauditor/cli/lint.py`, or `src/clauditor/spec.py`.

**Done when:** one atomic commit with message like
`#80: Add AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR check`,
all acceptance criteria pass.

**TDD:**

Write these 9 tests FIRST, then implement. Each failure message
should name the test and the expected issue code. Order in the
TDD workflow:

1. Write `TestUnquotedColonInScalarDetection::test_pre_79_fixture_flagged`
   with the verbatim pre-#79 frontmatter inline. Expected:
   `check_conformance(text, path)` returns at least one
   `ConformanceIssue` with `code == "AGENTSKILLS_FRONTMATTER_UNQUOTED_COLON_IN_SCALAR"`
   and `severity == "error"`.
2. Write `test_post_79_fixture_passes` (quoted version). Expected:
   zero issues of that code.
3. Write `test_clauditor_bundled_skill_passes`. Expected: zero
   issues of that code.
4. Write `TestQuoteAwareness::test_double_quoted_value_with_colon_space_passes`.
5. Write `test_single_quoted_value_with_colon_space_passes`.
6. Write `test_unquoted_value_with_colon_space_flagged`.
7. Write `test_url_in_unquoted_value_passes`.
8. Write `test_allowed_tools_colon_star_passes`.
9. Write `TestNestedMetadataScope::test_nested_unquoted_value_with_colon_space_flagged`
   and `test_nested_quoted_value_with_colon_space_passes`.

Stub `_check_unquoted_colon_in_scalar` to a no-op, run pytest, watch
the 6 positive-flag tests fail and the 3 pass-through tests pass.

Then implement per DEC-007. All 9 pass.

---

### US-002 — Quality Gate

**Description.** Standard 4-pass code-review gate over the full
changeset. No wheel-verification step this time (DEC-005 of the #75
plan was specific to a packaging concern; #80 has no packaging
surface). Focus passes on the algorithm's correctness edges
(regex false-positive / false-negative behavior) and terminology
consistency in the new doc note.

**Traces to:** standard quality conventions; no DEC-### drive this
directly.

**Files.** None modified unless review surfaces real issues.

**Depends on:** US-001.

**Acceptance criteria.**

- **4 code-review passes** via `code-reviewer` agent across the
  full changeset (`dev..HEAD`). Fix real issues each pass. Note
  false positives.
- **CodeRabbit review** on the open PR. Address real findings.
- **Ruff + pytest green:**
  - `uv run ruff check src/ tests/`
  - `uv run pytest --cov=clauditor --cov-report=term-missing`
    passes at 80% gate.
- **Explicit algorithm sanity check.** Read through
  `_check_unquoted_colon_in_scalar` once post-implementation with
  an eye on each DEC-007 design note:
  - Does it correctly skip when no `---` frontmatter is present?
  - Does it handle the no-closing-`---` case without raising
    (fall-through to existing INVALID_YAML)?
  - Are line numbers 1-based and consistent with YAML convention?
- **No new runtime deps.** `uv.lock` should be unchanged unless
  a test-only dep was added. Confirm with
  `git diff 1083cc3..HEAD -- uv.lock pyproject.toml`.

**Done when:** all passes complete, all real issues fixed, final
pytest + ruff green.

**TDD:** N/A — Quality Gate is verification.

---

### US-003 — Patterns & Memory

**Description.** Capture anything learned from this refactor. Two
candidate patterns stand out:

1. **"Check project conventions before adopting a 'standard'
   approach."** The ticket's initial lean toward PyYAML was
   reasonable on general grounds but violated an explicit project
   convention that was only discoverable by reading
   `tests/test_bundled_skill.py`'s module docstring.
   Generalizable lesson: when a ticket proposes a "standard" tool,
   check the project for a documented reason to avoid it before
   committing to the shape. Candidate rule:
   `.claude/rules/check-dep-conventions-before-proposing.md` (or
   fold as a section into an existing rules-maintenance doc).

2. **"Permissive parser + strict validator at conformance layer"
   as a recurring shape.** clauditor's `_frontmatter.py` is
   deliberately permissive; `conformance.py` adds strict checks on
   top. This refactor extends that pattern. If future tickets
   want similar "tighten strictness without touching the parser"
   work, there's now a cookbook.

**Traces to:** closing-ceremony convention (always last story).

**Files.**

- `.claude/rules/` — add a new rule only if one of the above
  patterns has concrete future callers on the horizon. Prefer
  small focused rules over omnibus prose.
- Memory — nothing obvious to add (the conventions codified in
  this work already live in `.claude/rules/` and module
  docstrings; no session-specific knowledge to persist).
- Plan doc `Session notes` extended with the closeout outcome.

**Depends on:** US-002 (Quality Gate).

**Acceptance criteria.**

- At most ONE new rule file added, if the "check dep conventions"
  pattern is worth codifying. If in doubt, skip — rules without
  concrete callers rot.
- Plan doc updated with outcome notes.

**Done when:** Memory/rules reflect anything durable; plan phase
advances to `devolved`.

**TDD:** N/A.

## Beads manifest

*(Phase 7 — filled on devolve.)*
