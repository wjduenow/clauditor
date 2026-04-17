# CLI Reference

Full reference for every `clauditor` subcommand: arguments, flags, the persistent metric history, and the exit codes scripts can key off of. Read this when you need to look up a specific option or wire clauditor into CI, not for the conceptual overview of the three-layer framework.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

```bash
clauditor init <skill.md>              # Generate starter eval.json
clauditor validate <skill.md>          # Run Layer 1 assertions
clauditor validate <skill.md> --json   # JSON output for CI
clauditor run <skill-name> --args "…"  # Run skill, print output
clauditor extract <skill.md>           # Layer 2 schema extraction
clauditor extract <skill.md> --dry-run # Print extraction prompt only
clauditor grade <skill.md>                             # Layer 3 quality grading (auto-increments iteration)
clauditor grade <skill.md> --variance 3                # Variance measurement
clauditor grade <skill.md> --only-criterion clarity    # Run a subset (repeatable, substring match)
clauditor grade <skill.md> --iteration 5               # Write to .clauditor/iteration-5/<skill>/
clauditor grade <skill.md> --iteration 5 --force       # Overwrite an existing iteration-5/
clauditor grade <skill.md> --diff                      # Compare against prior iteration
clauditor compare --skill <skill> --from 1 --to 2      # Diff two iterations by number
clauditor compare .clauditor/iteration-1/<skill> .clauditor/iteration-2/<skill>  # Diff by directory
clauditor compare before.txt after.txt --spec <skill.md>  # Re-grade two captures
clauditor trend <skill> --metric total.total     # Tab-separated history + ASCII sparkline
clauditor trend <skill> --list-metrics           # List available metric paths
clauditor trend <skill> --metric grader.input_tokens --command extract  # Filter by subcommand
clauditor triggers <skill.md>          # Trigger precision testing
clauditor capture <skill> -- "args"    # Run skill, save stdout to tests/eval/captured/
clauditor suggest <skill.md>           # Propose SKILL.md edits from prior failing iterations
clauditor doctor                       # Report environment diagnostics
```

## Persistent metric history

Every `clauditor grade`, `extract`, and `validate` run appends a JSON line to `.clauditor/history.jsonl`. Each record carries a `command` discriminator, a nested `metrics` dict, and (for `grade`) the `iteration` slot and on-disk `workspace_path`.

```json
{
  "schema_version": 1,
  "command": "grade",
  "ts": "2026-04-13T15:00:00+00:00",
  "skill": "find-restaurants",
  "iteration": 4,
  "workspace_path": ".clauditor/iteration-4/find-restaurants",
  "pass_rate": 0.83,
  "mean_score": 0.75,
  "metrics": {
    "skill":   {"input_tokens": 1200, "output_tokens": 800},
    "quality": {"input_tokens": 900,  "output_tokens": 350},
    "total":   {"input_tokens": 2100, "output_tokens": 1150, "total": 3250},
    "duration_seconds": 12.3
  }
}
```

Token buckets: `skill` (subprocess), `grader` (Layer 2 extract), `quality` (Layer 3 rubric), `triggers` (trigger precision). Buckets are **absent** when the command doesn't invoke them — e.g. `extract` records have `skill` + `grader`, `validate` records have `skill` only. `total` aggregates across all present buckets.

Use `clauditor trend <skill> --metric <dotted.path>` to view a series. Paths walk the nested `metrics` dict (`total.total`, `skill.output_tokens`, `quality.input_tokens`, `duration_seconds` for grade records; `grader.input_tokens` for extract records) with `pass_rate` and `mean_score` as top-level shortcuts. `--command {grade,extract,validate,all}` filters by subcommand (default `grade`); pass `--command extract` to surface `grader.*` paths. `--list-metrics` prints every resolvable metric path for the skill.

Runs with `--only-criterion` skip the history append to keep longitudinal data comparable.

## Exit codes

clauditor uses structured exit codes so scripts and CI pipelines can distinguish "the tool itself failed" from "the tool ran fine but the skill under test failed its gate."

| Code | Meaning                                                                                                                                              |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Success. The command completed and, where applicable, the skill passed its gate (all assertions satisfied, all criteria above threshold, no regression detected, no trigger miss). |
| `1`  | Signal failed. The tool ran fine, but the skill did not meet its bar: an L1 assertion failed, an L3 criterion scored below threshold, `clauditor compare` detected a regression relative to baseline, or a trigger classification was wrong. The on-disk artifacts are complete and valid; the skill needs fixing, not the tool. |
| `2`  | Input error. A user-supplied argument was missing, malformed, or incompatible with another flag (e.g. `--iteration` without an integer value, a skill `.md` file that does not exist, an eval spec that fails schema validation). The command exited before doing work; re-run with corrected arguments. |
| `3`  | Anthropic API error. `clauditor suggest` only. The Anthropic SDK returned a non-retriable failure (auth, malformed request, exhausted retries). No sidecar is written; re-run once the upstream issue is resolved. |

Commands that only invoke the Anthropic API transiently (`extract`, `grade`, `triggers`) funnel API failures through the same retry policy as `suggest` but surface them as exit 1 with an `ERROR:` line on stderr rather than a distinct code.
