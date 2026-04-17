---
name: clauditor
description: Run the clauditor capture/validate/grade workflow against a Claude Code skill. Use when evaluating a skill's output against an eval spec, when the user asks to validate or grade a skill, or when auditing a skill's stability across runs.
compatibility: Requires clauditor installed via pip/uv (provides the 'clauditor' CLI and the pytest plugin).
metadata:
  clauditor-version: "0.0.0-dev"
argument-hint: "[skill-path]"
disable-model-invocation: true
allowed-tools: Bash(uv run clauditor *)
---

# /clauditor — Validate and grade a Claude Code skill

You help the user evaluate a Claude Code skill using clauditor's three-layer
framework. Keep responses terse and concrete; prefer running the CLI over
explaining what it would do.

## Three-layer model

- **Layer 1 — assertions:** deterministic regex/string/count checks against
  the skill's output. Fast and free; runs in milliseconds and costs zero
  API tokens.
- **Layer 2 — extraction:** LLM-graded schema extraction (Haiku) that
  pulls structured fields from free-form output. Optional — only runs when
  the eval spec declares `sections`.
- **Layer 3 — grading:** LLM-graded rubric scoring (Sonnet) against
  user-authored `grading_criteria`. Costs tokens; run after L1 passes.

## Workflow

1. **Identify the skill file.** If `$ARGUMENTS` is non-empty, treat it as
   the path to a `SKILL.md` (or a legacy `.claude/commands/<name>.md`).
   Otherwise ask the user which skill to evaluate.

2. **Locate the eval spec.** Look for a sibling file named
   `<skill-name>.eval.json` next to the skill. For skill-directory layouts,
   check `<skill-dir>/assets/<skill-name>.eval.json` as well. If neither
   exists, ask the user whether to point at an explicit `--eval` path or
   stop.

3. **Run L1 validation first.** It is fast and free:

   ```bash
   uv run clauditor validate <skill-path>
   ```

   This runs the skill once, checks every `assertions[]` entry, and writes
   `assertions.json` into `.clauditor/iteration-N/<skill-name>/`. If any
   assertion fails, report the failing ids and stop — grading a broken
   skill wastes tokens.

4. **If L1 passes, offer L3 grading.** Ask the user to confirm (this
   costs Sonnet tokens):

   ```bash
   uv run clauditor grade <skill-path>
   ```

   This runs the skill, evaluates every `grading_criteria[]` entry against
   the rubric, and writes `grading.json` alongside the assertions sidecar.
   Report the overall `pass_rate`, any failing criterion ids, and the
   path to the sidecar for follow-up.

5. **Report concisely.** Surface:
   - Which layers ran (L1 / L2 / L3)
   - Pass/fail counts per layer
   - Sidecar paths the user can open to inspect full results
   - One-line next step (re-run, inspect transcript, tighten rubric)

## Common errors

- **`no eval spec found`** — the skill has no sibling `.eval.json`. Ask
  the user to author one or point `--eval` at an existing spec.
- **`duplicate id` / `missing id`** — every assertion, field, and
  criterion needs a unique string `id`. Edit the spec and re-run.
- **`no project root found`** — `clauditor` expects to run inside a
  project with `.git/` or `.claude/`. Use `--project-dir` or `cd` first.
