# Using /clauditor in Claude Code

Walkthrough of the bundled `/clauditor` slash command: what it does when invoked inside Claude Code, how it differs from the CLI entry points, and when to reach for each. Read this when you want conversational evaluation inside a Claude session; reach for the CLI reference when you're scripting CI.

> Returning from the [root README](../README.md). This doc is the full reference; the README has a summary with code examples.

Once `clauditor setup` has installed the symlink, the bundled skill is
available as a slash command in any Claude Code session rooted at this
project. The command is manual-only — Claude won't auto-invoke it,
because validating a skill has side effects (subprocess runs, sidecar
writes, potential token spend on L3 grading).

**Invoke with the path to the skill you want to evaluate:**

```text
/clauditor .claude/commands/my-skill.md
```

or, for a directory-layout skill:

```text
/clauditor .claude/skills/my-skill/SKILL.md
```

Running `/clauditor` without an argument prompts Claude to ask which
skill to evaluate.

**What Claude does:**

1. Locates the skill's eval spec — the sibling `.eval.json` auto-discovered
   by `skill_path.with_suffix('.eval.json')` (e.g. `SKILL.md` →
   `SKILL.eval.json`). Other locations (for example
   `<skill-dir>/assets/<skill-name>.eval.json`) require passing `--eval <path>`
   explicitly.
2. If no spec exists, bootstraps one via `clauditor propose-eval` —
   an LLM-assisted bootstrap that writes `<skill_stem>.eval.json` next to
   the file you pass. Claude starts with `--dry-run` for a cost-free
   preview of the proposer prompt, reviews it with you, then drops the
   flag to write the spec. See
   [`docs/cli-reference.md#propose-eval`](cli-reference.md#propose-eval)
   for the full flag reference.
3. Runs L1 validation first (`clauditor validate`) — free, sub-second,
   reports failing assertion ids.
4. If L1 passes, asks before running L3 grading (`clauditor grade`) —
   costs Sonnet tokens, writes a full `grading.json` sidecar.
5. Summarizes: which layers ran, pass/fail counts, sidecar paths you
   can open for details.

**When to use `/clauditor` vs. the CLI directly:**

- Use `/clauditor` when you're in a Claude Code conversation and want
  conversational context (Claude can explain failures, suggest fixes,
  iterate on the spec).
- Use `clauditor validate` / `clauditor grade` directly in CI,
  Makefiles, or scripted workflows where you want deterministic exit
  codes and no LLM narration.

The full skill playbook lives at
[`src/clauditor/skills/clauditor/SKILL.md`](../src/clauditor/skills/clauditor/SKILL.md)
(what Claude reads when the slash command fires).
