# Rule: Live-runner tests for internal-only skills install via tmp_path symlink

When a bundled skill is **intentionally excluded from `clauditor
setup`** (because it is internal / maintainer-only) and you want a
live-runner test that invokes `SkillRunner` against a real `claude`
CLI, **set up a throwaway `.claude/skills/<name>/` symlink under
`tmp_path` and pass that directory as `project_dir=` to the runner**.
Do NOT install the skill into the real repo just to make the test work
— that breaks the "internal-only" invariant the exclusion was created
to protect.

## The trap

```python
# WRONG — assumes the skill is installed at .claude/skills/<name>/ in the
# real project. For an internal-only skill, it isn't, so the claude CLI
# resolves `/my-internal-skill` to "Unknown command" and the subprocess
# returns exit 0 with an error in the result payload (not a crash).
def test_live_run(self) -> None:
    runner = SkillRunner(project_dir=Path.cwd())
    result = runner.run("my-internal-skill")
    assert result.succeeded  # AssertionError — output is empty
    # result.raw_messages has:
    #   {"type": "result", "result": "Unknown command: /my-internal-skill"}
```

The failure mode is **silent**: `exit_code=0`, `error=None`, empty
`output`. The stream-json `result` message carries the "Unknown
command" string, but the surface-level runner fields don't. You have
to print `raw_messages` to see what went wrong.

## The pattern

```python
def test_live_run(self, tmp_path: Path) -> None:
    skip = _live_run_skip_reason()
    if skip:
        pytest.skip(skip)

    # Build a throwaway project dir. tmp_path auto-cleans, so the
    # symlink cannot leak into the real .claude/skills/ tree.
    project_dir = tmp_path / "project"
    (project_dir / ".claude" / "skills").mkdir(parents=True)
    (project_dir / ".git").mkdir()  # satisfy project-root detection
    skill_root = SKILL_MD.parent  # src/clauditor/skills/<name>/
    (project_dir / ".claude" / "skills" / "<name>").symlink_to(skill_root)

    # Bump the timeout — live LLM runs that do WebFetch or codebase
    # inventory routinely exceed the 180s default.
    runner = SkillRunner(project_dir=project_dir, timeout=360)
    result = runner.run("<name>")
    assert result.succeeded, (
        f"live run failed: exit_code={result.exit_code} "
        f"error={result.error!r} "
        f"output_head={result.output[:500]!r}"
    )
```

Three invariants the setup must preserve:

- **`.git/` dir**: clauditor's project-root detection looks for
  `.git/` or `.claude/` markers. `tmp_path` under the system temp dir
  does not match the home-exclusion hazard (see
  `.claude/rules/project-root-home-exclusion.md`), but the marker is
  still required for the runner's project-root walk to accept the
  tmp dir.
- **Symlink, not copy**: the symlink points at the bundled skill
  dir in-place. A copy would diverge from the source if the test
  edits either side, and would miss the packaging shape that the
  real install uses.
- **`project_dir=tmp_path/project`, not `cwd`**: every test gets
  its own isolated project dir. Never reuse `Path.cwd()` — the
  `claude` CLI inherits the cwd's `.claude/` tree, which
  short-circuits the isolation.

## Why this shape

- **Respects the "internal-only" invariant.** The whole point of
  excluding the skill from `clauditor setup` is to keep it off the
  user's surface (no slash command, no docs, no list helper). If the
  test installs it into the real repo to make the runner happy, the
  invariant is violated every time the test runs. `tmp_path` keeps
  the install scoped to the test.
- **Catches real failures.** The triple-lock gate + fixture replay
  already catch schema regressions. This test's job is to catch
  **behavior drift**: Claude stops producing a "Deltas" section,
  `WebFetch` returns unexpected content, a new turn-limit trips the
  skill mid-run. Those only surface through an actual live
  invocation.
- **Auto-cleanup.** `tmp_path` is pytest-managed; the symlink
  disappears when the test ends. No teardown code to forget.
- **Silent-failure detection.** The runner returns `exit_code=0`
  even when the slash command is unknown (the `claude` CLI exits 0
  with an error payload in the stream-json `result`). The
  `result.succeeded` check + the `output_head` dump in the
  assertion message are how you tell "actually failed" from "looked
  like a pass". Keep both in the assertion so future failures are
  diagnosable without re-running.

## What NOT to do

- Do NOT install the skill permanently in the repo (`.claude/skills/
  <name>/` in the real working copy, or via an extension to
  `clauditor setup`) to make the test easier. That's exactly the
  exclusion this whole pattern is working around.
- Do NOT reuse `Path.cwd()` as `project_dir`. The claude CLI resolves
  `.claude/skills/` relative to the subprocess cwd; sharing cwd
  between the real project and a live test either pollutes the real
  `.claude/` tree (if the symlink is created) or makes the test
  fragile (if it depends on repo state).
- Do NOT forget to bump the timeout. 180s is too tight for a live
  run that does `WebFetch` + non-trivial codebase inspection —
  expect 1-2 minutes on Sonnet.
- Do NOT remove the triple-lock gate to "simplify" the test.
  `CLAUDITOR_RUN_LIVE=1` + `ANTHROPIC_API_KEY` + `claude` on PATH
  is what keeps default CI from spending tokens silently.
- Do NOT skip the `assert result.succeeded` and jump straight to
  assertion checks — an empty `result.output` passes `not_contains`
  assertions trivially and will green-light a broken live path.

## Canonical implementation

`tests/test_bundled_review_skill.py::TestLiveSkillRun::test_live_run_passes_l1_assertions`
— first and currently only anchor. Triple-lock gate via
`_live_run_skip_reason()`, `tmp_path` symlink setup, 360s timeout,
`result.succeeded` precondition, then `run_assertions` against the
live output.

The pattern was validated end-to-end on 2026-04-20 — live run of the
`/review-agentskills-spec` skill took 126.57s and passed all 5
declared L1 assertions. The first attempt (without the tmp-symlink
setup) failed silently with `"Unknown command:
/review-agentskills-spec"` in the stream-json result, which is the
failure mode this rule exists to prevent.

## Companion rules

- `.claude/rules/project-root-home-exclusion.md` — the reason the
  `.git/` marker is load-bearing (clauditor's marker walk).
- `.claude/rules/subprocess-cwd.md` — the runner's `cwd`
  override pattern; this rule is the test-side consumer of that
  contract.
- Memory: `feedback_review_agentskills_spec_internal.md` — the
  exclusion contract this rule preserves.

## When this rule applies

Any future bundled skill that is filtered out of `clauditor setup`
(per a maintainer-only decision) AND has a live-runner test that
invokes `SkillRunner.run(skill_name)` against the real `claude` CLI.
The symlink + `tmp_path` project dir is the cheapest way to install
the skill for the scope of one test without polluting the real
`.claude/skills/` tree.

## When this rule does NOT apply

- User-facing bundled skills that `clauditor setup` installs
  normally. A test against those can either (a) assume the user has
  run `setup` and use `project_dir=Path.cwd()`, or (b) call the
  setup plumbing against `tmp_path` to produce the same install
  shape.
- Replay / fixture tests that feed a canned output string through
  `run_assertions` — those never invoke `SkillRunner` and have no
  slash-command resolution concern.
- Unit tests that mock `SkillRunner` entirely. The mock substitutes
  for the claude CLI; no real install is needed.
- One-off diagnostic scripts in `scripts/` that invoke the skill
  ad-hoc. Those run interactively; the user is expected to set up
  their own environment.
