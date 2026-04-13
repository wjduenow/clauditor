# clauditor

<p align="center">
  <img src="docs/assets/clauditor-social-preview.png" alt="clauditor" width="600">
</p>

[![CI](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml/badge.svg)](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/clauditor)](https://pypi.org/project/clauditor/)
[![Python versions](https://img.shields.io/pypi/pyversions/clauditor)](https://pypi.org/project/clauditor/)
[![License](https://img.shields.io/github/license/wjduenow/clauditor)](https://github.com/wjduenow/clauditor/blob/dev/LICENSE)
[![codecov](https://codecov.io/gh/wjduenow/clauditor/branch/dev/graph/badge.svg)](https://codecov.io/gh/wjduenow/clauditor)

Auditor for Claude Code skills and slash commands. Validates structured output against schemas using layered evaluation — deterministic assertions, LLM-graded extraction, and quality regression testing. Catches when your skill produces the wrong shape, not just the wrong answer.

When you build a skill — like a slash command that finds restaurants or generates reports — you need to know it keeps working correctly after every change. clauditor answers three questions at different cost/confidence levels:

**"Does it have the right shape?"** (Layer 1 — free, instant)
Did the output include URLs? At least 5 results? The word "Venues"? No error messages? These are deterministic checks that run in milliseconds with no API costs. Good for CI on every commit.

**"Did it extract the right fields?"** (Layer 2 — pennies, ~1 second)
Uses a cheap, fast model (Haiku) to read the output and check: does each venue have a name, address, and phone number? Are there at least 3 entries in each section? Catches structural problems that string matching can't.

**"Is the answer actually good?"** (Layer 3 — dollars, release-gating)
Uses a stronger model (Sonnet) to grade output against a rubric you write: "Are venues within the specified distance? Are events on the right date?" Also does A/B testing (is the skill better than raw Claude?), variance measurement (does it give consistent results across runs?), and trigger precision testing (does the right query activate the right skill?).

You ship AI features faster because you catch regressions automatically instead of manually spot-checking output. Layer 1 runs in CI on every push for free. Layer 3 runs before releases to catch quality problems that would otherwise reach users. The layered approach means you're not burning API dollars on every commit — just on the checks that need intelligence.

## Install

```bash
pip install clauditor

# With LLM grading support (Layers 2 & 3):
pip install clauditor[grader]
```

Or install from source:

```bash
git clone https://github.com/wjduenow/clauditor.git
cd clauditor
uv sync --dev
```

## Quick Start

### 1. Create an eval spec for your skill

```bash
clauditor init .claude/commands/my-skill.md
```

This creates `my-skill.eval.json` alongside your skill file:

```json
{
  "skill_name": "my-skill",
  "test_args": "\"San Jose, CA\" --depth quick",
  "assertions": [
    {"type": "contains", "value": "Results"},
    {"type": "has_entries", "value": "3"},
    {"type": "has_urls", "value": "3"},
    {"type": "min_length", "value": "500"}
  ],
  "sections": [
    {
      "name": "Results",
      "min_entries": 3,
      "fields": [
        {"name": "name", "required": true},
        {"name": "address", "required": true}
      ]
    }
  ]
}
```

### 2. Validate against captured output

```bash
# Run skill and validate in one step:
clauditor validate .claude/commands/my-skill.md

# Or validate against pre-captured output:
clauditor validate .claude/commands/my-skill.md --output captured.txt

# JSON output for CI:
clauditor validate .claude/commands/my-skill.md --json
```

### 3. Use in pytest

```python
def test_my_skill(clauditor_runner):
    result = clauditor_runner.run("my-skill", '"San Jose, CA" --depth quick')
    result.assert_contains("Results")
    result.assert_has_entries(minimum=3)
    result.assert_has_urls(minimum=3)

def test_with_eval_spec(clauditor_spec):
    spec = clauditor_spec(".claude/commands/my-skill.md")
    results = spec.evaluate()
    assert results.passed, results.summary()
```

## Three Layers of Validation

### Layer 1: Deterministic Assertions (free, instant)

No API calls. Regex, string matching, and counting.

```python
result.assert_contains("Venues")           # substring check
result.assert_not_contains("Error")        # absence check
result.assert_matches(r"\*\*\d+\.")        # regex
result.assert_has_entries(minimum=5)        # numbered entries
result.assert_has_urls(minimum=3)           # URL count
result.assert_min_length(500)              # output length
```

Or define in `eval.json`:

```json
{
  "assertions": [
    {"type": "contains", "value": "Venues"},
    {"type": "regex", "value": "\\*\\*\\d+\\."},
    {"type": "has_urls", "value": "3"},
    {"type": "not_contains", "value": "Error"}
  ]
}
```

### Layer 2: LLM Schema Extraction (cheap, ~1 sec)

Uses Haiku to extract structured fields, then validates against your schema. Requires `pip install clauditor[grader]`.

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
      "min_entries": 3,
      "fields": [
        {"name": "name", "required": true},
        {"name": "address", "required": true},
        {"name": "hours", "required": true},
        {"name": "website", "required": true},
        {"name": "phone", "required": false}
      ]
    }
  ]
}
```

#### Field validation (`format`)

Each field can declare a `format` that validates the extracted value. The `format` key accepts **either** a registered format name **or** an inline regex — clauditor looks up the string in `FORMAT_REGISTRY` first and falls back to compiling it as a regex if there's no match.

Decision tree:

- Is there a registered name in `FORMAT_REGISTRY` that fits? Use it (e.g. `"format": "phone_us"`).
- Need something custom? Put a regex string directly in `format` (e.g. `"format": "^[a-z0-9-]+$"`).
- Lookup is **registry-first, regex-fallback**. Invalid regexes raise `ValueError` at spec construction time, so typos fail fast.

```json
{"name": "phone", "format": "phone_us"}
```

```json
{"name": "slug", "format": "^[a-z0-9-]+$"}
```

See [`FORMAT_REGISTRY` in `src/clauditor/formats.py`](src/clauditor/formats.py) for the full list of registered names (common entries: `phone_us`, `phone_intl`, `email`, `url`, `domain`, `date_iso`, `zip_us`, `uuid`).

### Layer 3: Quality Grading (expensive, release-only)

Uses Sonnet to grade skill output against a rubric you define. Requires `ANTHROPIC_API_KEY` and `pip install clauditor[grader]`.

#### Quality Grading

Define rubric criteria in your eval spec:

```json
{
  "grading_criteria": [
    "Are all venues within the specified distance?",
    "Are events actually happening on the target date?",
    "Do cost tiers match the budget filter?"
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
clauditor grade .claude/commands/my-skill.md --dry-run   # Print prompt, no API call
clauditor grade .claude/commands/my-skill.md --save       # Persist results to .clauditor/
clauditor grade .claude/commands/my-skill.md --diff       # Compare against prior saved results
```

Each criterion gets a pass/fail, score (0.0-1.0), evidence (quoted output), and reasoning. Use `--save` to persist results for regression tracking, and `--diff` to compare against a prior run (flags regressions where a criterion's score drops by more than 0.1).

#### A/B Comparison

Runs your skill and raw Claude side-by-side against the same rubric. Flags regressions where the baseline passes but your skill fails.

```bash
clauditor compare before.grade.json after.grade.json
```

Diffs two saved grade reports (from `--save`) or two captured outputs (with `--spec`), printing `[REGRESSION]` for pass→fail flips and `[IMPROVEMENT]` for fail→pass. Exits 1 on any regression.

#### Variance Measurement

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

#### Trigger Precision Testing

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

#### Python API

```python
import asyncio
from clauditor.quality_grader import grade_quality, measure_variance
from clauditor.comparator import compare_ab
from clauditor.triggers import test_triggers
from clauditor.spec import SkillSpec

spec = SkillSpec.from_file(".claude/commands/my-skill.md")

# Quality grading
report = asyncio.run(grade_quality(output, spec.eval_spec))
print(f"{report.pass_rate:.0%} passed, mean score {report.mean_score:.2f}")

# A/B comparison
ab = asyncio.run(compare_ab(spec))
print(f"Regressions: {len(ab.regressions)}")

# Variance
var = asyncio.run(measure_variance(spec, n_runs=3))
print(f"Stability: {var.stability:.0%}")

# Trigger precision
triggers = asyncio.run(test_triggers(spec.eval_spec))
print(f"Accuracy: {triggers.accuracy:.0%}, Precision: {triggers.precision:.0%}")
```

## CLI Reference

```bash
clauditor init <skill.md>              # Generate starter eval.json
clauditor validate <skill.md>          # Run Layer 1 assertions
clauditor validate <skill.md> --json   # JSON output for CI
clauditor run <skill-name> --args "…"  # Run skill, print output
clauditor extract <skill.md>           # Layer 2 schema extraction
clauditor extract <skill.md> --dry-run # Print extraction prompt only
clauditor grade <skill.md>             # Layer 3 quality grading
clauditor grade <skill.md> --variance 3  # Variance measurement
clauditor compare before.grade.json after.grade.json  # Diff two saved grade reports
clauditor grade <skill.md> --save      # Persist results to .clauditor/
clauditor grade <skill.md> --diff      # Compare against prior results
clauditor triggers <skill.md>          # Trigger precision testing
clauditor capture <skill> -- "args"    # Run skill, save stdout to tests/eval/captured/
clauditor doctor                       # Report environment diagnostics
```

## Pytest Integration

clauditor registers as a pytest plugin automatically. Available fixtures:

- `clauditor_runner` — pre-configured `SkillRunner`
- `clauditor_spec` — factory for loading `SkillSpec` from skill files
- `clauditor_grader` — factory for Layer 3 quality grading
- `clauditor_triggers` — factory for trigger precision testing
- `clauditor_capture` — factory returning a `Path` to `tests/eval/captured/<skill>.txt` for captured-output tests

Options:

```bash
pytest --clauditor-project-dir /path/to/project
pytest --clauditor-timeout 300
pytest --clauditor-claude-bin /usr/local/bin/claude
pytest --clauditor-grade              # Enable Layer 3 tests (costs money)
pytest --clauditor-model claude-sonnet-4-6  # Override grading model
```

## Eval Spec Format

Place `<skill-name>.eval.json` alongside your `.claude/commands/<skill-name>.md`:

```
.claude/commands/
├── find-kid-activities.md
├── find-kid-activities.eval.json    ← clauditor auto-discovers this
├── find-restaurants.md
└── find-restaurants.eval.json
```

**File-based output:** Many skills save results to files instead of printing to stdout. Use `output_file` for skills that write to one known path (e.g., `research/results.md`). Use `output_files` with glob patterns for skills that produce multiple files (e.g., `["research/*.md"]`). If both are set, `output_file` takes precedence. When set, clauditor reads the file(s) after running the skill instead of capturing stdout.

A complete eval spec with all three layers:

```json
{
  "skill_name": "find-kid-activities",
  "description": "Finds kid-friendly activities near a location",
  "test_args": "\"Cupertino, CA\" --ages 4-6 --count 5 --depth quick",

  "assertions": [
    {"type": "contains", "value": "Venues"},
    {"type": "has_entries", "value": "3"},
    {"type": "has_urls", "value": "3"},
    {"type": "min_length", "value": "500"},
    {"type": "not_contains", "value": "Error"}
  ],

  "sections": [
    {
      "name": "Venues",
      "min_entries": 3,
      "fields": [
        {"name": "name", "required": true},
        {"name": "address", "required": true},
        {"name": "website", "required": true}
      ]
    }
  ],

  "output_file": "research/results.md",
  "output_files": ["research/*.md", "research/*.json"],

  "grading_criteria": [
    "Are all venues within the specified distance?",
    "Are venues appropriate for the specified age range?",
    "Do cost tiers match the budget filter?"
  ],
  "grading_model": "claude-sonnet-4-6",
  "grade_thresholds": {
    "min_pass_rate": 0.7,
    "min_mean_score": 0.5
  },

  "trigger_tests": {
    "should_trigger": [
      "Find kid activities in Cupertino",
      "What are some things to do with kids near me?"
    ],
    "should_not_trigger": [
      "What's the weather today?",
      "Help me write a Python script"
    ]
  },

  "variance": {
    "n_runs": 5,
    "min_stability": 0.8
  }
}
```

See [`examples/`](examples/.claude/commands/example-skill.eval.json) for a complete working eval spec.

### Field validation with `format`

Each `FieldRequirement` accepts a single `format` key that validates the
extracted value. `format` does double duty:

1. **Registered format name** — a shorthand for a built-in regex in the
   format registry. Run `python -c "from clauditor.formats import list_formats; print(list_formats())"`
   to see the full list. Common entries: `phone_us`, `phone_intl`,
   `email`, `url`, `domain`, `date_iso`, `date_us`, `currency_usd`,
   `zip_us`, `percentage`, `ipv4`, `uuid`.
2. **Inline regex** — any string that isn't a registered name is
   compiled with `re.compile` and used as an anchored `fullmatch` against
   the value. Invalid regexes raise `ValueError` at spec load time.

```json
{
  "sections": [
    {
      "name": "Restaurants",
      "min_entries": 1,
      "max_entries": 3,
      "fields": [
        {"name": "name",    "required": true},
        {"name": "phone",   "required": true,  "format": "phone_us"},
        {"name": "website", "required": true,  "format": "domain"},
        {"name": "zip",     "required": false, "format": "^\\d{5}$"}
      ]
    }
  ]
}
```

**`url` vs `domain`:** LLMs commonly extract the display text of markdown
links (`[paesanosj.com](https://paesanosj.com/)` → `paesanosj.com`),
which are valid domains but not URLs with a scheme. Use `format: "url"`
only when you really need `https://…`; use `format: "domain"` to accept
bare hostnames too.

**`max_entries`:** A precision signal — when set, clauditor emits a
`count_max` assertion if extraction returns more entries than the cap.
Field-level checks still run over all extracted entries so you see both
the count failure and any per-entry failures.

> **Migration note (April 2026):** The legacy `"pattern"` key on
> `FieldRequirement` has been removed. Migrate by renaming `pattern` to
> `format` — inline regexes work as before; registered names are now the
> preferred ergonomics.

## License

Apache 2.0
