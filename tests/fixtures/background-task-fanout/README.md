# background-task-fanout fixture

TEST-ONLY known-bad fixture skill for `tests/test_background_task_fixture.py`.

## What it is

`SKILL.md` is a **deliberate anti-pattern**: a skill that launches three
`Task(run_in_background=true)` sub-agents and then summarizes and exits
**without polling** them. This reproduces the GitHub #97 failure mode that
clauditor's background-task non-completion heuristic
(`_detect_background_task_noncompletion` in
`src/clauditor/_harnesses/_claude_code.py`) is meant to catch.

## Why it exists

The live test asserts that running this skill under the real `claude` CLI
produces the `background-task:` warning **end-to-end** — i.e. the detector
fires on a genuine `Task(run_in_background=true)` fan-out, not just on a
synthetic stream-event fixture. Without a real known-bad skill, the only
coverage of the detector is the unit tests that feed it canned dicts.

## NOT user-facing

This skill is test-only. It must NEVER be:

- packaged into `src/clauditor/skills/`,
- installed by `clauditor setup`,
- listed in user-facing docs or any "list skills" surface.

It lives under `tests/fixtures/` precisely so it stays out of the package
and the setup surface.

## How the live test uses it

`tests/test_background_task_fixture.py::TestLiveSkillRun` follows
`.claude/rules/internal-skill-live-test-tmp-symlink.md`:

- triple-lock gate (`CLAUDITOR_RUN_LIVE=1` + `claude` on PATH +
  `ANTHROPIC_API_KEY`),
- a throwaway `tmp_path/project/.claude/skills/background-task-fanout`
  **symlink** to this directory (never a copy),
- a `.git` marker so project-root detection accepts the tmp dir,
- `project_dir=tmp_path/project` (never `cwd`),
- a 360s timeout,
- runs WITHOUT `--sync-tasks` / `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`
  (DEC-004) so the detector is not suppressed.

The test asserts the **warning**, not success (DEC-003): the skill is
*expected* to truncate.
