# clauditor

<p align="center">
  <img src="docs/assets/clauditor-social-preview.png" alt="clauditor" width="600">
</p>

[![CI](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml/badge.svg)](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/clauditor)](https://pypi.org/project/clauditor/)
[![Python versions](https://img.shields.io/pypi/pyversions/clauditor)](https://pypi.org/project/clauditor/)
[![License](https://img.shields.io/github/license/wjduenow/clauditor)](https://github.com/wjduenow/clauditor/blob/dev/LICENSE)
[![codecov](https://codecov.io/gh/wjduenow/clauditor/branch/dev/graph/badge.svg)](https://codecov.io/gh/wjduenow/clauditor)

Auditor for AgentSkills.io skills and Claude Integrations. Catches when your skill produces the wrong shape, not just the wrong answer — layered evaluation from free deterministic assertions through LLM-graded quality rubrics.

<details>
<summary>Contents</summary>

[Install](#install) · [Why clauditor?](#why-clauditor) · [One-minute example](#one-minute-example) · [Installing /clauditor](#installing-the-clauditor-slash-command) · [Using /clauditor](#using-clauditor-in-claude-code) · [Quick Start](#quick-start) · [Three Layers](#three-layers-of-validation) · [CLI Reference](#cli-reference) · [Pytest Integration](#pytest-integration) · [Eval Spec Format](#eval-spec-format) · [Reference docs](#reference-docs)

</details>

## Install

```bash
pip install clauditor           # CLI only (Layer 1)
pip install clauditor[grader]   # + LLM grading (Layers 2 & 3)
```

Source install: `git clone https://github.com/wjduenow/clauditor.git && cd clauditor && uv sync --dev`.

## Why clauditor?

- **Layer 1 — Does your skill hit the expected output shape?** Deterministic assertions — free, instant, CI-friendly.
- **Layer 2 — Does it pull the right fields?** Haiku-graded schema extraction — pennies per run.
- **Layer 3 — Is it actually useful?** Sonnet-graded rubric — dollars per run, release-gating.

## One-minute example

```bash
clauditor init .claude/commands/my-skill.md       # generate starter eval spec
clauditor validate .claude/commands/my-skill.md   # → "4/4 assertions passed (100%)"
```

Swap `validate` for `grade` once you've added `grading_criteria` to the spec.

## Installing the /clauditor slash command

From your project root, `uv run clauditor setup` creates a symlink at `.claude/skills/clauditor` pointing at the bundled Claude Code skill; `pip install --upgrade clauditor` then picks up skill updates automatically. Restart Claude Code once if `.claude/skills/` did not exist before.

<details>
<summary>Flags and details</summary>

- `--unlink` — remove the `/clauditor` symlink. Refuses symlinks not pointing at the installed clauditor package, so it won't touch user-authored skills.
- `--force` — overwrite an existing file or symlink at `.claude/skills/clauditor`.
- `--project-dir PATH` — override project-root detection (default walks up for `.git/` or `.claude/`).

Edits under `.claude/skills/clauditor/` are [hot-reloaded](https://code.claude.com/docs/en/skills#live-change-detection) by Claude Code. `uv run clauditor doctor` reports the symlink's health (absent / installed / stale / wrong-target / unmanaged).

</details>

## Using /clauditor in Claude Code

Invoke the slash command with a skill path — Claude locates the eval spec, runs L1, and asks before spending tokens on L3:

```text
/clauditor .claude/commands/my-skill.md
```

Full reference: [docs/skill-usage.md](docs/skill-usage.md).

## Quick Start

A new skill goes from "untested" to "covered" in three steps: `clauditor init` generates an eval spec, `clauditor validate` tightens L1 assertions against a real capture, then the same spec wires into pytest for regression coverage.

```bash
clauditor init .claude/commands/my-skill.md
clauditor validate .claude/commands/my-skill.md
clauditor validate .claude/commands/my-skill.md --json  # CI mode
```

**Covered in the full reference:** authoring `.eval.json`, captured-output mode (`--output captured.txt`), pytest fixtures (`clauditor_runner`, `clauditor_asserter`, `clauditor_spec`). Full reference: [docs/quick-start.md](docs/quick-start.md).

## Three Layers of Validation

L1 catches shape regressions for free, L2 uses Haiku to validate structured fields, L3 uses Sonnet to grade against a rubric — all three drive off the same eval spec:

```json
{"assertions": [...], "sections": [...], "grading_criteria": [...]}
```

Full reference: [docs/layers.md](docs/layers.md).

## CLI Reference

Stable exit-code contract (0 = pass, 1 = skill failed, 2 = input error, 3 = Anthropic error). `grade` auto-increments iteration slots under `.clauditor/iteration-N/<skill>/` and appends metrics to `history.jsonl`.

```bash
clauditor init <skill.md>             # Generate starter eval.json
clauditor validate <skill.md>         # Layer 1 assertions
clauditor grade <skill.md>            # Layer 3 quality grading
clauditor compare --skill <s> --from 1 --to 2  # Diff iterations
clauditor trend <skill> --metric total.total   # History + sparkline
```

**Covered in the full reference:** every subcommand flag (`--variance`, `--iteration`, `--diff`, …), exit codes, `history.jsonl` shape, `clauditor trend` metric paths. Full reference: [docs/cli-reference.md](docs/cli-reference.md).

## Pytest Integration

```python
def test_my_skill(clauditor_runner, clauditor_asserter):
    result = clauditor_runner.run("my-skill", '"San Jose, CA"')
    clauditor_asserter(result).assert_contains("Results")
```

Full reference: [docs/pytest-plugin.md](docs/pytest-plugin.md).

## Eval Spec Format

An `<skill-name>.eval.json` lives next to the skill's `.md` file and drives all three layers: deterministic assertions, LLM schema extraction (sections + tiered fields), and rubric grading. Optional blocks (`input_files`, `output_files`, `variance`, `trigger_tests`) add staging, file-based output capture, variance measurement, and trigger precision.

```json
{
  "skill_name": "find-kid-activities",
  "test_args":  "\"Cupertino, CA\" --ages 4-6",
  "assertions": [{"id": "has_venues", "type": "contains", "value": "Venues"}],
  "sections":   [{"name": "Venues", "tiers": [{"label": "default", "min_entries": 3, "fields": [{"id": "v_name", "name": "name", "required": true}]}]}],
  "grading_criteria": [{"id": "distance_ok", "criterion": "Are all venues within the specified distance?"}]
}
```

**Covered in the full reference:** the full eval-spec JSON shape, `input_files` staging rules, `output_file` / `output_files` capture, and the `format` validation DSL (`phone_us`, `url`, `domain`, … or inline regex). Full reference: [docs/eval-spec-reference.md](docs/eval-spec-reference.md).

<details>
<summary>Alignment with agentskills.io</summary>

clauditor implements (and extends) the workflow at [agentskills.io/skill-creation/evaluating-skills](https://agentskills.io/skill-creation/evaluating-skills):

| agentskills.io concept | clauditor |
|---|---|
| Test case (prompt + expected + files) | `.eval.json` with `test_args`, `input_files`, `sections`, `grading_criteria` |
| Deterministic assertions | **Layer 1** — `assertions.py`, `FORMAT_REGISTRY` (20 types) |
| LLM-judged structural checks | **Layer 2** — `grader.py`, tiered schema extraction |
| Rubric quality grading | **Layer 3** — `quality_grader.py`, per-criterion scoring + variance |
| Regression + longitudinal history | `clauditor compare`, `.clauditor/history.jsonl`, `clauditor trend --metric <dotted.path>` |
| Per-iteration workspace | `.clauditor/iteration-N/<skill>/` with sidecars + `run-*/` transcripts |

**Beyond the spec**: trigger precision testing, tiered extraction, pytest plugin, `input_files` staging, blind A/B judge, baseline pair runs, transcript capture, LLM-driven skill improvement proposer (`clauditor suggest`). **Out of scope**: human-in-the-loop feedback capture.

</details>

## Reference docs

- [`docs/architecture.md`](docs/architecture.md) — how clauditor works under the hood (mermaid diagrams of the grade flow)
- [`docs/quick-start.md`](docs/quick-start.md) — tutorial walkthrough from init → validate → pytest
- [`docs/layers.md`](docs/layers.md) — the three-layer framework in depth
- [`docs/cli-reference.md`](docs/cli-reference.md) — full subcommand + flag + exit-code reference
- [`docs/eval-spec-reference.md`](docs/eval-spec-reference.md) — complete `.eval.json` schema
- [`docs/pytest-plugin.md`](docs/pytest-plugin.md) — pytest fixtures and options
- [`docs/skill-usage.md`](docs/skill-usage.md) — using `/clauditor` in Claude Code
- [`docs/stream-json-schema.md`](docs/stream-json-schema.md) — `claude` stream-json parser contract
- [`CONTRIBUTING.md`](CONTRIBUTING.md#pre-release-dogfood) — maintainer pre-release dogfood gate + contribution workflow

## License

Apache 2.0
