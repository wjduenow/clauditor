# clauditor

Automated quality checks for [Agent Skills](https://agentskills.io). Catches when your skill produces the wrong shape, not just the wrong answer — layered evaluation from free deterministic assertions through LLM-graded quality rubrics.

## Install

```bash
pip install clauditor-eval
```

Layer 1 (deterministic assertions) works without an `ANTHROPIC_API_KEY`. Layers 2 & 3 and `propose-eval` require an `ANTHROPIC_API_KEY` or an authenticated `claude` CLI.

## Quick links

- [Quick Start](quick-start.md) — from zero to a passing eval in minutes
- [Three Layers](layers.md) — how L1 / L2 / L3 fit together
- [CLI Reference](cli-reference.md) — every subcommand, flag, and exit code
- [Eval Spec Format](eval-spec-reference.md) — complete `.eval.json` schema
- [Pytest Integration](pytest-plugin.md) — fixtures and options
- [Using /clauditor](skill-usage.md) — the bundled Agent Skill

## Source

[github.com/wjduenow/clauditor](https://github.com/wjduenow/clauditor)
