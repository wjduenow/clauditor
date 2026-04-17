# Super Plan: #47 — README restructure for navigability

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/47
- **Branch:** `feature/47-readme-restructure`
- **Worktree:** _n/a (working on branch directly)_
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/49
- **Sessions:** 1
- **Last session:** 2026-04-17

---

## Discovery

### Ticket Summary

**What:** Restructure the 770-line root `README.md` so it functions as
navigation + first-success landing, with deep reference material
promoted into `docs/` and gated behind links. Add a TOC and collapse
rarely-read reference into `<details>` blocks.

**Why:** First-screen content is positioning-heavy, not action-heavy.
The Install CTA doesn't appear until line 48; terminology like "Layer 1/2/3"
is used at line 18 without definition until line 222. Readers who land
from GitHub discovery scroll past value before finding setup.

**Done when:**
- Root README is ≤ ~150 lines
- First screen (top ~50 lines) contains: what it is, install, one success example
- Deep reference sections live in `docs/` under stable file names
- Existing external anchor references (if any) continue to resolve

### Key Findings (reshape the ticket)

**FINDING-1 — `docs/architecture.md` already exists with dense content.**
The ticket says "promote How It Works → `docs/architecture.md`" but the
file already has 4.6 KB of mermaid diagrams + a three-layer pipeline
breakdown (grade command flow, cost/time/speed table). This is a
**collision**, not a promotion target. Three possible resolutions:

- A. Leave existing `docs/architecture.md` alone; the root README's
  "How It Works" stays (it's only 20 lines, diagram + teaser) with a
  "See [docs/architecture.md](docs/architecture.md) for depth" link.
- B. Rename existing to `docs/technical-architecture.md`; repurpose
  `architecture.md` for the conceptual overview.
- C. Merge the README "How It Works" into the existing file as a new
  top-level section.

**FINDING-2 — Much larger promotion opportunity than the ticket listed.**
The three biggest sections by line count are NOT in the ticket's scope:

| Section | Lines | Density | Current placement |
|---|---:|---|---|
| Three Layers of Validation | 274 | 51% code | root README |
| Eval Spec Format | 152 | 79% code | root README |
| CLI Reference | 56 | 89% code | root README |

Promoting these three = 482 lines out of 770 → ~63% reduction before
adding nav teasers back. The original ticket scoped only Quick Start +
How It Works + the setup flag collapse. The ticket's success criteria
("≤150 lines") requires the bigger promotion.

**FINDING-3 — One rule-protected docs file exists already.**
`.claude/rules/stream-json-schema.md` mandates: "The human-readable
schema reference lives at `docs/stream-json-schema.md`... update that
function *and* this document *and* `.claude/rules/stream-json-schema.md`
in the same commit." The existing `docs/stream-json-schema.md` is
covered by that rule. We do NOT touch it in this ticket.

**FINDING-4 — No markdown tooling.** No markdownlint, no link checker,
no mkdocs/sphinx, no doc-deploy step. Link invariants must be
preserved by hand. CI does not catch broken internal links; tests
don't fail on them.

**FINDING-5 — No existing TOC style in the repo** to anchor on. We
pick a TOC shape as a design decision.

**FINDING-6 — `AGENTS.md` is out of scope.** It's contributor workflow
(beads + session-close protocol), does not duplicate README content,
does not need restructuring.

**FINDING-7 — Cross-file reference scan is clean.** No `README.md#…`
anchor fragments found in `.claude/`, `plans/`, `.github/`, or `docs/`
other than ordinary self-links within the README. The existing link
`README → docs/architecture.md` must stay live; otherwise the blast
radius of anchor changes is small.

### Section inventory (770 lines total)

| Heading | Lines | Category | Proposed default action |
|---|---:|---|---|
| (intro + badges) | 1-27 | nav | keep; tighten first screen |
| Alignment with agentskills.io | 28-47 (20) | explainer | move to `docs/agentskills-alignment.md` or collapse to `<details>` |
| Install | 48-64 (17) | nav | keep |
| Installing the /clauditor slash command | 65-111 (47) | tutorial | keep; wrap flag reference in `<details>` |
| Using /clauditor in Claude Code | 112-159 (48) | tutorial | shrink + link to `docs/skill-usage.md` |
| Quick Start | 160-221 (62) | tutorial | shrink + link to `docs/quick-start.md` |
| How It Works | 222-241 (20) | explainer | keep teaser; link to `docs/architecture.md` |
| Three Layers of Validation | 242-515 (274) | deep-ref | **promote to `docs/layers.md`** |
| CLI Reference | 516-571 (56) | deep-ref | **promote to `docs/cli-reference.md`** |
| Exit Codes | 572-584 (13) | deep-ref | collapse to `<details>` or promote |
| Pytest Integration | 585-605 (21) | deep-ref | **promote to `docs/pytest-plugin.md`** |
| Eval Spec Format | 606-757 (152) | deep-ref | **promote to `docs/eval-spec-reference.md`** |
| Notes | 758-761 (4) | misc | merge into nearest kept section |
| Reference docs | 762-767 (6) | nav | expand into explicit nav list |
| License | 768-770 (3) | nav | keep |

### Applicable `.claude/rules/`

- **`stream-json-schema.md`** — protects the existing
  `docs/stream-json-schema.md`. Do not touch. Reference as a template
  for the "root README is the nav; `docs/` is the truth" pattern.
- All other 17 rules — none apply directly to this doc-only
  restructure.

### Proposed Scope (expanded beyond original ticket)

1. Add a TOC near the top of README
2. Tighten first screen (intro + install CTA visible without scrolling)
3. Collapse "Alignment with agentskills.io" into `<details>` (or promote)
4. Collapse the `clauditor setup` flag reference into `<details>`
5. Promote "Three Layers of Validation" → `docs/layers.md` (+ teaser)
6. Promote "CLI Reference" → `docs/cli-reference.md` (+ teaser)
7. Promote "Eval Spec Format" → `docs/eval-spec-reference.md` (+ teaser)
8. Promote "Pytest Integration" → `docs/pytest-plugin.md` (+ teaser)
9. Promote "Quick Start" deep content → `docs/quick-start.md` (+ teaser)
10. Shrink "Using /clauditor" to a short section + link to
    `docs/skill-usage.md`
11. Resolve the `docs/architecture.md` collision (decision below)
12. Expand the "Reference docs" nav list to link every promoted file
13. Verify every moved anchor / every internal link still resolves;
    grep the whole repo for `README.md#…` patterns to catch breakage
14. Keep `docs/stream-json-schema.md` untouched per its rule anchor

### Scoping Questions

**Q1 — `docs/architecture.md` collision resolution (FINDING-1).**
- **A.** Leave existing `docs/architecture.md` alone. README's
  "How It Works" stays as a 20-line teaser with a link. ← simplest
- **B.** Rename existing → `docs/technical-architecture.md`; use
  `architecture.md` for the conceptual overview.
- **C.** Merge README "How It Works" into the existing
  `docs/architecture.md` as a new section; remove it from README
  entirely (link-only from root).

**Q2 — Restructure scope — stick with the 6-item ticket or expand?**
- **A.** Expanded scope (promote all 5 deep-ref sections; 13 items).
  Hits the "≤150 lines" goal. ~63% README reduction. ← recommended
- **B.** Original ticket only (6 items: How It Works + Quick Start +
  flag collapse + TOC + usage section shrink + link audit). ~25%
  reduction; won't hit the stated success criterion.
- **C.** Somewhere in between — pick which sections to promote.

**Q3 — TOC style (FINDING-5, no existing project convention).**
- **A.** Flat markdown list with anchors:
  ```
  ## Contents
  - [Install](#install)
  - [Quick Start](#quick-start)
  - [Reference docs](#reference-docs)
  ```
- **B.** GitHub-generated (`<!-- toc -->` via a generator) — adds
  tooling dependency.
- **C.** `<details><summary>Contents</summary>…</details>` collapsed
  by default — saves first-screen room.

**Q4 — "Alignment with agentskills.io" placement.**
- **A.** Promote to `docs/agentskills-alignment.md` — positioning
  content, not setup-path.
- **B.** Collapse to `<details>` in root — kept near the top for trust
  signaling but folded.
- **C.** Leave as-is.

**Q5 — Exit Codes table (13 lines).**
- **A.** Promote to `docs/exit-codes.md` (referenced from
  `docs/cli-reference.md`).
- **B.** Merge into `docs/cli-reference.md` as a subsection.
- **C.** Collapse to `<details>` in root README.

**Q6 — Commit strategy for the restructure.**
- **A.** One big commit — easy to review overall shape.
- **B.** One commit per promoted section + one for TOC + one for the
  link-audit verification. Gives bisect/revert granularity but more
  noise. ← recommended by super-plan convention
- **C.** One commit per phase (A: TOC + first-screen tighten, B:
  promote 5 sections, C: link audit). Middle ground.

---

## Architecture Review

### Resolved Scoping Choices (from Discovery)

- **Q1 → C:** Merge README "How It Works" into the existing
  `docs/architecture.md`; remove from README entirely (link-only).
- **Q2 → A:** Expanded scope — 13 items including the 4 big deep-ref
  promotions. Target ≤150-line README.
- **Q3 → C:** `<details><summary>Contents</summary>…</details>`
  collapsed hand-rolled TOC.
- **Q4 → B:** Collapse "Alignment with agentskills.io" into
  `<details>` in root.
- **Q5 → B:** Merge "Exit Codes" table into `docs/cli-reference.md`
  as a subsection.
- **Q6 → A:** One big commit (accepting a ~500-line diff in exchange
  for atomicity of the restructure).

### Review Summary

| Area | Verdict | Notes |
|---|---|---|
| Link integrity — external `README.md#…` refs | pass | Zero external refs found (grep of `src/`, `.github/`, `.claude/`, `CONTRIBUTING.md`, `AGENTS.md`, bundled skill, tests). The only live link into README is self-referential. |
| Link integrity — anchor stability for bookmarked sections | concern | Users who bookmarked `#three-layers-of-validation`, `#cli-reference`, `#eval-spec-format`, `#pytest-integration` will 404 after promotion. Mitigation: leave a short teaser at each old H2 that links to the promoted file, preserving the anchor. |
| Link integrity — promoted sections link back correctly | concern | Every promoted doc must open with a breadcrumb back to the root README so cold-entry readers can orient. Addressable via template. |
| Rendering — GitHub | pass | `<details>`, mermaid, tables, relative links all native-supported. |
| Rendering — PyPI long-description | pass | PyPI uses `readme_renderer` with GFM; `<details>`/`<summary>` are on the allowlist. Confirmed via PyPI policy. No images-in-details issues. |
| Rendering — plain-text mirrors (e.g. vim `less`) | concern | `<details>` shows both summary and content unfolded in plain-text renderers. Acceptable for this project (no known plain-text audience) but worth acknowledging. |
| First-screen check (top ~50 lines) | **BLOCKER** | Cannot verify until I draft the new header. Blocker because the stated success criterion ("first screen = install + one success example") depends on it. Resolved during refinement by committing to a concrete layout. |
| Content quality — teaser sufficiency | concern | A promoted-section teaser must let a reader decide whether to click without opening the linked doc. Shape needs a convention (1 sentence + 1 code snippet + "See [link] for full reference"). |
| `docs/` voice consistency | concern | Existing `docs/architecture.md` = prose + diagram + table. `docs/stream-json-schema.md` = prose + code examples. New promoted docs should open with the same shape (header → 1-paragraph purpose → link back to README → body). |
| Reversibility | pass | Single-commit restructure per Q6 = atomic `git revert`. All content preserved in git history. |
| Rule compliance — `stream-json-schema.md` | pass | We leave `docs/stream-json-schema.md` untouched; the rule's invariant (schema lives there, stays in lockstep with parser) is preserved. |
| Rule compliance — `plan-contradiction-stop.md` | pass | Plan explicitly re-verified preconditions (Discovery phase). |
| Rule compliance — all 16 other rules | N/A | None apply to doc-only restructures. |
| Pre-commit / CI | pass | No markdown tooling; restructure cannot break a check that doesn't exist. Post-merge, links verified by hand. |

### Blocker detail

**BLOCKER-1 — First-screen layout undrafted.**
The success criterion "first screen = what it is, install, one success
example" is untestable until we've committed to a concrete top-50-lines
shape. This isn't a real architecture blocker — it's a refinement
question that must be resolved before Detailing (we can't write
acceptance criteria for "first screen passes the check" without knowing
what the check tests against).

Resolution path: refinement Q-A below sketches three concrete first-
screen shapes; picking one closes the blocker.

### Concerns (to become refinement decisions)

The architecture review surfaced four concerns worth pinning down
explicitly before Detailing:

- **Anchor stability** (old bookmarked anchors) → leave teaser + link
  at each old H2? Or accept the break?
- **Promoted-doc breadcrumb** (cold entry readers from Google) → open
  with a "This is reference material from clauditor's README — return
  to the root" line? Or just a link to `../README.md`?
- **Teaser shape convention** → lock down a template so every
  promoted section reads consistently.
- **`docs/` file opener convention** → one-paragraph purpose + link
  back to README is the shape; formalize it.

---

## Refinement Log

### Resolved (full ledger)

| Q | Choice | Intent |
|---|---|---|
| Q1 | C | Merge README "How It Works" into existing `docs/architecture.md` |
| Q2 | A | Expanded scope — promote 4 big deep-ref sections |
| Q3 | C | `<details>` collapsed TOC, hand-rolled |
| Q4 | B | Collapse agentskills alignment to `<details>` in root |
| Q5 | B | Merge Exit Codes into `docs/cli-reference.md` |
| Q6 | A | Single-commit restructure |
| R-A | A1 + short "Why clauditor?" | First screen = tagline, badges, TOC, Install, short Why, One-minute example, links |
| R-B | B1 | Preserve every old H2 as a teaser with a link (anchor stability) |
| R-C | C1 | Every promoted doc opens with `# <Title>` → purpose paragraph → breadcrumb blockquote → body |
| R-D | D3 | Rich teaser: paragraph + short code block + list of key topics + link |

### Decisions

**DEC-001 — Collision resolution: merge into existing `docs/architecture.md`** (Q1=C)
Root README's "How It Works" section (H2 at line 222, ~20 lines) is
removed entirely. Its mermaid diagram and prose land in
`docs/architecture.md` as a new top-level section or merged into the
existing content. README keeps only a nav-list line pointing at
`docs/architecture.md` from the Reference docs section.

**DEC-002 — Scope: expanded to 13 items** (Q2=A)
Promote all four deep-ref sections (Three Layers, CLI Reference +
Exit Codes, Eval Spec Format, Pytest Integration) plus the three
shrinks (Quick Start, Using /clauditor, agentskills alignment) plus
TOC, plus link audit, plus architecture collision resolution. Target
≤150 line README — see DEC-011 re: length budget.

**DEC-003 — TOC: hand-rolled collapsed `<details>`** (Q3=C)
```markdown
<details>
<summary>Contents</summary>

- [Install](#install)
- [Installing the /clauditor slash command](#installing-the-clauditor-slash-command)
- [Using /clauditor in Claude Code](#using-clauditor-in-claude-code)
- [One-minute example](#one-minute-example)
- [Reference docs](#reference-docs)

</details>
```
Lives at the top of README after the title + tagline + badges.

**DEC-004 — Collapse agentskills alignment** (Q4=B)
Wrap the existing 20-line section in `<details><summary>…</summary>`
in root README. Do NOT promote to a separate file. The content stays
near the top for trust signaling but is folded by default.

**DEC-005 — Exit Codes → `docs/cli-reference.md`** (Q5=B)
The 13-line Exit Codes table becomes a subsection of the promoted
CLI reference file. No separate `docs/exit-codes.md`.

**DEC-006 — Single-commit restructure** (Q6=A)
One atomic commit. ~500 line diff. Accepts the reviewability tax for
atomic `git revert` if the restructure lands poorly. Commit message
will include the per-section promotion list for navigability.

**DEC-007 — First-screen layout** (R-A=A1+Why)
Top ~50 lines, in order:
1. `<p align="center">` logo + title + badges (unchanged from today)
2. One-sentence tagline
3. `<details>Contents</details>` TOC
4. `## Install` (3-line pip snippet)
5. `## Why clauditor?` — short version of the current 3-paragraph
   positioning content, tightened to ~6-8 lines (3 questions → 3
   one-line bullets).
6. `## One-minute example` — a runnable end-to-end snippet
   (`clauditor init` → `clauditor validate`) with expected output
   preview.

Replaces the current header through "Install" span (lines 1-64, mostly
positioning prose).

**DEC-008 — Anchor preservation strategy** (R-B=B1)
Every promoted section's original H2 stays in the README as a teaser
(DEC-010 shape). The H2 text stays identical so GitHub-generated
anchors (`#three-layers-of-validation`, `#cli-reference`,
`#eval-spec-format`, `#pytest-integration`) continue to resolve.
Bookmarked links land on the teaser instead of 404.

**DEC-009 — Promoted-doc opener template** (R-C=C1)
Each new file in `docs/` opens with:
```markdown
# <Title>

<One paragraph explaining the purpose and when you'd read this file.>

> Returning from the [root README](../README.md). This doc is the
> full reference; the README has a summary with code examples.

<body>
```
Matches the existing `docs/architecture.md` voice (prose-led).
Google-landed readers see the breadcrumb immediately.

**DEC-010 — Teaser shape: mixed D3 (high-traffic) + D2 (low-traffic)**
(R-D=D3 revised to mixed strategy per length budget)

**D3 rich teaser (~12-15 lines)** for high-traffic sections that
reward a fuller preview:

- Quick Start
- CLI Reference
- Eval Spec Format

```markdown
## <Section Title>

<One-paragraph what-it-is and when-you-use-it, ~3-5 lines.>

<Short code block, ~5-8 lines, showing the most common invocation
or shape. Enough to anchor the reader.>

**Covered in the full reference:**
- <topic 1>
- <topic 2>
- <topic 3>

Full reference: [docs/<file>.md](docs/<file>.md).
```

**D2 lean teaser (~4-6 lines)** for lower-traffic sections where a
pointer is sufficient:

- Three Layers of Validation (conceptual; most readers hit it once)
- Pytest Integration (niche audience — users who already write tests)
- Using /clauditor in Claude Code (already-short section just merged
  in #46; don't re-expand)

```markdown
## <Section Title>

<One sentence.>

```<5-line code example>```

Full reference: [docs/<file>.md](docs/<file>.md).
```

Traffic heuristic: a section is "high-traffic" if users revisit it
to look something up (reference material). A section is "low-traffic"
if users hit it once during onboarding and rarely return (conceptual
or niche).

**DEC-011 — Length budget: target ~155 lines**
*(refined from accepting-over to strict-budget-with-mixed-strategy)*

With DEC-010's mixed strategy:

- 3 D3 rich teasers × ~13 lines = ~39 lines
- 3 D2 lean teasers × ~5 lines = ~15 lines
- First-screen content (DEC-007) = ~50 lines
- Install (unchanged) = ~17 lines
- Installing the /clauditor slash command (with flag collapse) = ~25 lines
- Using /clauditor (D2 lean) = already in D2 bucket
- Reference docs (expanded nav list) = ~10 lines
- License = ~3 lines

Projected total: **~155 lines.** Close to the ticket's stated
success criterion (≤150) with a small reserve for polish.

If the actual diff lands at >165, trim D3 teasers or collapse
additional sections into `<details>` during Quality Gate.

---

## Detailed Breakdown

Natural ordering: extract docs files first → merge into existing
architecture → rewrite README → audit → quality gate → patterns.
All stories land in **one feature branch** (`feature/47-readme-restructure`)
and merge to `dev` as a **squash-merge** per DEC-006 (single commit
on dev).

**Validation command** per story: `uv run ruff check src/ tests/`
(no-op for doc-only stories but confirms no accidental drift) plus
`uv run pytest --cov=clauditor --cov-report=term-missing` (80%
global gate) plus manual link-resolution check on any rendered file.

---

### US-001 — Extract deep-ref sections into new `docs/*.md` files

**Description:** Lift the six deep-ref and tutorial sections out of
`README.md` into dedicated files under `docs/`. Pure extraction — no
content rewrite. Each new file follows DEC-009's opener template
(title + purpose paragraph + breadcrumb blockquote + body).

**Traces to:** DEC-002 (expanded scope), DEC-005 (Exit Codes merge),
DEC-009 (opener template)

**Acceptance criteria:**
- Six new files created, each with DEC-009 opener:
  - `docs/cli-reference.md` — contents of `## CLI Reference` (lines
    516-571) plus `## Exit Codes` (lines 572-584) merged as a
    subsection per DEC-005. Preserve mermaid/tables.
  - `docs/eval-spec-reference.md` — contents of `## Eval Spec Format`
    (lines 606-757). Full schema walkthrough with all 12+ JSON
    examples intact.
  - `docs/layers.md` — contents of `## Three Layers of Validation`
    (lines 242-515). Preserve the TD mermaid diagram.
  - `docs/pytest-plugin.md` — contents of `## Pytest Integration`
    (lines 585-605).
  - `docs/quick-start.md` — contents of `## Quick Start` (lines
    160-221) plus the "Notes" (lines 758-761) if relevant to Quick
    Start.
  - `docs/skill-usage.md` — contents of `## Using /clauditor in
    Claude Code` (lines 112-159).
- No content changes during extraction — byte-for-byte preservation
  where possible. Opener template is the only addition.
- `README.md` is not yet modified (US-003's job).
- Ruff + pytest still pass (smoke test; nothing Python changed).
- `ls docs/` shows 8 files (5 pre-existing + 6 new = 11? — check the
  actual pre-existing count: architecture, stream-json-schema, temp/,
  assets/. So 6 new files + 2 pre-existing `.md` = 8 `.md` files total).

**Done when:** All six new `docs/*.md` files exist with DEC-009
openers, each containing the complete extracted content from the
matching README section.

**Files:**
- `docs/cli-reference.md` (new)
- `docs/eval-spec-reference.md` (new)
- `docs/layers.md` (new)
- `docs/pytest-plugin.md` (new)
- `docs/quick-start.md` (new)
- `docs/skill-usage.md` (new)

**Depends on:** none

---

### US-002 — Merge "How It Works" into existing `docs/architecture.md`

**Description:** The README's `## How It Works` section (lines
222-241, ~20 lines, includes mermaid flowchart) gets merged into the
existing `docs/architecture.md`. DEC-001 resolves the collision by
folding the README content *into* the existing file rather than
overwriting.

**Traces to:** DEC-001 (collision resolution)

**Acceptance criteria:**
- `docs/architecture.md` preserves all its existing content.
- The README "How It Works" mermaid diagram + prose lands in
  `docs/architecture.md` as an appropriate section (top-level H2 or
  merged into an existing section — worker picks based on the existing
  file's shape).
- The merged content reads coherently; no duplicated explanations of
  the three-layer pipeline.
- `README.md` is not yet modified (US-003 removes the section).
- Pytest + ruff still pass.

**Done when:** `docs/architecture.md` contains the README's "How It
Works" content integrated without redundancy.

**Files:**
- `docs/architecture.md` (modified)

**Depends on:** none (can run in parallel with US-001)

---

### US-003 — Rewrite README

**Description:** Largest story. Rewrite `README.md` top-to-bottom
per DEC-007 (first-screen), DEC-003 (TOC), DEC-004 (agentskills
collapse), DEC-008 (anchor preservation), DEC-010 (teaser shapes),
plus the `setup` flag-reference `<details>` collapse. Also move the
"Maintainers" line (README:109-110) fully into `CONTRIBUTING.md`.

**Traces to:** DEC-003, DEC-004, DEC-007, DEC-008, DEC-010, DEC-011

**Acceptance criteria:**
- **First screen (DEC-007):** top ~50 lines contain title + badges
  + tagline + `<details>Contents</details>` + Install (3-line pip
  snippet) + short "Why clauditor?" (≤8 lines, 3 bullets or a
  tight paragraph) + One-minute example (runnable snippet with
  expected-output preview).
- **TOC (DEC-003):** hand-rolled list of README section anchors
  wrapped in `<details><summary>Contents</summary>…</details>`.
- **Agentskills alignment (DEC-004):** the existing 20-line section
  wrapped in `<details><summary>…</summary>`. Text unchanged.
- **`setup` flag reference:** flags (`--unlink`, `--force`,
  `--project-dir`) + restart note + diagnostic pointer wrapped in a
  `<details>` inside the install section.
- **Teasers (DEC-008 + DEC-010):** every old H2 from the promoted
  list preserved as a teaser pointing at its `docs/` file:
  - `## Quick Start` — D3 rich (paragraph + code block + 3 topics +
    link to `docs/quick-start.md`).
  - `## Three Layers of Validation` — D2 lean (one sentence + 5-line
    snippet + link to `docs/layers.md`).
  - `## CLI Reference` — D3 rich (paragraph + code block + topics +
    link to `docs/cli-reference.md`).
  - `## Pytest Integration` — D2 lean (one sentence + 5-line snippet
    + link to `docs/pytest-plugin.md`).
  - `## Eval Spec Format` — D3 rich (paragraph + JSON snippet +
    topics + link to `docs/eval-spec-reference.md`).
  - `## Using /clauditor in Claude Code` — D2 lean (one sentence +
    5-line snippet + link to `docs/skill-usage.md`).
- **"How It Works" deleted** — mermaid + prose now live in
  `docs/architecture.md` (US-002); README mentions it only from the
  Reference docs nav list.
- **Reference docs nav** expanded to list every promoted file with
  a one-line description of each.
- **Maintainers line** (README:109-110 pointing at `CONTRIBUTING.md`)
  removed from README; content moved into `CONTRIBUTING.md` if not
  already there.
- **Line count target:** README ≤ ~165 lines (DEC-011 budget).
- Ruff + pytest pass.

**Done when:** README renders with the new shape in GitHub's preview;
all teasers link to files that US-001 created; line count within
budget.

**Files:**
- `README.md` (full rewrite)
- `CONTRIBUTING.md` (ensure maintainer-pointer content is there)

**Depends on:** US-001 (teasers link to the new files), US-002
(implicit — "How It Works" removed because content moved)

---

### US-004 — Link audit + anchor verification

**Description:** Mechanical verification that no links are broken.
The restructure moved hundreds of lines of content; anchor drift is
the main risk per the architecture review.

**Traces to:** anchor stability commitment in DEC-008; ticket success
criterion "verify all internal links still resolve"

**Acceptance criteria:**
- Grep the whole repo for `README.md#…` patterns; every one still
  resolves to a live anchor (teasers preserved per DEC-008).
- Grep for `docs/…\.md` patterns; every target file exists.
- Every internal link in `README.md` and every new `docs/*.md` file
  resolves (spot-check rendered in GitHub preview or by hand).
- Every mermaid diagram that was in README still renders after its
  relocation (to `docs/architecture.md` or `docs/layers.md`).
- `docs/stream-json-schema.md` untouched (rule compliance).
- Ruff + pytest pass.
- Worker reports line count of final README.

**Done when:** Link audit passes; report lists any anchors that
changed and confirms external links are preserved.

**Files:** none modified (verification only); any link fixes discovered
during audit land as part of this story.

**Depends on:** US-001, US-002, US-003

---

### US-005 — Quality Gate

**Description:** Run code-reviewer agent 4 times across the full
changeset, fixing every real finding each pass. Run CodeRabbit
locally. Re-verify validation gate.

**Traces to:** all implementation DECs

**Acceptance criteria:**
- Code-reviewer agent invoked 4 times; findings fixed or rationalized
  as false positives.
- CodeRabbit run via `coderabbit review --plain --base dev --type
  committed`; findings addressed, review threads resolved on the PR.
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80% coverage.
- README renders correctly on GitHub (spot-check), `<details>` blocks
  expand, mermaid diagrams render in `docs/`, internal links resolve.
- Line count within DEC-011 budget (~155 target, ≤165 accepted;
  trim if over).

**Done when:** 4 reviewer passes + CodeRabbit clean + validation
gate green + PR threads resolved.

**Depends on:** US-001 through US-004

---

### US-006 — Patterns & Memory

**Description:** Distill any reusable patterns from this restructure
into `.claude/rules/` or `bd remember`.

**Traces to:** novel patterns from this ticket

**Acceptance criteria:**
- Evaluate whether any of these are new-rule-worthy:
  - "Root README is navigation; `docs/` is reference" as an explicit
    convention (candidate rule if likely to apply to future repos
    or sub-features).
  - DEC-009 opener template for `docs/*.md` files (candidate if we
    expect more promoted docs).
  - DEC-010 teaser template — useful reference for future doc churn.
  - Anchor preservation via teaser (DEC-008) — generalizable pattern
    for any future doc restructure.
- If any warrant a rule, author it in `.claude/rules/<name>.md`
  following the existing file shape (problem → pattern → why →
  canonical → when applies → when not).
- Run `bd remember` for transient insights worth searching later
  but not rule-worthy.
- Validation gate still passes.

**Done when:** rule files (if any) committed; `bd remember` insights
recorded; plan's Session Notes closed out.

**Files:**
- Potentially new `.claude/rules/*.md` file(s)
- `bd remember` invocations

**Depends on:** US-005

---

### Dependency graph

```
US-001 (extract 6 docs files) ─┐
                                │
US-002 (merge into architecture)┤
                                ├─► US-003 (rewrite README) ──► US-004 (link audit)
                                │                                      │
                                │                                      ▼
                                │                              US-005 (Quality Gate) ──► US-006 (Patterns)
```

US-001 and US-002 run in parallel. US-003 depends on both (needs new
files to link from and architecture content moved). US-004 depends on
US-003. QG and Patterns close out sequentially.

### Single-commit commitment (DEC-006)

Per DEC-006, all implementation stories land on the feature branch
across multiple commits (one per story per Ralph convention), and the
PR-to-dev merge uses **squash** so dev gets one atomic commit. The
squash message summarizes the per-section work from the merged
commits.

---

## Beads Manifest

- **Epic:** `clauditor-8r1`
- **Branch:** `feature/47-readme-restructure`
- **PR:** https://github.com/wjduenow/clauditor/pull/49

| Story | Bead ID | Depends on | Ready |
|---|---|---|---|
| US-001 Extract 6 docs files | `clauditor-8r1.1` | — | ✅ |
| US-002 Merge into architecture | `clauditor-8r1.2` | — | ✅ |
| US-003 Rewrite README | `clauditor-8r1.3` | US-001, US-002 | |
| US-004 Link audit | `clauditor-8r1.4` | US-003 | |
| US-005 Quality Gate | `clauditor-8r1.5` | US-001..US-004 | |
| US-006 Patterns & Memory | `clauditor-8r1.6` (P4) | US-005 | |

8 dependency edges wired. Kickoff set: `{clauditor-8r1.1,
clauditor-8r1.2}` run in parallel.

---

## Session Notes

### Session 1 — 2026-04-17

**Discovery complete.** Ticket fetched. Parallel scouts surfaced:
- A collision on `docs/architecture.md` (already exists with dense
  content).
- A much larger promotion opportunity than the 6-item ticket scope —
  Three Layers (274 lines), Eval Spec Format (152 lines), CLI
  Reference (56 lines), and Pytest Integration (21 lines) are all
  deep-ref sections that fit the pattern.
- One rule-protected docs file (`docs/stream-json-schema.md`) — do
  not touch.
- No markdown tooling. Link invariants must be hand-verified.

Awaiting user answers on Q1-Q6 before proceeding to Architecture
Review.
