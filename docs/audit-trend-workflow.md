# Audit, trend, and the regression-catching workflow

This doc walks the "did my SKILL.md edit improve or regress the skill?" workflow end-to-end: how `clauditor grade` builds up history over time, how `audit` and `trend` aggregate it, how `compare` deltas two snapshots, and how `badge` surfaces the latest result in CI. The individual command flags are in [docs/cli-reference.md](cli-reference.md); this doc tells the story of how they fit together.

> Returning from the [root README](../README.md). This doc is the full reference for the iteration-history workflow.

## The data flow

```
clauditor grade  →  .clauditor/iteration-N/<skill>/{assertions,extraction,grading,context}.json
                    .clauditor/history.jsonl  (append)
                                          │
                ┌─────────────────────────┼────────────────────────┐
                ▼                         ▼                        ▼
        clauditor audit          clauditor trend          clauditor badge
        (per-assertion           (one metric              (latest iteration
         pass-rate across         over time, TSV)          → shields.io JSON)
         last N iterations)
```

Every `grade` invocation:

1. Allocates a fresh `iteration-N-tmp/` staging dir.
2. Runs the skill subprocess + L1 assertions + (optional) L2 extraction + L3 grading.
3. Writes per-iteration JSON sidecars **into the staging dir** (`schema_version` first key in each).
4. Atomically renames `iteration-N-tmp/` → `iteration-N/`. Either every sidecar is visible or none are.
5. Appends one line to `history.jsonl` (`schema_version: 3` today) with the run's primary metrics, harness, and provider.

This is the data substrate the other three commands consume. There is no separate database; the iteration directory is the source of truth.

## `clauditor audit` — per-assertion pass-rate

Use audit when you want to find **which assertions are flaky** across recent iterations.

```bash
clauditor audit my-skill --last 20
clauditor audit my-skill --last 20 --min-fail-rate 0.1 --min-discrimination 0.2
clauditor audit my-skill --json > audit.json
```

Audit reads every iteration's sidecars (`assertions.json` for L1, `extraction.json` for L2, `grading.json` for L3), groups by the 4-tuple `(harness, provider, layer, id)`, and prints a pass-rate per group. The 4-tuple grouping means a run of the same skill under `(claude-code, anthropic)` and another under `(codex, openai)` produce two distinct rows rather than averaging across stacks.

Threshold flagging (`--min-fail-rate`, `--min-discrimination`) is the way you surface the "this assertion fires too often / doesn't discriminate" pattern in CI.

The `--verbose` flag includes the per-iteration `context.json` fields (model_grader, cost_usd, reasoning_tokens) in the rendered table — useful when you want to see "did this regression coincide with a model swap?"

## `clauditor trend` — one metric over time

Use trend when you want a **single metric over many iterations** for one skill — pass-rate, mean-score, total cost, token counts, grader duration.

```bash
clauditor trend my-skill --metric pass_rate --last 30
clauditor trend my-skill --metric grader.input_tokens --last 30
clauditor trend my-skill --list-metrics    # discover what's available
```

Output is TSV (iteration, timestamp, value). Pipe to `gnuplot`, `vega-lite`, your spreadsheet of choice, or just `awk '{print $3}'` for a quick eyeball.

### Cross-axis comparability refusal

**This is the most important behavior to understand.** Trend refuses by default to average across mismatched harness or provider axes:

```bash
$ clauditor trend my-skill --metric pass_rate
ERROR: Mixed providers detected in history for skill 'my-skill'
  (anthropic, openai). Pass --provider anthropic (or --provider openai)
  to filter, or --cross-provider to allow averaging.
```

You have three responses per axis:

1. **Filter**: `--provider anthropic` keeps only that stack's records.
2. **Opt in**: `--cross-provider` allows averaging, with a stderr WARNING that results may not be comparable.
3. **Fix the data**: re-run the missing stack so the history is uniform.

The same applies to `--harness` / `--cross-harness`. The two axes are independent — `--harness claude-code --cross-provider` is valid (filter harness, allow mixed provider).

Why the refusal exists: a user who ran the same eval under Claude+Sonnet one week and Codex+gpt-5.4 the next deserves a refusal, not a smooth pass-rate line that silently averages two non-comparable execution stacks. See [`.claude/rules/cross-axis-comparability-refusal.md`](../.claude/rules/cross-axis-comparability-refusal.md) for the full design rationale.

The refusal fires AFTER the full filtered set is loaded but BEFORE the `--last` slice, so a narrow window can't silently bypass it.

## `clauditor compare` — two-snapshot delta

Compare two specific grading reports (a "before" and "after") to measure the delta of a single change:

```bash
# Positional grade.json paths
clauditor compare \
  .clauditor/iteration-7/my-skill/grading.json \
  .clauditor/iteration-8/my-skill/grading.json

# Or by iteration number
clauditor compare --skill my-skill --from 7 --to 8

# A/B blind compare (different mode — judges two captured outputs)
clauditor compare --blind --skill my-skill capture-a.txt capture-b.txt
```

Delta mode honors the same cross-axis refusal as `trend`: if the two grading reports came from mismatched stacks, you get an exit-2 refusal naming both axes and the opt-in flags. `.txt` capture pairs silent-skip the check (they carry no metadata).

`--blind` mode is **untouched** by the cross-axis block — blind A/B is a per-call LLM judgment between two outputs and is *expected* to span stacks (that's the comparison).

## `clauditor badge` — surface the latest result

Generate a shields.io endpoint JSON from the latest iteration's sidecars:

```bash
clauditor badge .claude/skills/my-skill/SKILL.md --output .clauditor/badges/my-skill.json
clauditor badge .claude/skills/my-skill/SKILL.md --url-only
```

Produces a **pair** of files: the shields.io-strict JSON the badge service consumes, and a sibling `<name>.clauditor.json` carrying the full per-iteration context (layers, model, cost, reasoning tokens) for downstream tooling. See [docs/badges.md](badges.md) for the dual-file pattern and shields.io embedding recipes.

The badge reflects the **latest** iteration — it is a current-state indicator, not an aggregate. Pair it with `trend` for history visibility.

## A typical workflow

```bash
# 1. Grade the skill in its current state.
clauditor grade .claude/skills/my-skill/SKILL.md

# 2. Look at the per-assertion pass rate.
clauditor audit my-skill --last 5

# 3. Edit SKILL.md based on the failing criteria.
clauditor suggest .claude/skills/my-skill/SKILL.md
$EDITOR .claude/skills/my-skill/SKILL.md

# 4. Re-grade to get a new iteration.
clauditor grade .claude/skills/my-skill/SKILL.md

# 5. Delta the two iterations.
clauditor compare --skill my-skill --from N-1 --to N

# 6. Once happy, trend over time.
clauditor trend my-skill --metric pass_rate --last 20

# 7. Surface in README.
clauditor badge .claude/skills/my-skill/SKILL.md \
  --output .clauditor/badges/my-skill.json
git add .clauditor/badges/my-skill.json .clauditor/badges/my-skill.clauditor.json
```

If your history is mixed across harness/provider, step 6 will refuse with a clear error and tell you exactly which `--<axis>` filter or `--cross-<axis>` opt-in to add. That's the design intent — silent averaging across stacks is the failure mode it exists to prevent.

## Schema versions on disk

Useful when you're reading sidecars programmatically or migrating history. Loaders accept all listed versions; new versions are additive.

| File | Current `schema_version` | Notes |
| --- | --- | --- |
| `assertions.json` | 2 | Bumped at #152 (`harness` field). |
| `extraction.json` | 5 | Bumped through #86, #147, #152, #170 (transport, provider, harness, reasoning_tokens). |
| `grading.json` | 5 | Same lifecycle as extraction. |
| `context.json` | 1 | **Always-v1 by design** — nullable fields pre-declared so new metadata adds without bumping. |
| `history.jsonl` | 3 | Per-record; legacy v1/v2 lines default `provider="anthropic"`, `harness="claude-code"`. |
| `audit --json` output | 4 | Per-#154; adds `iteration_contexts` array. |
| `badge.json` | 1 (shields.io camelCase) + `badge.clauditor.json` v1 (sibling extension) | Two files, two lifecycles. |

For the full schema-bump rationale, see [`.claude/rules/json-schema-version.md`](../.claude/rules/json-schema-version.md).

## See also

- [docs/cli-reference.md](cli-reference.md) — every flag on every command in this workflow.
- [docs/cost-tracking.md](cost-tracking.md) — `context.json` fields and what `cost_usd` / `reasoning_tokens` mean.
- [docs/badges.md](badges.md) — the dual-file badge pattern and CI integration.
- [docs/codex-harness.md](codex-harness.md) — running skills under Codex, the harness axis the refusal protects.
