# Rule: Refresh a rule's framing when its context shifts; delete only when its pattern is obsolete

When a refactor changes the **context** a `.claude/rules/*.md` rule
describes (file locations, framing terminology, surrounding system
architecture) but does NOT change the **pattern** the rule codifies
(the technical shape, the foot-gun it prevents, the invariant it
protects), the correct action is to **refresh the rule's framing and
example paths in-place**. Do NOT delete the rule; do NOT create a
new rule that duplicates the pattern under a different name. Rules
are load-bearing because the pattern is durable, not because the
surrounding prose happens to mention specific files.

Deletion is correct only when the **pattern itself is obsolete** —
e.g. the foot-gun no longer exists because the underlying machinery
was replaced, or the invariant is now enforced by a different
mechanism (e.g. a type system, a schema validator, a build-time
check) that makes the rule's guidance vacuous.

## The question to ask

When a refactor touches a file the rule's canonical-implementation
section names, ask two questions in order:

1. **Is the pattern still load-bearing?** Will a future developer
   writing similar code still encounter the foot-gun or benefit from
   the invariant? If yes → **refresh**. If no → continue to Q2.
2. **Has the pattern been replaced by a stronger mechanism?** Is
   there now a type check / schema validator / build gate that makes
   the rule's guidance redundant? If yes → **delete** with a migration
   note in the deletion commit. If no → the pattern is still
   load-bearing; go back to Q1.

If both questions answer "no the pattern is still load-bearing",
refresh. Most refactor-driven rule-touch decisions end here.

## The refresh shape

A refresh edit-in-place typically includes:

- **Opening framing**: reword so the rule's "when this applies"
  matches the post-refactor context. Example: "When a bundled skill
  is excluded from `clauditor setup`" →  "When a maintainer-only
  skill lives at repo-root `.claude/skills/`". The intent is
  identical; only the anchoring context shifts.
- **Code-example path strings**: update to match the new file
  locations. Example: `# src/clauditor/skills/<name>/` →
  `# .claude/skills/<name>/ (repo root)`.
- **"Why this shape" reasoning**: re-audit each bullet for claims
  that refer to the old context. A bullet whose argument hinges on
  "the skill is in the package" no longer works after the skill
  moves out of the package — rewrite the argument to match the new
  reality while preserving the underlying truth the bullet defends.
- **"What NOT to do" anti-patterns**: verify each anti-pattern
  still describes a real failure mode post-refactor. An anti-pattern
  that was "don't put the skill in the real repo" after the skill
  legitimately moves INTO the real repo becomes "don't use the real
  repo as the test's `project_dir`" — the foot-gun is still real,
  it just wears a different shape.
- **Canonical-implementation section**: the file/function/class
  anchor typically stays (that's what's being refactored, not
  deleted). Historical validation notes and failure-mode examples
  stay **byte-verbatim** — they document real events and real
  failure signatures.

## Why this shape

- **Rules anchor on patterns, not files.** A rule like
  `pure-compute-vs-io-split.md` is about a code shape that recurs
  across the codebase. If one canonical anchor's file location
  changes, deleting the rule because "the anchor moved" destroys a
  pattern that still constrains five other files. The pattern is
  primary; the specific anchor is secondary.
- **Anti-patterns outlive their context.** The foot-gun a rule
  describes typically has a specific triggering condition
  ("subprocess inherits the cwd's `.claude/`", "bool is a subclass
  of int in Python", "the monotonic clock collides with the asyncio
  event loop"). These conditions are language-level or
  framework-level truths that don't evaporate when a single file
  moves. A rule describing such a truth should survive file-location
  churn.
- **Deletion is a lossy operation.** Once a rule is deleted, the
  next developer facing the same foot-gun re-learns it the hard way.
  The rule's purpose was to transfer a past session's painful
  discovery to future sessions as cheap prose. Delete only when the
  discovery genuinely no longer applies.
- **Refresh is cheap; replacement is not.** An in-place refresh is
  typically 10-30 lines changed in a 150-line rule file. Writing a
  new rule from scratch to cover the same pattern is a much larger
  cognitive + review cost, and the result is usually no better than
  the refreshed original.
- **The rule's title is a stable handle.** Rule filenames like
  `internal-skill-live-test-tmp-symlink.md` are referenced by git
  history, cross-linked from other rules, and sometimes cited by
  commit messages. Renaming or deleting them breaks those
  references for no functional gain. Refresh-in-place keeps every
  reference alive.

## What NOT to do

- Do NOT delete a rule because its canonical anchor moved. The
  anchor's location is secondary; the pattern's validity is
  primary. See "The question to ask" above — delete only when the
  pattern itself is obsolete.
- Do NOT create a parallel "replacement" rule alongside an old one
  when the refresh would do. Two rules describing the same pattern
  drift against each other within a few sessions; one wins and the
  other rots. Refresh-in-place preserves a single source of truth.
- Do NOT rewrite the historical validation notes in the
  canonical-implementation section. "The pattern was validated on
  2026-04-20 — live run took 126.57s" is a factual record, not
  prose to be smoothed. Preserve byte-verbatim.
- Do NOT preserve stale prose just because it used to be there. If
  a bullet in "Why this shape" argues from an obsolete context, the
  rule now contains a contradiction — rewrite the bullet to defend
  the same truth from the new context. Half-refreshed rules are
  worse than fully-refreshed ones.
- Do NOT refresh without validating. After the edit, run
  `rg '<old-path-string>' <rule-file>` and confirm zero hits for
  any path string that no longer exists. Terminology-sweep
  completeness matters; a refreshed rule that still mentions an
  old path is confusing.

## Canonical implementation

`.claude/rules/internal-skill-live-test-tmp-symlink.md` — the first
rule this pattern codifies. In #75 (`plans/super/75-move-review-skill.md`),
the `review-agentskills-spec` skill moved from
`src/clauditor/skills/review-agentskills-spec/` to
`.claude/skills/review-agentskills-spec/`. The rule's opening
framing initially leaned toward "bundled but excluded from
`clauditor setup`" — which stopped being accurate post-move.
DEC-002 and DEC-006 of the #75 plan captured the refresh-vs-delete
decision: the `tmp_path + symlink` pattern is still load-bearing
because the `claude` subprocess still inherits the cwd's `.claude/`
tree for slash-command discovery — that foot-gun exists regardless
of where the skill's source lives. So: refresh, not delete. US-002
of the plan landed the refresh (commit `9ba175d`).

The one-level shape of the refresh:

- Opening paragraph: "bundled / excluded from `clauditor setup`" →
  "maintainer-only skill lives at repo-root `.claude/skills/`".
- "The pattern" code example: `skill_root` comment `src/clauditor/skills/<name>/` →
  `.claude/skills/<name>/ (repo root)`.
- "Why this shape" first bullet: "Respects the internal-only
  invariant" (premised on the skill being packaged) → "Respects
  test isolation" (premised on `tmp_path` being a fresh project dir).
- "What NOT to do" first bullet: "Do NOT install the skill
  permanently in the repo" (false post-move — it IS in the repo) →
  "Do NOT extend `clauditor setup` to install `review-agentskills-spec`"
  (still the real anti-pattern).
- Canonical-implementation section: **byte-verbatim preservation**
  of the 2026-04-20 validation note and the
  "Unknown command: /review-agentskills-spec" failure-mode example.
- "When this rule applies" / "does NOT apply": reword "bundled skill
  filtered out of `clauditor setup`" → "maintainer-only skill living
  at repo-root `.claude/skills/`".

Traces to DEC-002 and DEC-006 of `plans/super/75-move-review-skill.md`.

## When this rule applies

Any future refactor that touches a file named in a
`.claude/rules/*.md` rule's canonical-implementation section. The
refactor may be a move (file relocation), a rename (symbol or file),
a split (one file becomes two), a merge (two files become one), or a
shape change (API signature, return type, module layout) that
invalidates path or symbol references inside the rule.

## When this rule does NOT apply

- Refactors that touch a file the rule does NOT name in its
  canonical-implementation section. If the rule's anchor is
  unaffected, the rule is unaffected — no refresh or deletion
  needed.
- Deletions of a canonical anchor where no replacement exists and
  the pattern genuinely has no other caller. Example: a rule
  codifying an API that the codebase no longer uses, with no sibling
  callers that would inherit the pattern. Delete with a migration
  note.
- Rules whose entire premise is obsolete because a stronger
  mechanism replaced them. Example: a rule codifying "always
  `isinstance` a JSON payload before indexing it" becomes vacuous
  if the codebase adopts a schema validator that runs before any
  indexing. Delete with a commit message explaining the
  replacement.
- Style preferences in the rule's prose (readability, grammar,
  example choice). Those belong in a separate small cleanup, not
  bundled with a refactor-driven refresh.
