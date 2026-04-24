# Three Layers of Validation

The conceptual heart of clauditor: why it splits skill evaluation into three layers, what each layer costs, and when to use which one. Read this to understand the framework's design before authoring an eval spec or writing your first test. The layers are independent — use L1 alone on every PR, reach for L2/L3 when you need deeper signal.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

```mermaid
flowchart TD
    SPEC["eval.json"] --> L1_IN["assertions[]"]
    SPEC --> L2_IN["sections[].tiers[].fields[]"]
    SPEC --> L3_IN["grading_criteria[]"]

    L1_IN --> L1["Layer 1\nString matching\nNo API calls"]
    L2_IN --> L2["Layer 2\nSchema extraction\nHaiku"]
    L3_IN --> L3["Layer 3\nRubric grading\nSonnet"]

    L1 --> R1["assertions.json"]
    L2 --> R2["extraction.json"]
    L3 --> R3["grading.json"]

    style L1 fill:#c8e6c9
    style L2 fill:#fff9c4
    style L3 fill:#ffccbc
```

## Layer 1: Deterministic Assertions (free, instant)

No API calls. Regex, string matching, and counting.

```python
from clauditor import SkillAsserter

asserter = SkillAsserter(result)
asserter.assert_contains("Venues")           # substring check
asserter.assert_not_contains("Error")        # absence check
asserter.assert_matches(r"\*\*\d+\.")        # regex
asserter.assert_has_entries(minimum=5)        # numbered entries
asserter.assert_has_urls(minimum=3)           # URL count
asserter.assert_min_length(500)              # output length
```

Or define in `eval.json`:

```json
{
  "assertions": [
    {"id": "contains_venues", "type": "contains", "needle": "Venues"},
    {"id": "regex_numbered", "type": "regex", "pattern": "\\*\\*\\d+\\."},
    {"id": "has_urls_3", "type": "has_urls", "count": 3},
    {"id": "no_error", "type": "not_contains", "needle": "Error"}
  ]
}
```

## Layer 2: LLM Schema Extraction (cheap, ~1 sec)

Uses Haiku to extract structured fields, then validates against your schema. Requires `pip install clauditor-eval`.

```python
import asyncio
from clauditor.grader import extract_and_grade
from clauditor.schemas import EvalSpec

spec = EvalSpec.from_file("my-skill.eval.json")
results = asyncio.run(extract_and_grade(output_text, spec))
assert results.passed, results.summary()
```

The eval spec defines what fields each section should have:

```json
{
  "sections": [
    {
      "name": "Venues",
      "tiers": [
        {
          "label": "default",
          "min_entries": 3,
          "fields": [
            {"id": "venue_name",    "name": "name",    "required": true},
            {"id": "venue_address", "name": "address", "required": true},
            {"id": "venue_hours",   "name": "hours",   "required": true},
            {"id": "venue_website", "name": "website", "required": true},
            {"id": "venue_phone",   "name": "phone",   "required": false}
          ]
        }
      ]
    }
  ]
}
```

### Field validation (`format`)

Each field can declare a `format` that validates the extracted value. The `format` key accepts **only** a registered format name from `FORMAT_REGISTRY` — inline regex is no longer accepted (#99). Unknown names raise `ValueError` at spec load time, so typos fail fast before any skill run.

Decision tree:

- Is there a registered name in `FORMAT_REGISTRY` that fits? Use it (e.g. `"format": "phone_us"`).
- Need a custom pattern? Author it as an **L1 `type: regex` assertion** instead — the registry-only contract keeps format names stable across history so trend reports don't churn when a pattern changes.

```json
{"name": "phone", "format": "phone_us"}
```

The same contract applies to L1 `has_format.format`:

```json
{"id": "five-phones", "type": "has_format", "format": "phone_us", "count": 5}
```

See [`FORMAT_REGISTRY` in `src/clauditor/formats.py`](../src/clauditor/formats.py) for the full list of registered names (common entries: `phone_us`, `phone_intl`, `email`, `url`, `domain`, `date_iso`, `zip_us`, `uuid`).

## Layer 3: Quality Grading (expensive, release-only)

Uses Sonnet to grade skill output against a rubric you define. Requires `ANTHROPIC_API_KEY` and `pip install clauditor-eval`.

### Quality Grading

Define rubric criteria in your eval spec:

```json
{
  "grading_criteria": [
    {"id": "distance_match", "criterion": "Are all venues within the specified distance?"},
    {"id": "events_on_date", "criterion": "Are events actually happening on the target date?"},
    {"id": "cost_tier_match", "criterion": "Do cost tiers match the budget filter?"}
  ],
  "grade_thresholds": {
    "min_pass_rate": 0.7,
    "min_mean_score": 0.5
  }
}
```

`grade_thresholds` controls when grading passes overall. `min_pass_rate` (default 0.7) is the fraction of criteria that must pass. `min_mean_score` (default 0.5) is the minimum average score across all criteria. Both must be met. This differs from `variance.min_stability`, which measures consistency across multiple runs rather than quality of a single run.

```bash
clauditor grade .claude/commands/my-skill.md
clauditor grade .claude/commands/my-skill.md --json
clauditor grade .claude/commands/my-skill.md --dry-run      # Print prompt, no API call
clauditor grade .claude/commands/my-skill.md --iteration 5  # Write to iteration-5/ explicitly
clauditor grade .claude/commands/my-skill.md --iteration 5 --force  # Overwrite existing iteration-5/
clauditor grade .claude/commands/my-skill.md --diff         # Compare against prior iteration
clauditor grade .claude/commands/my-skill.md --baseline     # Also run without skill for A/B delta
```

With `--baseline`, clauditor runs a second pass *without* the skill prefix, grades both arms against the same rubric, and writes an additional `baseline_*.json` sidecar bundle (`baseline_assertions.json`, `baseline_extraction.json`, `baseline_grading.json`) plus a `benchmark.json` delta summary (`{pass_rate, time_seconds, tokens}`) computed as *skill-arm minus baseline-arm*. Use this to quantify whether the skill is actually doing work on top of raw Claude.

Every `grade` run is persisted to `.clauditor/iteration-N/<skill>/` automatically. By default the iteration number auto-increments to the next free slot. Pass `--iteration N` to target a specific slot; if `iteration-N/` already exists the command errors unless you also pass `--force` to overwrite.

Each criterion gets a pass/fail, score (0.0-1.0), evidence (quoted output), and reasoning. Use `--diff` to compare against a prior iteration (flags regressions where a criterion's score drops by more than 0.1).

### Iteration workspace layout

`.clauditor/` is anchored at the repository root (the nearest ancestor of your CWD containing `.git/` or `.claude/`), so `grade` from any subdirectory writes to the same place. Each run produces:

```
.clauditor/
  iteration-1/
    my-skill/
      assertions.json     # L1 AssertionSet
      extraction.json     # L2 ExtractionReport (only when sections declared)
      grading.json        # L3 GradingReport
      timing.json         # skill name, iteration, n_runs, token + duration metrics
      run-0/
        output.txt        # rendered text blocks
        output.jsonl      # raw stream-json events
  iteration-2/
    my-skill/
      assertions.json
      grading.json
      timing.json
      baseline_*.json     # with --baseline: L1/L2/L3 sidecars for the skill-less arm
      benchmark.json      # with --baseline: delta block (pass_rate / time / tokens)
      run-0/
        output.txt
        output.jsonl
      run-1/              # additional runs appear under --variance N
        output.txt
        output.jsonl
  history.jsonl
```

### Regression Comparison

Diffs two grade reports, printing `[REGRESSION]` for pass→fail flips and `[IMPROVEMENT]` for fail→pass. Exits 1 on any regression. `compare` accepts three input forms:

```bash
# 1. Numeric iteration refs (preferred — pairs with auto-incremented iterations)
clauditor compare --skill my-skill --from 1 --to 2

# 2. Iteration directory paths
clauditor compare .clauditor/iteration-1/my-skill .clauditor/iteration-2/my-skill

# 3. Saved grade-report files
clauditor compare before.grade.json after.grade.json

# Or re-grade two raw captures against a spec:
clauditor compare before.txt after.txt --spec <skill.md>
```

For a true baseline A/B run (skill vs raw Claude against the same rubric), use `clauditor compare … --blind` on two captured outputs, or run the `compare` subcommand over two iteration folders to diff grading reports. The legacy `grade --compare` CLI flag and the `comparator.compare_ab()` Python entry point have both been removed.

#### Blind A/B comparison (`--blind`)

Rubric-based grading can miss holistic regressions where two outputs pass every criterion but one visibly feels worse. For that, pass `--blind` to have a Sonnet judge compare the two outputs side-by-side without knowing which version is which:

```bash
clauditor compare before.txt after.txt --spec <skill.md> --blind
```

The judge runs twice with the A/B positions swapped so position bias shows up as disagreement. Output includes a preference (`BEFORE` / `AFTER` / `TIE`), confidence, per-output holistic score, whether the two runs agreed on the winner, and the judge's reasoning. Currently only the file-pair form is supported (iteration refs like `--from/--to` are rejected); `--blind` requires `--spec` with `user_prompt` set on the eval spec (the natural-language query the judge will see) and uses `grading_criteria` from the spec as an optional rubric hint to the judge.

Example eval spec snippet for `--blind`:

```json
{
  "skill_name": "find-venues",
  "user_prompt": "Find kid-friendly activities in Cupertino within 5 miles for ages 4-6.",
  "grading_criteria": [
    {"id": "distance_match", "criterion": "Are all venues within the specified distance?"}
  ]
}
```

### Variance Measurement

Runs the skill N times and measures output stability across runs:

```bash
clauditor grade .claude/commands/my-skill.md --variance 5
```

Configure thresholds in the eval spec:

```json
{
  "variance": {
    "n_runs": 5,
    "min_stability": 0.8
  }
}
```

Reports `score_mean`, `score_stddev`, `pass_rate_mean`, and `stability` (fraction of runs where all criteria passed). Fails if stability drops below `min_stability`.

### Trigger Precision Testing

Tests whether an LLM correctly identifies which user queries should invoke your skill:

```bash
clauditor triggers .claude/commands/my-skill.md
clauditor triggers .claude/commands/my-skill.md --json
```

Define test queries in the eval spec:

```json
{
  "trigger_tests": {
    "should_trigger": [
      "Find kid activities in Cupertino",
      "What are some things to do with kids near me?"
    ],
    "should_not_trigger": [
      "What's the weather today?",
      "Help me write a Python script"
    ]
  }
}
```

Reports accuracy, precision, and recall. Passes only when every classification is correct.

### Python API

```python
import asyncio
from clauditor.quality_grader import grade_quality, measure_variance
from clauditor.triggers import test_triggers
from clauditor.spec import SkillSpec

spec = SkillSpec.from_file(".claude/commands/my-skill.md")
output = spec.run().output  # or Path("captured.txt").read_text()

# Quality grading
report = asyncio.run(grade_quality(output, spec.eval_spec))
print(f"{report.pass_rate:.0%} passed, mean score {report.mean_score:.2f}")

# Variance
var = asyncio.run(measure_variance(spec, n_runs=3))
print(f"Stability: {var.stability:.0%}")

# Trigger precision
triggers = asyncio.run(test_triggers(spec.eval_spec))
print(f"Accuracy: {triggers.accuracy:.0%}, Precision: {triggers.precision:.0%}")
```
