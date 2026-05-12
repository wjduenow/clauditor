# Cost tracking and per-iteration context

This doc covers how clauditor measures and persists the cost of an LLM grading run — both dollars (`cost_usd`) and reasoning-token effort — plus the broader `context.json` sidecar that captures comparability metadata so `audit` and `trend` can compare like-with-like across iterations.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a one-paragraph summary.

## What gets recorded

Every `clauditor grade` (or `validate`) iteration writes a sibling sidecar at `.clauditor/iteration-N/<skill>/context.json` with these fields (schema_version: 1, designed to stay at v1 — see [Always-v1 contract](#always-v1-contract) below):

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `int = 1` | Always first key on disk. |
| `harness` | `str` | `"claude-code"` or `"codex"`. Materialized by the four-layer harness resolver. |
| `provider` | `str \| None` | `"anthropic"` or `"openai"`, or `null` when no LLM grading happened (e.g. `validate`-only). |
| `model_runner` | `str \| None` | The model the harness actually used to run the skill. Null when the harness can't expose it (Claude Code's stream-json `result` carries no model field unless pinned). |
| `model_grader` | `str \| None` | The model the grader called. Null iff `provider` is null. |
| `system_prompt_source` | `str` | `"explicit"` / `"agents_md"` / `"skill_md"` — where the system prompt came from for this run. |
| `sandbox_mode` | `str \| None` | Codex-only: `"read-only"` / `"workspace-write"` / `"danger-full-access"`. Null when `harness != "codex"`. |
| `reasoning_tokens` | `int \| None` | Separately-billed thinking/reasoning tokens summed across the grader chain. See [Reasoning tokens](#reasoning-tokens). |
| `cost_usd` | `float \| None` | Estimated grader-call cost in USD. See [Cost estimation](#cost-estimation). |

## Cost estimation

The pure helper `clauditor._providers._pricing.estimate_cost(provider, model, input_tokens, output_tokens, reasoning_tokens=None)` returns a USD float (or `None` on lookup miss). The composition helper `compute_iteration_cost_usd` sums Layer 2 + Layer 3 grader-call cost from the already-populated `GradingReport` / `ExtractionReport` dataclasses.

The pricing table is hardcoded into clauditor at release time — it lives at `src/clauditor/_providers/_pricing.py::_PRICING_TABLE`. Today's coverage:

| Provider | Model | Input $/MTok | Output $/MTok |
| --- | --- | --- | --- |
| anthropic | claude-sonnet-4-6 | 3.00 | 15.00 |
| anthropic | claude-opus-4-7 | 15.00 | 75.00 |
| anthropic | claude-haiku-4-5 | 0.80 | 4.00 |
| openai | gpt-5.4 | 2.50 | 10.00 |
| openai | gpt-5.4-mini | 0.15 | 0.60 |
| openai | o4-mini | 1.10 | 4.40 |

Reasoning tokens roll into the output rate (model bills them at output prices). The cost is **all-or-nothing across the grader chain**: if any Layer 2 or Layer 3 call can't be priced (unknown model, missing usage data), `cost_usd` is `null` rather than a partial sum — a "roughly right" estimate is silently wrong for budgeting per the design decision in [#169](https://github.com/wjduenow/clauditor/issues/169).

### Stale-table warnings

Clauditor emits a one-time stderr warning per process when the pricing table is older than 90 days. Today's verification date is pinned at `_LAST_VERIFIED` in the same module. The warning names the canonical price sources (platform.claude.com, openai.com/api/pricing) so a maintainer can update the table. The warning is loud-but-safe: a typo in the date constant still fires the warning (treats the table as stale) rather than crashing the grading run.

### Unknown-model warnings

Calling `estimate_cost("anthropic", "claude-future-9", ...)` returns `None` AND emits a one-time-per-`(provider, model)` warning to stderr — distinct unknown models each get their own first-call warning rather than the first miss muting all subsequent ones. An unknown **provider** stays silent (different code path) so a typo'd provider doesn't flood stderr.

## Reasoning tokens

The `reasoning_tokens` field is the count of separately-billed thinking tokens the grader chain consumed. Sourced asymmetrically per provider:

- **Anthropic**: always `None`. The SDK's `Usage` object has no separately-billed thinking-token field; for extended-thinking models the thinking tokens are already included in `output_tokens`. Clauditor records `None` rather than fabricating a value.
- **OpenAI**: extracted from `usage.output_tokens_details.reasoning_tokens` via a defensive helper (`isinstance` guards, `bool`-vs-`int` discipline). `0` is preserved as a real signal ("model didn't reason on this call"); `None` means "couldn't read."

The grader chain may make multiple calls (variance reps, parse-retry attempts, blind-compare's two parallel calls). The chain-level aggregator `_sum_optional_reasoning_tokens` distinguishes "no source surfaced a count" (all-`None` → `None`) from "sources surfaced zero" (mixed or all-int → real sum), so a single `None` component doesn't poison a sum that has at least one real value.

## Always-v1 contract

`context.json` is engineered so anticipated follow-ups can populate new fields **without bumping the schema version**. The trick: nullable fields are pre-declared in v1. When [#169](https://github.com/wjduenow/clauditor/issues/169) wired up `cost_usd` and [#170](https://github.com/wjduenow/clauditor/issues/170) wired up `reasoning_tokens`, the on-disk shape stayed v1 and every existing reader kept working unchanged. This is the inverse of `grading.json` / `extraction.json`, which bumped at every additive change (v1→v5 today) because their fields weren't pre-declared.

Bumps cost integration churn (default-on-read defaults, loader branches, regression tests). Pre-declaring nullable fields when the follow-ups are known up-front avoids the churn.

## How to read it

### From the CLI

`clauditor audit` reads every iteration's `context.json` and surfaces the metadata in its grouped output (group key: `(harness, provider, layer, id)`). `clauditor trend` reads `history.jsonl` (which carries the harness + provider stamps but not the per-iteration context) and refuses to silently average across mismatched harness or provider axes unless `--cross-harness` / `--cross-provider` is passed.

`clauditor badge` reads the latest iteration's sidecars and emits both a shields.io endpoint JSON AND a sibling `<name>.clauditor.json` extension carrying the per-iteration context — see [docs/badges.md](badges.md) for the dual-file pattern.

### Programmatically

```python
from clauditor.context import IterationContext
import json

with open(".clauditor/iteration-7/my-skill/context.json") as f:
    ctx = IterationContext(**json.load(f))

print(f"Grader: {ctx.provider}/{ctx.model_grader}")
print(f"Cost: ${ctx.cost_usd:.4f}" if ctx.cost_usd else "Cost: unknown")
print(f"Reasoning: {ctx.reasoning_tokens or 0} tokens")
```

## When cost is `None`

| `cost_usd` is null because… | Fix |
| --- | --- |
| `validate`-only run (no grader call) | Expected. Cost only meaningful for `grade` / `extract`. |
| Grader call used an unknown model | Look for the stderr unknown-model warning. Add the model to `_PRICING_TABLE` and bump `_LAST_VERIFIED`. |
| One of Layer 2 / Layer 3 was priced but the other wasn't | All-or-nothing per [#169](https://github.com/wjduenow/clauditor/issues/169) DEC-002. Add the missing model. |
| Pricing table older than 90 days | Stale warning fires but cost is still computed. Update the table. |

## See also

- [docs/audit-trend-workflow.md](audit-trend-workflow.md) — how `audit` / `trend` consume the data.
- [docs/transport-architecture.md](transport-architecture.md) — provider / transport / harness axes.
- [docs/badges.md](badges.md) — the sibling `.clauditor.json` extension that surfaces context in the badge pair.
