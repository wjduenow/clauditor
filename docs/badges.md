# Badges for clauditor skills

Placement guidance, color-logic reference, and embedding recipe for the quality badge `clauditor badge` generates from a skill's latest iteration sidecars. Read this when you want a one-glance quality signal next to a skill in your README, a skill-catalog page, or (with tradeoffs) the SKILL.md body itself. For the CLI flag surface, see [cli-reference.md#badge](cli-reference.md#badge).

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

## Why a badge

A shields.io badge compresses L1 pass rate + L3 quality + (optional) variance stability into a single SVG next to the skill link. CI pipelines that already run `clauditor grade` on every commit regenerate `.clauditor/badges/<skill>.json` for free; the rendered badge then stays live without any extra hosting, SVG-generation, or PR-comment plumbing. A reader scanning a repo sees at a glance which skills pass their gate, which are yellow (L1 clean but L3 below threshold), and which are red.

## The shields.io endpoint pattern

`clauditor badge` writes a JSON file; [shields.io](https://shields.io) renders the SVG on demand from that JSON. The image URL is constant — it only needs to be pasted once, into whatever page hosts the badge. Each run of `clauditor badge` updates the JSON content in place, and the next time the page is loaded shields.io re-fetches the JSON and re-renders the SVG.

The net effect: one Markdown image line, no hosting, no action-bot commits beyond the JSON file itself.

## Placement hierarchy

clauditor supports three placement strategies. Pick the one that matches where your users encounter the skill.

### 1. Primary — README next to the skill link

The default. A skill listed in the project README's catalog table or feature section gets its badge embedded directly next to the link. Humans browsing the repo see the signal immediately, and the badge stays visible to anyone who never runs the skill.

```markdown
- [`my-skill`](.claude/skills/my-skill/SKILL.md) — one-line summary ![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/USER/REPO/main/.clauditor/badges/my-skill.json)
```

Generate that exact line with `clauditor badge <skill.md> --url-only` (auto-detects repo + branch; see [Embedding recipe](#embedding-recipe-url-only) below).

### 2. Secondary — dedicated `docs/skills.md` catalog page

When the project ships many skills, a dedicated catalog page (`docs/skills.md`, or similar) with a table of skills + per-row badges gives a single-pane quality view. This is the right placement when you want a maintainer-facing dashboard that the README does not need to carry. This repo ships one itself — see [docs/skills.md](./skills.md).

```markdown
| Skill | Purpose | Quality |
| ----- | ------- | ------- |
| [`my-skill`](../.claude/skills/my-skill/SKILL.md) | Find restaurants | ![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/USER/REPO/main/.clauditor/badges/my-skill.json) |
| [`other-skill`](../.claude/skills/other-skill/SKILL.md) | Draft PR descriptions | ![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/USER/REPO/main/.clauditor/badges/other-skill.json) |
```

### 3. Tradeoff — SKILL.md body embedding

Technically supported — a SKILL.md is just Markdown, so a badge image line renders fine. But Claude Code loads SKILL.md bodies **into agent context** when the skill is invoked. An image line in the body costs tokens on every skill run for zero agent utility (the agent does not render the image, and the badge's color/message is not operational information the agent needs).

Only put the badge in SKILL.md if:

- The skill is the only thing in its repo (no README to host the badge), AND
- You accept the per-run token cost on every invocation.

Otherwise prefer the README or catalog-page placement.

## Color logic

The badge color summarizes the combined L1 + L3 signal. The message surfaces the underlying counts. `clauditor badge` classifies per this table (DEC-007, DEC-009, DEC-020, DEC-024 of `plans/super/77-clauditor-badge.md`):

| Situation | Color | Message |
| --------- | ----- | ------- |
| L1 all pass + L3 passed, OR L1 all pass + L3 absent | `brightgreen` | `N/M` or `N/M · L3 XX%` |
| L1 all pass + L3 below thresholds | `yellow` | `N/M · L3 XX%` |
| Any L1 assertion failed | `red` | `N/M` |
| L1 all pass + L3 present but all results parse-failed | `red` | `N/M` (L3 fragment omitted) |
| No iteration exists, OR iteration exists but spec declares zero L1 assertions | `lightgrey` | `no data` |

When a `variance.json` sidecar is present, the message gains a `· XX% stable` tail (e.g. `8/8 · L3 92% · 80% stable`). The variance layer is optional and degrades gracefully when the sidecar is absent (today's steady state — no `variance.json` writer ships yet).

L3 `passed` is evaluated against the `thresholds` block the grading run itself already persisted to `grading.json` — the badge does not re-interpret `EvalSpec.grade_thresholds` (DEC-004). Whatever threshold was in force at grade time is the source of truth.

## Embedding recipe (`--url-only`)

The fastest way to get the exact Markdown image line for your README:

```bash
clauditor badge .claude/skills/my-skill/SKILL.md --url-only
```

Output:

```markdown
![clauditor](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/USER/REPO/main/.clauditor/badges/my-skill.json)
```

clauditor auto-detects the repo slug (from `git remote get-url origin`) and the default branch (from `git symbolic-ref refs/remotes/origin/HEAD`). When auto-detection fails it falls back to the literal placeholder `USER/REPO/main` with a stderr warning so the next paste is obvious. Override either with `--repo USER/REPO` or `--branch NAME`:

```bash
clauditor badge .claude/skills/my-skill/SKILL.md --url-only \
  --repo my-org/my-skills --branch release
```

## CI integration

A typical pipeline runs `clauditor grade` on every commit to keep the iteration-N sidecars fresh, then `clauditor badge` to regenerate the JSON. Commit the regenerated `.clauditor/badges/<skill>.json` back to a branch that shields.io can fetch (usually your default branch; or a `badges` branch dedicated to the rendered JSON). Alternatives that avoid pushing commits back to the default branch:

- Publish the JSON to GitHub Pages (`--output PATH` accepts absolute paths for this case; DEC-005).
- Use [`schneegans/dynamic-badges-action`](https://github.com/schneegans/dynamic-badges-action) to write the JSON into a gist shields.io already knows how to read.

A first-class `clauditor-action@v1` GitHub Action that wires this up end-to-end is a future ticket (listed as "optional phase 2" on issue #77); for now, wire `clauditor badge` into your existing CI as any other post-grade step.

## Schema reference

`clauditor badge` writes **two sidecar files** per invocation (a shields.io-valid payload plus a clauditor extension). Shields.io strictly validates its endpoint schema and rejects unknown top-level keys with an `invalid properties: <key>` SVG response, so the extension cannot be embedded — it lives in a sibling file. See [`.claude/rules/dual-version-external-schema-embed.md`](../.claude/rules/dual-version-external-schema-embed.md) for the full rationale.

### File 1 — `<skill>.json` (shields.io only)

Exactly the fields the shields.io `endpoint` contract requires, plus any whitelisted `--style` passthroughs (`style`, `logoSvg`, `logoColor`, `labelColor`, `cacheSeconds`, `link`). No clauditor metadata.

```json
{
  "schemaVersion": 1,
  "label": "clauditor",
  "message": "8/8 · L3 92%",
  "color": "brightgreen"
}
```

This is the file the shields.io `endpoint?url=...` fetcher reads from `raw.githubusercontent.com`.

### File 2 — `<skill>.clauditor.json` (extension)

Standalone clauditor telemetry. First key is `schema_version` per `.claude/rules/json-schema-version.md`; bumps independently of the shields.io schema (DEC-027).

```json
{
  "schema_version": 1,
  "skill_name": "my-skill",
  "generated_at": "2026-04-21T14:00:00Z",
  "iteration": 4,
  "layers": {
    "l1": {"count": 8, "total": 8, "pass_rate": 1.0, "passed": true},
    "l3": {
      "pass_rate": 0.92,
      "mean_score": 0.85,
      "passed": true,
      "thresholds": {"min_pass_rate": 0.7, "min_mean_score": 0.5}
    }
  }
}
```

Read by trend-audit and other forensic consumers; shields.io does not fetch this file.

L1 and L3 both carry a `passed: bool` field with **different semantics** (DEC-010): L1 `passed = true` means "every declared assertion passed"; L3 `passed = true` means "pass rate ≥ `min_pass_rate` AND mean score ≥ `min_mean_score`" (the grade met the thresholds the grading run used). The dataclass docstrings in `src/clauditor/badge.py` are the authoritative reference for each field.

### `--force` semantics for the pair

The DEC-011 overwrite policy applies to BOTH files as a set. Either file existing without `--force` fails the write. Commit both files together.

For the complete list of pure helpers that compose this payload, see [`src/clauditor/badge.py`](../src/clauditor/badge.py) (`compute_badge`, `Badge`, `ClauditorExtension`, `L1Summary`, `L3Summary`, `VarianceSummary`).
