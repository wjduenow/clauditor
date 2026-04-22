# Skills catalog

Catalog of skills shipped with this repository, with live [clauditor
badge](./badges.md) status next to each entry. Each badge reflects
the skill's most recent iteration data on the `dev` branch; the URL
is constant, so the image updates automatically when CI re-runs
`clauditor grade` + `clauditor badge` and commits the refreshed
`.clauditor/badges/<skill>.json` artifact.

> Returning from the [root README](../README.md). This doc is the
> secondary placement for badges — per the
> [placement hierarchy](./badges.md#placement-hierarchy), a catalog
> page is the canonical one-glance view when the repo ships multiple
> skills. For the full placement guide, the color-logic table, and
> the embedding recipe, see [docs/badges.md](./badges.md).

## How this catalog is populated

1. Each skill lives under `src/clauditor/skills/<name>/SKILL.md`.
2. `clauditor grade src/clauditor/skills/<name>/SKILL.md` produces
   the iteration sidecars under `.clauditor/iteration-N/<name>/`.
3. `clauditor badge src/clauditor/skills/<name>/SKILL.md` aggregates
   those sidecars into `.clauditor/badges/<name>.json`, which is
   committed so shields.io can fetch it via
   `raw.githubusercontent.com`.
4. This page's badges point at those JSON files via the shields.io
   `endpoint` pattern — one Markdown image per skill.

Adding a new skill? Run `clauditor badge src/clauditor/skills/<new>/
SKILL.md --url-only --repo wjduenow/clauditor --branch dev` to get
a ready-to-paste image line, then add a row below.

## Skills

### `/clauditor`

![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/wjduenow/clauditor/dev/.clauditor/badges/clauditor.json)

The bundled Claude Code skill for the clauditor workflow itself —
activates when a user's prompt mentions quality-testing or grading
a Claude Code skill. Walks the author through `clauditor validate`
(L1) and `clauditor grade` (L3), and can propose an eval spec via
`clauditor propose-eval` when one is missing.

Source: [`src/clauditor/skills/clauditor/SKILL.md`](../src/clauditor/skills/clauditor/SKILL.md) · Eval: [`clauditor.eval.json`](../src/clauditor/skills/clauditor/assets/clauditor.eval.json)

The eval spec is maintainer-only — `test_args` references
`.claude/commands/chunk.md` in this repo's dev-local tree — so the
badge's live metadata comes from CI runs against that fixture
rather than from a user's project. The badge currently shows
`lightgrey · no data` because no iteration exists on the
`dev` branch yet; the first CI run of `clauditor grade` on the
bundled skill will populate real L1/L3 scores.

## Interpreting a badge

| Color | Meaning |
|---|---|
| `brightgreen` | L1 assertions all pass; L3 grade met thresholds (or L3 not declared). |
| `yellow` | L1 all pass but L3 fell below the declared pass-rate / mean-score threshold. |
| `red` | Any L1 assertion failed, or L3 grading produced no scorable results. |
| `lightgrey` | No iteration has been recorded yet for this skill, or the eval spec declares zero L1 assertions. Run `clauditor grade` to populate. |

See [`docs/badges.md#color-logic`](./badges.md#color-logic) for the
full decision table, including the DEC-007 "zero L1 assertions"
edge case and the DEC-009 "L3 all parse-failed → red" branch.

## Regenerating a badge locally

```bash
# Produce the sidecars (spends Anthropic tokens via `claude -p`).
clauditor grade src/clauditor/skills/clauditor/SKILL.md

# Aggregate the latest iteration into the badge JSON.
clauditor badge src/clauditor/skills/clauditor/SKILL.md

# Commit the regenerated artifact so shields.io re-fetches.
git add .clauditor/badges/clauditor.json
git commit -m "Refresh clauditor badge"
```

Or bundle the two steps into a CI workflow — see
[`docs/badges.md#ci-integration`](./badges.md#ci-integration) for
the pattern.
