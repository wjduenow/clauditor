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

## 2. Validate against captured output

```bash
# Run skill and validate in one step:
clauditor validate .claude/commands/my-skill.md

# Or validate against pre-captured output:
clauditor validate .claude/commands/my-skill.md --output captured.txt

# JSON output for CI:
clauditor validate .claude/commands/my-skill.md --json
```

## 3. Use in pytest

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
