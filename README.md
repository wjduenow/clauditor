# clauditor

<p align="center">
  <img src="https://raw.githubusercontent.com/wjduenow/clauditor/dev/docs/assets/clauditor-social-preview.png" alt="clauditor" width="600">
</p>

[![CI](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml/badge.svg)](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/clauditor)](https://pypi.org/project/clauditor/)
[![Python versions](https://img.shields.io/pypi/pyversions/clauditor)](https://pypi.org/project/clauditor/)
[![License](https://img.shields.io/github/license/wjduenow/clauditor)](https://github.com/wjduenow/clauditor/blob/dev/LICENSE)
[![codecov](https://codecov.io/gh/wjduenow/clauditor/branch/dev/graph/badge.svg)](https://codecov.io/gh/wjduenow/clauditor)

Auditor for AgentSkills.io skills and Claude Integrations. Catches when your skill produces the wrong shape, not just the wrong answer — layered evaluation from free deterministic assertions through LLM-graded quality rubrics.

<details>
<summary>Contents</summary>

[Install](#install) · [Why clauditor?](#why-clauditor) · [One-minute example](#one-minute-example) · [Installing /clauditor](#installing-the-clauditor-slash-command) · [Using /clauditor](#using-clauditor-in-claude-code) · [Quick Start](#quick-start) · [Three Layers](#three-layers-of-validation) · [Suggest](#llm-assisted-skill-improvement-clauditor-suggest) · [CLI Reference](#cli-reference) · [Pytest Integration](#pytest-integration) · [Eval Spec Format](#eval-spec-format) · [Authentication](#authentication-and-api-keys) · [Reference docs](#reference-docs)

</details>

## Install

```bash
pip install clauditor-eval
```

Layer 1 (deterministic assertions) works without LLM credentials. Layers 2 & 3 and `propose-eval` require either an `ANTHROPIC_API_KEY` or the `claude` CLI transport (with the CLI installed and authenticated).

Source install: `git clone https://github.com/wjduenow/clauditor.git && cd clauditor && uv sync --dev`.

## Why clauditor?

- **Layer 1 — Does your skill hit the expected output shape?** Deterministic assertions — free, instant, CI-friendly.
- **Layer 2 — Does it pull the right fields?** Haiku-graded schema extraction — pennies per run.
- **Layer 3 — Is it actually useful?** Sonnet-graded rubric — dollars per run, release-gating.

## One-minute example

**Greenfield (no SKILL.md yet):**

```bash
clauditor init .claude/commands/my-skill.md       # generate starter eval spec
clauditor validate .claude/commands/my-skill.md   # → "4/4 assertions passed (100%)"
```

**Brownfield (SKILL.md already exists):**

```bash
clauditor propose-eval .claude/skills/my-skill/SKILL.md --dry-run  # preview (no tokens)
clauditor propose-eval .claude/skills/my-skill/SKILL.md            # LLM writes the spec
clauditor validate    .claude/skills/my-skill/SKILL.md             # run it
```

Swap `validate` for `grade` once you've added `grading_criteria` to the spec.

## Installing the /clauditor slash command

From your project root, `uv run clauditor setup` creates a symlink at `.claude/skills/clauditor` pointing at the bundled Claude Code skill; `pip install --upgrade clauditor` then picks up skill updates automatically. Restart Claude Code once if `.claude/skills/` did not exist before.

<details><summary>Flags and details</summary>

- `--unlink` — remove the `/clauditor` symlink. Refuses symlinks not pointing at the installed clauditor package, so it won't touch user-authored skills.
- `--force` — overwrite an existing file or symlink at `.claude/skills/clauditor`.
- `--project-dir PATH` — override project-root detection (default walks up for `.git/` or `.claude/`).

Edits under `.claude/skills/clauditor/` are [hot-reloaded](https://code.claude.com/docs/en/skills#live-change-detection) by Claude Code. `uv run clauditor doctor` reports the symlink's health (absent / installed / stale / wrong-target / unmanaged).

</details>

## Using /clauditor in Claude Code

Invoke the slash command with a skill path — Claude locates the eval spec, runs L1, and asks before spending tokens on L3; if no sibling `<skill>.eval.json` exists, Claude offers `clauditor propose-eval` as an LLM-assisted bootstrap (with a cost-free `--dry-run` preview) before grading. If L3 reports failing criteria, Claude offers `clauditor suggest` to propose a unified diff of SKILL.md edits motivated by the specific failing criterion ids.

```text
/clauditor .claude/commands/my-skill.md
```

Full reference: [docs/skill-usage.md](https://github.com/wjduenow/clauditor/blob/dev/docs/skill-usage.md).

## Quick Start

A new skill goes from "untested" to "covered" in four steps: `clauditor capture` records a real run, `clauditor propose-eval` bootstraps a full three-layer spec from the SKILL.md plus that capture, `clauditor validate` tightens L1 assertions, then the spec wires into pytest for regression coverage.

```bash
clauditor capture my-skill -- "initial context"   # save real output → tests/eval/captured/
clauditor propose-eval .claude/commands/my-skill.md  # LLM writes the spec from SKILL.md + capture
clauditor validate .claude/commands/my-skill.md
clauditor validate .claude/commands/my-skill.md --json  # CI mode
```

**Covered in the full reference:** the `capture` command and interactive-skill limitations, `propose-eval` options, pytest fixtures (`clauditor_runner`, `clauditor_asserter`, `clauditor_spec`). Full reference: [docs/quick-start.md](https://github.com/wjduenow/clauditor/blob/dev/docs/quick-start.md).

## Three Layers of Validation

L1 catches shape regressions for free, L2 uses Haiku to validate structured fields, L3 uses Sonnet to grade against a rubric — all three drive off the same eval spec:

```json
{"assertions": [...], "sections": [...], "grading_criteria": [...]}
```

Full reference: [docs/layers.md](https://github.com/wjduenow/clauditor/blob/dev/docs/layers.md).

## LLM-assisted skill improvement (`clauditor suggest`)

When `clauditor grade` returns failing L3 criteria, `clauditor suggest` reads that iteration's `grading.json`, asks Sonnet to propose minimal SKILL.md edits keyed to the failing criterion ids, and writes a unified diff plus a JSON sidecar with `motivated_by`, `anchor`, `confidence`, and per-edit rationale. Every proposal is hard-validated so its `anchor` appears exactly once in the target SKILL.md before anything lands on disk — no silent drift, no blind patches.

```bash
clauditor grade .claude/skills/my-skill/SKILL.md      # produces grading.json
clauditor suggest .claude/skills/my-skill/SKILL.md    # reads latest grading.json
# → unified diff on stdout; sidecar at .clauditor/suggestions/my-skill-<ts>.{diff,json}
```

Review, `git apply` (or hand-edit), then re-run `clauditor grade` to measure the score delta. The sidecar is stable (`schema_version: 1`) for downstream tooling.

**Covered in the full reference:** traceability via `motivated_by`, the anchor-safety contract, sidecar field-by-field reference, `--from-iteration`, `--with-transcripts`, `--model`, `--json`, and the full worked walkthrough. Full reference: [docs/skill-usage.md#proposing-skill-improvements](https://github.com/wjduenow/clauditor/blob/dev/docs/skill-usage.md#proposing-skill-improvements).

## CLI Reference

Stable exit-code contract (0 = pass, 1 = skill failed, 2 = input error, 3 = Anthropic error). `grade` auto-increments iteration slots under `.clauditor/iteration-N/<skill>/` and appends metrics to `history.jsonl`.

```bash
clauditor init <skill.md>             # Starter eval.json
clauditor propose-eval <skill.md>     # LLM-assisted EvalSpec bootstrap
clauditor lint <skill.md>             # Static agentskills.io spec conformance
clauditor validate <skill.md>         # Layer 1 assertions
clauditor grade <skill.md>            # Layer 3 quality grading
clauditor compare --skill <s> --from 1 --to 2  # Diff iterations
clauditor trend <skill> --metric total.total   # History + sparkline
clauditor badge <skill.md>            # Shields.io endpoint JSON for README embed
```

**Covered in the full reference:** every subcommand flag (`--variance`, `--iteration`, `--diff`, …), exit codes, `history.jsonl` shape, `clauditor trend` metric paths. Full reference: [docs/cli-reference.md](https://github.com/wjduenow/clauditor/blob/dev/docs/cli-reference.md).

## Pytest Integration

```python
def test_my_skill(clauditor_runner, clauditor_asserter):
    result = clauditor_runner.run("my-skill", '"San Jose, CA"')
    clauditor_asserter(result).assert_contains("Results")
```

Full reference: [docs/pytest-plugin.md](https://github.com/wjduenow/clauditor/blob/dev/docs/pytest-plugin.md).

## Eval Spec Format

An `<skill-name>.eval.json` lives next to the skill's `.md` file and drives all three layers: deterministic assertions, LLM schema extraction (sections + tiered fields), and rubric grading. Optional blocks (`input_files`, `output_files`, `variance`, `trigger_tests`) add staging, file-based output capture, variance measurement, and trigger precision.

```json
{
  "skill_name": "find-kid-activities",
  "test_args":  "\"Cupertino, CA\" --ages 4-6",
  "assertions": [{"id": "has_venues", "type": "contains", "needle": "Venues"}],
  "sections":   [{"name": "Venues", "tiers": [{"label": "default", "min_entries": 3, "fields": [{"id": "v_name", "name": "name", "required": true}]}]}],
  "grading_criteria": [{"id": "distance_ok", "criterion": "Are all venues within the specified distance?"}]
}
```

**Covered in the full reference:** the full eval-spec JSON shape, `input_files` staging rules, `output_file` / `output_files` capture, and the `format` validation DSL (`phone_us`, `url`, `domain`, … or inline regex). Full reference: [docs/eval-spec-reference.md](https://github.com/wjduenow/clauditor/blob/dev/docs/eval-spec-reference.md).

<details><summary>Alignment with agentskills.io</summary>

clauditor implements (and extends) the workflow at [agentskills.io/skill-creation/evaluating-skills](https://agentskills.io/skill-creation/evaluating-skills):

| agentskills.io concept | clauditor |
|---|---|
| Test case (prompt + expected + files) | `.eval.json` with `test_args`, `input_files`, `sections`, `grading_criteria` |
| Deterministic assertions | **Layer 1** — `assertions.py`, `FORMAT_REGISTRY` (20 types) |
| LLM-judged structural checks | **Layer 2** — `grader.py`, tiered schema extraction |
| Rubric quality grading | **Layer 3** — `quality_grader.py`, per-criterion scoring + variance |
| Regression + longitudinal history | `clauditor compare`, `.clauditor/history.jsonl`, `clauditor trend --metric <dotted.path>` |
| Per-iteration workspace | `.clauditor/iteration-N/<skill>/` with sidecars + `run-*/` transcripts |

**Beyond the spec**: trigger precision testing, tiered extraction, pytest plugin, `input_files` staging, blind A/B judge, baseline pair runs, transcript capture, LLM-driven skill improvement proposer (`clauditor suggest`), LLM-assisted EvalSpec bootstrap (`clauditor propose-eval`), Pro/Max subscription-auth option (`--no-api-key`) for research-heavy skills that exceed the API-tier rate limit, static spec-conformance check (`clauditor lint`). **Out of scope**: human-in-the-loop feedback capture.

Note: `--no-api-key` only affects the subprocess; the six LLM-mediated commands (`grade`, `propose-eval`, `suggest`, `triggers`, `extract`, `compare --blind`) route their own Anthropic call through a pluggable transport that accepts either `ANTHROPIC_API_KEY` or a `claude` CLI subscription by default. See [Authentication and API Keys](#authentication-and-api-keys).

</details>

## Skill compatibility

clauditor invokes skills through `claude -p` (non-interactive print mode), which is a strict subset of the interactive Claude Code runtime. Most patterns work transparently; a few have caveats today:

- **Works**: sequential `Task` calls (no `run_in_background`), parallel tool calls in the parent (multiple `tool_use` blocks per turn), every standard tool (`WebSearch`, `WebFetch`, `Bash`, `Read`, `Write`, `Edit`).
- **Works with `--sync-tasks` opt-in**: skills using `Task(run_in_background=true)` for parallel sub-agent fanout. The flag sets `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in the subprocess env, forcing background spawns synchronous so clauditor sees the full transcript. Resolves the [#97](https://github.com/wjduenow/clauditor/issues/97) output-truncation case for the parallel-research-fanout shape — without modifying the skill. **Caveat: you are evaluating a different execution model than what ships**, so async-specific logic (race conditions, late-arriving results, progress-while-async branches) goes untested. See the fidelity tradeoff in [`docs/skill-usage.md`](docs/skill-usage.md#skill-compatibility).
- **Loud failure today**: skills whose correctness depends on async semantics (not just latency-sensitive fanout) — true async-fidelity evaluation is blocked on upstream Claude Code gaining headless background-task polling, tracked in [anthropics/claude-code#52917](https://github.com/anthropics/claude-code/issues/52917) and catalogued in [`docs/adr/transport-research-103.md`](docs/adr/transport-research-103.md). `AskUserQuestion` and other interactive prompts also fall in this bucket — clauditor's print-mode transport has no input channel.

Full matrix and refactoring recipes: [`docs/skill-usage.md#skill-compatibility`](docs/skill-usage.md#skill-compatibility).

## Authentication and API Keys

The six LLM-mediated commands (`grade`, `extract`, `propose-eval`, `suggest`, `triggers`, `compare --blind`) work under either `ANTHROPIC_API_KEY` or a `claude` CLI subscription — the default `auto` transport picks CLI when the binary is on PATH, else falls back to the API. Full reference: [docs/transport-architecture.md](docs/transport-architecture.md).

Running `clauditor grade <skill> --transport cli` is the one-liner for subscription auth end-to-end: `--transport cli` implicitly strips `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` from the skill subprocess env, so both the grader and the skill use subscription auth. Pass `--transport api` to keep the keys.

## Reference docs

- [`docs/architecture.md`](docs/architecture.md) — how clauditor works under the hood (mermaid diagrams of the grade flow)
- [`docs/quick-start.md`](docs/quick-start.md) — tutorial walkthrough from init → validate → pytest
- [`docs/layers.md`](docs/layers.md) — the three-layer framework in depth
- [`docs/cli-reference.md`](docs/cli-reference.md) — full subcommand + flag + exit-code reference
- [`docs/eval-spec-reference.md`](docs/eval-spec-reference.md) — complete `.eval.json` schema
- [`docs/pytest-plugin.md`](docs/pytest-plugin.md) — pytest fixtures and options
- [`docs/skill-usage.md`](docs/skill-usage.md) — using `/clauditor` in Claude Code
- [`docs/badges.md`](docs/badges.md) — shields.io badges from iteration sidecars (`clauditor badge`)
- [`docs/stream-json-schema.md`](docs/stream-json-schema.md) — `claude` stream-json parser contract
- [`docs/transport-architecture.md`](docs/transport-architecture.md) — CLI vs SDK transport, auth-state matrix, precedence, migration
- [`CONTRIBUTING.md`](CONTRIBUTING.md#pre-release-dogfood) — maintainer pre-release dogfood gate + contribution workflow

## License

Apache 2.0
