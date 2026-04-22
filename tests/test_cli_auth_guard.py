"""Integration tests for the auth guard wired into the LLM-mediated CLI commands.

Traces to US-003 / DEC-002, DEC-003, DEC-004, DEC-009, DEC-011, DEC-017
of ``plans/super/83-subscription-auth-gap.md``.

Each LLM-mediated CLI command (``grade``, ``propose-eval``, ``suggest``,
``triggers``, ``extract``, ``compare --blind``) must:

- Exit 2 with a stderr message containing the three DEC-012 durable
  substrings (``ANTHROPIC_API_KEY``, ``Claude Pro``,
  ``console.anthropic.com``) plus the interpolated command name
  (``clauditor <cmd>``) when ``ANTHROPIC_API_KEY`` is absent.
- Honor ``--dry-run`` — with the env var unset, ``--dry-run`` must
  still exit 0 and produce no guard message on stderr (DEC-002).
  Four of the six commands have ``--dry-run``; ``suggest`` and
  ``compare --blind`` do not.

``compare --blind`` was added to the guarded set in QG pass 2 after
code review noticed it also routes through ``blind_compare_from_spec``
→ ``call_anthropic``; see DEC-017.

Per ``.claude/rules/pytester-inprocess-coverage-hazard.md`` these tests
invoke ``main([...])`` directly with ``monkeypatch`` — no pytester
runpytest_inprocess + mock.patch combination.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clauditor.cli import main
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.schemas import GradeThresholds

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


_DEC012_SUBSTRINGS = (
    "ANTHROPIC_API_KEY",
    "Claude Pro",
    "console.anthropic.com",
)


def _assert_guard_stderr(err: str, *, cmd_name: str) -> None:
    """Assert every DEC-012 substring + the interpolated command name appears."""
    for needle in _DEC012_SUBSTRINGS:
        assert needle in err, (
            f"expected {needle!r} in guard stderr, got: {err!r}"
        )
    assert f"clauditor {cmd_name}" in err, (
        f"expected 'clauditor {cmd_name}' in guard stderr, got: {err!r}"
    )


def _write_skill_md(tmp_path: Path, name: str = "greeter") -> Path:
    """Stage a modern-layout SKILL.md under ``tmp_path``."""
    skill_dir = tmp_path / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\n---\n# {name.title()}\n\nSay hi.\n"
    )
    return skill_md


def _write_eval_json(skill_md: Path, eval_data: dict) -> Path:
    """Write a sibling ``<skill_stem>.eval.json`` next to ``skill_md``."""
    eval_path = skill_md.with_suffix(".eval.json")
    eval_path.write_text(json.dumps(eval_data, indent=2))
    return eval_path


def _grade_eval_data() -> dict:
    """Minimal eval-spec shape acceptable to ``clauditor grade``."""
    return {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
        "grading_model": "claude-sonnet-4-6",
    }


def _triggers_eval_data() -> dict:
    return {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
        "grading_model": "claude-sonnet-4-6",
        "trigger_tests": {
            "should_trigger": ["hello there"],
            "should_not_trigger": ["weather today"],
        },
    }


def _extract_eval_data() -> dict:
    return {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "sections": [
            {
                "name": "Results",
                "tiers": [
                    {
                        "label": "primary",
                        "min_entries": 1,
                        "fields": [
                            {"id": "f1", "name": "name", "required": True}
                        ],
                    }
                ],
            }
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
        "grading_model": "claude-sonnet-4-6",
    }


def _stage_suggest_failing_run(tmp_path: Path) -> Path:
    """Stage the minimum files ``clauditor suggest`` needs to reach the guard.

    Writes a ``.git`` marker, a SKILL.md at ``tmp_path/greeter.md`` (legacy
    layout: suggest uses ``skill_path.stem`` for the skill name), and a
    failing grading.json + assertions.json under
    ``.clauditor/iteration-1/greeter/`` so ``load_suggest_input`` succeeds
    and the zero-signal early-exit does NOT fire (thereby exercising the
    guard's post-early-exit position).
    """
    (tmp_path / ".git").mkdir()
    skill_md = tmp_path / "greeter.md"
    skill_md.write_text("# Greeter\n\nSay hi.\n")

    skill_dir = tmp_path / ".clauditor" / "iteration-1" / "greeter"
    skill_dir.mkdir(parents=True)

    report = GradingReport(
        skill_name="greeter",
        model="claude-sonnet-4-6",
        results=[
            GradingResult(
                id="c1",
                criterion="friendly tone",
                passed=False,
                score=0.2,
                evidence="e",
                reasoning="r",
            )
        ],
        duration_seconds=0.0,
        thresholds=GradeThresholds(),
        metrics={},
    )
    (skill_dir / "grading.json").write_text(report.to_json())

    assertions_payload = {
        "schema_version": 1,
        "skill": "greeter",
        "iteration": 1,
        "runs": [
            {
                "run": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "results": [
                    {
                        "id": "a1",
                        "name": "contains hello",
                        "passed": False,
                        "kind": "contains",
                        "message": "no match",
                        "transcript_path": None,
                    }
                ],
            }
        ],
    }
    (skill_dir / "assertions.json").write_text(json.dumps(assertions_payload))

    return skill_md


# ---------------------------------------------------------------------------
# Missing-key guard: every command exits 2 with the DEC-012 substrings +
# the interpolated ``clauditor <cmd>`` label.
# ---------------------------------------------------------------------------


class TestAuthGuardMissingKey:
    """DEC-002/DEC-011/DEC-012: guard fires on missing ``ANTHROPIC_API_KEY``."""

    def test_grade_missing_key_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _grade_eval_data())
        # Stage an output file so ``--output`` path resolution does not
        # fail before the guard fires. The guard lands after --dry-run
        # but before any workspace allocation or LLM call.
        output = tmp_path / "o.txt"
        output.write_text("some captured output")

        rc = main(["grade", str(skill_md), "--output", str(output)])

        assert rc == 2
        err = capsys.readouterr().err
        _assert_guard_stderr(err, cmd_name="grade")

    def test_propose_eval_missing_key_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)

        rc = main(["propose-eval", str(skill_md)])

        assert rc == 2
        err = capsys.readouterr().err
        _assert_guard_stderr(err, cmd_name="propose-eval")

    def test_suggest_missing_key_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        skill_md = _stage_suggest_failing_run(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["suggest", str(skill_md)])

        assert rc == 2
        err = capsys.readouterr().err
        _assert_guard_stderr(err, cmd_name="suggest")

    def test_triggers_missing_key_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _triggers_eval_data())

        rc = main(["triggers", str(skill_md)])

        assert rc == 2
        err = capsys.readouterr().err
        _assert_guard_stderr(err, cmd_name="triggers")

    def test_extract_missing_key_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _extract_eval_data())
        # --output path so extract doesn't need a real subprocess before
        # the guard fires.
        output = tmp_path / "captured.txt"
        output.write_text("some output")

        rc = main(["extract", str(skill_md), "--output", str(output)])

        assert rc == 2
        err = capsys.readouterr().err
        _assert_guard_stderr(err, cmd_name="extract")

    def test_compare_blind_missing_key_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        """QG pass 2 of #83: ``compare --blind`` is also LLM-mediated.

        The blind A/B judge routes through ``blind_compare_from_spec`` →
        ``call_anthropic``, so subscription-only users need the same
        actionable exit-2 message.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md,
            {
                "skill_name": "greeter",
                "description": "A greeter",
                "user_prompt": "Say hi to the user.",
                "grading_criteria": [
                    {"id": "g1", "criterion": "greets warmly"},
                ],
            },
        )
        before = tmp_path / "before.txt"
        after = tmp_path / "after.txt"
        before.write_text("hi there")
        after.write_text("hello friend")

        rc = main(
            [
                "compare",
                str(before),
                str(after),
                "--spec",
                str(skill_md),
                "--blind",
            ]
        )

        assert rc == 2
        err = capsys.readouterr().err
        _assert_guard_stderr(err, cmd_name="compare --blind")


# ---------------------------------------------------------------------------
# --dry-run exempts the guard (DEC-002): four commands have --dry-run.
# Exit 0 with the env var unset, and no guard message on stderr.
# ---------------------------------------------------------------------------


class TestAuthGuardDryRunExempt:
    """DEC-002: ``--dry-run`` exempts the guard (no API call expected)."""

    def test_grade_dry_run_no_key_exits_0(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _grade_eval_data())

        rc = main(["grade", str(skill_md), "--dry-run"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY is not set" not in err

    def test_propose_eval_dry_run_no_key_exits_0(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)

        # propose-eval's --dry-run prints the prompt to stdout. Patch the
        # Anthropic entry point just in case; if the guard is misplaced,
        # a real call would still need mocking — but the point of the test
        # is to show exit 0 happens without the guard firing.
        fail_mock = AsyncMock(
            side_effect=AssertionError(
                "propose_eval must not be called under --dry-run"
            )
        )
        with patch(
            "clauditor.cli.propose_eval.propose_eval", new=fail_mock
        ):
            rc = main(["propose-eval", str(skill_md), "--dry-run"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY is not set" not in err
        assert fail_mock.await_count == 0

    def test_triggers_dry_run_no_key_exits_0(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _triggers_eval_data())

        rc = main(["triggers", str(skill_md), "--dry-run"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY is not set" not in err

    def test_extract_dry_run_no_key_exits_0(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _extract_eval_data())

        rc = main(["extract", str(skill_md), "--dry-run"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY is not set" not in err


# ---------------------------------------------------------------------------
# Regression guard (US-005 / DEC-003, DEC-009): eight non-LLM-mediated
# commands remain usable without ``ANTHROPIC_API_KEY``. The guard from
# US-003 must NOT have bled into any of these: ``validate``, ``capture``,
# ``run``, ``lint``, ``init``, ``badge``, ``audit``, ``trend``.
#
# Per AC#3 of the ticket body — each command may exit non-zero for
# unrelated input reasons (missing fixture, invalid input, unknown
# subcommand); the only invariant this class asserts is that the
# US-001 headline substring ``"ANTHROPIC_API_KEY is not set"`` is
# NEVER present in stderr. A false positive here means US-003 wired
# the guard into a command that should not need a key.
# ---------------------------------------------------------------------------


# Headline anchor from the US-001 error template — the single
# substring the regression guard must confirm is absent.
_AUTH_GUARD_HEADLINE = "ANTHROPIC_API_KEY is not set"


class TestRegressionNoApiKey:
    """Regression guard: eight commands stay usable without ``ANTHROPIC_API_KEY``."""

    @pytest.mark.parametrize(
        "cmd_name",
        ["validate", "capture", "run", "lint", "init"],
    )
    def test_skill_command_not_guarded(
        self, cmd_name, tmp_path, monkeypatch, capsys
    ):
        """Skill-accepting commands (need only a skill path arg) do not fire
        the US-003 auth guard when ``ANTHROPIC_API_KEY`` is unset.

        These commands accept a skill file path as their first positional.
        We pass a non-existent path so the command errors out early on
        input validation — well before any Anthropic-SDK code path. The
        assertion is narrow: whatever error surfaces, it must not
        contain the US-001 headline ``"ANTHROPIC_API_KEY is not set"``.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        missing_skill = tmp_path / "does-not-exist.md"

        # ``run`` / ``capture`` take a skill *name*, not a path; the rest
        # take a skill-file path. Either way, a non-existent target trips
        # an early exit without touching the Anthropic SDK.
        if cmd_name in ("run", "capture"):
            arg = "nonexistent-skill-xyz"
        else:
            arg = str(missing_skill)

        # ``main`` may or may not raise SystemExit depending on whether
        # argparse rejects the input. Swallow either path; exit-code
        # variability is explicitly allowed.
        try:
            main([cmd_name, arg])
        except SystemExit:
            pass

        err = capsys.readouterr().err
        assert _AUTH_GUARD_HEADLINE not in err, (
            f"{cmd_name!r} unexpectedly tripped the auth guard. "
            f"stderr: {err!r}"
        )

    @pytest.mark.parametrize(
        "cmd_args",
        [
            pytest.param(["audit", "nonexistent-skill"], id="audit"),
            pytest.param(
                ["trend", "nonexistent-skill", "--metric", "pass_rate"],
                id="trend",
            ),
            pytest.param(["badge", "nonexistent-skill"], id="badge"),
        ],
    )
    def test_history_command_not_guarded(
        self, cmd_args, tmp_path, monkeypatch, capsys
    ):
        """History-reading commands (and a non-existent subcommand) do not
        fire the US-003 auth guard when ``ANTHROPIC_API_KEY`` is unset.

        ``audit`` and ``trend`` read ``.clauditor/history.jsonl`` / the
        iteration workspaces; with an empty ``tmp_path`` cwd they exit
        with a "no data" / "no history" message. ``badge`` is listed in
        the plan (and in the US-001 error-message template's ``Commands
        that don't need a key:`` line) but landed on ``dev`` after this
        feature branch was cut; argparse will surface it as ``invalid
        choice`` — also crucially NOT via the auth-guard message.

        Once this branch rebases past the ``#81`` / ``#84`` ``badge``
        commits, this parametrization upgrades from "argparse rejects
        unknown subcommand" to "badge runs sidecar-only, no SDK call"
        with no further edit needed.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)

        try:
            main(cmd_args)
        except SystemExit:
            pass

        err = capsys.readouterr().err
        assert _AUTH_GUARD_HEADLINE not in err, (
            f"{cmd_args[0]!r} unexpectedly tripped the auth guard. "
            f"stderr: {err!r}"
        )
