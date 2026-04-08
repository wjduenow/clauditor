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

## Install

```bash
pip install clauditor

# With LLM grading support (Layer 2):
pip install clauditor[grader]
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

### Layer 3: Quality Grading (expensive, release-only)

Define rubric criteria in your eval spec for full model review:

```json
{
  "grading_criteria": [
    "Are all venues within the specified distance?",
    "Are events actually happening on the target date?",
    "Do cost tiers match the budget filter?"
  ]
}
```

```bash
# Grade against rubric:
clauditor grade .claude/commands/my-skill.md

# A/B comparison (skill vs raw Claude):
clauditor grade .claude/commands/my-skill.md --compare

# Trigger precision testing:
clauditor triggers .claude/commands/my-skill.md

# Dry run (print prompts, no API calls):
clauditor grade .claude/commands/my-skill.md --dry-run
```

## CLI Reference

```bash
clauditor init <skill.md>              # Generate starter eval.json
clauditor validate <skill.md>          # Run Layer 1 assertions
clauditor validate <skill.md> --json   # JSON output for CI
clauditor run <skill-name> --args "…"  # Run skill, print output
clauditor grade <skill.md>             # Layer 3 quality grading
clauditor grade <skill.md> --compare   # A/B comparison
clauditor grade <skill.md> --variance 3  # Variance measurement
clauditor triggers <skill.md>          # Trigger precision testing
```

## Pytest Integration

clauditor registers as a pytest plugin automatically. Available fixtures:

- `clauditor_runner` — pre-configured `SkillRunner`
- `clauditor_spec` — factory for loading `SkillSpec` from skill files
- `clauditor_grader` — factory for Layer 3 quality grading
- `clauditor_triggers` — factory for trigger precision testing

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

See [examples/](examples/) for complete eval specs.

## License

Apache 2.0
