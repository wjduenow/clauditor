# Rule: Edits to the bundled `/clauditor` SKILL.md workflow travel with parallel doc updates

When you edit the `## Workflow` section of
`src/clauditor/skills/clauditor/SKILL.md` — changing step count,
reordering, renaming a step, or inserting/removing a branch — the
**same PR** must update the matching narrative summaries in
`docs/skill-usage.md` and `README.md`'s "Using /clauditor in Claude
Code" section. Step-count and terminology drift between these three
files is the failure mode: a user reading the README learns one
story, opens `docs/skill-usage.md` for detail and finds a different
one, then invokes `/clauditor` and sees a third. All three are
load-bearing descriptions of the same workflow at different
abstraction levels.

## The triangle

```
README.md "Using /clauditor in Claude Code"   (D2 lean teaser, ~6-10 lines,
         │                                     links to skill-usage.md)
         ▼
docs/skill-usage.md "What Claude does" list   (narrative summary, N items,
         │                                     readable on GitHub)
         ▼
src/clauditor/skills/clauditor/SKILL.md       (instructional Markdown for
         ## Workflow                           Claude Code; canonical)
```

Read order for the reader flows top-down; edit order for the author
flows bottom-up. SKILL.md is canonical (it's the actual instruction
set Claude follows); the two docs are summaries that must stay in
sync with it.

## The pattern

1. **Edit SKILL.md first** — this is the source of truth.
2. **Update `docs/skill-usage.md`'s numbered list** to reflect the
   new step shape. The doc's abstraction level is *narrative
   summary*: SKILL.md's Steps 1+2 often collapse into a single
   `docs/skill-usage.md` item, and L2 is typically omitted (since
   SKILL.md runs L2 conditionally based on `sections`). Preserve
   that condensation; do not 1:1 copy SKILL.md's steps into
   skill-usage.md.
3. **Update `README.md`'s "Using /clauditor in Claude Code"
   section** only if the workflow's *shape* changed (a new branch,
   a new command name). Do not bump the README for pure prose
   cleanup in SKILL.md. When you do touch the README, respect the
   D2 lean teaser budget per
   `.claude/rules/readme-promotion-recipe.md` — one sentence
   extension, no new code block, heading text byte-identical.
4. **Mirror terminology across all three files**. Cross-reference
   the phrasing anchors established in `docs/cli-reference.md` (the
   CLI reference is the terminology source of truth for command
   descriptions — see DEC-009 of `plans/super/54-teach-propose-eval-workflow.md`
   for the canonical phrasing list).
5. **Add a regression assertion in `tests/test_bundled_skill.py`**
   for any load-bearing string the new workflow introduces
   (e.g. `"propose-eval"`, `"capture"`, a new command name). One
   `assert "<string>" in SKILL_MD.read_text()` per branch — simple
   prose-presence check, not structural. Structural assertions
   (e.g. "exactly N numbered steps") rot too fast for prose-edit
   velocity.

## Why this shape

- **Three files, one story**: the triangle is the canonical
  reader path. A writer who edits only SKILL.md silently desyncs
  the README and skill-usage; a reader who lands on the README
  first gets a version-zero view of a workflow the skill no
  longer follows. The "update all three in the same PR" rule is
  the only mechanism that keeps the story coherent across
  abstraction levels.
- **Condensation is intentional**: `docs/skill-usage.md` is a
  narrative summary, not a step-by-step mirror. Early versions
  of this pattern tried a 1:1 step mapping; that bloats the doc
  and makes future edits painful (every SKILL.md renumber
  cascades). Preserving the summary-level abstraction means
  docs/skill-usage.md changes are smaller and less churn-prone.
- **Terminology anchor lives in `docs/cli-reference.md`**: the
  CLI reference is where each command's description is
  authoritatively phrased (the "cost-free preview",
  "LLM-assisted bootstrap", "sibling `<skill>.eval.json`" kind
  of phrasing). SKILL.md and skill-usage.md mirror that
  terminology without duplicating the flag tables or examples.
  This concentrates the rebase hazard: when a CLI flag or
  description changes, you update cli-reference.md and grep the
  two summary files for the old phrase.
- **Regression assertion is the cheap insurance**: a single
  `assert "<string>" in body` catches the specific revert-
  without-noticing failure mode. The PR that adds the Step also
  adds the assertion; a future prose simplification that drops
  the string trips the test in CI. `.claude/rules/` does not
  prohibit structural tests, but prose-presence is the right
  ceiling for this class of edit per DEC-007 of
  `plans/super/54-teach-propose-eval-workflow.md`.
- **Body line cap protects the budget**: the
  `tests/test_bundled_skill.py` gate at 500 lines ensures the
  skill stays readable for Claude. New steps should be concise
  (ours landed at ~100 body lines); if a future addition would
  push past ~200-300 lines, split into a separate doc with a
  pointer rather than inlining.

## Canonical implementation

- **SKILL.md edit site**: `src/clauditor/skills/clauditor/SKILL.md`,
  the `## Workflow` section. Canonical example:
  the Step 3 "Bootstrap eval spec if missing" insertion (commit
  `6bb1e31`).
- **Narrative summary**: `docs/skill-usage.md`, the
  "What Claude does" numbered list. Canonical example: the
  5-item shape that followed the SKILL.md edit (commit
  `309933c`).
- **README teaser**: `README.md`, the `## Using /clauditor in
  Claude Code` section. Canonical example: the single-sentence
  extension mentioning the propose-eval fallback (also commit
  `309933c`).
- **Regression assertion**:
  `tests/test_bundled_skill.py::TestSkillMdBody::test_body_mentions_propose_eval`
  — one-line prose-presence check (commit `187571e`).
- **Terminology anchors**: `docs/cli-reference.md` — the
  `## propose-eval` subsection's phrasing list defined by DEC-009
  of `plans/super/54-teach-propose-eval-workflow.md`.
- **Companion rule**:
  `.claude/rules/readme-promotion-recipe.md` for the README
  teaser budget and anchor-preservation discipline.

## When this rule applies

- Any edit to `src/clauditor/skills/clauditor/SKILL.md`'s
  `## Workflow` section that changes: step count, step order,
  step titles, command names invoked, or the conditional
  branching between steps.
- Any edit that introduces a new clauditor subcommand the skill
  invokes (the `allowed-tools` line in SKILL.md frontmatter also
  needs updating — see DEC-001 of
  `plans/super/54-teach-propose-eval-workflow.md` for the
  wildcard-plus-explicit-entry pattern when the subcommand is
  already wildcarded).

## When this rule does NOT apply

- Prose cleanup inside a single step that does not change step
  count, name, or branching. A typo fix, grammar tweak, or
  clarification sentence in Step 4's body does not require
  touching `docs/skill-usage.md` or the README.
- Edits to SKILL.md frontmatter fields other than `allowed-tools`
  (e.g. `description`, `metadata.clauditor-version`, the build-
  stamped version line). Those are internal to the skill and do
  not have narrative-summary mirrors.
- Edits to unbundled skills (any skill not at
  `src/clauditor/skills/clauditor/`). The triangle is specific to
  the canonical bundled skill. Other bundled skills added in the
  future should define their own summary/teaser pair before this
  rule applies to them.
