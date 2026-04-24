---
name: find-kid-activities
description: Find free or low-cost kid-friendly venues and events near a location, filtered by date, distance, age range, cost tier, and category. Use when a parent asks for things to do with kids, family-outing ideas, or weekend activity suggestions for a specific city.
license: MIT
compatibility: "Works with Claude Code's WebFetch tool. No external dependencies beyond the model's browsing capability."
metadata:
  example-of: "examples/.claude/skills/find-kid-activities/"
argument-hint: "<city> [--dates <when>] [--distance <miles>] [--ages <range>] [--cost <tier>] [--type venues|events|both] [--count <n>]"
---

# /find-kid-activities — Find kid-friendly venues and events

You help a parent find things to do with kids in a specific location.
Return two sections: **Venues** (places open to drop in) and **Events**
(scheduled, time-bound activities).

## Inputs

- **City** (required, positional): e.g. `"Cupertino, CA"`.
- **`--dates`**: `today`, `this weekend`, or an ISO date.
- **`--distance`**: e.g. `15mi` — radius from city center.
- **`--ages`**: range like `4-6`, `0-2`, `7-10`.
- **`--cost`**: comma-separated tiers from `Free, $, $$, $$$`.
- **`--type`**: `venues`, `events`, or `both` (default).
- **`--count`**: target number of venues + events combined (default 5).

## Output shape

Markdown with two H2 sections. Each entry is a numbered bold heading
followed by a fielded list:

```
## Venues

**1. Sample Park**
- name: Sample Park
- address: 123 Main St, Cupertino, CA
- hours: 7am-9pm daily
- website: https://example.com/park
- cost: Free
- ages: All ages

## Events

**1. Story Time at the Library**
- name: Story Time at the Library
- date: 2026-04-25
- time: 10:30am
- event_url: https://example.com/storytime
- ages: 3-6
```

Required venue fields: `name`, `address`, `hours`, `website`, `cost`,
`ages`. Required event fields: `name`, `date`, `time`, `event_url`,
`ages`. Include `phone` for venues when available.

## Workflow

1. Parse the city + flags.
2. Use `WebFetch` to look up local family-activity guides, library
   event calendars, and parks/recreation pages.
3. Filter by distance, age range, cost tier.
4. Render the two-section output. Aim for at least 2 venues; events
   can be empty if nothing is scheduled.
5. End cleanly — do not ask follow-up questions.
