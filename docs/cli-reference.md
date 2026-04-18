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
clauditor propose-eval <skill.md>      # LLM-assisted EvalSpec bootstrap (SKILL.md + optional capture)
clauditor doctor                       # Report environment diagnostics
```

## propose-eval

LLM-assisted EvalSpec bootstrap. `clauditor propose-eval <skill.md>` reads the SKILL.md file and (optionally) a captured skill run, asks Sonnet to propose a full three-layer EvalSpec (L1 assertions, L2 tiered extraction, L3 rubric), validates the proposal through `EvalSpec.from_dict`, and writes a sibling `<skill>.eval.json` next to the SKILL.md (the same path `SkillSpec.from_file` and `clauditor init` auto-discover). Use it to skip the blank-spec drudgery when onboarding a new skill.

### Required inputs

- `<skill_md>` (positional) — path to the SKILL.md file. The generated spec is written to the sibling `<skill_stem>.eval.json` (e.g. `foo.md` → `foo.eval.json`, `SKILL.md` → `SKILL.eval.json`).

### Flags

| Flag | Purpose |
| ---- | ------- |
| `--from-capture PATH` | Override capture discovery with an explicit file. Wins over `--from-iteration`. |
| `--from-iteration N` | Load the capture from `.clauditor/runs/iteration-N/<skill>/run-0/output.txt` (N must be a positive integer). |
| `--force` | Overwrite an existing sibling `<skill>.eval.json`. Without it, the command refuses with exit 1. |
| `--dry-run` | Print the built proposer prompt to stdout and exit; do not call Anthropic and do not write a file. Cost-free preview. |
| `--model MODEL` | Override the proposer model (default: `claude-sonnet-4-6`). |
| `--json` | Emit the full `ProposeEvalReport` JSON envelope on stdout (includes `schema_version`, tokens, duration, validation errors). |
| `-v, --verbose` | Log capture source, redaction count, model, and token estimates to stderr. |
| `--project-dir PATH` | Override project root (default: cwd). Used for capture discovery and relative-path reporting. |

### Examples

```bash
# Basic bootstrap — uses DEC-001 capture discovery, writes eval.json
clauditor propose-eval .claude/commands/my-skill.md

# Preview the built proposer prompt (no Anthropic call)
clauditor propose-eval .claude/commands/my-skill.md --dry-run

# Bootstrap from an explicit capture file (takes precedence over discovery)
clauditor propose-eval .claude/commands/my-skill.md --from-capture tests/eval/captured/my-skill.txt

# Overwrite an existing eval.json
clauditor propose-eval .claude/commands/my-skill.md --force
```

### Exit codes

Mirrors the DEC-006 contract in `src/clauditor/cli/propose_eval.py`:

| Code | Meaning |
| ---- | ------- |
| `0` | Success — prompt printed (`--dry-run`), report envelope printed (`--json`), or spec written to `eval.json`. |
| `1` | Response-parse failure from the proposer (malformed JSON, missing top-level shape) OR collision: `eval.json` already exists and `--force` was not passed. |
| `2` | Spec-validation failure OR pre-call input error — the proposed dict did not survive `EvalSpec.from_dict` (missing required fields, duplicate ids, invalid `format` strings, …), OR the prompt exceeded the token budget, OR `--from-capture`/`--from-iteration` pointed at a missing/invalid target. Errors printed on stderr. |
| `3` | Anthropic API error — auth failure, rate-limit exhaustion, connection error, or any non-retriable SDK error surfaced by `clauditor._anthropic.call_anthropic`. |

### Capture discovery (DEC-001)

When neither `--from-capture` nor `--from-iteration` is provided, the loader looks for a capture file in this order and uses the first match:

1. `<project_dir>/tests/eval/captured/<skill_name>.txt` (primary — the same directory `clauditor capture` writes to).
2. `<project_dir>/.clauditor/captures/<skill_name>.txt` (fallback).

The `<skill_name>` is resolved from the SKILL.md frontmatter's `name` field, falling back to the containing directory's basename, and finally to the literal string `"skill"` if neither source matches the security regex (`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`). This clamp blocks path-traversal attempts via a malicious `name:` field (e.g. `"../../etc/passwd"`). If no capture is found, the proposer runs against the SKILL.md alone — the quality of the generated spec will be lower, but the command does not error out. Malformed frontmatter emits a stderr warning and falls through to treating the whole file as body.

### Token budget (DEC-005 / DEC-011)

The prompt is pre-checked against a **50,000-token cap** via a `len(prompt) / 4` heuristic before the Anthropic call. This is a coarse safety rail that prevents a mid-stream 413 when a SKILL.md + capture is pathologically large. Oversize inputs fail fast with an `ERROR:` line on stderr and exit code **2** (pre-call input error per DEC-006) before any Anthropic call is made.

### Security / scrubbing (DEC-008)

Captured skill output is scrubbed through `clauditor.transcripts.redact` before it lands in the prompt OR the sidecar. The redaction is non-mutating (per `.claude/rules/non-mutating-scrub.md`): secret-shaped substrings (Anthropic keys, GitHub PATs, Bearer tokens) are replaced in a new copy while the caller's in-memory representation — if any — stays untouched. Both CLI-override paths (`--from-capture`, `--from-iteration`) apply the same scrub; the loader-discovered path is scrubbed by the loader itself.

### Relationship to `init` and `capture`

- `clauditor init` writes a **skill stub** (`SKILL.md` + starter `eval.json`) for a brand-new skill. Use it first when the skill itself does not yet exist.
- `clauditor propose-eval` fills in an `eval.json` for a skill whose **SKILL.md already exists**. It does not write a skill stub and does not regenerate SKILL.md.
- `clauditor capture <skill> -- "args"` produces the captured run that `propose-eval` reads as grounding context. Capturing before `propose-eval` typically lifts the quality of the generated spec (the proposer sees what real output looks like).

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
