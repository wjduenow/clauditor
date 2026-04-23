# Quick Start

End-to-end walkthrough from "I have a skill" to "it's validated, graded, and covered in pytest." Read this after installing clauditor when you want a concrete example of the init → validate → test loop. For a deeper dive into each layer's behavior, see [Three Layers of Validation](layers.md).

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

## 1. Create an eval spec for your skill

```bash
clauditor init .claude/commands/my-skill.md
```

This creates `my-skill.eval.json` alongside your skill file:

```json
{
  "skill_name": "my-skill",
  "description": "Eval spec for /my-skill",
  "test_args": "",
  "assertions": [
    {"id": "min_length_500", "type": "min_length", "length": 500},
    {"id": "has_urls_3", "type": "has_urls", "count": 3},
    {"id": "has_entries_3", "type": "has_entries", "count": 3},
    {"id": "no_error", "type": "not_contains", "needle": "Error"}
  ],
  "sections": [
    {
      "name": "Results",
      "tiers": [
        {
          "label": "default",
          "min_entries": 3,
          "fields": [
            {"id": "results_name", "name": "name", "required": true},
            {"id": "results_address", "name": "address", "required": true}
          ]
        }
      ]
    }
  ],
  "grading_criteria": [
    {"id": "relevant", "criterion": "Are results relevant to the query?"},
    {"id": "specific", "criterion": "Are descriptions specific (not generic filler)?"}
  ],
  "grading_model": "claude-sonnet-4-6",
  "trigger_tests": {
    "should_trigger": [],
    "should_not_trigger": []
  },
  "variance": {
    "n_runs": 3,
    "min_stability": 0.8
  }
}
```

Fill in `test_args` and customize assertions, sections, and grading criteria for your skill. Or skip straight to step 2 — `propose-eval` can bootstrap a full three-layer spec from the SKILL.md plus a capture.

## 2. Capture a real run (recommended before propose-eval)

```bash
# Run the skill and save output to tests/eval/captured/my-skill.txt
clauditor capture my-skill

# Pass initial context if the skill needs it upfront
clauditor capture my-skill -- "San Jose, CA"

# Subscription auth + longer watchdog for research-heavy skills
clauditor capture my-skill --no-api-key --timeout 300
```

The captured output becomes the grounding context for `propose-eval` (auto-discovered from `tests/eval/captured/`) and a replayable fixture for tightening assertions in `validate`.

**Interactive skills:** `capture` runs `claude -p` — strictly single-turn, no stdin. If the skill asks a question mid-run it will hang at that point. Put all decision context in `-- args` (and mirror it in `test_args` in your eval spec) so the skill gets everything upfront.

## 3. Bootstrap a full eval spec with propose-eval

```bash
# Reads SKILL.md + the capture from step 2, writes my-skill.eval.json
clauditor propose-eval .claude/commands/my-skill.md
```

Requires `ANTHROPIC_API_KEY` or an authenticated `claude` CLI.

## 4. Validate against captured output

```bash
# Run skill and validate in one step:
clauditor validate .claude/commands/my-skill.md

# JSON output for CI:
clauditor validate .claude/commands/my-skill.md --json
```

## 5. Use in pytest

```python
def test_my_skill(clauditor_runner, clauditor_asserter):
    result = clauditor_runner.run("my-skill", '"San Jose, CA" --depth quick')
    asserter = clauditor_asserter(result)
    asserter.assert_contains("Results")
    asserter.assert_has_entries(minimum=3)
    asserter.assert_has_urls(minimum=3)

def test_with_eval_spec(clauditor_spec):
    spec = clauditor_spec(".claude/commands/my-skill.md")
    results = spec.evaluate()
    assert results.passed, results.summary()
```
