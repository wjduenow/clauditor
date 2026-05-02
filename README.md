# clauditor

<p align="center">
  <img src="https://raw.githubusercontent.com/wjduenow/clauditor/dev/docs/assets/clauditor-social-preview.png" alt="clauditor" width="600">
</p>

[![CI](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml/badge.svg)](https://github.com/wjduenow/clauditor/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/clauditor)](https://pypi.org/project/clauditor/)
[![Python versions](https://img.shields.io/pypi/pyversions/clauditor)](https://pypi.org/project/clauditor/)
[![License](https://img.shields.io/github/license/wjduenow/clauditor)](https://github.com/wjduenow/clauditor/blob/dev/LICENSE)
[![codecov](https://codecov.io/gh/wjduenow/clauditor/branch/dev/graph/badge.svg)](https://codecov.io/gh/wjduenow/clauditor)

Automated quality checks for [Agent Skills](https://agentskills.io). A skill is a reusable instruction file (`SKILL.md`) that tells Claude how to do a task. clauditor answers three questions about every run: **Did it run?** **Did it return the right structure?** **Was the answer actually good?** First two checks cost pennies and run in CI; the third is for release gates.

<details markdown="1">
<summary>Contents</summary>

[Why clauditor?](#why-clauditor) · [Install](#install) · [One-minute example](#one-minute-example) · [Installing /clauditor](#installing-the-clauditor-slash-command) · [Using /clauditor](#using-clauditor-in-claude-code) · [Quick Start](#quick-start) · [Three Layers](#three-layers-of-validation) · [Suggest](#llm-assisted-skill-improvement-clauditor-suggest) · [CLI Reference](#cli-reference) · [Pytest Integration](#pytest-integration) · [Eval Spec Format](#eval-spec-format) · [Skill compatibility](#skill-compatibility) · [Authentication](#authentication-and-api-keys) · [Reference docs](#reference-docs)

</details>

## Why clauditor?

Three checks, cheap to expensive:

- **Layer 1 — Did your skill produce the right structure?** A free, instant check that runs in CI. Catches: *"the output is missing the Venues section,"* *"no phone numbers were extracted,"* *"the URL is malformed."* No LLM calls; pure regex and string matching against your assertions.
- **Layer 2 — Did it pull the right fields?** A small LLM (Anthropic's Haiku model, ~pennies per run) reads the skill's output and validates it against a schema you write. Catches: *"the venue's address field is empty,"* *"tier-1 entries are missing a website URL."*
- **Layer 3 — Was the answer actually useful?** A stronger LLM (Anthropic's Sonnet model, ~dollars per run) grades the output against your rubric. Run on release, not every commit. Catches: *"the venues are too far from the requested area,"* *"the recommendations don't match the kid's age range."*

The same `eval.json` file drives all three layers. You write it once; clauditor uses it for static checks, structured-field grading, and rubric scoring.

## Install

```bash
pip install clauditor-eval
clauditor --version
```

Layer 1 works without any LLM credentials. Layers 2 & 3 and `propose-eval` need either an Anthropic API key (`ANTHROPIC_API_KEY`) or the `claude` CLI installed and signed in to a Claude Pro/Max subscription — see [Authentication](#authentication-and-api-keys).

<details markdown="1">
<summary>Installing from source (for contributors)</summary>

```bash
git clone https://github.com/wjduenow/clauditor.git
cd clauditor
uv sync --dev
```

[uv](https://docs.astral.sh/uv/) is the project's package manager; it's a faster drop-in for `pip` + `venv`.

</details>

## One-minute example

Both paths below assume you already have a `SKILL.md` — clauditor checks skills, it doesn't write them. A skill lives in `.claude/skills/<name>/SKILL.md` (modern layout) or `.claude/commands/<name>.md` (older layout); run `ls .claude/` from your project root if you're not sure which you have.

**I want a minimal eval spec I'll fill in myself.** `clauditor init` reads your SKILL.md and writes a bare-bones `eval.json` next to it — no LLM call, no tokens spent. You add assertions and grading criteria from there.

```bash
clauditor init .claude/skills/my-skill/SKILL.md       # generate a starter eval spec
clauditor validate .claude/skills/my-skill/SKILL.md   # run Layer 1 against the skill
```

Expected output:

```text
✓ Running /my-skill...
4/4 assertions passed (100%)
```

**I want clauditor to write a richer eval spec for me.** `clauditor propose-eval` sends your SKILL.md (and optionally a captured real-world run) to an LLM and gets back a populated `eval.json` with assertions, sections, and grading criteria already drafted.

```bash
clauditor propose-eval .claude/skills/my-skill/SKILL.md --dry-run  # preview the prompt — no tokens spent
clauditor propose-eval .claude/skills/my-skill/SKILL.md            # LLM writes the eval spec
clauditor validate    .claude/skills/my-skill/SKILL.md             # run Layer 1 against it
```

`--dry-run` prints the prompt clauditor would send to the LLM without making any API call — a cost-free preview so you can iterate on inputs before spending tokens.

Swap `validate` for `grade` once you've added `grading_criteria` to the spec to run Layer 3.

## Installing the /clauditor slash command

If you use [Claude Code](https://claude.com/claude-code) interactively, you can type `/clauditor <skill>` in the prompt instead of running CLI commands — Claude reads the eval spec, runs the checks, and shows you results in-line. This is optional; the CLI works without it.

From your project root, `uv run clauditor setup` creates a symlink at `.claude/skills/clauditor` pointing at the bundled Claude Code skill; `pip install --upgrade clauditor` then picks up skill updates automatically. Restart Claude Code once if `.claude/skills/` did not exist before.

<details markdown="1"><summary>Flags and details</summary>

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

An `<skill-name>.eval.json` lives next to the skill's `.md` file and drives all three layers. In plain English, it lists: **what input to test the skill with**, **structural rules the output must satisfy** (assertions), **fields the output should contain** (sections + tiers, used by Layer 2), and **rubric questions for the LLM judge** (grading criteria, used by Layer 3).

```json
{
  "skill_name": "find-kid-activities",
  "test_args": "\"Cupertino, CA\" --ages 4-6",

  "assertions": [
    {"id": "has_venues", "type": "contains", "needle": "Venues"}
  ],

  "sections": [
    {
      "name": "Venues",
      "tiers": [
        {
          "label": "default",
          "min_entries": 3,
          "fields": [
            {"id": "v_name", "name": "name", "required": true}
          ]
        }
      ]
    }
  ],

  "grading_criteria": [
    {"id": "distance_ok", "criterion": "Are all venues within the specified distance?"}
  ]
}
```

In this example: `test_args` is the prompt clauditor passes to the skill. The single L1 assertion checks the output literally contains the word "Venues". The `sections` block tells Layer 2 to find a "Venues" section with at least 3 entries, each with a `name` field. The `grading_criteria` block gives Layer 3 a yes/no question to grade the output on.

Optional blocks (`input_files`, `output_files`, `variance`, `trigger_tests`) add staging, file-based output capture, variance measurement, and trigger precision.

**Covered in the full reference:** the full eval-spec JSON shape, `input_files` staging rules, `output_file` / `output_files` capture, and the `format` validation DSL (`phone_us`, `url`, `domain`, … or inline regex). Full reference: [docs/eval-spec-reference.md](https://github.com/wjduenow/clauditor/blob/dev/docs/eval-spec-reference.md).

<details markdown="1"><summary>Alignment with agentskills.io</summary>

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

clauditor works for most skills out of the box. A few patterns need a workaround or aren't supported yet:

- **Skills with parallel sub-tasks** (the `Task(run_in_background=true)` pattern): pass `--sync-tasks` to force them to run sequentially. Output capture works correctly, but the *async behavior itself* (race conditions, late-arriving results) is not tested — you're evaluating a slightly different execution model than what ships.
- **Skills that ask the user mid-run** (e.g. `AskUserQuestion` to clarify intent): not supported directly — clauditor runs skills non-interactively, so the question never gets an answer and the run hangs. The fix is usually to take all parameters in the initial prompt; see the worked before/after example and the `not_contains AskUserQuestion` regression assertion in [`docs/skill-usage.md#recipe-skills-that-ask-the-user-mid-run`](docs/skill-usage.md#recipe-skills-that-ask-the-user-mid-run), with [`examples/.claude/skills/find-kid-activities/SKILL.eval.json`](examples/.claude/skills/find-kid-activities/SKILL.eval.json) as the canonical anchor.
- **Skills whose correctness depends on async timing**: cannot be tested accurately yet. Blocked on an upstream Claude Code feature.

<details markdown="1">
<summary>Technical detail and upstream tracking</summary>

clauditor invokes skills through `claude -p` (non-interactive print mode), which is a strict subset of the interactive Claude Code runtime. **Works**: sequential `Task` calls, parallel tool calls in the parent turn, every standard tool (`WebSearch`, `WebFetch`, `Bash`, `Read`, `Write`, `Edit`). **Works with `--sync-tasks`**: skills using `Task(run_in_background=true)` — the flag sets `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in the subprocess env, resolving the [#97](https://github.com/wjduenow/clauditor/issues/97) output-truncation case. **Loud failure today**: skills whose correctness depends on true async semantics — blocked on upstream Claude Code gaining headless background-task polling, tracked in [anthropics/claude-code#52917](https://github.com/anthropics/claude-code/issues/52917) and catalogued in [`docs/adr/transport-research-103.md`](docs/adr/transport-research-103.md).

Full matrix and refactoring recipes: [`docs/skill-usage.md#skill-compatibility`](docs/skill-usage.md#skill-compatibility).

</details>

## Authentication and API Keys

**Do I need an Anthropic API key?**

- **Just running Layer 1 checks** (`validate`, `lint`, `init`, `capture`) → **no key needed**. These are deterministic and never call an LLM.
- **You have a Claude Pro/Max subscription and the `claude` CLI installed** → **no API key needed**. clauditor's default `auto` transport detects the CLI on PATH and uses your subscription auth for grading. Pass `--transport cli` to be explicit.
- **Otherwise** → set `ANTHROPIC_API_KEY` (get one at [console.anthropic.com](https://console.anthropic.com/)) before running `grade`, `extract`, `propose-eval`, `suggest`, `triggers`, or `compare --blind`.

The six LLM-mediated commands above route their Anthropic call through a pluggable transport — either the HTTP SDK (`--transport api`) or a subprocess to the local `claude` CLI (`--transport cli`). The default `auto` setting picks CLI when available, else API. Full reference: [docs/transport-architecture.md](docs/transport-architecture.md).

clauditor also supports multi-provider grading: pass `--grading-provider {anthropic,openai,auto}` (or set `CLAUDITOR_GRADING_PROVIDER` / `EvalSpec.grading_provider`) to route the LLM-grader call through the OpenAI SDK with `OPENAI_API_KEY` instead. Under the default `auto`, clauditor infers the provider from the `grading_model` prefix (`claude-*` → anthropic, `gpt-*` / `o[0-9]+*` → openai).

Running `clauditor grade <skill> --transport cli` is the one-liner for subscription auth end-to-end: it implicitly strips `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` from the skill subprocess env, so both the grader and the skill use subscription auth. Pass `--transport api` to keep the keys.

## Reference docs

- [`docs/architecture.md`](docs/architecture.md) — how clauditor works under the hood (mermaid diagrams of the grade flow)
- [`docs/quick-start.md`](docs/quick-start.md) — tutorial walkthrough from init → validate → pytest
- [`docs/layers.md`](docs/layers.md) — the three-layer framework in depth
- [`docs/cli-reference.md`](docs/cli-reference.md) — full subcommand + flag + exit-code reference
- [`docs/eval-spec-reference.md`](docs/eval-spec-reference.md) — complete `.eval.json` schema
- [`docs/pytest-plugin.md`](docs/pytest-plugin.md) — pytest fixtures and options
- [`docs/skill-usage.md`](docs/skill-usage.md) — using `/clauditor` in Claude Code
- [`docs/skills.md`](docs/skills.md) — catalog of skills shipped with this repo, with live badge status
- [`docs/badges.md`](docs/badges.md) — shields.io badges from iteration sidecars (`clauditor badge`)
- [`docs/stream-json-schema.md`](docs/stream-json-schema.md) — `claude` stream-json parser contract
- [`docs/transport-architecture.md`](docs/transport-architecture.md) — CLI vs SDK transport, auth-state matrix, precedence, migration
- [`CONTRIBUTING.md`](CONTRIBUTING.md#pre-release-dogfood) — maintainer pre-release dogfood gate + contribution workflow

## License

Apache 2.0
