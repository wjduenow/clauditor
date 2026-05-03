# Rule: Update PR title + body when a planning PR transitions to implementation

When a feature ships through this project's two-phase workflow — first
a planning PR (commits = plan files only, title suffixed `(plan)`,
body says "Phase: detailing — awaiting approval"), then implementation
commits added to the same branch — the **first implementation push
must also update the PR's title and body** to reflect the new state.
A title still saying `(plan)` and a body still telling reviewers to
"approve → devolve" misleads anyone arriving cold at the PR. The PR's
own metadata is the canonical signal of what it represents; a stale
suffix is a small but real lie that compounds across reviews.

## The transition

A planning PR is opened when the super-plan (`plans/super/<N>-<slug>.md`)
is committed and the user reviews it. Its shape is fixed:

- **Title:** `<N>: <short summary> (plan)`
- **Body:**
  - `Phase: detailing (awaiting approval)`
  - "Stories: K implementation + Quality Gate + Patterns & Memory"
  - "Decisions: M captured (DEC-001 through DEC-M)"
  - "Approve in Claude Code → say 'devolve' to create beads epic + tasks"
- **Draft state:** typically draft.

Once the user devolves the plan into beads and Ralph (or any other
implementation-phase mechanism) starts pushing real source/test
commits to the same branch, the PR is no longer a plan — it is the
implementation. Three artifacts must change at the moment of that
first implementation push:

1. **Title:** drop the `(plan)` suffix; rename to match the epic
   title (e.g. `#147: sidecar v3 with provider field (plan)` →
   `#147: Multi-provider — sidecar v3 with provider field`).
2. **Body:** replace the planning-phase template with an
   implementation summary covering changes, testing, compounding
   updates, and separable follow-ups. Keep the linked ticket / epic
   id at the top so the audit trail stays.
3. **Draft state:** flip from draft → ready for review **after** the
   implementation lands and validation passes (the Ralph PR
   graduation step in `/ralph-run`).

The three are not interchangeable. Title + body change at the *first
implementation push*; draft graduation happens at the *last* push
(when the work is ready for review). Conflating them either lies
about completion (graduate too early) or buries the signal that
implementation has started (rename too late).

## The mechanical recipe

Use the GitHub REST API directly, NOT GraphQL via `gh pr edit`.
Reason: the GraphQL endpoint emits a noisy "Projects (classic) is
being deprecated" warning on this repo and may fail the call even
when the underlying state change would have succeeded. The REST
`PATCH /repos/{owner}/{repo}/pulls/{n}` endpoint is unaffected. This
mirrors the same REST-vs-GraphQL discipline already documented for
draft graduation in `/ralph-run` Step 6.

```bash
# Title update — single field
gh api -X PATCH /repos/<owner>/<repo>/pulls/<N> \
    -f title="<new title without (plan) suffix>"

# Body update — multi-line via -F body=@/path/to/body.md
gh api -X PATCH /repos/<owner>/<repo>/pulls/<N> \
    -F body=@/tmp/pr-body.md
```

The body should follow the project's PR template:

```markdown
## Summary

<one-paragraph what-this-ships-and-why>

**Linked ticket:** #<N>
**Epic:** `<bead-id>`
**Stories shipped:** K implementation + Quality Gate + Patterns & Memory
**Decisions:** M (DEC-001 through DEC-M; see `plans/super/<N>-<slug>.md`)

## Changes
<grouped by user-story or by area>

## Testing
- `<lint cmd>` — clean
- `<test cmd>` — N passed, X% coverage
- <code review passes / CodeRabbit / etc>

## Compounding update
<rules touched, docs updated, memory entries written>

## Out of scope (separable follow-ups)
- <linked future tickets, with the cross-issue separability rationale>
```

## Why this shape

- **The PR is the audit artifact, not the workflow narrative.** A
  reader who lands on the PR a year from now (debugging a regression,
  cherry-picking onto a long-lived branch) sees the title + body
  first. A `(plan)` suffix on a fully-shipped feature is wrong, and
  "Approve in Claude Code → say 'devolve'" is meaningless to anyone
  outside the original planning session. The body should describe
  what shipped, not the workflow that produced it.
- **`(plan)` is a phase marker, not a feature name.** Keeping it
  past the planning phase is like keeping a `WIP:` prefix on a
  merged commit — once the state changes, the marker is a lie.
- **Title rename happens at the same edit as the first
  implementation merge** so reviewers (CodeRabbit, Bugbot, humans)
  who see the PR for the first time post-implementation get the
  correct framing immediately. Late renames let a reviewer waste
  time looking for "the plan" or commenting on a "plan" that's
  actually code.
- **Draft graduation is separate** from title rename because the
  signals mean different things. Dropping `(plan)` says "this is
  implementation, not a plan." Marking ready says "the
  implementation is ready for review." Both are correct at
  different moments — the first when work begins, the second when
  work completes.
- **REST API over GraphQL** for the metadata edit avoids the
  classic-projects deprecation warning that breaks `gh pr edit` on
  repos with classic project boards. Same reason `/ralph-run`'s PR
  graduation step uses REST. One fewer thing for future automation
  to debug.
- **Body update preserves the linked-ticket reference** so the audit
  trail back to `#<N>` stays intact. Reviewers and downstream
  consumers (release notes, changelog generation, post-merge
  follow-ups) all rely on that link.

## What NOT to do

- Do NOT keep the `(plan)` suffix in the title past the first
  implementation push. The title is checked into git history
  (commit messages, merge commits, release notes); a stale
  suffix follows the work forever.
- Do NOT use `gh pr edit --title <...>` on this repo. It calls the
  GraphQL endpoint, which emits the classic-projects deprecation
  warning and may fail silently. Use the REST API.
- Do NOT graduate the PR from draft → ready as part of the same
  edit that updates the title. Title-update happens at the *first*
  implementation push; draft graduation happens at the *last* push
  (after validation passes). Conflating them ships
  half-implemented work to reviewers.
- Do NOT rewrite the body to drop the linked ticket / epic id /
  decision-trace links. Those references are how downstream
  tooling and humans navigate back to the planning artifact. The
  template's "**Linked ticket:**" / "**Epic:**" / "**Decisions:**"
  block stays.
- Do NOT delete the planning-phase body wholesale before saving its
  outline somewhere. The "Stories shipped: K + QG + P&M" line and
  the decision count are factual records — fold them into the
  implementation body's summary block, do not lose them.
- Do NOT rename the title to something *different* from the epic
  title. The title should match the bead epic's canonical name so
  audit aggregation and search work consistently. If the epic title
  itself is wrong, fix the epic title first (via `bd update <epic>
  --title <...>`), then mirror it onto the PR.

## Canonical implementation

PR #165 (`feature/147-sidecar-provider-field`) on 2026-05-03 — the
first implementation transition that surfaced this rule.

- **Original title:** `#147: sidecar v3 with provider field (plan)`
- **Renamed to:** `#147: Multi-provider — sidecar v3 with provider field`
  (matches `clauditor-6ne` epic title)
- **Original body:** "Phase: detailing (awaiting approval)" with the
  "Approve in Claude Code → say 'devolve'" next-step block
- **Replaced with:** Summary / Changes / Testing / Compounding /
  Out-of-scope sections covering all 9 stories shipped (3216 tests,
  98.61% coverage)
- **REST API call:**
  `gh api -X PATCH /repos/wjduenow/clauditor/pulls/165
  -f title="..."`
  followed by a second PATCH with `-F body=@/tmp/pr165-body.md`.
  The `gh pr edit --title` attempt failed with the classic-projects
  GraphQL warning before the REST PATCH was used.

The user surfaced the gap mid-`/closeout`: implementation had
shipped (9 stories merged, PR graduated to ready, all reviewer
threads resolved), but the title still said `(plan)`. That is the
exact failure mode this rule prevents — the metadata staleness
survives all the way to the post-review state and is only caught
by a human glancing at the PR list.

## When this rule applies

- The first implementation push to a planning-phase PR (Ralph's
  first worker merge into `feature/<N>-<slug>`, OR a manual
  `git push` of source/test commits onto a planning-only branch).
- Any future workflow that opens a draft PR with a `(plan)` suffix
  and accumulates implementation commits on the same branch.
- Bead epics that ship behind a "plan first, then devolve"
  workflow per `super-plan` / `kickoff` skills.

The trigger is **commits adding source/test code to a branch
whose PR title carries `(plan)`**. The orchestrator detecting that
condition (Ralph, `/closeout`, a future `/devolve-merge` skill, a
human reviewer) is responsible for performing the title + body
update before pushing further.

## When this rule does NOT apply

- PRs that never used the planning-phase shape (e.g. small bug fixes
  opened as direct implementation PRs without a `plans/super/*.md`
  file). There is no `(plan)` suffix to drop and no
  planning-phase body to replace.
- Plan-only PRs that intentionally ship without follow-up
  implementation (rare — typically a "discovery" PR that captures a
  decision but doesn't gate code). The `(plan)` suffix is correct
  there and stays.
- Draft graduation. That's a separate transition handled by
  `/ralph-run`'s Step 6 PR-graduation block. This rule covers
  title + body; graduation covers draft state.
- One-off `gh pr comment` or `gh pr review` operations. Those don't
  alter the PR's identity metadata.

## Companion rules

- `/ralph-run` Step 6 (PR graduation) — uses the same REST-vs-
  GraphQL discipline for the `draft=false` flip. The two operations
  rhyme: this rule covers title/body, `/ralph-run` covers draft
  state, both prefer the REST PATCH endpoint to side-step the
  classic-projects deprecation warning.
- `.claude/rules/readme-promotion-recipe.md` — the broader pattern
  of "metadata reflects current state, not workflow stage." A
  promoted README section's H2 must match the canonical doc; a PR's
  title must match the canonical epic. Same shape, different
  surface.
- `.claude/rules/bundled-skill-docs-sync.md` — analogous "edit one,
  edit the others in the same PR" discipline. SKILL.md changes
  travel with `docs/skill-usage.md` + README; planning PRs
  transitioning to implementation travel with title + body
  updates.
