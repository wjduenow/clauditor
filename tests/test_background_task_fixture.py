"""Live-gated end-to-end test for the #97 background-task warning.

A single canary test that runs a deliberately-bad fixture skill
(``tests/fixtures/background-task-fanout/SKILL.md``) under the real
``claude`` CLI and asserts the background-task non-completion heuristic
(``_detect_background_task_noncompletion``, GitHub #97) fires
end-to-end.

The fixture skill launches three ``Task(run_in_background=true)``
sub-agents and exits without polling them — exactly the failure mode the
detector is meant to catch. ``claude -p`` does not poll background tasks,
so the run is *expected* to truncate. Per DEC-003 we therefore assert the
WARNING, not ``result.succeeded``:

- ``result.error_category == "background-task"``,
- a ``"background-task:"``-prefixed entry in ``result.warnings``,
- ``result.succeeded_cleanly is False``,
- ``result.stream_events`` is non-empty (silent-failure guard against the
  "Unknown command / empty output" misconfiguration this rule's symlink
  setup exists to prevent).

Per DEC-004 the run uses no ``--sync-tasks`` /
``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`` override — that env var would
suppress the detector.

Gated triple-lock per
``.claude/rules/internal-skill-live-test-tmp-symlink.md``:
``CLAUDITOR_RUN_LIVE=1`` + ``claude`` CLI on PATH + ``ANTHROPIC_API_KEY``.
The fixture is TEST-ONLY and never packaged into ``src/clauditor/skills/``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from clauditor.runner import _BACKGROUND_TASK_WARNING_PREFIX, SkillRunner
from clauditor.spec import SkillSpec

SKILL_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "background-task-fanout"
)
SKILL_MD = SKILL_DIR / "SKILL.md"


def _live_run_skip_reason() -> str | None:
    """Return a skip reason, or ``None`` if the live run may proceed.

    Three gates, all required (mirrors
    ``tests/test_bundled_review_skill.py``):
    - ``CLAUDITOR_RUN_LIVE=1`` (explicit opt-in, never implicit).
    - ``claude`` CLI on ``PATH``.
    - ``ANTHROPIC_API_KEY`` set in the environment.
    """
    if os.environ.get("CLAUDITOR_RUN_LIVE") != "1":
        return "live skill run is opt-in; set CLAUDITOR_RUN_LIVE=1 to enable"
    if shutil.which("claude") is None:
        return "live run requires the 'claude' CLI on PATH"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "live run requires ANTHROPIC_API_KEY"
    return None


@pytest.mark.live
class TestLiveSkillRun:
    """Canary: a real ``Task(run_in_background=true)`` skill must warn.

    Gated triple-lock so this class never runs in default CI and never
    spends tokens implicitly. Spends Sonnet tokens when it does run; the
    fixture's sub-agent tasks are trivial and bounded to keep spend low.
    """

    def test_live_run_emits_background_task_warning(
        self, tmp_path: Path
    ) -> None:
        skip = _live_run_skip_reason()
        if skip:
            pytest.skip(skip)

        # The fixture is test-only: it lives under ``tests/fixtures/`` and
        # is never installed by ``clauditor setup``. Build a throwaway
        # project dir with a one-off symlink to the fixture skill so the
        # claude CLI can resolve ``/background-task-fanout`` without the
        # skill ever becoming user-facing. Per
        # ``.claude/rules/internal-skill-live-test-tmp-symlink.md``:
        # symlink (not copy), ``.git`` marker for project-root detection,
        # ``project_dir=tmp_path/project`` (never cwd), 360s timeout.
        project_dir = tmp_path / "project"
        (project_dir / ".claude" / "skills").mkdir(parents=True)
        (project_dir / ".git").mkdir()  # satisfy project-root detection
        (
            project_dir / ".claude" / "skills" / "background-task-fanout"
        ).symlink_to(SKILL_DIR)

        spec = SkillSpec.from_file(SKILL_MD)
        # Do NOT pass any sync-tasks / CLAUDE_CODE_DISABLE_BACKGROUND_TASKS
        # override (DEC-004) — that would suppress the detector. The run is
        # expected to truncate; a generous timeout covers launch latency.
        runner = SkillRunner(project_dir=project_dir, timeout=360)
        result = runner.run(spec.skill_name)

        diag = (
            f"\nerror_category={result.error_category!r}"
            f"\nwarnings={result.warnings!r}"
            f"\nsucceeded_cleanly={result.succeeded_cleanly}"
            f"\nexit_code={result.exit_code}"
            f"\nstream_events_len={len(result.stream_events)}"
            f"\noutput_head={result.output[:500]!r}"
        )

        # Silent-failure guard: a missing-symlink / "Unknown command"
        # misconfiguration produces empty output with no stream events,
        # which would trivially "pass" weaker assertions. Require the
        # claude CLI to have actually streamed something.
        assert result.stream_events, (
            "live run produced no stream events — likely a misconfigured "
            "project_dir / unresolved slash command (NOT the warning under "
            f"test).{diag}"
        )

        # DEC-003: assert the WARNING, not result.succeeded. The skill is
        # *expected* to truncate because claude -p does not poll background
        # tasks.
        assert result.error_category == "background-task", (
            f"expected error_category=='background-task'.{diag}"
        )
        assert any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), (
            "expected a 'background-task:'-prefixed entry in "
            f"warnings.{diag}"
        )
        assert result.succeeded_cleanly is False, (
            f"expected succeeded_cleanly is False.{diag}"
        )
