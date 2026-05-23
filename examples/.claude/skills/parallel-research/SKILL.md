---
name: parallel-research
description: Research a topic across three lanes — editorial overviews, authoritative primary sources, and cross-source verification — then synthesize a sourced briefing. Use when a user asks for a thorough, multi-source research summary on a topic, place, or product.
license: MIT
compatibility: "Works with Claude Code's WebSearch/WebFetch tools and sequential Task calls. Does NOT use run_in_background, so it runs end-to-end under clauditor's `claude -p` transport."
metadata:
  example-of: "examples/.claude/skills/parallel-research/"
argument-hint: "<topic> [--depth quick|deep] [--lanes <n>] [--count <n>]"
---

# /parallel-research — Multi-lane research fan-out (sequential)

You research a topic across three distinct lanes and synthesize the
findings into one sourced briefing. This is the **refactored-good**
shape of a research fan-out: each lane runs as a **sequential `Task`
call** (NOT `Task(run_in_background=true)`), so the parent waits for
every lane to finish before synthesizing. That keeps the full
transcript visible to `claude -p` — the transport clauditor uses.

> This skill is the runnable companion to **Recipe A** in clauditor's
> `docs/skill-usage.md`. It echoes the `find-restaurants --depth deep`
> parallel-research motif but drops `run_in_background` so it
> evaluates cleanly under clauditor.

## Inputs

- **Topic** (required, positional): e.g. `"electric kettles"`.
- **`--depth`**: `quick` (one lane, light synthesis) or `deep`
  (all three lanes; default).
- **`--lanes`**: override the lane count (default 3 in deep mode).
- **`--count`**: target number of sources per lane (default 3).

## Research lanes

In `deep` mode the three lanes are:

1. **Editorial** — secondary overviews, roundups, and guides that
   summarize the landscape.
2. **Authoritative** — primary sources, official docs, standards
   bodies, manufacturer specs.
3. **Verification** — independent corroboration that cross-checks
   claims from the first two lanes and flags disagreements.

## Output shape

Markdown with three H2 lane sections plus a synthesis section. Each
lane entry is a numbered bold heading followed by a fielded list. The
`example.com` URLs below are illustrative of the output *shape* only — a
live run cites the real sources found via WebSearch/WebFetch:

```text
## Editorial

**1. The Wirecutter Guide**
- name: The Wirecutter Guide
- source: https://example.com/editorial/kettles
- claim: Gooseneck kettles win for pour-over precision.

## Authoritative

**1. Manufacturer Spec Sheet**
- name: Manufacturer Spec Sheet
- source: https://example.com/specs/kettle-a
- claim: Heats 1L to boil in 4 minutes at 1500W.

## Verification

**1. Independent Lab Test**
- name: Independent Lab Test
- source: https://example.com/labs/kettle-test
- claim: Confirms the 4-minute boil time within margin.

## Synthesis

A short paragraph reconciling the lanes, noting agreements and any
conflicts surfaced by the verification lane.
```

Required entry fields in every lane: `name`, `source`, `claim`.

## Workflow

1. Parse the topic + flags. In `quick` mode, run only the Editorial
   lane and a light synthesis.
2. **Run each lane as a sequential `Task` call** — dispatch lane 1,
   wait for it, then lane 2, then lane 3. Do **not** use
   `run_in_background=true`; the parent must collect every lane's
   output before synthesizing so the full transcript stays visible
   to the eval harness.
3. Within a lane, the sub-agent may emit multiple `WebSearch` /
   `WebFetch` `tool_use` blocks in one turn (parent-side parallel
   tool calls are fine — only background `Task` spawns are not).
4. Render the three lane sections, then a **Synthesis** section that
   reconciles the lanes and flags any conflicts the verification
   lane caught.
5. End cleanly — do not ask follow-up questions.
