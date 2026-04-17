# Rule: README promotion recipe — nav in root, reference in `docs/`

When the root `README.md` grows past ~300 lines, or its first screen
(top ~50 lines) no longer shows install + one success example,
**promote deep-reference sections into `docs/<name>.md` and leave
anchor-preserving teasers behind**. The root README's job is
navigation + first-success landing; `docs/` is the full truth.
Every piece of this recipe — the trigger, the teaser shape, the
promoted-doc opener, the anchor-preservation via identical H2
text — is load-bearing, and skipping any one of them breaks either
readability, discoverability, or bookmarked external references.

## The trigger

Promote when either condition holds:

- **Length**: root README exceeds ~300 lines AND a majority of those
  lines are deep-reference material (dense JSON examples, full flag
  tables, schema walkthroughs) rather than tutorial flow.
- **First-screen miss**: the top ~50 lines do not contain all of:
  a one-sentence tagline, install command, and one runnable success
  example. Positioning prose ("why", "philosophy", "alignment with
  X") pushing install below the fold is the canonical symptom.

Classify every existing H2 into one of four categories:

- **nav** — TOC, install, reference-docs list, license. Keep in root.
- **tutorial** — Quick Start, "how to use it in Claude Code". Keep
  in root as a tight version; promote the deep walkthrough.
- **deep-ref** — CLI flag tables, full eval-spec schema, layer
  internals, plugin surface. **Promote to `docs/<name>.md`.**
- **explainer** — positioning, alignment-with-X, history. Collapse
  to `<details>` in root, or promote if it is large.

## The teaser shape (two variants by traffic)

Every promoted H2 stays in `README.md` with the same heading text
(so GitHub-generated anchors keep resolving — bookmarked
`README.md#three-layers-of-validation` must not 404). The body of
the teaser is either D3 rich or D2 lean based on traffic heuristic.

**Traffic heuristic:**

- **High-traffic (D3 rich)**: users revisit this section to look
  something up. Examples: CLI reference, eval-spec schema, Quick
  Start.
- **Low-traffic (D2 lean)**: users hit it once during onboarding and
  rarely return. Examples: niche conceptual overviews, plugin
  surface for an audience that already writes tests.

**D3 rich teaser (~12-15 lines)** — paragraph + code block + key
topics list + link:

```markdown
## <Section Title>

<One-paragraph what-it-is and when-you-use-it, ~3-5 lines.>

<Short code block, ~5-8 lines, showing the most common invocation
or shape.>

**Covered in the full reference:** <topic 1>, <topic 2>, <topic 3>.
Full reference: [docs/<file>.md](docs/<file>.md).
```

**D2 lean teaser (~4-6 lines)** — one sentence + small code block
+ link:

```markdown
## <Section Title>

<One sentence.>

`<5-line code example>`

Full reference: [docs/<file>.md](docs/<file>.md).
```

## The promoted-doc opener template

Every new file in `docs/` opens with the same four-element shape so
a cold-entry reader (Google-landed on the deep doc, no README
context) gets oriented immediately:

```markdown
# <Title>

<One paragraph explaining the purpose and when you'd read this
file. 2-4 sentences.>

> Returning from the [root README](../README.md). This doc is the
> full reference; the README has a summary with code examples.

<body>
```

The breadcrumb blockquote is load-bearing: it is the only
orientation a Google-landed reader gets. Without it they have no
signal that the doc is reference material rather than standalone
truth.

## Why each piece matters

- **Nav-vs-reference split**: the reader who lands on the README
  from GitHub discovery wants "what is this / does it work / how do
  I install it" — not a 150-line schema walkthrough. The reader who
  needs the schema walkthrough is on a return visit and is happy to
  click through. The promotion serves both by separating first-look
  (root) from return-look (docs).
- **Teaser preserves the anchor**: external bookmarks, old issues,
  and prior commits link at `README.md#<anchor>`. Deleting the H2
  breaks every one of those links. The teaser keeps the anchor live,
  and the `docs/` link ensures the reader still finds the full
  content.
- **D3 vs D2 traffic tiering**: every teaser being rich-D3 would
  reinflate the README (6 promotions × 15 lines = 90 lines of
  teasers alone). Every teaser being lean-D2 loses the "preview
  enough to decide whether to click" property for high-traffic
  sections. Splitting by traffic keeps the budget tight where lean
  is fine and informative where readers actually need the preview.
- **Identical H2 text, not a rename**: GitHub derives anchors from
  H2 text via slugification. `## Three Layers of Validation`
  produces `#three-layers-of-validation`. Renaming to `## The Three
  Layers` silently produces a different anchor and breaks every
  external reference. Keep the text byte-identical.
- **Promoted-doc breadcrumb**: cold-entry readers outnumber
  README-path readers for any doc that accumulates inbound SEO. The
  blockquote is the single line that tells them "this is reference
  material — there is a summary with code in the README." Without
  it, the reader has no idea whether the file is the authoritative
  truth, a fragment of a larger story, or stale.
- **Length budget as a forcing function**: a target like "≤150
  lines" (or "≤165 with polish reserve") is not aesthetic — it is
  the only discipline that keeps future authors from re-bloating
  the root README one paragraph at a time. When a new feature lands
  and adds 40 lines of reference material, the budget forces the
  author to promote it rather than append.

## What NOT to do

- Do NOT rename the promoted H2 when writing the teaser. The
  anchor-preservation invariant depends on byte-identical heading
  text.
- Do NOT promote a section by deleting it outright and adding a
  one-line `See [docs/X.md]` link with no H2. That breaks every
  bookmarked `#<anchor>` reference.
- Do NOT omit the breadcrumb blockquote from the promoted doc "to
  save a line." A Google-landed reader with no context is the
  primary audience for a deep-ref file.
- Do NOT mix D3 and D2 within a single teaser by "hedging". Pick
  one shape per section based on the traffic heuristic; a
  half-rich/half-lean teaser is worse than either.
- Do NOT skip the length budget check during a restructure. If the
  diff lands at >165 lines, trim D3 teasers to D2 or collapse
  additional sections into `<details>` before merging.

## Canonical implementation

Epic #47 (`plans/super/47-readme-restructure.md`) — the restructure
that took the root README from 770 lines to ~165 and codified this
recipe. Concrete anchors:

- **Trigger**: the plan's Section inventory table (lines 89-107)
  showed three sections (Three Layers 274 lines, Eval Spec Format
  152 lines, CLI Reference 56 lines) totalling 482 of 770 lines
  (~63%) as deep-ref. First-screen install was at line 48.
- **Teaser shape (D3 rich)**: `README.md`'s `## Quick Start`, `##
  CLI Reference`, and `## Eval Spec Format` sections. Each is a
  paragraph + short code block + "Covered in the full reference"
  list + `docs/<file>.md` link.
- **Teaser shape (D2 lean)**: `README.md`'s `## Three Layers of
  Validation`, `## Pytest Integration`, and `## Using /clauditor in
  Claude Code` sections. Each is one sentence + small code block +
  `docs/<file>.md` link.
- **Promoted-doc opener**: every new file under `docs/` (seven of
  them: `cli-reference.md`, `eval-spec-reference.md`, `layers.md`,
  `pytest-plugin.md`, `quick-start.md`, `skill-usage.md`, plus the
  merged content in `architecture.md`) opens with the four-element
  shape. `docs/pytest-plugin.md` lines 1-6 are the minimal example;
  `docs/quick-start.md` lines 1-6 are the mid-length example.
- **Anchor preservation**: every old README H2 text from the
  promoted list (`## Quick Start`, `## Three Layers of Validation`,
  `## CLI Reference`, `## Pytest Integration`, `## Eval Spec
  Format`, `## Using /clauditor in Claude Code`) is preserved
  byte-identical in the teaser. GitHub slug-derived anchors
  (`#quick-start`, `#three-layers-of-validation`, etc.) keep
  resolving.

Plan decisions codifying the recipe: DEC-002 (expanded scope from
the 6-item ticket to cover four deep-ref promotions), DEC-007
(first-screen layout), DEC-008 (anchor preservation via teaser),
DEC-009 (promoted-doc opener template), DEC-010 (mixed D3/D2 teaser
strategy), DEC-011 (~155-line budget).

## When this rule applies

Any future README restructure or new top-level doc set: a project's
README grows past 300 lines, or inbound SEO starts landing readers
on `docs/*.md` files without context, or a new feature adds >40
lines of reference material and the author must decide whether to
append or promote. Apply the full recipe — trigger classification +
teaser shape + opener template + anchor preservation — not just one
piece.

The rule also applies to derivative docs sets a future feature may
introduce (e.g. a `docs/adr/` decision-record directory, a
`docs/recipes/` how-to set). The opener template + breadcrumb
invariant generalize; the teaser shape applies when each sub-doc has
a matching summary in the parent index page.

## When this rule does NOT apply

- Single-file projects with no `docs/` directory, where the README
  is the only doc and readers have no alternative landing page.
  Length-trim in place instead.
- Rule-protected docs (e.g. `docs/stream-json-schema.md`, which is
  anchored by `.claude/rules/stream-json-schema.md`). Those have
  their own update contract that takes precedence over this
  restructure recipe.
- Internal-only debug notes, session logs, or ephemeral planning
  documents that no external reader will ever land on. The
  breadcrumb + teaser machinery is for published reference content,
  not for scratch files.
- Cases where the "deep-ref" content is genuinely small (<20 lines)
  and a full promotion would produce more ceremony than value.
  Collapse to `<details>` in root instead.
