"""Tests for clauditor CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor import setup as clauditor_setup
from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.cli import main
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult
from clauditor.schemas import (
    FieldRequirement,
    GradeThresholds,
    SectionRequirement,
    TierRequirement,
    TriggerTests,
)
from clauditor.triggers import TriggerReport, TriggerResult

# Import conftest factories so existing call sites keep working under their
# historical names. The canonical implementations live in tests/conftest.py.
from tests.conftest import (
    build_eval_spec as _make_eval_spec,
)
from tests.conftest import (
    make_grading_report,
    make_skill_result,
)
from tests.conftest import (
    make_spec as _make_spec,
)

# ---------------------------------------------------------------------------
# Parametrized error-path tests that span multiple commands.
# Consolidates the "missing skill file" (US-002) and "no eval spec" error
# paths that were previously duplicated across 4-6 per-command test classes.
# ---------------------------------------------------------------------------


# Commands whose CLI signature is `<cmd> <skill.md>` and route through
# SkillSpec.from_file. Each must surface a file-not-found as rc=2 with a
# clean stderr message (US-002: no traceback, include init hint).
_MISSING_SKILL_FILE_COMMANDS = [
    pytest.param(["validate", "nonexistent.md"], id="validate"),
    pytest.param(["grade", "nonexistent.md"], id="grade"),
    pytest.param(["triggers", "nonexistent.md"], id="triggers"),
    pytest.param(["extract", "nonexistent.md"], id="extract"),
]


@pytest.mark.parametrize("argv", _MISSING_SKILL_FILE_COMMANDS)
def test_command_missing_skill_file_exits_2(argv, capsys):
    """US-002: every <cmd> skill.md path surfaces FNF as rc=2 with init hint."""
    with patch(
        "clauditor.cli.SkillSpec.from_file",
        side_effect=FileNotFoundError("Skill file not found: nonexistent.md"),
    ):
        rc = main(argv)

    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "nonexistent.md" in err
    assert "clauditor init nonexistent.md" in err


# Commands whose CLI signature is `<cmd> <skill.md>` and require an
# eval_spec. Each must surface "no eval spec" as rc=1 with the init hint.
_NO_EVAL_SPEC_COMMANDS = [
    pytest.param(["validate", "skill.md"], id="validate"),
    pytest.param(["grade", "skill.md"], id="grade"),
    pytest.param(["triggers", "skill.md"], id="triggers"),
    pytest.param(["extract", "skill.md"], id="extract"),
]


@pytest.mark.parametrize("argv", _NO_EVAL_SPEC_COMMANDS)
def test_command_no_eval_spec_exits_1(argv, capsys):
    """Every <cmd> skill.md path surfaces missing eval spec as rc=1."""
    spec = _make_spec(eval_spec=None)
    with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
        rc = main(argv)

    assert rc == 1
    err = capsys.readouterr().err
    assert "No eval spec" in err
    assert "clauditor init skill.md" in err


class TestCmdValidate:
    """Tests for the validate subcommand."""

    def test_validate_with_output_file(self, tmp_path):
        """Validate reads output file, runs assertions, returns 0."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world with some content")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--output", str(output_file)])

        assert rc == 0

    def test_validate_json_output(self, tmp_path):
        """--json flag produces valid JSON output."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world with some content")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(
                ["validate", "skill.md", "--output", str(output_file), "--json"]
            )

        # rc may be 0 or 1 depending on assertion pass, but JSON should be valid
        # (contains assertion should pass since output has "hello")
        assert rc == 0

    def test_validate_run_skill(self, tmp_path, monkeypatch):
        """Without --output, runs the skill to get output."""
        # chdir into tmp_path so the new US-006 workspace staging does
        # NOT pollute the repo's real .clauditor/ directory.
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output", duration_seconds=1.5,
        )

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 0
        spec.run.assert_called_once()

    def test_validate_run_skill_fails(self, capsys, tmp_path, monkeypatch):
        """Returns 1 when skill run fails."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="", exit_code=1, duration_seconds=0.5, error="timeout",
        )

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 1
        assert "Skill failed" in capsys.readouterr().err

    def test_validate_output_file_missing_exits_2(self, capsys, tmp_path):
        """--output pointing at a non-existent path exits 2 with clean error."""
        missing = tmp_path / "no-such-file.txt"
        spec = _make_spec(eval_spec=_make_eval_spec())
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--output", str(missing)])

        assert rc == 2
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "Output file not found" in err
        assert str(missing) in err


class TestCmdRun:
    """Tests for the run subcommand."""

    def test_run_happy_path(self, capsys):
        """Runs skill and prints output."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="skill output here", skill_name="my-skill",
            duration_seconds=2.0,
        )

        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        assert rc == 0
        assert "skill output here" in capsys.readouterr().out

    def test_run_with_error(self, capsys):
        """Prints error to stderr and returns non-zero exit code."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="", exit_code=1, skill_name="my-skill",
            duration_seconds=0.5, error="command not found",
        )

        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        assert rc == 1
        assert "command not found" in capsys.readouterr().err


class TestCmdGrade:
    """Tests for the grade subcommand."""

    def _make_grading_report(self, passed=True):
        return make_grading_report(passed=passed)

    def test_grade_with_output(self, tmp_path, monkeypatch):
        """Grades pre-captured output, returns 0 when passed."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_grading_report(passed=True)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--output", str(output_file)])

        assert rc == 0

    def test_grade_no_grading_criteria(self, capsys):
        """Returns 1 when no grading_criteria defined."""
        eval_spec = _make_eval_spec(grading_criteria=[])
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md"])

        assert rc == 1
        assert "No grading_criteria" in capsys.readouterr().err

    def test_grade_dry_run(self, capsys):
        """--dry-run prints prompt and returns 0 without API calls."""
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md", "--dry-run"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Model:" in out
        assert "Prompt:" in out

    def test_grade_failed(self, tmp_path, monkeypatch):
        """Returns 1 when grading fails."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("bad output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_grading_report(passed=False)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--output", str(output_file)])

        assert rc == 1


class TestCmdGradeInputFilesStaging:
    """Tests for US-004: CLI threads run_dir to spec.run so eval input_files
    are staged per-run, and captured-output mode warns + skips staging."""

    def _make_grading_report(self, passed=True):
        return make_grading_report(passed=passed, score=0.9)

    def _staging_spec(self, eval_spec, outputs):
        """Build a MagicMock SkillSpec whose .run() mirrors the real
        SkillSpec.run: when run_dir is passed and eval_spec.input_files
        is non-empty, call stage_inputs(run_dir, [...]). Returns
        ``outputs`` popped per-call as SkillResults."""
        from pathlib import Path as _Path

        from clauditor.workspace import stage_inputs

        spec = _make_spec(eval_spec=eval_spec)
        outputs_iter = iter(outputs)

        def _run(args=None, *, run_dir=None, **kwargs):
            # kwargs absorbs US-006 ``timeout_override`` / ``env_override``
            # plumbing without coupling this staging fixture to their
            # presence.
            if run_dir is not None and eval_spec.input_files:
                stage_inputs(
                    run_dir, [_Path(p) for p in eval_spec.input_files]
                )
            return next(outputs_iter)

        spec.run = MagicMock(side_effect=_run)
        return spec

    def _ok_result(self, text="ok"):
        return make_skill_result(
            output=text, duration_seconds=0.1,
            input_tokens=1, output_tokens=1,
            stream_events=[{"type": "assistant", "text": text}],
        )

    def test_grade_stages_input_files_into_iteration_run_dir_on_finalize(
        self, tmp_path, monkeypatch
    ):
        """Primary (non --output) grade threads run_dir so input_files
        are staged under iteration-1/<skill>/run-0/inputs/."""
        monkeypatch.chdir(tmp_path)
        source = tmp_path / "sales.csv"
        source.write_bytes(b"date,amount\n2024-01-01,100\n")

        eval_spec = _make_eval_spec(input_files=[str(source)])
        spec = self._staging_spec(eval_spec, [self._ok_result("primary")])
        report = self._make_grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md"])

        assert rc == 0
        staged = (
            tmp_path
            / ".clauditor"
            / "iteration-1"
            / "test-skill"
            / "run-0"
            / "inputs"
            / "sales.csv"
        )
        assert staged.is_file()
        assert staged.read_bytes() == source.read_bytes()

    def test_grade_variance_stages_inputs_per_run(
        self, tmp_path, monkeypatch
    ):
        """--variance 2 stages input_files into every run-K dir."""
        monkeypatch.chdir(tmp_path)
        source = tmp_path / "sales.csv"
        source.write_bytes(b"k,v\na,1\n")

        eval_spec = _make_eval_spec(input_files=[str(source)])
        spec = self._staging_spec(
            eval_spec,
            [
                self._ok_result("primary"),
                self._ok_result("variance 1"),
                self._ok_result("variance 2"),
            ],
        )
        report = self._make_grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--variance", "2"])

        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        staged_paths = []
        for k in (0, 1, 2):
            staged = skill_dir / f"run-{k}" / "inputs" / "sales.csv"
            assert staged.is_file(), f"run-{k} missing staged input"
            assert staged.read_bytes() == source.read_bytes()
            staged_paths.append(staged)
        # Independence: mutating run-0's copy must not affect run-1 / run-2.
        staged_paths[0].write_bytes(b"tampered")
        assert staged_paths[1].read_bytes() == source.read_bytes()
        assert staged_paths[2].read_bytes() == source.read_bytes()

    def test_grade_captured_output_mode_with_input_files_warns(  # noqa: E501
        self, tmp_path, monkeypatch, capsys
    ):
        """--output <file> + non-empty input_files prints a stderr warning
        and does NOT create any inputs/ dir under the iteration workspace."""
        monkeypatch.chdir(tmp_path)
        source = tmp_path / "sales.csv"
        source.write_bytes(b"k,v\na,1\n")
        captured = tmp_path / "captured.txt"
        captured.write_text("pre-captured skill output")

        eval_spec = _make_eval_spec(input_files=[str(source)])
        # Captured-output mode does not invoke spec.run for the primary.
        spec = self._staging_spec(eval_spec, [])
        report = self._make_grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(captured)]
            )

        assert rc == 0
        err = capsys.readouterr().err
        assert (
            "WARNING: --output bypasses the runner; "
            "input_files declaration is ignored." in err
        )
        spec.run.assert_not_called()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert skill_dir.is_dir()
        # No inputs/ dir should exist under any run-K
        assert not any(
            p.name == "inputs" for p in skill_dir.rglob("inputs")
        )


class TestOnlyCriterion:
    """Tests for --only-criterion filter on the grade subcommand."""

    def _report(self):
        return make_grading_report(criterion="x", score=1.0)

    def _run(self, tmp_path, criteria, extra_args, monkeypatch=None):
        if monkeypatch is not None:
            monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "o.txt"
        output_file.write_text("out")
        eval_spec = _make_eval_spec(grading_criteria=list(criteria))
        spec = _make_spec(eval_spec=eval_spec)
        mock_grade = AsyncMock(return_value=self._report())
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch("clauditor.quality_grader.grade_quality", mock_grade),
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)] + extra_args
            )
        return rc, mock_grade, spec

    def test_single_substring_filters(self, tmp_path, monkeypatch):
        """--only-criterion foo keeps only matching criteria."""
        rc, mock_grade, spec = self._run(
            tmp_path,
            ["foo bar", "baz qux", "other foo"],
            ["--only-criterion", "foo"],
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["foo bar", "other foo"]
        # Grader called with filtered spec
        mock_grade.assert_called_once()
        passed_spec = mock_grade.call_args.args[1]
        assert passed_spec.grading_criteria == ["foo bar", "other foo"]

    def test_multiple_substrings_union(self, tmp_path, monkeypatch):
        """Multiple --only-criterion flags use OR semantics."""
        rc, mock_grade, spec = self._run(
            tmp_path,
            ["alpha", "beta", "gamma", "alphabeta"],
            ["--only-criterion", "alpha", "--only-criterion", "gamma"],
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["alpha", "gamma", "alphabeta"]

    def test_no_match_exits_2(self, tmp_path, capsys):
        """No match prints Available and returns exit code 2."""
        output_file = tmp_path / "o.txt"
        output_file.write_text("out")
        eval_spec = _make_eval_spec(
            grading_criteria=["clarity", "accuracy"]
        )
        spec = _make_spec(eval_spec=eval_spec)
        mock_grade = AsyncMock(return_value=self._report())
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch("clauditor.quality_grader.grade_quality", mock_grade),
        ):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--only-criterion",
                    "nonexistent",
                ]
            )
        assert rc == 2
        err = capsys.readouterr().err
        assert "No grading criteria match filter" in err
        assert "Available:" in err
        assert "clarity" in err
        assert "accuracy" in err
        # Grader must NOT have been called
        mock_grade.assert_not_called()

    def test_no_flag_passes_all(self, tmp_path, monkeypatch):
        """Without --only-criterion, all criteria are passed through."""
        rc, mock_grade, spec = self._run(
            tmp_path, ["one", "two", "three"], [], monkeypatch=monkeypatch
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["one", "two", "three"]

    def test_case_insensitive(self, tmp_path, monkeypatch):
        """--only-criterion FOO matches criterion 'foo'."""
        rc, _mock, spec = self._run(
            tmp_path,
            ["foo", "bar"],
            ["--only-criterion", "FOO"],
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["foo"]

    @pytest.mark.parametrize(
        "extra,label",
        [
            (["--iteration", "3"], "--iteration"),
            (["--force"], "--force"),
            (["--diff"], "--diff"),
        ],
    )
    def test_only_criterion_rejects_conflicting_flags(
        self, tmp_path, monkeypatch, capsys, extra, label
    ):
        """--only-criterion + --iteration/--force/--diff is a hard error.

        Pass 3 bug 1/2 regression guard: these combinations could either
        destroy the existing iteration-N baseline (--force) or report a
        confusing diff against an abandoned slot (--diff).
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        output_file = tmp_path / "o.txt"
        output_file.write_text("out")
        eval_spec = _make_eval_spec(grading_criteria=["foo", "bar"])
        spec = _make_spec(eval_spec=eval_spec)
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--only-criterion",
                    "foo",
                    *extra,
                ]
            )
        assert rc == 2
        assert label in capsys.readouterr().err

    def test_only_criterion_does_not_publish_iteration_dir(
        self, tmp_path, monkeypatch
    ):
        """--only-criterion must not leave an iteration-N/ dir on disk.

        A partial grading.json from a filtered run would otherwise be
        picked up later as a bogus baseline by --diff / compare /
        _find_prior_grading_json. Pass 2 bug 2 regression guard.
        """
        (tmp_path / ".git").mkdir()
        rc, _mock, _spec = self._run(
            tmp_path,
            ["foo", "bar"],
            ["--only-criterion", "foo"],
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        clauditor_dir = tmp_path / ".clauditor"
        if clauditor_dir.exists():
            iteration_dirs = sorted(clauditor_dir.glob("iteration-*"))
            final_dirs = [
                d for d in iteration_dirs if not d.name.endswith("-tmp")
            ]
            assert final_dirs == [], (
                f"--only-criterion unexpectedly published {final_dirs}"
            )


class TestCmdGradeSaveDiff:
    """Tests for the iteration workspace layout (US-004) and --diff."""

    def _make_grading_report(self, skill_name="test-skill", passed=True, score=0.9):
        return make_grading_report(
            skill_name=skill_name, passed=passed, score=score,
        )

    def _patch_grade(self, spec, report):
        return (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        )

    def test_grade_writes_iteration_one_when_empty(self, tmp_path, monkeypatch):
        """An empty .clauditor/ allocates iteration-1 with the full layout."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        spec = _make_spec(eval_spec=_make_eval_spec())
        report = self._make_grading_report()

        s_patch, g_patch = self._patch_grade(spec, report)
        with s_patch, g_patch:
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )

        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert skill_dir.is_dir()
        assert (skill_dir / "grading.json").is_file()
        assert (skill_dir / "timing.json").is_file()
        assert (skill_dir / "run-0" / "output.txt").is_file()
        assert (skill_dir / "run-0" / "output.jsonl").is_file()
        assert (
            (skill_dir / "run-0" / "output.txt").read_text()
            == "some skill output"
        )
        # No tmp dir should remain after a successful finalize.
        assert not (tmp_path / ".clauditor" / "iteration-1-tmp").exists()
        # grading.json round-trips through GradingReport
        rt = GradingReport.from_json(
            (skill_dir / "grading.json").read_text()
        )
        assert rt.skill_name == "test-skill"
        # timing.json holds metrics
        timing = json.loads((skill_dir / "timing.json").read_text())
        assert timing["iteration"] == 1
        assert "metrics" in timing

    def test_grade_auto_increments_across_runs(self, tmp_path, monkeypatch):
        """Two runs in a row produce iteration-1/ then iteration-2/."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        report = self._make_grading_report()

        s, g = self._patch_grade(spec, report)
        with s, g:
            assert main(
                ["grade", "skill.md", "--output", str(output_file)]
            ) == 0
            assert main(
                ["grade", "skill.md", "--output", str(output_file)]
            ) == 0

        assert (tmp_path / ".clauditor" / "iteration-1" / "test-skill").is_dir()
        assert (tmp_path / ".clauditor" / "iteration-2" / "test-skill").is_dir()

    def test_grade_iteration_explicit_collision_errors(
        self, tmp_path, monkeypatch, capsys
    ):
        """Re-running --iteration N without --force errors non-zero."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        report = self._make_grading_report()

        s, g = self._patch_grade(spec, report)
        with s, g:
            assert main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--iteration",
                    "5",
                ]
            ) == 0
            rc2 = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--iteration",
                    "5",
                ]
            )
        assert rc2 != 0
        err = capsys.readouterr().err
        assert "iteration-5" in err
        assert "--force" in err

    def test_grade_iteration_force_overwrites(self, tmp_path, monkeypatch):
        """--force replaces an existing iteration-N/ cleanly."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("first")
        spec = _make_spec(eval_spec=_make_eval_spec())
        first_report = self._make_grading_report(score=0.9)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=first_report,
            ),
        ):
            assert main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--iteration",
                    "5",
                ]
            ) == 0

        # Second run with different content + --force
        output_file.write_text("second")
        second_report = self._make_grading_report(score=0.55)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=second_report,
            ),
        ):
            assert main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--iteration",
                    "5",
                    "--force",
                ]
            ) == 0

        skill_dir = tmp_path / ".clauditor" / "iteration-5" / "test-skill"
        assert (skill_dir / "run-0" / "output.txt").read_text() == "second"
        rt = GradingReport.from_json((skill_dir / "grading.json").read_text())
        assert rt.results[0].score == 0.55

    def test_grade_variance_produces_run_subdirs(self, tmp_path, monkeypatch):
        """--variance N produces N+1 run-K/ subdirs under one iteration."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("primary text")

        spec = _make_spec(eval_spec=_make_eval_spec())
        # Variance runs invoke spec.run() — return distinct outputs.
        spec.run.side_effect = [
            make_skill_result(
                output=f"variance run {i}", duration_seconds=0.5,
                input_tokens=10, output_tokens=5,
                stream_events=[{"type": "result", "i": i}],
            )
            for i in range(2)
        ]

        report = self._make_grading_report()
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                [
                    "grade", "skill.md", "--output", str(output_file),
                    "--variance", "2",
                ]
            )
        assert rc == 0

        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        for k in (0, 1, 2):
            assert (skill_dir / f"run-{k}" / "output.txt").is_file()
            assert (skill_dir / f"run-{k}" / "output.jsonl").is_file()
        assert (
            (skill_dir / "run-0" / "output.txt").read_text() == "primary text"
        )
        assert (
            (skill_dir / "run-1" / "output.txt").read_text() == "variance run 0"
        )
        assert (
            (skill_dir / "run-2" / "output.txt").read_text() == "variance run 1"
        )
        # Single grading.json at the skill level (not per-run)
        assert (skill_dir / "grading.json").is_file()

    def test_cmd_grade_writes_assertions_json(self, tmp_path, monkeypatch):
        """US-002: cmd_grade persists Layer 1 AssertionSet as assertions.json
        keyed by the stable spec ids (DEC-001, DEC-007)."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world this is the skill output")

        eval_spec = _make_eval_spec(
            assertions=[
                {"id": "has-hello", "type": "contains", "needle": "hello"},
                {"id": "min-len", "type": "min_length", "length": 5},
            ]
        )
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_grading_report()

        s, g = self._patch_grade(spec, report)
        with s, g:
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )
        assert rc == 0

        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assertions_path = skill_dir / "assertions.json"
        assert assertions_path.is_file()

        payload = json.loads(assertions_path.read_text())
        # FIX-7 (#25): sidecar envelopes pin schema_version=1.
        assert payload["schema_version"] == 1
        assert payload["skill"] == "test-skill"
        assert payload["iteration"] == 1
        assert len(payload["runs"]) == 1
        run0 = payload["runs"][0]
        assert run0["run"] == 0
        ids = [r["id"] for r in run0["results"]]
        assert ids == ["has-hello", "min-len"]
        # Every result carries an id (no position-keyed fallback).
        assert all(r["id"] is not None for r in run0["results"])
        assert all(r["passed"] for r in run0["results"])
        # US-004: captured-text mode (--output) has no subprocess run,
        # so transcript_path must be None for every result.
        assert all(
            r["transcript_path"] is None for r in run0["results"]
        )

    def test_cmd_grade_threads_transcript_path_on_assertions(
        self, tmp_path, monkeypatch
    ):
        """US-004: in subprocess mode, every AssertionResult in
        assertions.json carries a repo-relative transcript_path
        pointing at run-K/output.jsonl."""
        monkeypatch.chdir(tmp_path)
        eval_spec = _make_eval_spec(
            assertions=[
                {"id": "has-primary", "type": "contains", "needle": "primary"},
                {"id": "min-len", "type": "min_length", "length": 3},
            ]
        )
        spec = _make_spec(eval_spec=eval_spec)
        spec.run = MagicMock(
            return_value=make_skill_result(
                output="primary output here",
                stream_events=[
                    {"type": "assistant", "text": "primary output here"}
                ],
                input_tokens=10, output_tokens=5, duration_seconds=0.5,
            )
        )
        report = self._make_grading_report()
        s, g = self._patch_grade(spec, report)
        with s, g:
            rc = main(["grade", "skill.md"])
        assert rc == 0

        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        payload = json.loads(
            (skill_dir / "assertions.json").read_text()
        )
        run0 = payload["runs"][0]
        assert len(run0["results"]) == 2
        expected = ".clauditor/iteration-1/test-skill/run-0/output.jsonl"
        for r in run0["results"]:
            assert r["transcript_path"] == expected
        # And the file that path names actually exists on disk.
        assert (skill_dir / "run-0" / "output.jsonl").is_file()

    def test_cmd_grade_no_transcript_suppresses_run_dir(
        self, tmp_path, monkeypatch
    ):
        """US-005: --no-transcript skips run-K/output.jsonl writes and
        leaves every AssertionResult.transcript_path as None, while
        assertions.json / grading.json still land."""
        monkeypatch.chdir(tmp_path)
        eval_spec = _make_eval_spec(
            assertions=[
                {"id": "has-primary", "type": "contains", "needle": "primary"},
                {"id": "min-len", "type": "min_length", "length": 3},
            ]
        )
        spec = _make_spec(eval_spec=eval_spec)
        spec.run = MagicMock(
            return_value=make_skill_result(
                output="primary output here",
                stream_events=[
                    {"type": "assistant", "text": "primary output here"}
                ],
                input_tokens=10, output_tokens=5, duration_seconds=0.5,
            )
        )
        report = self._make_grading_report()
        s, g = self._patch_grade(spec, report)
        with s, g:
            rc = main(["grade", "skill.md", "--no-transcript"])
        assert rc == 0

        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        # No run-K dir written at all.
        assert not (skill_dir / "run-0" / "output.jsonl").exists()
        assert not (skill_dir / "run-0" / "output.txt").exists()
        assert not (skill_dir / "run-0").exists()
        # assertions.json still persisted, but transcript_path is None.
        payload = json.loads(
            (skill_dir / "assertions.json").read_text()
        )
        run0 = payload["runs"][0]
        assert len(run0["results"]) == 2
        for r in run0["results"]:
            assert r["transcript_path"] is None
        # grading.json still persisted.
        assert (skill_dir / "grading.json").is_file()

    def test_cmd_grade_variance_runs_each_have_assertions_json_entry(
        self, tmp_path, monkeypatch
    ):
        """US-002: variance runs each persist their own record in
        assertions.json (one entry per run-K)."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("primary output text long enough")

        eval_spec = _make_eval_spec(
            assertions=[
                {"id": "has-text", "type": "contains", "needle": "text"},
            ]
        )
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.side_effect = [
            make_skill_result(
                output=f"variance text run {i}",
                duration_seconds=0.5, input_tokens=10, output_tokens=5,
                stream_events=[{"type": "result", "i": i}],
            )
            for i in range(2)
        ]
        report = self._make_grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--variance",
                    "2",
                ]
            )
        assert rc == 0

        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        payload = json.loads((skill_dir / "assertions.json").read_text())
        assert [r["run"] for r in payload["runs"]] == [0, 1, 2]
        for entry in payload["runs"]:
            assert len(entry["results"]) == 1
            assert entry["results"][0]["id"] == "has-text"
            assert entry["results"][0]["passed"] is True

    def test_grade_primary_skill_subprocess_path(
        self, tmp_path, monkeypatch
    ):
        """No --output: cmd_grade runs the skill subprocess and captures
        its stream_events + token totals into run-0/ and metrics.

        Covers the primary-run branch (306-322).
        """
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run = MagicMock(
            return_value=make_skill_result(
                output="primary output",
                stream_events=[
                    {"type": "assistant", "text": "primary output"}
                ],
                input_tokens=100, output_tokens=50, duration_seconds=1.5,
            )
        )
        report = self._make_grading_report()
        s, g = self._patch_grade(spec, report)
        with s, g:
            rc = main(["grade", "skill.md"])
        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert (skill_dir / "run-0" / "output.txt").read_text() == (
            "primary output"
        )
        # stream_events serialized to output.jsonl
        jsonl_lines = [
            line
            for line in (skill_dir / "run-0" / "output.jsonl")
            .read_text()
            .splitlines()
            if line.strip()
        ]
        assert len(jsonl_lines) == 1
        assert json.loads(jsonl_lines[0])["type"] == "assistant"
        # timing.json has skill bucket tokens
        timing = json.loads((skill_dir / "timing.json").read_text())
        metrics = timing["metrics"]
        assert metrics["skill"]["input_tokens"] == 100
        assert metrics["skill"]["output_tokens"] == 50

    def test_grade_primary_skill_failure_returns_1(
        self, tmp_path, monkeypatch, capsys
    ):
        """If the primary skill subprocess fails, grade exits 1 with error.

        The workspace staging dir must be aborted (not finalized).
        """
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run = MagicMock(
            return_value=make_skill_result(
                output="", exit_code=1, error="skill blew up",
            )
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md"])
        assert rc == 1
        assert "skill blew up" in capsys.readouterr().err
        # No iteration-N/ should remain.
        clauditor_dir = tmp_path / ".clauditor"
        if clauditor_dir.exists():
            assert list(clauditor_dir.glob("iteration-*/")) == []

    def test_grade_variance_run_failure_returns_1(
        self, tmp_path, monkeypatch, capsys
    ):
        """If a variance subrun fails, grade exits 1 with error.

        Covers the variance-run failure branch (329-333).
        """
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        # First call succeeds (primary), second fails (variance run 1).
        spec.run = MagicMock(
            side_effect=[
                make_skill_result(
                    output="primary", input_tokens=10, output_tokens=5,
                    duration_seconds=0.5,
                ),
                make_skill_result(
                    output="", exit_code=1, error="variance kaboom",
                ),
            ]
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md", "--variance", "1"])
        assert rc == 1
        assert "variance kaboom" in capsys.readouterr().err.lower()
        # No iteration-N/ should remain.
        clauditor_dir = tmp_path / ".clauditor"
        if clauditor_dir.exists():
            assert list(clauditor_dir.glob("iteration-*/")) == []

    def test_grade_invalid_skill_name_errors(
        self, tmp_path, monkeypatch, capsys
    ):
        """Path-traversal skill names surface as a clean error from cmd_grade.

        Covers the InvalidSkillNameError catch in cmd_grade (PR review).
        """
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        # SkillSpec.from_file returns a spec whose skill_name is unsafe.
        spec = _make_spec(
            skill_name="../evil", eval_spec=_make_eval_spec()
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )
        assert rc == 2
        assert "invalid skill name" in capsys.readouterr().err

    def test_grade_programmatic_value_error_caught(
        self, tmp_path, monkeypatch, capsys
    ):
        """cmd_grade converts allocate_iteration ValueError to exit 2.

        argparse rejects --iteration<1 at the CLI boundary, but the
        programmatic path (direct call with a crafted Namespace) can
        still reach the allocator; cmd_grade's `except ValueError`
        branch surfaces the error cleanly.
        """
        import argparse

        from clauditor.cli import cmd_grade

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        output_file = tmp_path / "o.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        ns = argparse.Namespace(
            skill="skill.md",
            eval=None,
            output=str(output_file),
            model=None,
            variance=None,
            dry_run=False,
            iteration=0,  # invalid — triggers ValueError in allocator
            force=False,
            only_criterion=None,
            diff=False,
            json=False,
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = cmd_grade(ns)
        assert rc == 2
        assert "iteration must be" in capsys.readouterr().err

    def test_grade_iteration_zero_rejected_by_argparse(
        self, tmp_path, monkeypatch, capsys
    ):
        """--iteration 0 is rejected at argparse (uses _positive_int)."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main(["grade", "skill.md", "--iteration", "0"])
        err = capsys.readouterr().err
        assert "--iteration" in err or "must be" in err or "invalid" in err

    def test_grade_finalize_race_surfaces_clean_error(
        self, tmp_path, monkeypatch, capsys
    ):
        """Concurrent finalize() race produces exit 1, not a traceback.

        Covers the IterationExistsError catch around
        _cmd_grade_with_workspace.
        """
        import errno as _errno

        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        report = self._make_grading_report()

        enotempty = OSError(_errno.ENOTEMPTY, "Directory not empty")
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
            patch(
                "clauditor.workspace.os.rename", side_effect=enotempty
            ),
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "finalized by a concurrent writer" in err

    def test_grade_json_variance_output_shape(
        self, tmp_path, monkeypatch, capsys
    ):
        """--json --variance emits a populated data.variance sub-object.

        Covers the `if variance_report:` JSON branch (475-483).
        """
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "o.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        # Variance runs call spec.run() — return a real SkillResult so
        # the stream_events and token accounting paths don't blow up.
        spec.run = MagicMock(
            return_value=make_skill_result(
                output="variance run",
                input_tokens=5, output_tokens=3, duration_seconds=0.1,
            )
        )
        report = self._make_grading_report()
        s, g = self._patch_grade(spec, report)
        with s, g:
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--variance",
                    "2",
                    "--json",
                ]
            )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["variance"] is not None
        assert payload["variance"]["n_runs"] == 3
        assert "score_mean" in payload["variance"]
        assert "stability" in payload["variance"]

    def test_grade_crash_leaves_no_iteration_dir(self, tmp_path, monkeypatch):
        """An exception mid-write must not leave a finalized iteration-N/."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        boom = AsyncMock(side_effect=RuntimeError("kaboom"))
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch("clauditor.quality_grader.grade_quality", boom),
            pytest.raises(RuntimeError),
        ):
            main(["grade", "skill.md", "--output", str(output_file)])

        # iteration-1/ must not exist (only an orphan tmp dir is allowed).
        assert not (tmp_path / ".clauditor" / "iteration-1").exists()

    def test_grade_diff_shows_regression_against_prior_iteration(
        self, tmp_path, monkeypatch, capsys
    ):
        """--diff compares against the most recent prior iteration's grading.json."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())

        prior_report = self._make_grading_report(score=0.9, passed=True)
        s, g = self._patch_grade(spec, prior_report)
        with s, g:
            assert main(
                ["grade", "skill.md", "--output", str(output_file)]
            ) == 0

        current_report = self._make_grading_report(score=0.4, passed=False)
        s, g = self._patch_grade(spec, current_report)
        with s, g:
            main(
                ["grade", "skill.md", "--output", str(output_file), "--diff"]
            )
        out = capsys.readouterr().out
        assert "REGRESSION" in out

    def test_grade_diff_no_prior_warns(self, tmp_path, monkeypatch, capsys):
        """--diff with no prior iteration warns, does not error."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")
        spec = _make_spec(eval_spec=_make_eval_spec())
        s, g = self._patch_grade(spec, self._make_grading_report())
        with s, g:
            rc = main(
                ["grade", "skill.md", "--output", str(output_file), "--diff"]
            )
        assert rc == 0
        err = capsys.readouterr().err
        assert "No prior iteration" in err


class TestCmdGradeLayer2Persistence:
    """US-003 (#25): cmd_grade wires Layer 2 and writes extraction.json."""

    def _make_grading_report(self):
        return make_grading_report(passed=True, score=0.9)

    def _make_sectioned_eval_spec(self):
        return _make_eval_spec(
            sections=[
                SectionRequirement(
                    name="Venues",
                    tiers=[
                        TierRequirement(
                            label="primary",
                            min_entries=0,
                            fields=[
                                FieldRequirement(
                                    name="venue_name",
                                    required=True,
                                    id="venues.primary.venue_name.v1",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

    def _make_extraction_report(self, **overrides):
        from clauditor.grader import ExtractionReport, FieldExtractionResult

        defaults = dict(
            skill_name="test-skill",
            model="claude-haiku-4-5-20251001",
            results=[
                FieldExtractionResult(
                    field_id="venues.primary.venue_name.v1",
                    field_name="venue_name",
                    section="Venues",
                    tier="primary",
                    entry_index=0,
                    required=True,
                    presence_passed=True,
                    format_passed=None,
                    evidence="Cafe Foo",
                ),
            ],
            input_tokens=42,
            output_tokens=7,
        )
        defaults.update(overrides)
        return ExtractionReport(**defaults)

    def test_cmd_grade_invokes_layer2_when_sections_declared(
        self, tmp_path, monkeypatch
    ):
        """Spec with sections writes iteration-*/<skill>/extraction.json."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("some output")

        spec = _make_spec(eval_spec=self._make_sectioned_eval_spec())
        grading_report = self._make_grading_report()
        extraction_report = self._make_extraction_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=grading_report,
            ),
            patch(
                "clauditor.grader.extract_and_report",
                new_callable=AsyncMock,
                return_value=extraction_report,
            ) as mock_extract,
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )

        assert rc == 0
        assert mock_extract.await_count == 1
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        extraction_path = skill_dir / "extraction.json"
        assert extraction_path.is_file()

    def test_cmd_grade_skips_layer2_when_no_sections(
        self, tmp_path, monkeypatch
    ):
        """Spec with no sections: no extraction.json, no L2 LLM calls."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("some output")

        spec = _make_spec(eval_spec=_make_eval_spec(sections=[]))
        grading_report = self._make_grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=grading_report,
            ),
            patch(
                "clauditor.grader.extract_and_report",
                new_callable=AsyncMock,
            ) as mock_extract,
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )

        assert rc == 0
        mock_extract.assert_not_awaited()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert skill_dir.is_dir()
        assert not (skill_dir / "extraction.json").exists()

    def test_extraction_json_keyed_by_field_id(
        self, tmp_path, monkeypatch
    ):
        """extraction.json on disk uses stable FieldRequirement.id as key."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("some output")

        spec = _make_spec(eval_spec=self._make_sectioned_eval_spec())
        grading_report = self._make_grading_report()
        # ExtractionReport without explicit tokens (default None) to exercise
        # the "no token_counts" branch separately from the happy path.
        extraction_report = self._make_extraction_report(
            input_tokens=None, output_tokens=None,
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=grading_report,
            ),
            patch(
                "clauditor.grader.extract_and_report",
                new_callable=AsyncMock,
                return_value=extraction_report,
            ),
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )

        assert rc == 0
        extraction_path = (
            tmp_path
            / ".clauditor"
            / "iteration-1"
            / "test-skill"
            / "extraction.json"
        )
        payload = json.loads(extraction_path.read_text())
        assert "fields" in payload
        assert "venues.primary.venue_name.v1" in payload["fields"]
        entries = payload["fields"]["venues.primary.venue_name.v1"]
        assert isinstance(entries, list) and len(entries) == 1
        assert entries[0]["field_name"] == "venue_name"
        assert entries[0]["evidence"] == "Cafe Foo"
        assert entries[0]["passed"] is True


class TestBaselineFlag:
    """US-004 (#25): --baseline flag on cmd_grade writes baseline sidecars."""

    def _make_grading_report(self):
        return make_grading_report(passed=True, score=0.9)

    def _make_baseline_skill_result(self, output="baseline output"):
        return make_skill_result(
            output=output, skill_name="__baseline__",
            duration_seconds=0.75, input_tokens=11, output_tokens=22,
        )

    def _make_sectioned_eval_spec(self):
        return _make_eval_spec(
            assertions=[
                {
                    "type": "contains",
                    "needle": "hello",
                    "id": "a.hello.v1",
                }
            ],
            sections=[
                SectionRequirement(
                    name="Venues",
                    tiers=[
                        TierRequirement(
                            label="primary",
                            min_entries=0,
                            fields=[
                                FieldRequirement(
                                    name="venue_name",
                                    required=True,
                                    id="venues.primary.venue_name.v1",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

    def _make_extraction_report(self):
        from clauditor.grader import ExtractionReport, FieldExtractionResult

        return ExtractionReport(
            skill_name="test-skill",
            model="claude-haiku-4-5-20251001",
            results=[
                FieldExtractionResult(
                    field_id="venues.primary.venue_name.v1",
                    field_name="venue_name",
                    section="Venues",
                    tier="primary",
                    entry_index=0,
                    required=True,
                    presence_passed=True,
                    format_passed=None,
                    evidence="Cafe Foo",
                ),
            ],
        )

    def _prepare_spec(self, eval_spec):
        spec = _make_spec(eval_spec=eval_spec)
        spec.runner = MagicMock()
        spec.runner.run_raw.return_value = self._make_baseline_skill_result()
        return spec

    def _prepare_live_spec(self, eval_spec=None):
        """Like _prepare_spec but wires spec.run() with a hello-world
        SkillResult so compute_benchmark has non-None duration/tokens."""
        spec = self._prepare_spec(
            eval_spec if eval_spec is not None else self._make_sectioned_eval_spec()
        )
        spec.run = MagicMock(
            return_value=make_skill_result(
                output="hello world",
                stream_events=[{"type": "assistant", "text": "hello world"}],
                input_tokens=100, output_tokens=50, duration_seconds=2.5,
            )
        )
        return spec

    @staticmethod
    def _pair_reports_c1c2():
        """Return (primary, baseline) GradingReports where delta.pass_rate=+0.5.

        Primary has one passing 'c1'; baseline has passing 'c1' + failing 'c2'.
        Delta of 1.0 (primary) - 0.5 (baseline) = +0.50 exercises the
        signed delta arithmetic + format specifiers used by every delta-block
        test in this class.
        """
        primary = make_grading_report(criterion="c1", passed=True, score=0.9)
        baseline = make_grading_report(
            criterion="c1", passed=True, score=0.9,
            duration_seconds=0.5,
            extra_results=[
                GradingResult(
                    criterion="c2", passed=False, score=0.3,
                    evidence="no", reasoning="no",
                ),
            ],
        )
        return primary, baseline

    @staticmethod
    def _patches(spec, grading_report=None, extraction_report=None,
                 grading_side_effect=None):
        """Build the standard trio of patches for a baseline grade test."""
        grading_kwargs = (
            dict(side_effect=grading_side_effect)
            if grading_side_effect is not None
            else dict(return_value=grading_report)
        )
        extraction_kwargs = (
            dict(return_value=extraction_report)
            if extraction_report is not None else {}
        )
        return (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                **grading_kwargs,
            ),
            patch(
                "clauditor.grader.extract_and_report",
                new_callable=AsyncMock,
                **extraction_kwargs,
            ),
        )

    def test_grade_without_baseline_flag_writes_no_baseline_files(
        self, tmp_path, monkeypatch
    ):
        """Default cmd_grade produces no baseline_*.json and no run_raw call."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world")

        spec = self._prepare_spec(self._make_sectioned_eval_spec())
        p1, p2, p3 = self._patches(
            spec, grading_report=self._make_grading_report(),
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md", "--output", str(output_file)])

        assert rc == 0
        spec.runner.run_raw.assert_not_called()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert not (skill_dir / "baseline.json").exists()
        assert not (skill_dir / "baseline_assertions.json").exists()
        assert not (skill_dir / "baseline_extraction.json").exists()
        assert not (skill_dir / "baseline_grading.json").exists()

    def test_grade_with_baseline_flag_writes_all_baseline_sidecars(
        self, tmp_path, monkeypatch
    ):
        """--baseline writes all four baseline sidecars for a sectioned spec."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world")

        spec = self._prepare_spec(self._make_sectioned_eval_spec())
        p1, p2, p3 = self._patches(
            spec, grading_report=self._make_grading_report(),
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(
                ["grade", "skill.md", "--output", str(output_file), "--baseline"]
            )

        assert rc == 0
        spec.runner.run_raw.assert_called_once()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"

        baseline_meta = json.loads((skill_dir / "baseline.json").read_text())
        assert baseline_meta["skill"] == "test-skill"
        assert baseline_meta["iteration"] == 1
        assert baseline_meta["exit_code"] == 0
        assert baseline_meta["input_tokens"] == 11
        assert baseline_meta["output_tokens"] == 22
        assert "output" in baseline_meta
        assert "duration_seconds" in baseline_meta

        baseline_assertions = json.loads(
            (skill_dir / "baseline_assertions.json").read_text()
        )
        assert baseline_assertions["skill"] == "test-skill"
        assert baseline_assertions["iteration"] == 1
        assert "results" in baseline_assertions

        baseline_extraction = json.loads(
            (skill_dir / "baseline_extraction.json").read_text()
        )
        assert "fields" in baseline_extraction

        baseline_grading = json.loads(
            (skill_dir / "baseline_grading.json").read_text()
        )
        assert baseline_grading["skill_name"] == "test-skill"

    def test_baseline_results_keyed_by_same_ids_as_primary(
        self, tmp_path, monkeypatch
    ):
        """Baseline assertion/field ids match primary spec ids."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world")

        spec = self._prepare_spec(self._make_sectioned_eval_spec())
        p1, p2, p3 = self._patches(
            spec, grading_report=self._make_grading_report(),
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(
                ["grade", "skill.md", "--output", str(output_file), "--baseline"]
            )

        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"

        baseline_assertions = json.loads(
            (skill_dir / "baseline_assertions.json").read_text()
        )
        ids = {r["id"] for r in baseline_assertions["results"]}
        assert "a.hello.v1" in ids

        baseline_extraction = json.loads(
            (skill_dir / "baseline_extraction.json").read_text()
        )
        assert "venues.primary.venue_name.v1" in baseline_extraction["fields"]

    def test_baseline_skips_extraction_when_no_sections(
        self, tmp_path, monkeypatch
    ):
        """No sections => no baseline_extraction.json; other 3 still written."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world")

        spec = self._prepare_spec(_make_eval_spec(sections=[]))
        p1, p2, p3 = self._patches(
            spec, grading_report=self._make_grading_report(),
        )
        with p1, p2, p3 as mock_extract:
            rc = main(
                ["grade", "skill.md", "--output", str(output_file), "--baseline"]
            )

        assert rc == 0
        mock_extract.assert_not_awaited()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert (skill_dir / "baseline.json").exists()
        assert (skill_dir / "baseline_assertions.json").exists()
        assert (skill_dir / "baseline_grading.json").exists()
        assert not (skill_dir / "baseline_extraction.json").exists()

    def test_grade_with_baseline_flag_writes_benchmark_sidecar(
        self, tmp_path, monkeypatch
    ):
        """#28 US-002: --baseline also writes benchmark.json with the delta."""
        monkeypatch.chdir(tmp_path)

        spec = self._prepare_live_spec()
        # side_effect: primary arm passes 1.0, baseline arm passes 0.5 →
        # delta.pass_rate == +0.50. Exercises real arithmetic in the
        # persisted benchmark.json.
        primary_report, baseline_report = self._pair_reports_c1c2()
        p1, p2, p3 = self._patches(
            spec,
            grading_side_effect=[primary_report, baseline_report],
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md", "--baseline"])

        assert rc == 0
        spec.runner.run_raw.assert_called_once()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        benchmark_path = skill_dir / "benchmark.json"
        assert benchmark_path.exists()
        benchmark = json.loads(benchmark_path.read_text())
        assert benchmark["schema_version"] == 1
        assert benchmark["skill_name"] == "test-skill"
        assert "run_summary" in benchmark
        run_summary = benchmark["run_summary"]
        assert "with_skill" in run_summary
        assert "without_skill" in run_summary
        assert "delta" in run_summary
        assert isinstance(run_summary["delta"]["pass_rate"], float)
        assert run_summary["delta"]["pass_rate"] == pytest.approx(0.5)

    def test_grade_without_baseline_flag_writes_no_benchmark_sidecar(
        self, tmp_path, monkeypatch
    ):
        """#28 US-002: benchmark.json is absent when --baseline is not passed."""
        monkeypatch.chdir(tmp_path)

        spec = self._prepare_live_spec()
        p1, p2, p3 = self._patches(
            spec, grading_report=self._make_grading_report(),
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md"])

        assert rc == 0
        spec.runner.run_raw.assert_not_called()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert not (skill_dir / "benchmark.json").exists()

    def test_grade_with_baseline_flag_prints_delta_block(
        self, tmp_path, monkeypatch, capsys
    ):
        """#28 US-003: --baseline prints the plain delta block on stdout."""
        monkeypatch.chdir(tmp_path)

        spec = self._prepare_live_spec()
        # Use side_effect so primary and baseline arms differ — exercises
        # the signed delta and the format specifiers in the delta block.
        primary_report, baseline_report = self._pair_reports_c1c2()
        p1, p2, p3 = self._patches(
            spec,
            grading_side_effect=[primary_report, baseline_report],
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md", "--baseline"])

        assert rc == 0
        out = capsys.readouterr().out
        # DEC-010 format: header then three labelled metric rows, each
        # with a per-arm mean pair.
        header_idx = out.find("baseline delta:")
        assert header_idx != -1
        pr_idx = out.find("pass_rate", header_idx)
        ts_idx = out.find("time_seconds", pr_idx)
        tk_idx = out.find("tokens", ts_idx)
        assert pr_idx != -1
        assert ts_idx != -1
        assert tk_idx != -1
        assert header_idx < pr_idx < ts_idx < tk_idx
        assert "with_skill" in out[header_idx:]
        assert "without_skill" in out[header_idx:]
        # Non-zero signed delta: pass_rate primary=1.0, baseline=0.5 →
        # delta=+0.50, rendered with explicit + sign per DEC-010.
        pr_line_end = out.find("\n", pr_idx)
        pr_line = out[pr_idx:pr_line_end]
        assert "+0.50" in pr_line
        # time_seconds: delta is sourced from SkillResult duration
        # (primary 2.5 vs baseline 0.75) → positive, rendered under
        # "{:+.1f}" so a literal "+" must appear in the value slot.
        ts_line_end = out.find("\n", ts_idx)
        ts_line = out[ts_idx:ts_line_end]
        assert "+" in ts_line

    def test_grade_with_baseline_and_json_routes_delta_block_to_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        """#28: --baseline + --json routes delta block to stderr so stdout
        stays parseable JSON (same pattern as --diff routing)."""
        monkeypatch.chdir(tmp_path)

        spec = self._prepare_live_spec()
        primary_report = make_grading_report(criterion="c1", passed=True, score=0.9)
        baseline_report = make_grading_report(
            criterion="c1", passed=True, score=0.9,
            duration_seconds=0.5,
            extra_results=[
                GradingResult(
                    criterion="c2", passed=False, score=0.3,
                    evidence="no", reasoning="no",
                ),
            ],
        )
        p1, p2, p3 = self._patches(
            spec,
            grading_side_effect=[primary_report, baseline_report],
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md", "--baseline", "--json"])

        assert rc == 0
        captured = capsys.readouterr()
        # stdout still carries the JSON grade payload — the delta block
        # must NOT leak into it or it would corrupt JSON consumers.
        assert "baseline delta:" not in captured.out
        # Slice from the first '{' to skip the unrelated progress
        # messages ("Running ...") that the runner prints ahead of the
        # JSON payload. What we care about is that the JSON body is
        # intact and the delta block isn't mixed in.
        brace = captured.out.find("{")
        assert brace != -1, "no JSON payload on stdout"
        payload = json.loads(captured.out[brace:])
        assert payload["skill"] == "test-skill"
        # stderr carries the delta block (routed there under --json
        # so stdout stays parseable by automated consumers).
        assert "baseline delta:" in captured.err
        assert "pass_rate" in captured.err
        assert "time_seconds" in captured.err
        assert "tokens" in captured.err

    def test_grade_without_baseline_flag_prints_no_delta_block(
        self, tmp_path, monkeypatch, capsys
    ):
        """#28 US-003: no --baseline => no 'baseline delta:' line on stdout."""
        monkeypatch.chdir(tmp_path)

        spec = self._prepare_live_spec()
        p1, p2, p3 = self._patches(
            spec, grading_report=self._make_grading_report(),
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "baseline delta:" not in out

    def test_run_baseline_phase_threads_cwd_to_run_raw(
        self, tmp_path, monkeypatch
    ):
        """FIX-15: _run_baseline_phase must stage inputs and pass cwd."""
        from clauditor.cli import _run_baseline_phase

        monkeypatch.chdir(tmp_path)
        # Real input file in cwd so schemas validation accepts it.
        (tmp_path / "fixture.txt").write_text("seed")
        eval_spec = _make_eval_spec(
            input_files=["fixture.txt"],
            sections=[],
        )
        spec = self._prepare_spec(eval_spec)
        grading_report = self._make_grading_report()
        skill_dir = tmp_path / "skill-dir"
        skill_dir.mkdir()

        with patch(
            "clauditor.quality_grader.grade_quality",
            new_callable=AsyncMock,
            return_value=grading_report,
        ):
            _run_baseline_phase(
                spec=spec,
                skill_dir=skill_dir,
                iteration=1,
                model="claude-sonnet-4-6",
            )

        spec.runner.run_raw.assert_called_once()
        call_kwargs = spec.runner.run_raw.call_args.kwargs
        assert "cwd" in call_kwargs
        assert call_kwargs["cwd"] == skill_dir / "baseline-run" / "inputs"
        # The staged input file exists at the expected path.
        assert (
            skill_dir / "baseline-run" / "inputs" / "fixture.txt"
        ).is_file()

    def test_run_baseline_phase_threads_env_and_timeout_overrides(
        self, tmp_path, monkeypatch
    ):
        """#64 QG: _run_baseline_phase must forward env_override and
        timeout_override to run_raw so --no-api-key and --timeout
        apply to both arms of a --baseline run."""
        from clauditor.cli import _run_baseline_phase

        monkeypatch.chdir(tmp_path)
        eval_spec = _make_eval_spec(sections=[])
        spec = self._prepare_spec(eval_spec)
        grading_report = self._make_grading_report()
        skill_dir = tmp_path / "skill-dir"
        skill_dir.mkdir()

        sentinel_env = {"PATH": "/fake"}
        with patch(
            "clauditor.quality_grader.grade_quality",
            new_callable=AsyncMock,
            return_value=grading_report,
        ):
            _run_baseline_phase(
                spec=spec,
                skill_dir=skill_dir,
                iteration=1,
                model="claude-sonnet-4-6",
                env_override=sentinel_env,
                timeout_override=77,
            )

        kwargs = spec.runner.run_raw.call_args.kwargs
        assert kwargs["env"] is sentinel_env
        assert kwargs["timeout"] == 77

    def test_run_baseline_phase_defaults_env_and_timeout_to_none(
        self, tmp_path, monkeypatch
    ):
        """Back-compat: _run_baseline_phase without overrides passes
        env=None and timeout=None (today's Popen-inherit-os.environ
        behavior)."""
        from clauditor.cli import _run_baseline_phase

        monkeypatch.chdir(tmp_path)
        eval_spec = _make_eval_spec(sections=[])
        spec = self._prepare_spec(eval_spec)
        grading_report = self._make_grading_report()
        skill_dir = tmp_path / "skill-dir"
        skill_dir.mkdir()

        with patch(
            "clauditor.quality_grader.grade_quality",
            new_callable=AsyncMock,
            return_value=grading_report,
        ):
            _run_baseline_phase(
                spec=spec,
                skill_dir=skill_dir,
                iteration=1,
                model="claude-sonnet-4-6",
            )

        kwargs = spec.runner.run_raw.call_args.kwargs
        assert kwargs["env"] is None
        assert kwargs["timeout"] is None

    # ---- #28 US-004: --min-baseline-delta gate ----

    def _make_report_with_pass_rate(
        self, num_passing: int, total: int
    ) -> GradingReport:
        """Build a GradingReport whose pass_rate == num_passing/total.

        Score is 0.9 on passing results and 0.8 on failing results so
        mean_score stays above the default 0.5 threshold — the primary
        report must still satisfy ``passed`` in tests where we want
        the gate (not the grade) to drive the exit code.
        """
        results = []
        for i in range(total):
            passed = i < num_passing
            results.append(
                GradingResult(
                    criterion=f"criterion-{i}",
                    passed=passed,
                    score=0.9 if passed else 0.8,
                    evidence="ok",
                    reasoning="ok",
                )
            )
        return GradingReport(
            skill_name="test-skill",
            model="claude-sonnet-4-6",
            results=results,
            duration_seconds=1.0,
            thresholds=GradeThresholds(),
            metrics={},
        )

    def _prepare_gate_spec(self):
        """Spec used by gate tests — non-``--output`` path with a real
        primary SkillResult (benchmark requires it to be non-None)."""
        return self._prepare_live_spec()

    @pytest.mark.parametrize(
        "primary_pass, baseline_pass, threshold, expected_rc, err_substrs",
        [
            # delta 0.50 >= threshold 0.40 → exit 0.
            pytest.param((2, 2), (1, 2), "0.4", 0, (), id="above-threshold"),
            # delta 0.30 < threshold 0.40 → exit 1 with message.
            pytest.param(
                (10, 10), (7, 10), "0.4", 1,
                ("baseline delta", "0.30", "0.40"),
                id="below-threshold",
            ),
            # DEC-009: observed delta 0.0 with threshold 0.0 → exit 0.
            pytest.param((2, 2), (2, 2), "0.0", 0, (), id="zero-equality"),
            # DEC-009: observed delta -0.05 with threshold 0.0 → exit 1.
            pytest.param((19, 20), (20, 20), "0.0", 1, (), id="zero-regression"),
        ],
    )
    def test_grade_min_baseline_delta_gate(
        self, tmp_path, monkeypatch, capsys,
        primary_pass, baseline_pass, threshold, expected_rc, err_substrs,
    ):
        """Primary/baseline pass-rates drive the gate exit code."""
        monkeypatch.chdir(tmp_path)
        spec = self._prepare_gate_spec()
        primary = self._make_report_with_pass_rate(*primary_pass)
        baseline = self._make_report_with_pass_rate(*baseline_pass)
        p1, p2, p3 = self._patches(
            spec, grading_side_effect=[primary, baseline],
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(
                [
                    "grade", "skill.md", "--baseline",
                    "--min-baseline-delta", threshold,
                ]
            )

        assert rc == expected_rc
        if err_substrs:
            err = capsys.readouterr().err
            for s in err_substrs:
                assert s in err

    def test_min_baseline_delta_without_baseline_errors(
        self, tmp_path, monkeypatch, capsys
    ):
        """--min-baseline-delta without --baseline → exit 2 + stderr."""
        monkeypatch.chdir(tmp_path)
        spec = self._prepare_spec(self._make_sectioned_eval_spec())

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--min-baseline-delta",
                    "0.5",
                ]
            )

        assert rc == 2
        err = capsys.readouterr().err
        assert "--min-baseline-delta requires --baseline" in err

    def test_min_baseline_delta_with_output_errors(
        self, tmp_path, monkeypatch, capsys
    ):
        """--min-baseline-delta under --output → exit 2 + stderr diagnostic.

        --output bypasses live subprocess metrics, so benchmark.delta cannot
        be computed; silently skipping the gate would be a correctness bug.
        """
        monkeypatch.chdir(tmp_path)
        spec = self._prepare_spec(self._make_sectioned_eval_spec())
        output_file = tmp_path / "canned.txt"
        output_file.write_text("skill output")

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--baseline",
                    "--min-baseline-delta",
                    "0.1",
                ]
            )

        assert rc == 2
        err = capsys.readouterr().err
        assert "--min-baseline-delta is incompatible with --output" in err

    def test_grade_without_min_baseline_delta_no_gate(
        self, tmp_path, monkeypatch
    ):
        """--baseline without --min-baseline-delta → gate not applied."""
        monkeypatch.chdir(tmp_path)
        spec = self._prepare_gate_spec()
        # Identical pass rates (delta = 0.0) — without --min-baseline-delta
        # no gate is applied, so the run still exits 0.
        primary = self._make_report_with_pass_rate(10, 10)
        baseline = self._make_report_with_pass_rate(10, 10)
        p1, p2, p3 = self._patches(
            spec, grading_side_effect=[primary, baseline],
            extraction_report=self._make_extraction_report(),
        )
        with p1, p2, p3:
            rc = main(["grade", "skill.md", "--baseline"])

        assert rc == 0

    def test_min_baseline_delta_argparse_rejects_out_of_range(self):
        """_unit_float rejects values outside [0.0, 1.0] via argparse."""
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "grade",
                    "skill.md",
                    "--baseline",
                    "--min-baseline-delta",
                    "1.5",
                ]
            )
        assert exc_info.value.code == 2


class TestCmdCompare:
    """Tests for the compare subcommand (US-003)."""

    def _make_saved_grade_json(
        self, tmp_path, name: str, *, criterion_passes: dict[str, bool]
    ):
        """Write a GradingReport to a .grade.json file and return the path."""
        path = tmp_path / f"{name}.grade.json"
        # _write_grading_json_dir writes under dir_path/grading.json; here
        # we want a flat .grade.json file so we inline a tiny variant.
        results = [
            GradingResult(
                criterion=c, passed=p, score=0.9 if p else 0.3,
                evidence="", reasoning="",
            )
            for c, p in criterion_passes.items()
        ]
        report = GradingReport(
            skill_name=name, model="test-model", results=results,
            duration_seconds=0.0, thresholds=GradeThresholds(), metrics={},
        )
        path.write_text(report.to_json())
        return path

    @pytest.mark.parametrize(
        "before_passes, after_passes, expected_rc, expected_out_substrs",
        [
            pytest.param(
                {"c1": True, "c2": True},
                {"c1": True, "c2": True},
                0,
                ("no flips",),
                id="no-flips",
            ),
            pytest.param(
                {"c1": True, "c2": True},
                {"c1": True, "c2": False},
                1,
                ("[REGRESSION]", "c2"),
                id="regression",
            ),
            pytest.param(
                {"c1": True, "c2": False},
                {"c1": True, "c2": True},
                0,
                ("[IMPROVEMENT]", "c2"),
                id="improvement",
            ),
        ],
    )
    def test_compare_two_grade_json(
        self, tmp_path, capsys,
        before_passes, after_passes, expected_rc, expected_out_substrs,
    ):
        """Compare of two .grade.json files yields flip outcomes + rc."""
        before = self._make_saved_grade_json(
            tmp_path, "before", criterion_passes=before_passes
        )
        after = self._make_saved_grade_json(
            tmp_path, "after", criterion_passes=after_passes
        )
        rc = main(["compare", str(before), str(after)])
        assert rc == expected_rc
        out = capsys.readouterr().out
        for s in expected_out_substrs:
            assert s in out

    def test_compare_two_txt_with_spec(self, tmp_path, capsys):
        """Two .txt files plus --spec re-grade both and diff Layer 1 results."""
        before_txt = tmp_path / "before.txt"
        after_txt = tmp_path / "after.txt"
        before_txt.write_text("nothing matching")
        after_txt.write_text("hello world")

        eval_spec = _make_eval_spec(
            assertions=[{"type": "contains", "needle": "hello"}]
        )
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(
                [
                    "compare",
                    str(before_txt),
                    str(after_txt),
                    "--spec",
                    "skill.md",
                ]
            )

        assert rc == 0
        out = capsys.readouterr().out
        assert "[IMPROVEMENT]" in out

    def test_compare_txt_without_spec_errors(self, tmp_path, capsys):
        """.txt diff without --spec errors with a clear message."""
        before_txt = tmp_path / "before.txt"
        after_txt = tmp_path / "after.txt"
        before_txt.write_text("a")
        after_txt.write_text("b")
        rc = main(["compare", str(before_txt), str(after_txt)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--spec" in err

    def test_compare_mixed_extensions_errors(self, tmp_path, capsys):
        """Mixed .txt + .grade.json inputs error before loading."""
        before_txt = tmp_path / "before.txt"
        before_txt.write_text("something")
        after = self._make_saved_grade_json(
            tmp_path, "after", criterion_passes={"c1": True}
        )
        rc = main(["compare", str(before_txt), str(after)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Mismatched" in err


def _write_grading_json_dir(dir_path, criterion_passes):
    """Write a grading.json with the given criterion pass map."""
    dir_path.mkdir(parents=True, exist_ok=True)
    results = [
        GradingResult(
            criterion=c,
            passed=p,
            score=0.9 if p else 0.3,
            evidence="",
            reasoning="",
        )
        for c, p in criterion_passes.items()
    ]
    report = GradingReport(
        skill_name=dir_path.name,
        model="test-model",
        results=results,
        duration_seconds=0.0,
        thresholds=GradeThresholds(),
        metrics={},
    )
    (dir_path / "grading.json").write_text(report.to_json())
    return dir_path


class TestCmdCompareIterationDirs:
    """compare auto-detects iteration directory layouts (clauditor-yng.6)."""

    def test_compare_two_iteration_dirs(self, tmp_path, capsys):
        before_dir = _write_grading_json_dir(
            tmp_path / ".clauditor" / "iteration-1" / "foo",
            {"c1": True, "c2": True},
        )
        after_dir = _write_grading_json_dir(
            tmp_path / ".clauditor" / "iteration-2" / "foo",
            {"c1": True, "c2": False},
        )
        rc = main(["compare", str(before_dir), str(after_dir)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "[REGRESSION]" in out
        assert "c2" in out

    def test_compare_dir_missing_grading_json_errors(self, tmp_path, capsys):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        rc = main(["compare", str(empty_dir), str(other)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no grading.json found" in err


class TestCmdCompareNumericRefs:
    """compare --skill/--from/--to numeric ref form (clauditor-yng.6)."""

    def test_compare_numeric_refs(self, tmp_path, monkeypatch, capsys):
        # Set up a fake repo root marker so resolve_clauditor_dir() finds it.
        (tmp_path / ".git").mkdir()
        _write_grading_json_dir(
            tmp_path / ".clauditor" / "iteration-1" / "foo",
            {"c1": True, "c2": True},
        )
        _write_grading_json_dir(
            tmp_path / ".clauditor" / "iteration-2" / "foo",
            {"c1": True, "c2": False},
        )
        monkeypatch.chdir(tmp_path)
        rc = main(["compare", "--skill", "foo", "--from", "1", "--to", "2"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "[REGRESSION]" in out
        assert "c2" in out

    @pytest.mark.parametrize(
        "argv_tail, cd_git, err_substrs",
        [
            # Missing --to
            pytest.param(
                ["compare", "--skill", "foo", "--from", "1"],
                False,
                ("all be provided together",),
                id="partial-args",
            ),
            # No positional, no --skill
            pytest.param(
                ["compare"],
                False,
                ("positional paths", "--skill"),  # either/or is fine
                id="missing-positional",
            ),
            # --skill with path-traversal name
            pytest.param(
                ["compare", "--skill", "../evil", "--from", "1", "--to", "2"],
                True,
                ("invalid skill name",),
                id="path-traversal-skill",
            ),
        ],
    )
    def test_compare_numeric_ref_errors(
        self, tmp_path, monkeypatch, capsys, argv_tail, cd_git, err_substrs
    ):
        """Numeric-ref CLI errors all surface as rc=2 with clear stderr."""
        if cd_git:
            (tmp_path / ".git").mkdir()
            monkeypatch.chdir(tmp_path)
        rc = main(argv_tail)
        assert rc == 2
        err = capsys.readouterr().err
        # Accept if any substring appears (some tests use "A or B" semantics).
        assert any(s in err for s in err_substrs), err

    def test_compare_positional_and_numeric_conflict_errors(
        self, tmp_path, capsys
    ):
        before = tmp_path / "before.grade.json"
        after = tmp_path / "after.grade.json"
        report = GradingReport(
            skill_name="x",
            model="m",
            results=[],
            duration_seconds=0.0,
            thresholds=GradeThresholds(),
            metrics={},
        )
        before.write_text(report.to_json())
        after.write_text(report.to_json())
        rc = main(
            [
                "compare",
                str(before),
                str(after),
                "--skill", "foo",
                "--from", "1", "--to", "2",
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "cannot combine" in err or "--skill" in err

    def test_compare_mismatched_file_types_exits_2(self, tmp_path, capsys):
        """A .txt and a .grade.json pair surfaces as rc=2 with kind mismatch."""
        txt = tmp_path / "before.txt"
        txt.write_text("some output")
        gj = tmp_path / "after.grade.json"
        report = GradingReport(
            skill_name="x", model="m", results=[],
            duration_seconds=0.0, thresholds=GradeThresholds(), metrics={},
        )
        gj.write_text(report.to_json())
        rc = main(["compare", str(txt), str(gj)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Mismatched file types" in err

    def test_compare_unsupported_file_type_exits_2(self, tmp_path, capsys):
        """A pair with unsupported extensions (e.g. .json, .log) rc=2."""
        a = tmp_path / "a.log"
        b = tmp_path / "b.log"
        a.write_text("x")
        b.write_text("y")
        rc = main(["compare", str(a), str(b)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Unsupported file type" in err



def _make_blind_report(**overrides):
    """Build a BlindReport for compare --blind tests."""
    from clauditor.quality_grader import BlindReport

    defaults = dict(
        preference="b",
        confidence=0.8,
        score_a=0.72,
        score_b=0.85,
        reasoning="After is clearer and more complete.",
        model="claude-sonnet-4-6",
        position_agreement=True,
    )
    defaults.update(overrides)
    return BlindReport(**defaults)


def _write_pair(tmp_path, before_text="before content", after_text="after content"):
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    before.write_text(before_text)
    after.write_text(after_text)
    return before, after


def _blind_argv(before, after):
    return [
        "compare",
        str(before),
        str(after),
        "--spec",
        "skill.md",
        "--blind",
    ]


class TestCmdCompareBlind:
    """--blind flag on compare subcommand (clauditor-dmw.3, #24 US-003)."""

    @pytest.mark.parametrize(
        "report_overrides, expected_substr",
        [
            pytest.param(
                dict(preference="b", reasoning="After is clearer."),
                ("preference: AFTER", "0.80", "After is clearer."),
                id="after-branch",
            ),
            pytest.param(
                dict(preference="a"),
                ("preference: BEFORE",),
                id="before-branch",
            ),
            pytest.param(
                dict(preference="tie"),
                ("preference: TIE",),
                id="tie",
            ),
            pytest.param(
                dict(position_agreement=False),
                ("position agreement: no",),
                id="position-bias",
            ),
        ],
    )
    def test_blind_outcomes_render(
        self, tmp_path, capsys, report_overrides, expected_substr
    ):
        """The BlindReport fields drive the stdout rendering."""
        before, after = _write_pair(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec(user_prompt="Write a hello world"))
        report = _make_blind_report(**report_overrides)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.blind_compare",
                new=AsyncMock(return_value=report),
            ),
        ):
            rc = main(_blind_argv(before, after))
        assert rc == 0
        out = capsys.readouterr().out
        for s in expected_substr:
            assert s in out

    def test_compare_blind_reads_both_txt_files(self, tmp_path):
        """Pair of .txt files are forwarded positionally to blind_compare."""
        before, after = _write_pair(
            tmp_path,
            before_text="UNIQUE_BEFORE_CONTENT_XYZ",
            after_text="UNIQUE_AFTER_CONTENT_XYZ",
        )
        spec = _make_spec(eval_spec=_make_eval_spec(user_prompt="Do a thing"))
        mock_blind = AsyncMock(return_value=_make_blind_report())
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch("clauditor.quality_grader.blind_compare", new=mock_blind),
        ):
            rc = main(_blind_argv(before, after))
        assert rc == 0
        assert mock_blind.call_count == 1
        args = mock_blind.call_args.args
        # user_prompt, output_a, output_b, rubric_hint
        assert args[0] == "Do a thing"
        assert args[1] == "UNIQUE_BEFORE_CONTENT_XYZ"
        assert args[2] == "UNIQUE_AFTER_CONTENT_XYZ"

    # ---- error paths --------------------------------------------------

    def test_compare_blind_requires_spec(self, tmp_path, capsys):
        before, after = _write_pair(tmp_path)
        rc = main(["compare", str(before), str(after), "--blind"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--spec" in err
        assert "blind" in err.lower()

    def test_compare_blind_with_iteration_refs_errors(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        rc = main(
            [
                "compare", "--skill", "foo",
                "--from", "3", "--to", "4",
                "--spec", "skill.md", "--blind",
            ]
        )
        assert rc == 2
        assert "file-pair form" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "before_content, after_content, expected_substrs",
        [
            pytest.param(
                ("text", "ok"),
                "_MISSING_",
                ("does not exist", "after.txt"),
                id="missing-file",
            ),
            pytest.param(
                ("bytes", b"\xff\xfe\xfd"),
                ("text", "ok"),
                ("UTF-8", "before.txt"),
                id="non-utf8-before",
            ),
            pytest.param(
                ("text", "before content"),
                ("bytes", b"\xff\xfe\xfd"),
                ("UTF-8", "after.txt"),
                id="non-utf8-after",
            ),
        ],
    )
    def test_compare_blind_file_errors(
        self, tmp_path, capsys, before_content, after_content, expected_substrs
    ):
        """rc=2 for missing / non-UTF-8 inputs, with file name in stderr."""
        def _mk(name, content):
            p = tmp_path / name
            if content == "_MISSING_":
                return p  # intentionally not created
            kind, data = content
            if kind == "bytes":
                p.write_bytes(data)
            else:
                p.write_text(data)
            return p

        before = _mk("before.txt", before_content)
        after = _mk("after.txt", after_content)
        spec = _make_spec(eval_spec=_make_eval_spec(user_prompt="Write a hello world"))
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(_blind_argv(before, after))
        assert rc == 2
        err = capsys.readouterr().err
        for s in expected_substrs:
            assert s in err

    def test_compare_blind_no_eval_spec_errors(self, tmp_path, capsys):
        # Covers the "No eval spec found" branch (cli.py lines 745-750).
        before, after = _write_pair(tmp_path)
        spec = _make_spec(eval_spec=None)
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(_blind_argv(before, after))
        assert rc == 2
        assert "No eval spec" in capsys.readouterr().err

    def test_compare_blind_empty_user_prompt_errors(self, tmp_path, capsys):
        # Covers the whitespace-only user_prompt path.
        before, after = _write_pair(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec(user_prompt="   \n"))
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(_blind_argv(before, after))
        assert rc == 2
        err = capsys.readouterr().err
        assert "user_prompt" in err
        # Fail-fast: the "Running blind A/B judge" progress line must NOT
        # appear when validation fails.
        assert "Running blind A/B judge" not in err

    def test_compare_blind_without_positional_args_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        """--blind with no positional file args exits 2 with file-pair hint."""
        # No before/after; not numeric-form either. Must hit the
        # "requires file-pair form" branch (cli/compare.py:277-283).
        monkeypatch.chdir(tmp_path)
        rc = main(["compare", "--blind", "--spec", "skill.md"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "file-pair form" in err

    def test_compare_blind_with_grade_json_pair_exits_2(self, tmp_path, capsys):
        """--blind with .grade.json pair (not .txt) exits 2."""
        # Both positional args are .grade.json — hit the file-kind check
        # at cli/compare.py:286-295 that rejects non-txt pairs under --blind.
        gj_a = tmp_path / "a.grade.json"
        gj_b = tmp_path / "b.grade.json"
        report = GradingReport(
            skill_name="x", model="m", results=[],
            duration_seconds=0.0, thresholds=GradeThresholds(), metrics={},
        )
        gj_a.write_text(report.to_json())
        gj_b.write_text(report.to_json())
        rc = main(
            ["compare", str(gj_a), str(gj_b), "--spec", "skill.md", "--blind"]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "file-pair form" in err


class TestCmdGradeCompareFlagRemoved:
    """US-003: the legacy --compare flag on grade is gone."""

    def test_grade_compare_flag_rejected(self, capsys):
        import pytest as _pytest

        with _pytest.raises(SystemExit):
            main(["grade", "skill.md", "--compare"])
        err = capsys.readouterr().err
        assert "--compare" in err or "unrecognized" in err


class TestCmdTriggers:
    """Tests for the triggers subcommand."""

    def _make_trigger_report(self, passed=True):
        return TriggerReport(
            skill_name="test-skill",
            skill_description="A test skill",
            model="claude-sonnet-4-6",
            results=[
                TriggerResult(
                    query="test query",
                    expected_trigger=True,
                    predicted_trigger=True if passed else False,
                    passed=passed,
                    confidence=0.95,
                    reasoning="Matches skill intent",
                ),
            ],
        )

    def test_triggers_happy_path(self, capsys):
        """Runs trigger testing and prints report."""
        eval_spec = _make_eval_spec(
            trigger_tests=TriggerTests(
                should_trigger=["find activities"],
                should_not_trigger=["weather today"],
            )
        )
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_trigger_report(passed=True)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.triggers.test_triggers",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["triggers", "skill.md"])

        assert rc == 0

    def test_triggers_dry_run(self, capsys):
        """--dry-run prints prompts and returns 0."""
        eval_spec = _make_eval_spec(
            trigger_tests=TriggerTests(
                should_trigger=["find activities"],
                should_not_trigger=["weather today"],
            )
        )
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["triggers", "skill.md", "--dry-run"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Model:" in out
        assert "should_trigger" in out
        assert "should_not_trigger" in out

    def test_triggers_no_model_exits_2(self, capsys):
        """Missing grading_model (neither --model nor spec) exits 2."""
        eval_spec = _make_eval_spec(
            grading_model="",
            trigger_tests=TriggerTests(
                should_trigger=["q"],
                should_not_trigger=[],
            ),
        )
        spec = _make_spec(eval_spec=eval_spec)
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["triggers", "skill.md"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "No grading model specified" in err
        assert "--model" in err

    def test_triggers_missing_trigger_tests_exits_1(self, capsys):
        """Non-dry-run with no trigger_tests on the spec must exit 1 with a
        clear error, matching the --dry-run branch. Otherwise CI cannot
        distinguish 'passed zero triggers' from 'spec forgot trigger_tests'.
        """
        eval_spec = _make_eval_spec(trigger_tests=None)
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["triggers", "skill.md"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "No trigger_tests defined" in err


class TestCmdInit:
    """Tests for the init subcommand."""

    def test_init_creates_file(self, tmp_path):
        """Creates eval.json with starter content."""
        skill_path = tmp_path / "my-skill.md"
        skill_path.write_text("# My Skill")

        rc = main(["init", str(skill_path)])

        assert rc == 0
        eval_path = tmp_path / "my-skill.eval.json"
        assert eval_path.exists()
        data = json.loads(eval_path.read_text())
        assert data["skill_name"] == "my-skill"
        assert "assertions" in data
        assert "grading_criteria" in data

    def test_init_existing_no_force(self, tmp_path, capsys):
        """Returns 1 when eval.json already exists without --force."""
        skill_path = tmp_path / "my-skill.md"
        skill_path.write_text("# My Skill")
        eval_path = tmp_path / "my-skill.eval.json"
        eval_path.write_text("{}")

        rc = main(["init", str(skill_path)])

        assert rc == 1
        assert "already exists" in capsys.readouterr().err

    def test_init_force_overwrites(self, tmp_path):
        """--force overwrites existing eval.json."""
        skill_path = tmp_path / "my-skill.md"
        skill_path.write_text("# My Skill")
        eval_path = tmp_path / "my-skill.eval.json"
        eval_path.write_text("{}")

        rc = main(["init", str(skill_path), "--force"])

        assert rc == 0
        data = json.loads(eval_path.read_text())
        assert data["skill_name"] == "my-skill"

    def test_init_modern_layout_uses_parent_dir_name(self, tmp_path):
        """Modern layout (``<dir>/SKILL.md``) derives name from parent dir
        via frontmatter ``name:`` — not the ``"SKILL"`` file stem."""
        skill_dir = tmp_path / ".claude" / "skills" / "foo"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\n"
            "name: foo\n"
            "description: A test skill\n"
            "---\n"
            "\n"
            "# Body\n"
        )

        rc = main(["init", str(skill_path)])

        assert rc == 0
        eval_path = skill_dir / "SKILL.eval.json"
        assert eval_path.exists()
        data = json.loads(eval_path.read_text())
        assert data["skill_name"] == "foo"
        assert data["description"] == "Eval spec for /foo"

    def test_init_missing_skill_file(self, tmp_path, capsys):
        """Missing skill file exits 1 with a descriptive stderr error."""
        skill_path = tmp_path / "does-not-exist.md"

        rc = main(["init", str(skill_path)])

        assert rc == 1
        assert "skill file not found" in capsys.readouterr().err

    def test_init_unreadable_skill_file(self, tmp_path, capsys):
        """OSError while reading the skill file exits 1 with an error message
        that includes the underlying exception string."""
        skill_path = tmp_path / "foo.md"
        skill_path.write_text("# foo")

        with patch(
            "clauditor.cli.init.Path.read_text",
            side_effect=OSError("Permission denied"),
        ):
            rc = main(["init", str(skill_path)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot read" in err
        assert "Permission denied" in err

    def test_init_non_utf8_skill_file(self, tmp_path, capsys):
        """UnicodeDecodeError (non-UTF-8 skill file) exits 1 with a clean
        message instead of an uncaught traceback. ``read_text`` is called
        with ``encoding='utf-8'`` and ``UnicodeDecodeError`` is a
        ``ValueError`` subclass (not ``OSError``), so the except clause
        must catch both explicitly."""
        skill_path = tmp_path / "bogus.md"
        # Raw bytes that don't decode as UTF-8 (a Latin-1 é followed by
        # high-range bytes).
        skill_path.write_bytes(b"\xc3\x28\xa0\xa1")

        rc = main(["init", str(skill_path)])

        assert rc == 1
        err = capsys.readouterr().err
        assert f"cannot read {skill_path}" in err
        # The underlying codec error is appended to the message.
        assert "utf-8" in err or "codec" in err

    def test_init_frontmatter_disagreement_silent(self, tmp_path, capsys):
        """When frontmatter ``name:`` disagrees with the filesystem-derived
        name, frontmatter wins. Per DEC-008 of
        ``plans/super/71-agentskills-lint.md``, ``derive_skill_name`` no
        longer emits a stderr warning for this case — the equivalent
        ``AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`` conformance code moves
        to ``clauditor.conformance.check_conformance``, wired in by
        US-006."""
        skill_dir = tmp_path / ".claude" / "skills" / "foo"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\n"
            "name: bar\n"
            "description: Disagreement test\n"
            "---\n"
            "\n"
            "# Body\n"
        )

        rc = main(["init", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "clauditor.spec:" not in captured.err
        assert "frontmatter name" not in captured.err
        eval_path = skill_dir / "SKILL.eval.json"
        data = json.loads(eval_path.read_text())
        assert data["skill_name"] == "bar"
        assert data["description"] == "Eval spec for /bar"

    def test_init_generated_spec_uses_per_type_keys(self, tmp_path):
        """Regression (DEC-001/DEC-002 of #67): generated eval.json loads
        via ``EvalSpec.from_file`` (no legacy ``value`` keys) and uses
        native JSON ints for counts/lengths — the starter scaffold must
        stay in lockstep with the per-type validator.
        """
        from clauditor.schemas import EvalSpec

        skill_path = tmp_path / "my-skill.md"
        skill_path.write_text("# My Skill")

        rc = main(["init", str(skill_path)])
        assert rc == 0

        eval_path = tmp_path / "my-skill.eval.json"
        # Substring check: the generated file must not contain any
        # legacy ``"value":`` assertion key. Cheap guard against a
        # future regression that reverts the scaffold.
        raw = eval_path.read_text(encoding="utf-8")
        assert '"value":' not in raw, (
            "generated eval.json must not contain legacy 'value' keys"
        )

        # Must load cleanly via EvalSpec.from_file — the per-type
        # required-key + type-check validator from US-001 rejects the
        # legacy shape at load time.
        spec = EvalSpec.from_file(eval_path)
        assert spec.skill_name == "my-skill"
        # Starter scaffold ships 4 assertions per DEC-001 mapping.
        assert len(spec.assertions) == 4

        # Each assertion uses the per-type semantic key (DEC-001)
        # with native JSON ints for counts/lengths (DEC-002). A
        # future scaffold edit that swaps a key name (``length`` →
        # ``len``) or reverts to stringly-typed ints (``500`` →
        # ``"500"``) must fail this test, not silently pass.
        by_id = {a["id"]: a for a in spec.assertions}
        assert by_id["min_length_500"]["length"] == 500
        assert isinstance(by_id["min_length_500"]["length"], int)
        assert by_id["has_urls_3"]["count"] == 3
        assert isinstance(by_id["has_urls_3"]["count"], int)
        assert by_id["has_entries_3"]["count"] == 3
        assert isinstance(by_id["has_entries_3"]["count"], int)
        assert by_id["no_error"]["needle"] == "Error"


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """Scratch project root + fake installed-package skill dir.

    Creates a ``.git`` marker at ``tmp_path`` so
    :func:`clauditor.setup.find_project_root` resolves, and plants a
    sentinel SKILL.md inside a fake ``site-packages/clauditor/skills/
    clauditor/`` tree whose location is returned by the monkeypatched
    ``clauditor.cli.setup.files`` callable.
    """
    # Project root with a .git marker.
    (tmp_path / ".git").mkdir()
    # Fake installed-package skill tree.
    pkg_skill = tmp_path / "fake-pkg" / "clauditor" / "skills" / "clauditor"
    pkg_skill.mkdir(parents=True)
    (pkg_skill / "SKILL.md").write_text("# sentinel\n")
    monkeypatch.chdir(tmp_path)

    # Replace cli.files so `files("clauditor") / "skills" / "clauditor"`
    # lands in the fake tree.
    def fake_files(pkg_name):
        assert pkg_name == "clauditor"
        return tmp_path / "fake-pkg" / "clauditor"

    # Patch both seams: cmd_setup calls files() from cli.setup, and
    # cmd_doctor calls files() from cli.doctor — tests that chain
    # setup fixtures into doctor assertions need both.
    monkeypatch.setattr("clauditor.cli.setup.files", fake_files)
    monkeypatch.setattr("clauditor.cli.doctor.files", fake_files)
    return {
        "project_root": tmp_path,
        "pkg_skill_root": pkg_skill,
        "dest": tmp_path / ".claude" / "skills" / "clauditor",
    }


class TestCmdSetup:
    """Tests for the ``clauditor setup`` subcommand."""

    def test_setup_creates_symlink_when_absent(self, setup_env, capsys):
        """Dest doesn't exist → creates symlink, exit 0, stdout ok."""
        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        assert not dest.exists()

        rc = main(["setup"])

        assert rc == 0
        assert dest.is_symlink()
        # Resolved target should match the bundled pkg skill root.
        assert dest.resolve() == pkg_skill.resolve()
        out = capsys.readouterr().out
        assert "Installed /clauditor" in out
        assert str(dest) in out

    def test_setup_noop_when_already_our_symlink(self, setup_env, capsys):
        """Dest is symlink → pkg_skill → 'already installed', exit 0."""
        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        import os as _os
        _os.symlink(pkg_skill, dest)

        rc = main(["setup"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "already installed" in out

    def test_setup_refuses_existing_regular_file(self, setup_env, capsys):
        """Dest is regular file → exit 1, stderr contains 'use --force'."""
        dest = setup_env["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("not a symlink\n")

        rc = main(["setup"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "use --force" in err
        assert "regular file" in err

    def test_setup_refuses_existing_real_dir(self, setup_env, capsys):
        """Real directory at dest (not a symlink) without --force → exit 1."""
        dest = setup_env["dest"]
        dest.mkdir(parents=True)
        (dest / "user-authored.txt").write_text("not ours\n")

        rc = main(["setup"])

        assert rc == 1
        assert dest.is_dir()  # preserved — no silent clobber
        assert not dest.is_symlink()
        err = capsys.readouterr().err
        assert "exists (directory)" in err
        assert "--force" in err

    def test_setup_refuses_wrong_symlink(self, setup_env, capsys, tmp_path):
        """Dest is symlink → elsewhere → exit 1."""
        dest = setup_env["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        import os as _os
        _os.symlink(elsewhere, dest)

        rc = main(["setup"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "pointing elsewhere" in err

    def test_setup_force_replaces_regular_file(self, setup_env, capsys):
        """Regular file + --force → replaced with symlink, exit 0."""
        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("not a symlink\n")

        rc = main(["setup", "--force"])

        assert rc == 0
        assert dest.is_symlink()
        assert dest.resolve() == pkg_skill.resolve()
        assert "Installed /clauditor" in capsys.readouterr().out

    def test_setup_force_replaces_real_dir(self, setup_env, capsys):
        """Regular directory + --force → replaced with symlink, exit 0."""
        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        dest.mkdir(parents=True)
        (dest / "some-file.txt").write_text("junk\n")

        rc = main(["setup", "--force"])

        assert rc == 0
        assert dest.is_symlink()
        assert dest.resolve() == pkg_skill.resolve()
        assert "Installed /clauditor" in capsys.readouterr().out

    def test_setup_unlink_removes_our_symlink(self, setup_env, capsys):
        """--unlink on our symlink → removed, exit 0."""
        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        import os as _os
        _os.symlink(pkg_skill, dest)

        rc = main(["setup", "--unlink"])

        assert rc == 0
        assert not dest.exists()
        assert not dest.is_symlink()
        out = capsys.readouterr().out
        assert "Removed .claude/skills/clauditor" in out

    def test_setup_unlink_refuses_non_symlink(self, setup_env, capsys):
        """--unlink on regular file → exit 1, stderr 'not a symlink'."""
        dest = setup_env["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("not a symlink\n")

        rc = main(["setup", "--unlink"])

        assert rc == 1
        # File still there, not removed.
        assert dest.exists()
        err = capsys.readouterr().err
        assert "not a symlink" in err

    def test_setup_unlink_noop_when_absent(self, setup_env, capsys):
        """--unlink with nothing present → exit 0, 'not installed' info."""
        dest = setup_env["dest"]
        assert not dest.exists()

        rc = main(["setup", "--unlink"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "not installed" in out

    def test_setup_unlink_refuses_wrong_target_symlink(self, setup_env, capsys):
        """--unlink on a symlink pointing elsewhere → exit 1, preserved."""
        dest = setup_env["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        elsewhere = setup_env["project_root"] / "other-dir"
        elsewhere.mkdir()
        import os as _os

        _os.symlink(elsewhere, dest)

        rc = main(["setup", "--unlink"])

        assert rc == 1
        # Symlink preserved — we do NOT silently delete user-authored
        # symlinks just because `--unlink` was passed (DEC-009).
        assert dest.is_symlink()
        assert dest.resolve() == elsewhere.resolve()
        err = capsys.readouterr().err
        assert "target does not match" in err or "does not match" in err

    def test_setup_retries_on_race_then_succeeds(
        self, setup_env, monkeypatch, capsys
    ):
        """FileExistsError on first os.symlink → re-plan → success (DEC-010)."""
        from clauditor.cli import setup as cli_module

        call_count = {"n": 0}
        original_install = cli_module._install_symlink

        def racy_install(dest, pkg_skill_root):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FileExistsError("simulated TOCTOU race")
            return original_install(dest, pkg_skill_root)

        monkeypatch.setattr("clauditor.cli.setup._install_symlink", racy_install)

        rc = main(["setup"])

        assert rc == 0
        assert call_count["n"] == 2  # first raced, second succeeded
        dest = setup_env["dest"]
        assert dest.is_symlink()
        assert "Installed /clauditor" in capsys.readouterr().out

    def test_setup_exits_1_after_two_race_attempts(
        self, setup_env, monkeypatch, capsys
    ):
        """Persistent FileExistsError → exit 1 with concurrent-mod error."""

        def always_race(dest, pkg_skill_root):
            raise FileExistsError("persistent race")

        monkeypatch.setattr("clauditor.cli.setup._install_symlink", always_race)

        rc = main(["setup"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "concurrent modification" in err

    def test_setup_rejects_zip_style_install(
        self, tmp_path, monkeypatch, capsys
    ):
        """files() returning a non-Path (e.g. zip-style Traversable) → exit 2.
        Symlinking into a zip extraction would leave a dangling pointer when
        the as_file context exits, so we refuse up front.
        """
        # Fake cwd with project marker so project-root resolution succeeds
        # if we ever reach it (we shouldn't — the early-return fires first).
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)

        class FakeTraversable:
            """Not a Path subclass — simulates importlib.resources returning
            a MultiplexedPath / zipfile.Path for a zipped install.
            """

            def __truediv__(self, _other):
                return self

        monkeypatch.setattr(
            "clauditor.cli.setup.files", lambda _pkg: FakeTraversable()
        )

        rc = main(["setup"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "stable filesystem path" in err
        assert "zip" in err.lower() or "pex" in err.lower()

    def test_remove_existing_tolerates_missing_path(self, tmp_path):
        """_remove_existing on a path that does not exist is a no-op
        (exotic/vanished type falls through to unlink(missing_ok=True)).
        """
        from clauditor.cli import setup as cli_module

        ghost = tmp_path / "never-existed"
        assert not ghost.exists()

        # Must not raise — exotic/missing type falls through to the
        # missing-ok unlink branch.
        cli_module._remove_existing(ghost)

        assert not ghost.exists()

    def test_setup_unlink_race_target_already_gone(
        self, setup_env, monkeypatch, capsys
    ):
        """--unlink where the symlink was removed before our unlink call →
        treat as success (user wanted it gone, it's gone). Symmetric with
        the install-side retry loop (DEC-010).
        """
        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        import os as _os

        _os.symlink(pkg_skill, dest)  # plan_setup sees it
        # Race: remove the symlink right before cmd_setup dispatches
        # the REMOVE_SYMLINK branch. We simulate this by chaining the
        # side effect into the real dispatch via monkeypatch.
        real_plan = clauditor_setup.plan_setup

        def racy_plan(cwd, pkg_skill_root, *, force, unlink):
            action = real_plan(cwd, pkg_skill_root, force=force, unlink=unlink)
            if action is clauditor_setup.SetupAction.REMOVE_SYMLINK:
                dest.unlink()  # concurrent peer gets there first
            return action

        monkeypatch.setattr("clauditor.cli.setup.setup_module.plan_setup", racy_plan)

        rc = main(["setup", "--unlink"])

        assert rc == 0
        assert "Removed .claude/skills/clauditor" in capsys.readouterr().out

    def test_setup_errors_when_no_project_root(self, tmp_path, monkeypatch, capsys):
        """No .git, no .claude → exit 2, stderr 'no project root'."""
        # Fake package skill tree outside any git checkout.
        pkg_skill = tmp_path / "fake-pkg" / "clauditor" / "skills" / "clauditor"
        pkg_skill.mkdir(parents=True)
        (pkg_skill / "SKILL.md").write_text("# sentinel\n")

        # Run from a subdir with no project markers in its ancestry.
        subdir = tmp_path / "nope"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        def fake_files(pkg_name):
            assert pkg_name == "clauditor"
            return tmp_path / "fake-pkg" / "clauditor"

        monkeypatch.setattr("clauditor.cli.setup.files", fake_files)

        rc = main(["setup"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "no project root" in err

    def test_setup_project_dir_override(self, tmp_path, monkeypatch, capsys):
        """--project-dir overrides cwd for project-root resolution."""
        # Project root for the override.
        override = tmp_path / "override-proj"
        override.mkdir()
        (override / ".git").mkdir()
        # Fake package tree.
        pkg_skill = tmp_path / "fake-pkg" / "clauditor" / "skills" / "clauditor"
        pkg_skill.mkdir(parents=True)
        (pkg_skill / "SKILL.md").write_text("# sentinel\n")

        # cwd is a markerless subdir — without --project-dir it would fail.
        sub = tmp_path / "elsewhere"
        sub.mkdir()
        monkeypatch.chdir(sub)

        def fake_files(pkg_name):
            assert pkg_name == "clauditor"
            return tmp_path / "fake-pkg" / "clauditor"

        monkeypatch.setattr("clauditor.cli.setup.files", fake_files)

        rc = main(["setup", "--project-dir", str(override)])

        assert rc == 0
        dest = override / ".claude" / "skills" / "clauditor"
        assert dest.is_symlink()
        assert dest.resolve() == pkg_skill.resolve()
        assert "Installed /clauditor" in capsys.readouterr().out


def _make_sections():
    """Create sample sections for Layer 2 testing."""
    return [
        SectionRequirement(
            name="Results",
            tiers=[
                TierRequirement(
                    label="default",
                    min_entries=2,
                    fields=[
                        FieldRequirement(name="name", required=True),
                        FieldRequirement(name="address", required=True),
                    ],
                )
            ],
        )
    ]


class TestCmdExtract:
    """Tests for the extract subcommand."""

    def _make_extraction_results(self, passed=True):
        return AssertionSet(
            results=[
                AssertionResult(
                    name="section:Results:count",
                    passed=passed,
                    message="Section 'Results' has 3 entries (need >=2)",
                    kind="count",
                ),
                AssertionResult(
                    name="section:Results[0].name",
                    passed=passed,
                    message=(
                        "Field present"
                        if passed
                        else "Missing required field 'name'"
                    ),
                    kind="presence",
                    evidence="Test Place" if passed else None,
                ),
            ]
        )

    def test_extract_dry_run(self, capsys):
        """--dry-run prints prompt and returns 0 without API calls."""
        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["extract", "skill.md", "--dry-run"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Model:" in out
        assert "Prompt:" in out
        assert "Results" in out

    def test_extract_no_sections_error(self, capsys):
        """Returns 1 when no sections defined in eval spec."""
        eval_spec = _make_eval_spec(sections=[])
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["extract", "skill.md"])

        assert rc == 1
        assert "No sections defined" in capsys.readouterr().err

    def test_extract_output_file_missing_exits_2(self, capsys, tmp_path):
        """--output pointing at a non-existent path exits 2 with clean error."""
        missing = tmp_path / "no-such-file.txt"
        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["extract", "skill.md", "--output", str(missing)])

        assert rc == 2
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "Output file not found" in err
        assert str(missing) in err

    def test_extract_live_skill_failure_exits_1(self, capsys):
        """Live-run path: a skill that returns ``not succeeded`` → rc=1
        with a stderr 'Skill failed' line. Covers the non-output-file
        branch at cli/extract.py:97-99.
        """
        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = make_skill_result(
            output="", exit_code=1, duration_seconds=0.2, error="boom",
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["extract", "skill.md"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Skill failed" in err
        assert "boom" in err


    def test_extract_with_output_file(self, tmp_path):
        """Reads output file, calls extract_and_grade, returns 0 on pass."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output with results")

        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        results = self._make_extraction_results(passed=True)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(["extract", "skill.md", "--output", str(output_file)])

        assert rc == 0

    def test_extract_json_output(self, tmp_path, capsys):
        """--json flag produces valid JSON output."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output with results")

        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        results = self._make_extraction_results(passed=True)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(
                ["extract", "skill.md", "--output", str(output_file), "--json"]
            )

        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["skill"] == "test-skill"
        assert data["passed"] is True
        assert "results" in data
        assert len(data["results"]) == 2

    def test_extract_failed(self, tmp_path):
        """Returns 1 when extraction grading fails."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("bad output")

        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        results = self._make_extraction_results(passed=False)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(["extract", "skill.md", "--output", str(output_file)])

        assert rc == 1

    def _make_raw_data_results(self):
        raw = {"Venues": [{"name": "A"}]}
        return AssertionSet(
            results=[
                AssertionResult(
                    name="grader:parse:Venues",
                    passed=False,
                    message="shape wrong",
                    kind="custom",
                    raw_data=raw,
                ),
            ]
        ), raw

    def test_extract_verbose_prints_raw_data(self, tmp_path, capsys):
        """US-005: -v prints raw_data for failing assertion that has it."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("output")

        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        results, raw = self._make_raw_data_results()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(
                ["extract", "skill.md", "--output", str(output_file), "-v"]
            )

        assert rc == 1
        out = capsys.readouterr().out
        assert "Raw data for grader:parse:Venues" in out
        assert json.dumps(raw, indent=2) in out

    def test_extract_without_verbose_omits_raw_data(self, tmp_path, capsys):
        """US-005: without -v, raw_data is not printed even when present."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("output")

        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        results, _ = self._make_raw_data_results()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(["extract", "skill.md", "--output", str(output_file)])

        assert rc == 1
        out = capsys.readouterr().out
        assert "Raw data for" not in out


class TestCmdCapture:
    """Tests for the capture subcommand (US-006)."""

    def _mock_result(self, output: str = "captured stdout") -> SkillResult:
        return make_skill_result(
            output=output, skill_name="find-restaurants", duration_seconds=1.0,
        )

    def test_capture_default_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants"])
        assert rc == 0
        out_path = tmp_path / "tests/eval/captured/find-restaurants.txt"
        assert out_path.exists()
        assert out_path.read_text() == "captured stdout"
        mock_runner.run.assert_called_once_with(
            "find-restaurants", "", env=None, timeout=None,
        )

    def test_capture_custom_out(self, tmp_path):
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result("abc")
        target = tmp_path / "sub" / "custom.txt"
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--out", str(target)])
        assert rc == 0
        assert target.read_text() == "abc"

    def test_capture_versioned_appends_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--versioned"])
        assert rc == 0
        captured_dir = tmp_path / "tests/eval/captured"
        files = list(captured_dir.glob("find-restaurants-*.txt"))
        assert len(files) == 1
        # Stem matches find-restaurants-YYYY-MM-DD
        import re as _re
        assert _re.fullmatch(
            r"find-restaurants-\d{4}-\d{2}-\d{2}", files[0].stem
        )

    def test_capture_out_plus_versioned(self, tmp_path):
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        target = tmp_path / "snap.txt"
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main([
                "capture", "find-restaurants",
                "--out", str(target), "--versioned",
            ])
        assert rc == 0
        files = list(tmp_path.glob("snap-*.txt"))
        assert len(files) == 1

    def test_capture_strips_leading_slash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "/find-restaurants"])
        assert rc == 0
        mock_runner.run.assert_called_once_with(
            "find-restaurants", "", env=None, timeout=None,
        )
        assert (tmp_path / "tests/eval/captured/find-restaurants.txt").exists()

    def test_capture_passes_trailing_args(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--", "near", "San Jose"])
        assert rc == 0
        mock_runner.run.assert_called_once_with(
            "find-restaurants", "near San Jose", env=None, timeout=None,
        )

    def test_capture_runner_failure_returns_nonzero(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="", exit_code=2, skill_name="find-restaurants",
            duration_seconds=0.1, error="boom",
        )
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants"])
        assert rc == 1
        assert "boom" in capsys.readouterr().err


class TestNoApiKeyFlag:
    """US-006: --no-api-key strips both auth env vars on every skill-invoking CLI.

    Each test threads the new ``env_override`` / ``env`` kwarg through
    and asserts the resulting dict does NOT contain ``ANTHROPIC_API_KEY``
    or ``ANTHROPIC_AUTH_TOKEN`` (DEC-001, DEC-006, DEC-007).
    """

    def _assert_env_stripped(self, env: dict | None) -> None:
        assert env is not None, "env_override must be a dict, got None"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_validate_no_api_key_threads_env_override(
        self, tmp_path, monkeypatch
    ):
        """validate --no-api-key → SkillSpec.run(env_override=<stripped>)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output", duration_seconds=0.5,
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--no-api-key"])
        assert rc == 0
        spec.run.assert_called_once()
        env = spec.run.call_args.kwargs.get("env_override")
        self._assert_env_stripped(env)

    def test_grade_no_api_key_threads_env_override(
        self, tmp_path, monkeypatch
    ):
        """grade --no-api-key → SkillSpec.run(env_override=<stripped>)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = make_skill_result(
            output="primary output", duration_seconds=0.5,
            input_tokens=10, output_tokens=5,
        )
        report = make_grading_report(passed=True)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--no-api-key"])
        assert rc == 0
        spec.run.assert_called()
        env = spec.run.call_args.kwargs.get("env_override")
        self._assert_env_stripped(env)

    def test_capture_no_api_key_threads_env(self, tmp_path, monkeypatch):
        """capture --no-api-key → SkillRunner.run(env=<stripped>)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="captured stdout", skill_name="find-restaurants",
            duration_seconds=0.5,
        )
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--no-api-key"])
        assert rc == 0
        mock_runner.run.assert_called_once()
        env = mock_runner.run.call_args.kwargs.get("env")
        self._assert_env_stripped(env)

    def test_run_no_api_key_threads_env(self, monkeypatch):
        """run --no-api-key → SkillRunner.run(env=<stripped>)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="skill output", skill_name="my-skill",
            duration_seconds=0.5,
        )
        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill", "--no-api-key"])
        assert rc == 0
        mock_runner.run.assert_called_once()
        env = mock_runner.run.call_args.kwargs.get("env")
        self._assert_env_stripped(env)

    def test_validate_without_no_api_key_passes_env_none(
        self, tmp_path, monkeypatch
    ):
        """Without --no-api-key, env_override stays None (today's behavior)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output", duration_seconds=0.5,
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])
        assert rc == 0
        assert spec.run.call_args.kwargs.get("env_override") is None

    def test_grade_baseline_threads_env_and_timeout_to_run_raw(
        self, tmp_path, monkeypatch
    ):
        """#64 QG: grade --baseline --no-api-key --timeout X threads env
        AND timeout to the baseline's run_raw call, not just the primary
        arm's spec.run. End-to-end guard for the pass-2 baseline-plumbing
        fix (argparse → _write_workspace_sidecars →
        _write_baseline_and_benchmark → _run_baseline_phase → run_raw)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = make_skill_result(
            output="primary output",
            duration_seconds=0.5,
            input_tokens=10,
            output_tokens=5,
        )
        spec.runner = MagicMock()
        spec.runner.run_raw.return_value = make_skill_result(
            output="baseline output",
            duration_seconds=0.3,
            input_tokens=8,
            output_tokens=4,
            skill_name="__baseline__",
        )
        report = make_grading_report(passed=True)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                [
                    "grade", "skill.md",
                    "--baseline", "--no-api-key", "--timeout", "60",
                ]
            )
        assert rc == 0
        spec.runner.run_raw.assert_called_once()
        kwargs = spec.runner.run_raw.call_args.kwargs
        self._assert_env_stripped(kwargs.get("env"))
        assert kwargs.get("timeout") == 60


class TestSyncTasksFlag:
    """Tier 1.5 of GitHub #103: ``--sync-tasks`` on validate / grade /
    capture / run threads ``sync_tasks_override=True`` (for spec-
    routed commands) or directly mutates the env dict (for
    runner-routed commands).
    """

    _VAR = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"

    def test_validate_sync_tasks_threads_override(
        self, tmp_path, monkeypatch
    ):
        """validate --sync-tasks → SkillSpec.run(sync_tasks_override=True)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output", duration_seconds=0.5,
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--sync-tasks"])
        assert rc == 0
        assert (
            spec.run.call_args.kwargs.get("sync_tasks_override") is True
        )

    def test_validate_without_sync_tasks_threads_none(
        self, tmp_path, monkeypatch
    ):
        """Without --sync-tasks, sync_tasks_override stays None."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output", duration_seconds=0.5,
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])
        assert rc == 0
        assert (
            spec.run.call_args.kwargs.get("sync_tasks_override") is None
        )

    def test_grade_sync_tasks_threads_override(
        self, tmp_path, monkeypatch
    ):
        """grade --sync-tasks → SkillSpec.run(sync_tasks_override=True)."""
        monkeypatch.chdir(tmp_path)
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = make_skill_result(
            output="primary output",
            duration_seconds=0.5,
            input_tokens=10,
            output_tokens=5,
        )
        report = make_grading_report(passed=True)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--sync-tasks"])
        assert rc == 0
        assert (
            spec.run.call_args.kwargs.get("sync_tasks_override") is True
        )

    def test_capture_sync_tasks_sets_env_var(self, tmp_path, monkeypatch):
        """capture --sync-tasks → runner.run(env={...DISABLE...=1})."""
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="captured stdout",
            skill_name="find-restaurants",
            duration_seconds=0.5,
        )
        with patch(
            "clauditor.cli.capture.SkillRunner", return_value=mock_runner
        ):
            rc = main(
                ["capture", "find-restaurants", "--sync-tasks"]
            )
        assert rc == 0
        env = mock_runner.run.call_args.kwargs.get("env")
        assert env is not None
        assert env[self._VAR] == "1"

    def test_run_sync_tasks_sets_env_var(self, monkeypatch):
        """run --sync-tasks → runner.run(env={...DISABLE...=1})."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="skill output",
            skill_name="my-skill",
            duration_seconds=0.5,
        )
        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill", "--sync-tasks"])
        assert rc == 0
        env = mock_runner.run.call_args.kwargs.get("env")
        assert env is not None
        assert env[self._VAR] == "1"

    def test_run_no_api_key_plus_sync_tasks_composes(self, monkeypatch):
        """--no-api-key and --sync-tasks compose: env has the sync var
        AND the auth keys are stripped."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="ok", skill_name="my-skill", duration_seconds=0.5,
        )
        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(
                ["run", "my-skill", "--no-api-key", "--sync-tasks"]
            )
        assert rc == 0
        env = mock_runner.run.call_args.kwargs.get("env")
        assert env is not None
        assert env[self._VAR] == "1"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_grade_baseline_sync_tasks_threads_to_run_raw(
        self, tmp_path, monkeypatch
    ):
        """grade --baseline --sync-tasks threads the env var to
        run_raw so both arms of the baseline delta share sync mode."""
        monkeypatch.chdir(tmp_path)
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = make_skill_result(
            output="primary output",
            duration_seconds=0.5,
            input_tokens=10,
            output_tokens=5,
        )
        spec.runner = MagicMock()
        spec.runner.run_raw.return_value = make_skill_result(
            output="baseline output",
            duration_seconds=0.3,
            input_tokens=8,
            output_tokens=4,
            skill_name="__baseline__",
        )
        report = make_grading_report(passed=True)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                ["grade", "skill.md", "--baseline", "--sync-tasks"]
            )
        assert rc == 0
        spec.runner.run_raw.assert_called_once()
        env = spec.runner.run_raw.call_args.kwargs.get("env")
        assert env is not None
        assert env[self._VAR] == "1"


class TestTimeoutFlag:
    """US-006: --timeout SECONDS threads to the runner, rejects <= 0 at parse time.

    Covers argparse-level validation (exit 2 on 0, negative, non-int) and
    the happy path (positive int → ``timeout_override`` / ``timeout``
    kwarg on the underlying run call). Defaults to None (DEC-014).
    """

    def test_validate_timeout_300_threads_override(
        self, tmp_path, monkeypatch
    ):
        """validate --timeout 300 → SkillSpec.run(timeout_override=300)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output", duration_seconds=0.5,
        )
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--timeout", "300"])
        assert rc == 0
        assert spec.run.call_args.kwargs.get("timeout_override") == 300

    def test_validate_timeout_zero_exits_2(self, capsys):
        """--timeout 0 → argparse rejects with exit 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["validate", "skill.md", "--timeout", "0"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "must be >= 1" in err or "must be > 0" in err

    def test_validate_timeout_negative_exits_2(self, capsys):
        """--timeout -5 → argparse rejects with exit 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["validate", "skill.md", "--timeout", "-5"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "must be >= 1" in err or "must be > 0" in err

    def test_validate_timeout_non_int_exits_2(self, capsys):
        """--timeout foo → argparse rejects with exit 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["validate", "skill.md", "--timeout", "foo"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "not an integer" in err or "invalid" in err.lower()

    def test_run_timeout_default_none_preserves_fallback(self):
        """run (no --timeout) → SkillRunner.run(timeout=None), so the runner's
        self.timeout default of 300s kicks in."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="skill output", skill_name="my-skill",
            duration_seconds=0.5,
        )
        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])
        assert rc == 0
        assert mock_runner.run.call_args.kwargs.get("timeout") is None

    def test_run_timeout_300_threads_to_runner(self):
        """run --timeout 300 → SkillRunner.run(timeout=300)."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="skill output", skill_name="my-skill",
            duration_seconds=0.5,
        )
        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill", "--timeout", "300"])
        assert rc == 0
        assert mock_runner.run.call_args.kwargs.get("timeout") == 300

    def test_capture_timeout_300_threads_to_runner(self, tmp_path, monkeypatch):
        """capture --timeout 300 → SkillRunner.run(timeout=300)."""
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="captured stdout", skill_name="find-restaurants",
            duration_seconds=0.5,
        )
        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--timeout", "300"])
        assert rc == 0
        assert mock_runner.run.call_args.kwargs.get("timeout") == 300


class TestCLITransportFlag:
    """US-004 (#86): ``--transport {api,cli,auto}`` argparse flag on six commands.

    DEC-012 / DEC-017. Each of the six LLM-mediated commands (``grade``,
    ``extract``, ``propose-eval``, ``suggest``, ``triggers``, ``compare``)
    accepts ``--transport`` with the shared ``_transport_choice`` validator.
    Invalid values exit 2 at argparse time per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            ["grade", "skill.md", "--transport", "sdk"],
            ["extract", "skill.md", "--transport", "sdk"],
            ["propose-eval", "skill.md", "--transport", "sdk"],
            ["suggest", "skill.md", "--transport", "sdk"],
            ["triggers", "skill.md", "--transport", "sdk"],
            ["compare", "a.txt", "b.txt", "--transport", "sdk"],
        ],
    )
    def test_invalid_transport_exits_2(self, cmd, capsys):
        """Invalid ``--transport sdk`` rejected by argparse with exit 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(cmd)
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        # Either argparse's "invalid _transport_choice value" wrapper
        # or our own "must be one of" message must appear.
        assert "must be one of" in err or "invalid" in err.lower()

    @pytest.mark.parametrize(
        "cmd",
        [
            ["grade", "--help"],
            ["extract", "--help"],
            ["propose-eval", "--help"],
            ["suggest", "--help"],
            ["triggers", "--help"],
            ["compare", "--help"],
        ],
    )
    def test_six_commands_advertise_transport_in_help(self, cmd, capsys):
        """Each of the six commands advertises ``--transport`` in help text."""
        with pytest.raises(SystemExit) as exc_info:
            main(cmd)
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--transport" in out

    @pytest.mark.parametrize("value", ["api", "cli", "auto"])
    def test_transport_choice_validator_accepts_each_literal(self, value):
        """The shared argparse type validator accepts every literal
        value and returns it unchanged. Pure unit test — no main
        round-trip, no help-text sniffing.
        """
        from clauditor.cli import _transport_choice

        assert _transport_choice(value) == value

    @pytest.mark.parametrize("value", ["sdk", "", "API", "CLI", " api"])
    def test_transport_choice_validator_rejects_invalid(self, value):
        """Anything outside ``{"api", "cli", "auto"}`` raises
        ``ArgumentTypeError``, which argparse translates into a clean
        exit 2 at CLI parse time (covered separately by
        ``test_invalid_transport_exits_2``).
        """
        import argparse

        from clauditor.cli import _transport_choice

        with pytest.raises(
            argparse.ArgumentTypeError, match="must be one of"
        ):
            _transport_choice(value)


class TestCmdDoctor:
    """Tests for the doctor subcommand (US-007)."""

    def test_doctor_always_exits_zero(self, capsys):
        rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        # At least one check per category reported
        assert "python" in out
        assert "anthropic" in out
        assert "claude-cli" in out
        assert "pytest-plugin" in out
        assert "editable-install" in out

    def test_doctor_python_too_old(self, capsys):
        # Mimic sys.version_info shape enough for the doctor's usage.
        class _FakeVersion(tuple):
            major = 3
            minor = 10
            micro = 0

        fake_version = _FakeVersion((3, 10, 0))
        with patch.object(sys, "version_info", fake_version):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if line.startswith("[fail]") and "python" in line
        ]
        assert lines

    def test_doctor_missing_anthropic(self, capsys):
        real_find_spec = __import__("importlib").util.find_spec

        def fake_find_spec(name):
            if name == "anthropic":
                return None
            return real_find_spec(name)

        with patch("importlib.util.find_spec", side_effect=fake_find_spec):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        # Pin [warn] to the same line as the anthropic check, not just
        # anywhere in the output (editable-install also emits [warn]).
        anthropic_lines = [
            line for line in out.splitlines() if "anthropic" in line
        ]
        assert len(anthropic_lines) == 1
        assert anthropic_lines[0].startswith("[warn]")

    def test_doctor_missing_claude_binary(self, capsys):
        with patch("shutil.which", return_value=None):
            rc = main(["doctor"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude-cli" in out
        # Expect info marker for missing claude CLI (not fail — absence is valid)
        lines = [line for line in out.splitlines() if "claude-cli" in line]
        assert any("[info]" in line for line in lines)

    # --- DEC-013 clauditor-skill-symlink check (5 states) ---------------

    def test_doctor_reports_info_when_skill_not_installed(
        self, setup_env, capsys
    ):
        """Dest doesn't exist → info with 'run clauditor setup'."""
        dest = setup_env["dest"]
        assert not dest.exists()

        rc = main(["doctor"])

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "clauditor-skill-symlink" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[info]")
        assert "not installed" in lines[0]
        assert "clauditor setup" in lines[0]

    def test_doctor_reports_ok_when_our_symlink_installed(
        self, setup_env, capsys
    ):
        """Dest is our symlink → ok with resolved target."""
        import os as _os

        dest = setup_env["dest"]
        pkg_skill = setup_env["pkg_skill_root"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _os.symlink(pkg_skill, dest)

        rc = main(["doctor"])

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "clauditor-skill-symlink" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[ok]")
        assert str(pkg_skill.resolve()) in lines[0]

    def test_doctor_reports_warn_for_stale_symlink(
        self, setup_env, capsys, tmp_path
    ):
        """Dangling symlink (target removed) → warn with '--force to fix'."""
        import os as _os

        dest = setup_env["dest"]
        # Create a symlink pointing at a target that we then delete.
        vanished = tmp_path / "vanished-target"
        vanished.mkdir()
        dest.parent.mkdir(parents=True, exist_ok=True)
        _os.symlink(vanished, dest)
        # Now remove the target, leaving a dangling symlink.
        vanished.rmdir()

        assert dest.is_symlink()
        assert not dest.exists()

        rc = main(["doctor"])

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "clauditor-skill-symlink" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[warn]")
        assert "stale symlink" in lines[0]
        assert "--force" in lines[0]

    def test_doctor_reports_warn_for_wrong_target_symlink(
        self, setup_env, capsys, tmp_path
    ):
        """Symlink → somewhere else → warn 'doesn't match'."""
        import os as _os

        dest = setup_env["dest"]
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        dest.parent.mkdir(parents=True, exist_ok=True)
        _os.symlink(elsewhere, dest)

        rc = main(["doctor"])

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "clauditor-skill-symlink" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[warn]")
        assert "doesn't match" in lines[0]
        assert str(elsewhere.resolve()) in lines[0]

    @pytest.mark.parametrize("kind", ["file", "dir"])
    def test_doctor_reports_warn_for_non_symlink_file_or_dir(
        self, setup_env, capsys, kind
    ):
        """Regular file or real directory (not a symlink) → warn 'unmanaged'."""
        dest = setup_env["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            dest.write_text("not a symlink\n")
            expected_kind = "file"
        else:
            dest.mkdir()
            (dest / "junk.txt").write_text("stuff\n")
            expected_kind = "directory"

        rc = main(["doctor"])

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "clauditor-skill-symlink" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[warn]")
        assert expected_kind in lines[0]
        assert "unmanaged" in lines[0]

    def test_doctor_reports_info_when_no_project_root(
        self, tmp_path, monkeypatch, capsys
    ):
        """No .git or .claude in ancestry → info line (DEC-013, 6th state)."""
        pkg_skill = tmp_path / "fake-pkg" / "clauditor" / "skills" / "clauditor"
        pkg_skill.mkdir(parents=True)
        (pkg_skill / "SKILL.md").write_text("# sentinel\n")

        # cwd is a markerless subdir under tmp_path.
        subdir = tmp_path / "nowhere"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        def fake_files(pkg_name):
            assert pkg_name == "clauditor"
            return tmp_path / "fake-pkg" / "clauditor"

        monkeypatch.setattr("clauditor.cli.doctor.files", fake_files)

        rc = main(["doctor"])

        assert rc == 0
        out = capsys.readouterr().out
        lines = [
            line for line in out.splitlines()
            if "clauditor-skill-symlink" in line
        ]
        assert len(lines) == 1
        assert lines[0].startswith("[info]")
        assert "no project root found; run from a project directory" in lines[0]
        # doctor has no --project-dir flag, so must not suggest one.
        assert "--project-dir" not in lines[0]


class TestDoctorPep660Detection:
    """Tests for ``_is_pep660_editable`` — PEP 660 editable-install signal.

    The doctor's symlink-based fallback misses import-hook / .pth editable
    installs; this helper reads ``direct_url.json`` from the dist-info as
    the primary signal. Tests mock ``importlib.metadata.distribution`` so
    they don't depend on the worker's actual install method.
    """

    def _fake_dist(self, *, direct_url_body):
        """Return a stub Distribution whose read_text returns the body."""
        dist = MagicMock()
        dist.read_text.return_value = direct_url_body
        return dist

    def test_editable_true(self):
        """direct_url.json with dir_info.editable == True → editable."""
        from clauditor.cli.doctor import _is_pep660_editable

        body = '{"url": "file:///abs/path", "dir_info": {"editable": true}}'
        with patch(
            "importlib.metadata.distribution",
            return_value=self._fake_dist(direct_url_body=body),
        ):
            assert _is_pep660_editable() is True

    def test_editable_false(self):
        """direct_url.json with dir_info.editable == False → not editable."""
        from clauditor.cli.doctor import _is_pep660_editable

        body = '{"url": "file:///abs", "dir_info": {"editable": false}}'
        with patch(
            "importlib.metadata.distribution",
            return_value=self._fake_dist(direct_url_body=body),
        ):
            assert _is_pep660_editable() is False

    def test_editable_missing_dir_info(self):
        """direct_url.json without dir_info → not editable (non-dir URL)."""
        from clauditor.cli.doctor import _is_pep660_editable

        body = '{"url": "https://example.com/wheel"}'
        with patch(
            "importlib.metadata.distribution",
            return_value=self._fake_dist(direct_url_body=body),
        ):
            assert _is_pep660_editable() is False

    def test_malformed_direct_url_json(self):
        """Malformed JSON → not editable (falls through)."""
        from clauditor.cli.doctor import _is_pep660_editable

        with patch(
            "importlib.metadata.distribution",
            return_value=self._fake_dist(direct_url_body="{not json"),
        ):
            assert _is_pep660_editable() is False

    def test_no_direct_url_json(self):
        """Missing direct_url.json → not editable (read_text returns None)."""
        from clauditor.cli.doctor import _is_pep660_editable

        with patch(
            "importlib.metadata.distribution",
            return_value=self._fake_dist(direct_url_body=None),
        ):
            assert _is_pep660_editable() is False

    def test_package_not_found(self):
        """Distribution lookup failure → not editable."""
        import importlib.metadata

        from clauditor.cli.doctor import _is_pep660_editable

        with patch(
            "importlib.metadata.distribution",
            side_effect=importlib.metadata.PackageNotFoundError("clauditor"),
        ):
            assert _is_pep660_editable() is False


class TestCmdTrend:
    """Tests for the trend subcommand (US-006)."""

    def _seed(self, path, skill="test-skill", n=3):
        from clauditor import history

        for i in range(n):
            history.append_record(
                skill=skill,
                pass_rate=0.5 + i * 0.1,
                mean_score=0.6 + i * 0.05,
                metrics={"count": i + 1},
                command="grade",
                path=path,
            )

    def test_happy_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl")

        rc = main(["trend", "test-skill", "--metric", "pass_rate"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0.5" in out
        assert "0.7" in out
        # stdout ends with the last data row's newline — every non-empty
        # line must be a TSV data row (#106).
        lines = [ln for ln in out.splitlines() if ln]
        assert lines
        for ln in lines:
            assert "\t" in ln, f"non-data trailing line on stdout: {ln!r}"

    def test_metric_in_metrics_dict(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl")

        rc = main(["trend", "test-skill", "--metric", "count"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "1" in out and "3" in out

    def test_missing_metric_exits_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl")

        rc = main(["trend", "test-skill", "--metric", "nonexistent"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "nonexistent" in err

    def test_no_history_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["trend", "test-skill", "--metric", "pass_rate"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "no history" in err.lower() or "no records" in err.lower()

    def test_stdout_ends_with_last_data_row_no_sparkline_artifact(
        self, tmp_path, monkeypatch, capsys
    ):
        """Regression for #106: stdout must end with the last data row's
        newline — no trailing sparkline/artifact line. Users piping the
        output to awk/jq otherwise get a non-parseable trailing line."""
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl", n=2)

        rc = main(["trend", "test-skill", "--metric", "pass_rate"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        assert len(lines) == 2, (
            f"expected only data rows, got {len(lines)} lines: {lines!r}"
        )
        for ln in lines:
            assert "\t" in ln, (
                f"non-TSV trailing line on stdout: {ln!r} — "
                "sparkline/artifact regression"
            )

    def test_last_n_truncates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl", n=10)

        rc = main(["trend", "test-skill", "--metric", "pass_rate", "--last", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "\t" in ln]
        assert len(data_lines) == 5

    def test_trend_skips_bad_version_records(self, tmp_path, monkeypatch, capsys):
        """cmd_trend skips records with wrong schema_version (DEC-003)."""
        import json as _json

        from clauditor import history

        monkeypatch.chdir(tmp_path)
        path = tmp_path / ".clauditor" / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        bad_rec = {
            "schema_version": 99,
            "command": "grade",
            "ts": "2026-01-01T00:00:00+00:00",
            "skill": "test-skill",
            "pass_rate": 0.4,
            "mean_score": 0.5,
            "metrics": {},
        }
        with path.open("w", encoding="utf-8") as f:
            f.write(_json.dumps(bad_rec) + "\n")

        # One valid record via the API.
        history.append_record(
            "test-skill",
            0.9,
            0.8,
            {},
            command="grade",
            path=path,
            iteration=1,
            workspace_path="ws/1",
        )

        rc = main(["trend", "test-skill", "--metric", "pass_rate"])
        assert rc == 0
        out = capsys.readouterr().out
        # Bad record skipped, only valid record appears.
        assert "0.4" not in out
        assert "0.9" in out


class TestCmdTrendCommandFilter:
    """cmd_trend --command filter (US-006)."""

    def _seed(self, path, skill="test-skill"):
        from clauditor import history

        for i in range(3):
            history.append_record(
                skill=skill,
                pass_rate=0.5 + i * 0.1,
                mean_score=0.6,
                metrics={},
                command="grade",
                path=path,
            )
        for i in range(2):
            history.append_record(
                skill=skill,
                pass_rate=None,
                mean_score=None,
                metrics={"skill": {"input_tokens": 100 + i}},
                command="extract",
                path=path,
            )

    def test_default_is_grade(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl")
        rc = main(["trend", "test-skill", "--metric", "pass_rate"])
        assert rc == 0
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "\t" in ln]
        assert len(data_lines) == 3

    def test_command_all_unions(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl")
        rc = main(
            ["trend", "test-skill", "--metric", "pass_rate", "--command", "all"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "\t" in ln]
        # extract records have pass_rate=None so resolve_path returns None;
        # only the 3 grade records land in the output.
        assert len(data_lines) == 3

    def test_command_extract_none_values(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl")
        rc = main(
            [
                "trend",
                "test-skill",
                "--metric",
                "pass_rate",
                "--command",
                "extract",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "pass_rate" in err

    def test_positive_int_rejects_non_integer(self):
        """--last type=_positive_int rejects non-integer values via
        argparse (covers trend._positive_int ValueError branch)."""
        from clauditor.cli.trend import _positive_int

        with pytest.raises(argparse.ArgumentTypeError, match="not an integer"):
            _positive_int("abc")

    def test_positive_int_rejects_zero_and_negative(self):
        """_positive_int enforces >= 1 (covers trend._positive_int < 1 branch)."""
        from clauditor.cli.trend import _positive_int

        with pytest.raises(argparse.ArgumentTypeError, match=">= 1"):
            _positive_int("0")
        with pytest.raises(argparse.ArgumentTypeError, match=">= 1"):
            _positive_int("-5")

    def test_command_filter_empty_result_exits_1(self, tmp_path, monkeypatch, capsys):
        """Requesting a command filter that matches zero records surfaces
        as rc=1 with 'Try --command all' hint."""
        from clauditor import history

        monkeypatch.chdir(tmp_path)
        path = tmp_path / ".clauditor" / "history.jsonl"
        # Seed only grade records, then filter by validate → zero matches.
        history.append_record(
            skill="test-skill",
            pass_rate=0.8,
            mean_score=0.7,
            metrics={},
            command="grade",
            path=path,
        )
        rc = main(
            [
                "trend", "test-skill", "--metric", "pass_rate",
                "--command", "validate",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "validate" in err
        assert "--command all" in err


class TestCmdTrendDottedPath:
    """cmd_trend dotted-path resolution (US-006)."""

    def test_dotted_nested_metric(self, tmp_path, monkeypatch, capsys):
        from clauditor import history

        monkeypatch.chdir(tmp_path)
        path = tmp_path / ".clauditor" / "history.jsonl"
        for tok in (500, 600, 700):
            history.append_record(
                skill="test-skill",
                pass_rate=0.8,
                mean_score=0.7,
                metrics={"grader": {"input_tokens": tok}},
                command="grade",
                path=path,
            )
        rc = main(["trend", "test-skill", "--metric", "grader.input_tokens"])
        assert rc == 0
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "\t" in ln]
        assert len(data_lines) == 3
        assert "500" in out
        assert "600" in out
        assert "700" in out
        # stdout ends with the last data row — no trailing artifact (#106).
        assert "\t" in out.splitlines()[-1]


class TestCmdTrendListMetrics:
    """cmd_trend --list-metrics (US-006)."""

    def _seed_full(self, path, skill="test-skill"):
        from clauditor import history

        history.append_record(
            skill=skill,
            pass_rate=0.8,
            mean_score=0.7,
            metrics={
                "skill": {"input_tokens": 100, "output_tokens": 50},
                "grader": {"input_tokens": 500, "output_tokens": 200},
                "total": {
                    "input_tokens": 900,
                    "output_tokens": 400,
                    "total": 1300,
                },
                "duration_seconds": 2.5,
            },
            command="grade",
            path=path,
        )
        history.append_record(
            skill=skill,
            pass_rate=None,
            mean_score=None,
            metrics={
                "skill": {"input_tokens": 80, "output_tokens": 30},
                "duration_seconds": 1.2,
            },
            command="extract",
            path=path,
        )

    def test_list_metrics_default_grade(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed_full(tmp_path / ".clauditor" / "history.jsonl")
        rc = main(["trend", "test-skill", "--list-metrics"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        assert lines == sorted(lines)
        assert "pass_rate" in lines
        assert "mean_score" in lines
        assert "skill.input_tokens" in lines
        assert "grader.input_tokens" in lines
        assert "total.total" in lines
        assert "duration_seconds" in lines

    def test_list_metrics_command_extract(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed_full(tmp_path / ".clauditor" / "history.jsonl")
        rc = main(
            ["trend", "test-skill", "--list-metrics", "--command", "extract"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        # Extract record had null pass_rate/mean_score, so those are absent
        assert "pass_rate" not in lines
        assert "mean_score" not in lines
        assert "skill.input_tokens" in lines
        assert "duration_seconds" in lines
        # And no grader bucket in extract seed
        assert "grader.input_tokens" not in lines

    def test_list_metrics_command_all(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed_full(tmp_path / ".clauditor" / "history.jsonl")
        rc = main(
            ["trend", "test-skill", "--list-metrics", "--command", "all"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        # Union includes grade-only grader plus extract fields
        assert "pass_rate" in lines
        assert "grader.input_tokens" in lines
        assert "skill.input_tokens" in lines
        assert "duration_seconds" in lines

    def test_list_metrics_empty_exits_1(self, tmp_path, monkeypatch, capsys):
        from clauditor import history

        monkeypatch.chdir(tmp_path)
        path = tmp_path / ".clauditor" / "history.jsonl"
        history.append_record(
            skill="test-skill",
            pass_rate=None,
            mean_score=None,
            metrics={},
            command="grade",
            path=path,
        )
        rc = main(["trend", "test-skill", "--list-metrics"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "no metric paths" in err


class TestCmdTrendMutuallyExclusive:
    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(
                ["trend", "test-skill", "--metric", "pass_rate", "--list-metrics"],
                id="metric-and-list-metrics-conflict",
            ),
            pytest.param(
                ["trend", "test-skill"],
                id="neither-required",
            ),
        ],
    )
    def test_argparse_rejects(self, tmp_path, monkeypatch, argv):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main(argv)


class TestCmdGradeHistory:
    """cmd_grade appends a history record (US-006)."""

    def test_grade_appends_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        spec = _make_spec(eval_spec=_make_eval_spec())
        report = make_grading_report(passed=True, score=0.9)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--output", str(output_file)])

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        record = json.loads(history_path.read_text().splitlines()[0])
        assert record["skill"] == "test-skill"
        assert record["pass_rate"] == 1.0
        assert record["mean_score"] == 0.9
        assert record["schema_version"] == 1
        assert record["command"] == "grade"

    def test_grade_history_records_metrics(self, tmp_path, monkeypatch):
        """cmd_grade records real bucketed metrics in history.jsonl."""
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        spec = _make_spec(eval_spec=_make_eval_spec())
        report = make_grading_report(
            criterion="c",
            score=1.0,
            input_tokens=500,
            output_tokens=200,
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(["grade", "skill.md", "--output", str(output_file)])

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        record = json.loads(history_path.read_text().splitlines()[0])
        metrics = record["metrics"]
        # --output path -> no skill run -> skill tokens 0
        assert metrics["skill"]["input_tokens"] == 0
        assert metrics["skill"]["output_tokens"] == 0
        assert metrics["quality"]["input_tokens"] == 500
        assert metrics["quality"]["output_tokens"] == 200
        assert metrics["total"]["total"] == 700
        assert "grader" not in metrics
        assert "triggers" not in metrics

    def test_grade_variance_rolls_tokens_into_history_and_grading_json(
        self, tmp_path, monkeypatch
    ):
        """--variance N: skill + grader tokens across all runs roll up into
        both history.jsonl metrics and the iteration's grading.json."""
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some output")

        spec = _make_spec(eval_spec=_make_eval_spec())
        # Each variance run: 10 in / 5 out skill tokens, 0.5s duration.
        spec.run.side_effect = [
            make_skill_result(
                output=f"v{i}", duration_seconds=0.5,
                input_tokens=10, output_tokens=5,
            )
            for i in range(3)
        ]

        # Each grade_quality call returns the same per-run report shape:
        # 200 in / 100 out grader tokens. Total runs = 1 primary + 3 variance
        # = 4 grader calls -> quality totals 800 / 400.
        per_run_report = make_grading_report(
            criterion="c", score=1.0, input_tokens=200, output_tokens=100
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=per_run_report,
            ),
        ):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--variance",
                    "3",
                ]
            )

        assert rc == 0

        history_path = tmp_path / ".clauditor" / "history.jsonl"
        record = json.loads(history_path.read_text().splitlines()[0])
        m = record["metrics"]
        # Primary --output -> skill 0/0/0.0; variance: 3 * (10/5/0.5) = 30/15/1.5
        assert m["skill"]["input_tokens"] == 30
        assert m["skill"]["output_tokens"] == 15
        assert m["duration_seconds"] == pytest.approx(1.5)
        # Quality: 4 grader calls * 200/100 = 800/400
        assert m["quality"]["input_tokens"] == 800
        assert m["quality"]["output_tokens"] == 400
        assert m["total"]["total"] == 30 + 15 + 800 + 400

        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        grade_data = json.loads((skill_dir / "grading.json").read_text())
        assert grade_data["metrics"] == m
        timing = json.loads((skill_dir / "timing.json").read_text())
        assert timing["metrics"] == m
        assert timing["n_runs"] == 4

    def test_grade_writes_metrics_to_grading_json(self, tmp_path, monkeypatch):
        """grading.json carries the same metrics dict as history.jsonl."""
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some output")

        spec = _make_spec(eval_spec=_make_eval_spec())
        report = make_grading_report(
            criterion="c", score=1.0, input_tokens=300, output_tokens=100
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                ["grade", "skill.md", "--output", str(output_file)]
            )

        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        grading_path = skill_dir / "grading.json"
        assert grading_path.exists()
        data = json.loads(grading_path.read_text())
        assert data["metrics"]["quality"]["input_tokens"] == 300
        assert data["metrics"]["quality"]["output_tokens"] == 100
        assert data["metrics"]["total"]["total"] == 400

        rt = GradingReport.from_json(grading_path.read_text())
        assert rt.metrics is not None
        assert rt.metrics["quality"]["input_tokens"] == 300

    def test_grade_history_record_has_iteration_and_workspace_path(
        self, tmp_path, monkeypatch
    ):
        """history.jsonl records carry iteration + workspace_path."""
        monkeypatch.chdir(tmp_path)
        output_file = tmp_path / "output.txt"
        output_file.write_text("hi")

        spec = _make_spec(eval_spec=_make_eval_spec())
        report = make_grading_report(criterion="c", score=1.0)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            assert main(
                ["grade", "skill.md", "--output", str(output_file)]
            ) == 0

        history_path = tmp_path / ".clauditor" / "history.jsonl"
        record = json.loads(history_path.read_text().splitlines()[0])
        assert record["iteration"] == 1
        assert record["workspace_path"] is not None
        assert record["workspace_path"].endswith(
            "iteration-1/test-skill"
        ) or record["workspace_path"].endswith(
            "iteration-1\\test-skill"
        )

    def test_grade_only_criterion_still_skips_history(
        self, tmp_path, monkeypatch
    ):
        """--only-criterion must not write a history record (regression #18)."""
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some output")

        spec = _make_spec(
            eval_spec=_make_eval_spec(grading_criteria=["foo bar", "baz"])
        )
        report = make_grading_report(criterion="foo bar", score=1.0)

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--only-criterion",
                    "foo",
                ]
            )

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        assert not history_path.exists()


class TestCmdExtractHistory:
    """cmd_extract appends a history record with command=extract (US-005)."""

    def test_extract_appends_history(self, tmp_path, monkeypatch):
        """--output path records skill=0, grader=tokens from AssertionSet."""
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        results = AssertionSet(
            results=[
                AssertionResult(
                    name="section:Results:count",
                    passed=True,
                    message="ok",
                    kind="count",
                )
            ],
            input_tokens=500,
            output_tokens=200,
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(["extract", "skill.md", "--output", str(output_file)])

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        assert history_path.exists()
        lines = [ln for ln in history_path.read_text().splitlines() if ln]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["skill"] == "test-skill"
        assert record["schema_version"] == 1
        assert record["command"] == "extract"
        assert record["pass_rate"] is None
        assert record["mean_score"] is None
        metrics = record["metrics"]
        # --output path -> no skill run -> skill tokens 0
        assert metrics["skill"]["input_tokens"] == 0
        assert metrics["skill"]["output_tokens"] == 0
        assert metrics["grader"]["input_tokens"] == 500
        assert metrics["grader"]["output_tokens"] == 200
        assert "quality" not in metrics
        assert "triggers" not in metrics
        assert metrics["total"]["total"] == 700

    def test_extract_live_run_records_skill_and_grader_tokens(
        self, tmp_path, monkeypatch
    ):
        """Live skill run records skill tokens + grader tokens; total=850."""
        monkeypatch.chdir(tmp_path)

        spec = _make_spec(eval_spec=_make_eval_spec(sections=_make_sections()))
        spec.run.return_value = make_skill_result(
            output="some skill output",
            duration_seconds=2.5, input_tokens=100, output_tokens=50,
        )
        results = AssertionSet(
            results=[
                AssertionResult(
                    name="section:Results:count",
                    passed=True, message="ok", kind="count",
                )
            ],
            input_tokens=500, output_tokens=200,
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.grader.extract_and_grade",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            rc = main(["extract", "skill.md"])

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        record = json.loads(history_path.read_text().splitlines()[0])
        assert record["command"] == "extract"
        metrics = record["metrics"]
        assert metrics["skill"]["input_tokens"] == 100
        assert metrics["skill"]["output_tokens"] == 50
        assert metrics["grader"]["input_tokens"] == 500
        assert metrics["grader"]["output_tokens"] == 200
        assert metrics["total"]["total"] == 850
        assert metrics["duration_seconds"] == 2.5
        assert "quality" not in metrics
        assert "triggers" not in metrics


class TestCmdValidateHistory:
    """cmd_validate appends a history record with command=validate (US-005)."""

    def test_validate_live_run_appends_history(self, tmp_path, monkeypatch):
        """Layer 1 live run records skill tokens only; pass_rate from assertions."""
        monkeypatch.chdir(tmp_path)

        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world output",
            duration_seconds=1.5, input_tokens=100, output_tokens=50,
        )

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        assert history_path.exists()
        lines = [ln for ln in history_path.read_text().splitlines() if ln]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["skill"] == "test-skill"
        assert record["schema_version"] == 1
        assert record["command"] == "validate"
        # Layer 1 pass_rate is 1.0 (the "contains hello" assertion passes)
        assert record["pass_rate"] == 1.0
        assert record["mean_score"] is None
        metrics = record["metrics"]
        assert metrics["skill"]["input_tokens"] == 100
        assert metrics["skill"]["output_tokens"] == 50
        assert metrics["duration_seconds"] == 1.5
        assert "grader" not in metrics
        assert "quality" not in metrics
        assert "triggers" not in metrics
        assert metrics["total"]["total"] == 150

    def test_validate_with_output_file_records_zeros(
        self, tmp_path, monkeypatch
    ):
        """--output path records zero skill tokens/duration."""
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("hello world content")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--output", str(output_file)])

        assert rc == 0
        history_path = tmp_path / ".clauditor" / "history.jsonl"
        record = json.loads(history_path.read_text().splitlines()[0])
        assert record["command"] == "validate"
        metrics = record["metrics"]
        assert metrics["skill"]["input_tokens"] == 0
        assert metrics["skill"]["output_tokens"] == 0
        assert metrics["duration_seconds"] == 0.0
        assert "grader" not in metrics


class TestCmdValidateWorkspace:
    """cmd_validate persists an iteration workspace on live runs (US-006)."""

    def _live_spec(self, output_text: str = "hello world output"):
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output=output_text,
            duration_seconds=1.5, input_tokens=100, output_tokens=50,
            stream_events=[
                {"type": "system", "session_id": "abc"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "hi"}]
                    },
                },
            ],
        )
        return spec

    def test_validate_live_run_publishes_iteration(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = self._live_spec()

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert skill_dir.is_dir()
        assert (skill_dir / "run-0" / "output.jsonl").is_file()
        assert (skill_dir / "run-0" / "output.txt").is_file()
        assertions_path = skill_dir / "assertions.json"
        assert assertions_path.is_file()
        # No Layer 3 artifacts for validate.
        assert not (skill_dir / "grading.json").exists()
        assert not (skill_dir / "timing.json").exists()

        payload = json.loads(assertions_path.read_text())
        assert payload["schema_version"] == 1
        assert payload["skill"] == "test-skill"
        assert payload["iteration"] == 1
        assert len(payload["runs"]) == 1
        run_row = payload["runs"][0]
        assert run_row["run"] == 0
        # transcript_path wired onto assertion rows.
        for r in run_row["results"]:
            assert r["transcript_path"].endswith("run-0/output.jsonl")

        # History row.
        hist = tmp_path / ".clauditor" / "history.jsonl"
        lines = [ln for ln in hist.read_text().splitlines() if ln]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["command"] == "validate"
        assert rec["iteration"] == 1
        assert rec["workspace_path"].endswith("iteration-1/test-skill")

    def test_validate_no_transcript_skips_run_dir(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        spec = self._live_spec()

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md", "--no-transcript"])

        assert rc == 0
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        assert skill_dir.is_dir()
        assert not (skill_dir / "run-0").exists()
        payload = json.loads((skill_dir / "assertions.json").read_text())
        for r in payload["runs"][0]["results"]:
            assert r["transcript_path"] is None

    def test_validate_shares_counter_with_grade(self, tmp_path, monkeypatch):
        """A prior ``iteration-1`` dir forces validate onto ``iteration-2``."""
        monkeypatch.chdir(tmp_path)
        # Simulate a prior grade run.
        prior = tmp_path / ".clauditor" / "iteration-1" / "other-skill"
        prior.mkdir(parents=True)

        spec = self._live_spec()
        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 0
        assert (
            tmp_path / ".clauditor" / "iteration-2" / "test-skill"
        ).is_dir()
        assert not (
            tmp_path / ".clauditor" / "iteration-1" / "test-skill"
        ).exists()

    def test_validate_skill_failure_aborts_workspace(
        self, tmp_path, monkeypatch
    ):
        """Failed skill run cleans up the staging dir and returns 1."""
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="", exit_code=1, duration_seconds=0.5, error="boom",
        )

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 1
        clauditor_dir = tmp_path / ".clauditor"
        # Neither a staging dir nor a finalized iteration should remain.
        assert not (clauditor_dir / "iteration-1").exists()
        assert not (clauditor_dir / "iteration-1-tmp").exists()

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param("InvalidSkillNameError", id="invalid-skill-name"),
            pytest.param(ValueError("boom"), id="value-error"),
        ],
    )
    def test_validate_allocate_error_returns_2(
        self, tmp_path, monkeypatch, capsys, exc
    ):
        """allocate_iteration exceptions all return exit 2 with ERROR on stderr."""
        from clauditor.workspace import InvalidSkillNameError

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())

        # Late-bind InvalidSkillNameError so the test import still runs.
        side_effect = (
            InvalidSkillNameError("bad/name")
            if exc == "InvalidSkillNameError"
            else exc
        )
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.cli.validate.allocate_iteration",
                side_effect=side_effect,
            ),
        ):
            rc = main(["validate", "skill.md"])

        assert rc == 2
        assert "ERROR" in capsys.readouterr().err

    def test_validate_staging_exception_aborts_and_reraises(
        self, tmp_path, monkeypatch
    ):
        """A raise inside the staging block calls workspace.abort() and
        re-raises, leaving no iteration published and no iteration-N-tmp
        dir behind. Exercises the generic except branch."""
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="hello world", duration_seconds=0.5,
        )

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.cli.validate.run_assertions",
                side_effect=RuntimeError("boom in staging"),
            ),
            pytest.raises(RuntimeError, match="boom in staging"),
        ):
            main(["validate", "skill.md"])

        clauditor_dir = tmp_path / ".clauditor"
        assert not (clauditor_dir / "iteration-1").exists()
        assert not (clauditor_dir / "iteration-1-tmp").exists()


class TestCmdSuggest:
    """Tests for the suggest subcommand (US-005 / DEC-008 exit codes)."""

    def _write_skill(self, tmp_path, text="# My Skill\n\nThis skill does things.\n"):
        p = tmp_path / "my-skill.md"
        p.write_text(text)
        return p

    def _write_grading_json(self, skill_dir, *, all_pass=True):
        skill_dir.mkdir(parents=True, exist_ok=True)
        results = [
            GradingResult(
                id="c1",
                criterion="Is the output correct?",
                passed=all_pass,
                score=0.9 if all_pass else 0.2,
                evidence="e",
                reasoning="r",
            )
        ]
        report = GradingReport(
            skill_name="my-skill",
            model="claude-sonnet-4-6",
            results=results,
            duration_seconds=0.0,
            thresholds=GradeThresholds(),
            metrics={},
        )
        (skill_dir / "grading.json").write_text(report.to_json())

    def _write_assertions(self, skill_dir, *, passed: bool):
        # Schema mirrors cmd_grade at cli.py:849 — results nested under
        # runs[].results so load_suggest_input exercises the real path.
        payload = {
            "schema_version": 1,
            "skill": "my-skill",
            "iteration": 1,
            "runs": [{
                "run": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "results": [{
                    "id": "a1",
                    "name": "contains hello",
                    "passed": passed,
                    "kind": "contains",
                    "message": "ok" if passed else "no match",
                    "transcript_path": None,
                }],
            }],
        }
        (skill_dir / "assertions.json").write_text(json.dumps(payload))

    def _write_passing_assertions(self, skill_dir):
        self._write_assertions(skill_dir, passed=True)

    def _write_failing_assertions(self, skill_dir):
        self._write_assertions(skill_dir, passed=False)

    def _fake_report(self, **overrides):
        from clauditor.suggest import EditProposal, SuggestReport

        defaults = dict(
            skill_name="my-skill",
            model="claude-sonnet-4-6",
            generated_at="2026-01-01T00:00:00.000000Z",
            source_iteration=1,
            source_grading_path=".clauditor/iteration-1/my-skill/grading.json",
            input_tokens=10,
            output_tokens=20,
            duration_seconds=0.5,
            edit_proposals=[
                EditProposal(
                    id="edit-0",
                    anchor="This skill does things.",
                    replacement="This skill does things correctly.",
                    rationale="clarity",
                    confidence=0.9,
                    motivated_by=["a1"],
                )
            ],
            summary_rationale="make it clearer",
            validation_errors=[],
            parse_error=None,
            api_error=None,
        )
        defaults.update(overrides)
        return SuggestReport(**defaults)

    def _setup_failing_run(self, tmp_path, monkeypatch):
        """Stage the common "failing iteration-1 run" fixture used by most
        suggest tests: a .git marker, the skill file, a failing grading.json,
        and a failing assertions.json under iteration-1/my-skill."""
        (tmp_path / ".git").mkdir()
        self._write_skill(tmp_path)
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "my-skill"
        self._write_grading_json(skill_dir, all_pass=False)
        self._write_failing_assertions(skill_dir)
        monkeypatch.chdir(tmp_path)
        return skill_dir

    def test_no_prior_grade_exits_1_with_message(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".git").mkdir()
        self._write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["suggest", "my-skill.md"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "clauditor grade" in err
        # No sidecar created on failure.
        assert not (tmp_path / ".clauditor" / "suggestions").exists()

    def test_zero_failures_exits_0_without_calling_sonnet(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / ".git").mkdir()
        self._write_skill(tmp_path)
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "my-skill"
        self._write_grading_json(skill_dir, all_pass=True)
        self._write_passing_assertions(skill_dir)
        monkeypatch.chdir(tmp_path)

        sentinel = AsyncMock()
        with patch("clauditor.cli.suggest.propose_edits", new=sentinel):
            rc = main(["suggest", "my-skill.md"])

        assert rc == 0
        sentinel.assert_not_called()
        err = capsys.readouterr().err
        assert "No improvement suggestions" in err
        assert not (tmp_path / ".clauditor" / "suggestions").exists()

    def test_success_prints_diff_and_writes_sidecar(
        self, tmp_path, monkeypatch, capsys
    ):
        self._setup_failing_run(tmp_path, monkeypatch)
        report = self._fake_report()
        with patch(
            "clauditor.cli.suggest.propose_edits",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["suggest", "my-skill.md"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "--- SKILL.md" in out
        assert "+++ SKILL.md (proposed)" in out

        sidecar_dir = tmp_path / ".clauditor" / "suggestions"
        assert sidecar_dir.is_dir()
        jsons = list(sidecar_dir.glob("my-skill-*.json"))
        diffs = list(sidecar_dir.glob("my-skill-*.diff"))
        assert len(jsons) == 1
        assert len(diffs) == 1

    def test_modern_layout_locates_iteration_by_parent_dir_name(
        self, tmp_path, monkeypatch, capsys
    ):
        """Regression for #100: modern-layout ``<dir>/SKILL.md`` must
        derive ``skill_name`` from the parent dir (or frontmatter
        ``name:``), not from ``skill_path.stem`` which returns the
        literal ``"SKILL"``. Pre-fix this test errored with
        "no iteration under ... contains SKILL/grading.json"."""
        (tmp_path / ".git").mkdir()
        skill_dir_src = tmp_path / ".claude" / "skills" / "my-skill"
        skill_dir_src.mkdir(parents=True)
        skill_md = skill_dir_src / "SKILL.md"
        skill_md.write_text(
            "---\n"
            "name: my-skill\n"
            "description: does things\n"
            "---\n"
            "\n"
            "# My Skill\n"
            "\n"
            "This skill does things.\n"
        )
        iteration_dir = tmp_path / ".clauditor" / "iteration-1" / "my-skill"
        self._write_grading_json(iteration_dir, all_pass=False)
        self._write_failing_assertions(iteration_dir)
        monkeypatch.chdir(tmp_path)

        report = self._fake_report()
        with patch(
            "clauditor.cli.suggest.propose_edits",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["suggest", ".claude/skills/my-skill/SKILL.md"])

        err = capsys.readouterr().err
        assert "SKILL/grading.json" not in err, (
            f"suggest used literal SKILL stem as skill_name: {err!r}"
        )
        assert rc == 0, f"expected exit 0, got {rc}; stderr={err!r}"

    def test_json_flag_prints_sidecar_json_to_stdout(
        self, tmp_path, monkeypatch, capsys
    ):
        self._setup_failing_run(tmp_path, monkeypatch)
        report = self._fake_report()
        with patch(
            "clauditor.cli.suggest.propose_edits",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["suggest", "my-skill.md", "--json"])

        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert list(data.keys())[0] == "schema_version"
        assert data["schema_version"] == 1
        assert data["skill_name"] == "my-skill"

    def test_from_iteration_is_forwarded(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        self._write_skill(tmp_path)
        # Create iteration-1 AND iteration-3; request iteration-1 explicitly.
        skill_dir_1 = tmp_path / ".clauditor" / "iteration-1" / "my-skill"
        self._write_grading_json(skill_dir_1, all_pass=False)
        self._write_failing_assertions(skill_dir_1)
        skill_dir_3 = tmp_path / ".clauditor" / "iteration-3" / "my-skill"
        self._write_grading_json(skill_dir_3, all_pass=False)
        self._write_failing_assertions(skill_dir_3)
        monkeypatch.chdir(tmp_path)

        captured = {}

        async def _fake_propose(suggest_input, *, model=None, transport="auto"):
            captured["source_iteration"] = suggest_input.source_iteration
            return self._fake_report(source_iteration=suggest_input.source_iteration)

        with patch("clauditor.cli.suggest.propose_edits", new=_fake_propose):
            rc = main(["suggest", "my-skill.md", "--from-iteration", "1"])

        assert rc == 0
        assert captured["source_iteration"] == 1

    def test_with_transcripts_forwarded(self, tmp_path, monkeypatch):
        skill_dir = self._setup_failing_run(tmp_path, monkeypatch)
        run_dir = skill_dir / "run-0"
        run_dir.mkdir()
        (run_dir / "output.jsonl").write_text(
            json.dumps({"type": "assistant", "text": "hi"}) + "\n"
        )

        captured = {}

        async def _fake_propose(suggest_input, *, model=None, transport="auto"):
            captured["transcripts"] = suggest_input.transcript_events
            return self._fake_report()

        with patch("clauditor.cli.suggest.propose_edits", new=_fake_propose):
            rc = main(
                ["suggest", "my-skill.md", "--with-transcripts"]
            )

        assert rc == 0
        assert captured["transcripts"] is not None
        assert len(captured["transcripts"]) == 1
        assert len(captured["transcripts"][0]) == 1

    @pytest.mark.parametrize(
        "report_overrides, expected_rc, expected_err_substrs",
        [
            pytest.param(
                dict(
                    api_error="anthropic API error: RuntimeError('boom')",
                    edit_proposals=[],
                    summary_rationale="",
                ),
                3,
                ("anthropic API error",),
                id="anthropic-error-rc3",
            ),
            pytest.param(
                dict(
                    parse_error="ValueError: missing 'edits' key",
                    edit_proposals=[],
                    summary_rationale="",
                ),
                1,
                ("unparseable JSON",),
                id="parse-error-rc1",
            ),
            pytest.param(
                dict(
                    edit_proposals=[],
                    validation_errors=[
                        "edit-0 (motivated_by=['a1']): anchor not found in SKILL.md"
                    ],
                ),
                2,
                ("anchor validation", "edit-0"),
                id="anchor-validation-rc2",
            ),
        ],
    )
    def test_suggest_report_errors_no_sidecar(
        self,
        tmp_path,
        monkeypatch,
        capsys,
        report_overrides,
        expected_rc,
        expected_err_substrs,
    ):
        """Various SuggestReport error fields surface with distinct exit codes
        and no sidecar is written."""
        self._setup_failing_run(tmp_path, monkeypatch)
        report = self._fake_report(**report_overrides)
        with patch(
            "clauditor.cli.suggest.propose_edits",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["suggest", "my-skill.md"])

        assert rc == expected_rc
        err = capsys.readouterr().err
        for s in expected_err_substrs:
            assert s in err
        assert not (tmp_path / ".clauditor" / "suggestions").exists()

    def test_verbose_emits_stderr_bundle_summary(
        self, tmp_path, monkeypatch, capsys
    ):
        self._setup_failing_run(tmp_path, monkeypatch)
        report = self._fake_report()
        with patch(
            "clauditor.cli.suggest.propose_edits",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["suggest", "my-skill.md", "-v"])

        assert rc == 0
        err = capsys.readouterr().err
        assert "from iteration" in err
        assert "failing_assertions=1" in err

    def test_write_sidecar_oserror_exits_1_with_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        # Regression: an OSError from write_sidecar (disk full, read-only
        # .clauditor, whatever) must exit 1 with a stderr message, not
        # propagate a bare traceback through cmd_suggest.
        self._setup_failing_run(tmp_path, monkeypatch)
        report = self._fake_report()
        with (
            patch(
                "clauditor.cli.suggest.propose_edits",
                new=AsyncMock(return_value=report),
            ),
            patch(
                "clauditor.cli.suggest.write_sidecar",
                side_effect=OSError("disk full"),
            ),
        ):
            rc = main(["suggest", "my-skill.md"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "could not write sidecar" in err
        assert "disk full" in err

    def test_skill_file_not_found_exits_1_with_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        # Coverage: the early "skill file not found" short-circuit
        # before any signal loading happens.
        monkeypatch.chdir(tmp_path)
        rc = main(["suggest", "nonexistent.md"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "skill file not found" in err

    def test_invalid_skill_name_exits_1_with_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        # Regression (Copilot finding): a skill file whose stem fails
        # workspace.validate_skill_name (leading dot, space, etc.) used
        # to leak InvalidSkillNameError out of _cmd_suggest_impl as a
        # bare traceback. Catch it and exit 1 with a clean stderr
        # message.
        (tmp_path / ".git").mkdir()
        # Name with a leading dot — skill_path.stem is ".hidden" which
        # validate_skill_name rejects. The file exists so the early
        # file-not-found branch doesn't short-circuit.
        bad = tmp_path / ".hidden.md"
        bad.write_text("# Skill\n\nDo the thing.\n")
        monkeypatch.chdir(tmp_path)

        rc = main(["suggest", str(bad.name)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "invalid skill name" in err

    def test_oserror_during_load_exits_1_with_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        # Regression: if grading.json (or SKILL.md) vanishes between the
        # exists-check and the read, load_suggest_input raises
        # FileNotFoundError. The CLI must catch OSError and exit 1
        # instead of leaking a traceback.
        self._setup_failing_run(tmp_path, monkeypatch)

        with patch(
            "clauditor.cli.suggest.load_suggest_input",
            side_effect=FileNotFoundError(
                "iteration-1/my-skill/grading.json vanished"
            ),
        ):
            rc = main(["suggest", "my-skill.md"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "could not load grade-run signals" in err
        assert "my-skill" in err

    def test_non_utf8_skill_file_exits_1(
        self, tmp_path, monkeypatch, capsys
    ):
        # Regression: SKILL.md with invalid UTF-8 bytes must exit 1
        # cleanly instead of propagating a UnicodeDecodeError traceback.
        (tmp_path / ".git").mkdir()
        skill_dir = tmp_path / ".clauditor" / "iteration-1" / "my-skill"
        self._write_grading_json(skill_dir, all_pass=False)
        self._write_failing_assertions(skill_dir)
        # Invalid UTF-8 sequence (lone continuation byte).
        (tmp_path / "my-skill.md").write_bytes(b"# Skill\n\n\x80 bad\n")
        monkeypatch.chdir(tmp_path)

        rc = main(["suggest", "my-skill.md"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "UTF-8" in err or "decode" in err


class TestLoadSpecOrReport:
    """Direct tests for ``cli._load_spec_or_report`` I/O error handling.

    US-005 (#62) expanded the except clause from ``FileNotFoundError``
    only to ``(FileNotFoundError, OSError)`` with branched messages:
    missing file keeps the existing ``clauditor init`` hint, other
    ``OSError`` subclasses get a generic ``ERROR: cannot read ...``
    message. Traces to DEC-010 of ``plans/super/62-skill-md-layout.md``.
    """

    def test_file_not_found_keeps_init_hint_message(self, capsys):
        """FileNotFoundError preserves the byte-identical init-hint message."""
        from clauditor.cli import _load_spec_or_report

        with patch(
            "clauditor.cli.SkillSpec.from_file",
            side_effect=FileNotFoundError(
                "Skill file not found: missing.md"
            ),
        ):
            result = _load_spec_or_report("missing.md", None)

        assert result is None
        err = capsys.readouterr().err
        # Byte-identical to the pre-US-005 message: name the path and
        # suggest `clauditor init` as the next step.
        assert (
            "ERROR: Skill file not found: missing.md. "
            "Run 'clauditor init missing.md' to create one."
        ) in err

    def test_permission_error_emits_cannot_read_message(self, capsys):
        """PermissionError (OSError subclass) routes to the generic branch."""
        from clauditor.cli import _load_spec_or_report

        with patch(
            "clauditor.cli.SkillSpec.from_file",
            side_effect=PermissionError("Permission denied"),
        ):
            result = _load_spec_or_report("protected.md", None)

        assert result is None
        err = capsys.readouterr().err
        assert "ERROR: cannot read protected.md: Permission denied" in err
        # The init hint must NOT appear on the generic-OSError branch —
        # the file exists, it's just unreadable.
        assert "clauditor init" not in err

    def test_is_a_directory_error_emits_cannot_read_message(
        self, tmp_path, capsys
    ):
        """IsADirectoryError (OSError subclass) routes to the generic branch.

        Pass ``tmp_path`` itself (a directory) as the skill path; the
        real ``SkillSpec.from_file`` will try to ``read_text()`` it and
        raise ``IsADirectoryError``. This exercises the helper against
        a real OS error rather than a mocked one.
        """
        from clauditor.cli import _load_spec_or_report

        result = _load_spec_or_report(str(tmp_path), None)

        assert result is None
        err = capsys.readouterr().err
        assert f"ERROR: cannot read {tmp_path}:" in err
        assert "clauditor init" not in err

    def test_unicode_decode_error_emits_cannot_read_message(self, capsys):
        """UnicodeDecodeError (ValueError subclass) routes to the generic branch.

        ``SkillSpec.from_file`` reads the skill file with
        ``encoding="utf-8"``; a non-UTF-8 file raises
        ``UnicodeDecodeError``, which is NOT an ``OSError`` subclass.
        The except clause explicitly catches both so the user sees a
        clean error message instead of an uncaught traceback.
        """
        from clauditor.cli import _load_spec_or_report

        with patch(
            "clauditor.cli.SkillSpec.from_file",
            side_effect=UnicodeDecodeError(
                "utf-8", b"\xff\xfe", 0, 1, "invalid start byte"
            ),
        ):
            result = _load_spec_or_report("weird.md", None)

        assert result is None
        err = capsys.readouterr().err
        assert "ERROR: cannot read weird.md:" in err
        assert "clauditor init" not in err


class TestRenderSkillError:
    """Direct tests for ``cli._render_skill_error`` (US-005 / #63).

    Pure helper: no I/O, reads only from the passed ``SkillResult`` and
    module-level constants. Traces to DEC-002 (warnings as ``(warning:
    ...)`` trailer — neutral label since ``warnings`` may contain
    non-stderr entries like interactive-hang tags or malformed-line
    notices), DEC-003 (1000-char truncation), DEC-004 (category
    hint as a second line), DEC-011 (signature + ``_CATEGORY_HINTS``
    lookup) of ``plans/super/63-runner-error-surfacing.md``.
    """

    def _mk(
        self,
        *,
        error: str | None = None,
        error_category: str | None = None,
        warnings: list[str] | None = None,
    ) -> SkillResult:
        return SkillResult(
            output="",
            exit_code=-1,
            skill_name="s",
            args="",
            error=error,
            error_category=error_category,
            warnings=warnings if warnings is not None else [],
        )

    def test_error_none_no_category_returns_unknown_fallback(self):
        """No error, no category → default unknown fallback."""
        from clauditor.cli import _render_skill_error

        result = self._mk(error=None, error_category=None, warnings=[])
        assert _render_skill_error(result) == "Unknown error"

    def test_error_none_rate_limit_category_returns_hint_only(self):
        """No error text, known category → hint is the base text (no duplicate)."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error=None, error_category="rate_limit", warnings=[]
        )
        assert (
            _render_skill_error(result)
            == "Hint: retry in ~60s (rate limit)"
        )

    @pytest.mark.parametrize(
        ("category", "expected"),
        [
            ("rate_limit", "Hint: retry in ~60s (rate limit)"),
            (
                "auth",
                "Hint: check the ANTHROPIC_API_KEY environment variable",
            ),
            (
                "interactive",
                "Hint: ensure all parameters are in test_args; "
                "/clauditor cannot drive interactive skills",
            ),
            (
                "background-task",
                "Hint: skill launched Task(run_in_background=true) and "
                "exited before polling — claude -p does not poll "
                "background tasks, so output is likely truncated",
            ),
            (
                "timeout",
                "Hint: skill exceeded the run timeout — "
                "increase the timeout with --timeout SECONDS (e.g. --timeout 600)",
            ),
            (
                "subprocess",
                "Hint: the claude CLI itself errored — see stream_events",
            ),
            ("api", "Hint: see the error text above"),
        ],
    )
    def test_error_none_each_category_returns_corresponding_hint(
        self, category, expected
    ):
        """Every category key in _CATEGORY_HINTS resolves to its hint text."""
        from clauditor.cli import _CATEGORY_HINTS, _render_skill_error

        # Sanity: the parametrize table matches the module-level dict.
        assert _CATEGORY_HINTS[category] == expected

        result = self._mk(
            error=None, error_category=category, warnings=[]
        )
        assert _render_skill_error(result) == expected

    def test_error_set_no_category_returns_error_only(self):
        """Error text with no category → error text alone, no hint."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error="API Error X", error_category=None, warnings=[]
        )
        assert _render_skill_error(result) == "API Error X"

    def test_error_set_and_category_returns_two_lines(self):
        """Error text + known category → error / hint on two lines."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error="API Error: 429 rate limit",
            error_category="rate_limit",
            warnings=[],
        )
        assert _render_skill_error(result) == (
            "API Error: 429 rate limit\n"
            "Hint: retry in ~60s (rate limit)"
        )

    def test_long_error_text_truncated(self):
        """Error > 1000 chars is truncated with a stream_events pointer."""
        from clauditor.cli import _ERROR_TEXT_MAX_CHARS, _render_skill_error

        assert _ERROR_TEXT_MAX_CHARS == 1000  # anchor the constant
        result = self._mk(error="X" * 2000, warnings=[])
        out = _render_skill_error(result)

        expected = "X" * 1000 + " ... (truncated; see stream_events)"
        assert out == expected
        # Exact length: 1000 X's + the suffix.
        assert len(out) == 1000 + len(" ... (truncated; see stream_events)")
        assert out.endswith(" ... (truncated; see stream_events)")

    def test_short_error_text_not_truncated(self):
        """Error <= 1000 chars is returned as-is (no truncation marker)."""
        from clauditor.cli import _render_skill_error

        result = self._mk(error="X" * 100, warnings=[])
        out = _render_skill_error(result)
        assert "(truncated" not in out
        assert out == "X" * 100

    def test_warnings_append_trailer(self):
        """Non-empty warnings[0] → `(warning: <first-non-empty-line>)` trailer."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error="boom",
            error_category=None,
            warnings=["line1", "line3"],  # first-warning only is rendered
        )
        assert _render_skill_error(result) == "boom\n(warning: line1)"

    def test_multiline_warning_uses_first_nonempty_line(self):
        """Leading empty/whitespace lines are skipped; first non-empty wins."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error="boom",
            warnings=["\n\n  real first line\nsecond line"],
        )
        # We pick the first line whose .strip() is non-empty and emit
        # its stripped form — surrounding whitespace is dropped for
        # cleanliness on the one-line trailer.
        assert (
            _render_skill_error(result)
            == "boom\n(warning: real first line)"
        )

    def test_warnings_all_empty_no_trailer(self):
        """Whitespace-only warnings[0] → no trailer; just the error text."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error="boom",
            warnings=["", "   "],
        )
        assert _render_skill_error(result) == "boom"

    def test_warnings_plus_category_produces_three_components(self):
        """Error + category + warning → three lines in order."""
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error="API Error: 429",
            error_category="rate_limit",
            warnings=["stderr blather"],
        )
        assert _render_skill_error(result) == (
            "API Error: 429\n"
            "Hint: retry in ~60s (rate limit)\n"
            "(warning: stderr blather)"
        )

    def test_empty_string_error_treated_as_none(self):
        """``error=""`` is equivalent to ``error=None`` (matches spec.py idiom)."""
        from clauditor.cli import _render_skill_error

        result = self._mk(error="", error_category=None, warnings=[])
        assert _render_skill_error(result) == "Unknown error"

    def test_custom_unknown_fallback(self):
        """The ``unknown_fallback`` kwarg overrides the default."""
        from clauditor.cli import _render_skill_error

        result = self._mk(error=None, error_category=None, warnings=[])
        assert (
            _render_skill_error(result, unknown_fallback="nothing here")
            == "nothing here"
        )

    def test_unknown_category_uses_fallback(self):
        """An error_category that's not a key in _CATEGORY_HINTS falls back.

        Python's type system can't enforce the ``Literal[...]`` at
        runtime, so the helper defensively checks membership in
        ``_CATEGORY_HINTS`` rather than ``is not None``.
        """
        from clauditor.cli import _render_skill_error

        result = self._mk(
            error=None,
            error_category="some_unexpected_string",  # type: ignore[arg-type]
            warnings=[],
        )
        assert _render_skill_error(result) == "Unknown error"


# ---------------------------------------------------------------------------
# US-006 / DEC-011 / DEC-012 regression: the five CLI commands that surface
# skill-run errors now route through ``_render_skill_error``. Cover the two
# ticket repros (429 + interactive-hang) and at least one per-command hit so
# we catch any regression that reverts ``{result.error}`` inline substitution.
# ---------------------------------------------------------------------------


class TestCmdValidateErrorSurfacingRegression:
    """Regression guard for #63 ticket repros at the ``validate`` call site.

    Before US-005/US-006, ``cmd_validate`` printed
    ``f"ERROR: Skill failed to run: {skill_result.error}"`` which rendered
    ``"... : None"`` when ``error`` was ``None`` (the 429 / interactive-
    hang repros). After adopting ``_render_skill_error``, the provider
    error text is surfaced verbatim AND a ``Hint: ...`` line is appended.
    Traces to DEC-011 (helper returns tail only), DEC-012 (all five
    commands) of ``plans/super/63-runner-error-surfacing.md``.
    """

    def test_validate_429_error_surfaces_actual_text(
        self, tmp_path, monkeypatch, capsys
    ):
        """A 429 ``rate_limit`` failure prints the full error + hint line.

        Ticket repro: before the helper, stderr read
        ``"ERROR: Skill failed to run: None"``. After, stderr carries the
        actual ``API Error: ...`` payload and the ``rate_limit`` hint.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        spec.run.return_value = make_skill_result(
            output="",
            exit_code=1,
            duration_seconds=0.5,
            error=(
                "API Error: Request rejected (429). "
                "Rate limit exceeded for your organization."
            ),
        )
        spec.run.return_value.error_category = "rate_limit"

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "API Error: Request rejected (429)" in err
        assert "Rate limit exceeded for your organization." in err
        assert "Hint: retry in ~60s (rate limit)" in err
        assert ": None" not in err
        assert "Unknown error" not in err

    def test_validate_interactive_hang_surfaces_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """Interactive-hang heuristic prints its warning + hint line.

        Ticket repro: before the helper, an interactive-hang reach at
        a failing run path rendered ``"... : None"`` because
        ``error_category="interactive"`` leaves ``result.error`` ``None``.
        After, the first warning line is rendered as the base text and
        the interactive hint is appended.
        """
        from clauditor.runner import _INTERACTIVE_HANG_WARNING

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        spec = _make_spec(eval_spec=_make_eval_spec())
        # Synthesize a failing run (output="") with interactive-hang
        # signals so the ``not succeeded`` branch fires the helper.
        # Realistic interactive-hang shape: the skill emitted a question
        # as assistant text and exited cleanly, so `succeeded` is True
        # (non-empty output, exit_code=0). Only `succeeded_cleanly`
        # catches it — proving the CLI guard is strict.
        spec.run.return_value = make_skill_result(
            output="What color do you want?",
            exit_code=0,
            duration_seconds=0.5,
            error=None,
        )
        spec.run.return_value.error_category = "interactive"
        spec.run.return_value.warnings = [_INTERACTIVE_HANG_WARNING]

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "interactive-hang:" in err
        # Interactive hint from _CATEGORY_HINTS (the tail after the
        # first-warning-as-base).
        assert "/clauditor cannot drive interactive skills" in err
        assert "failed to run: None" not in err
        assert "Unknown error" not in err


class TestCmdRunErrorSurfacingRegression:
    """Regression guard for ``cmd_run`` adopting ``_render_skill_error``."""

    def test_run_429_error_surfaces_actual_text(self, capsys):
        """A 429 ``rate_limit`` failure renders full error + hint line."""
        mock_runner = MagicMock()
        result = make_skill_result(
            output="",
            exit_code=1,
            skill_name="my-skill",
            duration_seconds=0.5,
            error=(
                "API Error: Request rejected (429). "
                "Rate limit exceeded for your organization."
            ),
        )
        result.error_category = "rate_limit"
        mock_runner.run.return_value = result

        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "API Error: Request rejected (429)" in err
        assert "Hint: retry in ~60s (rate limit)" in err
        assert ": None" not in err
        assert "Unknown error" not in err

    def test_run_interactive_hang_surfaces_warning(self, capsys):
        """Interactive-hang under ``cmd_run`` now renders (previously the
        ``if result.error:`` guard silently suppressed it since
        ``result.error`` is ``None`` for the heuristic).
        """
        from clauditor.runner import _INTERACTIVE_HANG_WARNING

        mock_runner = MagicMock()
        result = make_skill_result(
            output="Would you like me to continue?",
            exit_code=0,
            skill_name="my-skill",
            duration_seconds=0.5,
            error=None,
        )
        result.error_category = "interactive"
        result.warnings = [_INTERACTIVE_HANG_WARNING]
        mock_runner.run.return_value = result

        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        err = capsys.readouterr().err
        # ``cmd_run`` returns ``result.exit_code`` verbatim; interactive
        # hangs exit 0 at the subprocess level, but the render path now
        # still fires because ``succeeded_cleanly`` is False.
        assert rc == 0
        assert "interactive-hang:" in err
        assert "/clauditor cannot drive interactive skills" in err
        assert "Unknown error" not in err

    def test_run_happy_path_no_error_rendered(self, capsys):
        """A clean run emits no ERROR line (guard now checks
        ``succeeded_cleanly``)."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_skill_result(
            output="skill output here",
            skill_name="my-skill",
            duration_seconds=1.0,
        )

        with patch("clauditor.cli.run.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "skill output here" in captured.out
        assert "ERROR" not in captured.err


class TestCmdCaptureErrorSurfacingRegression:
    """Regression guard for ``cmd_capture`` adopting ``_render_skill_error``."""

    def test_capture_429_error_surfaces_actual_text(
        self, tmp_path, monkeypatch, capsys
    ):
        """A 429 ``rate_limit`` failure renders full error + hint line."""
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        result = make_skill_result(
            output="",
            exit_code=2,
            skill_name="find-restaurants",
            duration_seconds=0.1,
            error=(
                "API Error: Request rejected (429). "
                "Rate limit exceeded for your organization."
            ),
        )
        result.error_category = "rate_limit"
        mock_runner.run.return_value = result

        with patch("clauditor.cli.capture.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "API Error: Request rejected (429)" in err
        assert "Hint: retry in ~60s (rate limit)" in err
        assert ": None" not in err
        assert "Unknown error" not in err


class TestCmdGradeErrorSurfacingRegression:
    """Regression guard for ``cmd_grade`` adopting ``_render_skill_error``.

    Covers both call sites: primary-run failure (~line 369) and
    variance-run failure (~line 395).
    """

    def test_grade_primary_429_error_surfaces_actual_text(
        self, tmp_path, monkeypatch, capsys
    ):
        """Primary-run 429 surfaces the full error + hint line."""
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        result = make_skill_result(
            output="",
            exit_code=1,
            duration_seconds=0.5,
            error=(
                "API Error: Request rejected (429). "
                "Rate limit exceeded for your organization."
            ),
        )
        result.error_category = "rate_limit"
        spec.run = MagicMock(return_value=result)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "API Error: Request rejected (429)" in err
        assert "Hint: retry in ~60s (rate limit)" in err
        assert ": None" not in err
        assert "Unknown error" not in err

    def test_grade_variance_429_error_surfaces_actual_text(
        self, tmp_path, monkeypatch, capsys
    ):
        """Variance-run 429 surfaces the full error + hint line.

        Per ``.claude/rules/mock-side-effect-for-distinct-calls.md``:
        distinct side_effect values per call since primary succeeds and
        variance fails.
        """
        monkeypatch.chdir(tmp_path)
        spec = _make_spec(eval_spec=_make_eval_spec())
        primary_ok = make_skill_result(
            output="primary output",
            input_tokens=10,
            output_tokens=5,
            duration_seconds=0.5,
        )
        variance_bad = make_skill_result(
            output="",
            exit_code=1,
            error=(
                "API Error: Request rejected (429). "
                "Rate limit exceeded for your organization."
            ),
        )
        variance_bad.error_category = "rate_limit"
        spec.run = MagicMock(side_effect=[primary_ok, variance_bad])

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md", "--variance", "1"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "Variance skill run failed" in err
        assert "API Error: Request rejected (429)" in err
        assert "Hint: retry in ~60s (rate limit)" in err
        assert ": None" not in err
        assert "Unknown error" not in err


class TestCmdExtractErrorSurfacingRegression:
    """Regression guard for ``cmd_extract`` adopting ``_render_skill_error``."""

    def test_extract_429_error_surfaces_actual_text(self, capsys):
        """A 429 ``rate_limit`` failure renders full error + hint line."""
        eval_spec = _make_eval_spec(sections=_make_sections())
        spec = _make_spec(eval_spec=eval_spec)
        result = make_skill_result(
            output="",
            exit_code=1,
            duration_seconds=0.2,
            error=(
                "API Error: Request rejected (429). "
                "Rate limit exceeded for your organization."
            ),
        )
        result.error_category = "rate_limit"
        spec.run.return_value = result

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["extract", "skill.md"])

        err = capsys.readouterr().err
        assert rc == 1
        assert "Skill failed:" in err
        assert "API Error: Request rejected (429)" in err
        assert "Hint: retry in ~60s (rate limit)" in err
        assert ": None" not in err
        assert "Unknown error" not in err


class TestResolveGraderTransport:
    """Regression tests for the four-layer transport precedence in grader commands.

    ``_resolve_grader_transport`` is a shared helper used by all six LLM-mediated
    CLI commands.  These tests verify the precedence chain (CLI > env > spec >
    default) and that whitespace-only env values are treated as unset.
    """

    def test_default_is_auto(self, monkeypatch):
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import _resolve_grader_transport
        args = argparse.Namespace(transport=None)
        result = _resolve_grader_transport(args, None)
        assert result == "auto"

    def test_cli_flag_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "cli")
        from clauditor.cli import _resolve_grader_transport
        args = argparse.Namespace(transport="api")
        result = _resolve_grader_transport(args, None)
        assert result == "api"

    def test_env_wins_over_spec(self, monkeypatch):
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "api")
        from clauditor.cli import _resolve_grader_transport
        eval_spec = MagicMock()
        eval_spec.transport = "cli"
        args = argparse.Namespace(transport=None)
        result = _resolve_grader_transport(args, eval_spec)
        assert result == "api"

    def test_spec_wins_over_default(self, monkeypatch):
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import _resolve_grader_transport
        eval_spec = MagicMock()
        eval_spec.transport = "cli"
        args = argparse.Namespace(transport=None)
        result = _resolve_grader_transport(args, eval_spec)
        assert result == "cli"

    def test_whitespace_only_env_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "   ")
        from clauditor.cli import _resolve_grader_transport
        eval_spec = MagicMock()
        eval_spec.transport = "cli"
        args = argparse.Namespace(transport=None)
        result = _resolve_grader_transport(args, eval_spec)
        # whitespace-only env → treated as None → spec wins
        assert result == "cli"

    def test_no_eval_spec_falls_through_to_default(self, monkeypatch):
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import _resolve_grader_transport
        args = argparse.Namespace(transport=None)
        result = _resolve_grader_transport(args, None)
        assert result == "auto"

    def test_invalid_env_exits_2(self, monkeypatch, capsys):
        """Invalid ``CLAUDITOR_TRANSPORT`` value → SystemExit(2) + ERROR line."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "sdk")
        from clauditor.cli import _resolve_grader_transport
        args = argparse.Namespace(transport=None)
        with pytest.raises(SystemExit) as exc_info:
            _resolve_grader_transport(args, None)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "sdk" in captured.err
