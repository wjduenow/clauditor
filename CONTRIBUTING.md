# Contributing to clauditor

## Pre-release dogfood

Before tagging a new release, clauditor must pass two gates against its
own bundled slash-command skill. These are not run in CI (see rationale
below); they are maintainer-run immediately before any release tag.

### The two gates

1. **L1 — deterministic assertions (free, seconds):**

   ```bash
   uv run clauditor validate src/clauditor/skills/clauditor/SKILL.md
   ```

2. **L3 — LLM-graded quality (costs Sonnet tokens, ~1 minute):**

   ```bash
   uv run clauditor grade src/clauditor/skills/clauditor/SKILL.md \
     --eval src/clauditor/skills/clauditor/assets/clauditor.eval.json
   ```

Both commands must exit 0. If either gate fails, do NOT tag the
release — a failing bundled skill is the user-visible failure mode
that this checklist exists to catch.

### Why manual, not CI

The `clauditor_spec` pytest fixture (the natural candidate for a
per-PR CI check) shells out to the live `claude -p` subprocess on
every invocation — it is not a mock. Running dogfood on every pull
request would:

- Burn Anthropic API tokens on unrelated changes.
- Couple CI uptime to Claude's infrastructure uptime (unrelated
  Claude outages would turn every PR red).
- Add 30–60 seconds of latency to every CI run.

Pre-release is where the bundled-skill regression actually matters:
we ship a wheel with a broken `/clauditor` at most once per release,
not once per merge.

### Debugging a failed gate

If either gate fails, do not tag. Investigate via the sidecar
artifacts clauditor persists under `.clauditor/iteration-N/clauditor/`:

- `assertions.json` — per-assertion pass/fail with the matching text
  excerpt.
- `grading.json` — per-criterion verdict, score, evidence, and
  reasoning.
- `run-0/output.txt` / `run-0/output.jsonl` — the raw skill output
  and stream-json transcript.

File a beads issue describing the regression (what changed, which
gate failed, which criterion or assertion) and hold the release
until the issue is closed.

### Lineage

This protocol is recorded in DEC-007 of
[`plans/super/43-setup-slash-command.md`](plans/super/43-setup-slash-command.md)
and is delivered by the `clauditor-3xy` bead epic (see `bd show
clauditor-3xy` for the full story list).
