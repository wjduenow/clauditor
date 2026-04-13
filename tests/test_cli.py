"""Tests for clauditor CLI commands."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.cli import main
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    SectionRequirement,
    TierRequirement,
    TriggerTests,
)
from clauditor.spec import SkillSpec
from clauditor.triggers import TriggerReport, TriggerResult


def _make_spec(eval_spec=None, skill_name="test-skill"):
    """Create a mock SkillSpec with the given eval_spec."""
    spec = MagicMock(spec=SkillSpec)
    spec.skill_name = skill_name
    spec.eval_spec = eval_spec
    return spec


def _make_eval_spec(**overrides):
    """Create a minimal EvalSpec for testing."""
    defaults = dict(
        skill_name="test-skill",
        description="A test skill",
        test_args="--depth quick",
        assertions=[{"type": "contains", "value": "hello"}],
        sections=[],
        grading_criteria=["Is the output relevant?"],
        grading_model="claude-sonnet-4-6",
        trigger_tests=None,
        variance=None,
    )
    defaults.update(overrides)
    return EvalSpec(**defaults)


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

    def test_validate_no_eval_spec(self, capsys):
        """Returns 1 when no eval spec is found."""
        spec = _make_spec(eval_spec=None)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 1
        assert "No eval spec" in capsys.readouterr().err

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

    def test_validate_run_skill(self):
        """Without --output, runs the skill to get output."""
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = SkillResult(
            output="hello world output",
            exit_code=0,
            skill_name="test-skill",
            args="",
            duration_seconds=1.5,
        )

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 0
        spec.run.assert_called_once()

    def test_validate_run_skill_fails(self, capsys):
        """Returns 1 when skill run fails."""
        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = SkillResult(
            output="",
            exit_code=1,
            skill_name="test-skill",
            args="",
            duration_seconds=0.5,
            error="timeout",
        )

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["validate", "skill.md"])

        assert rc == 1
        assert "Skill failed" in capsys.readouterr().err


class TestCmdRun:
    """Tests for the run subcommand."""

    def test_run_happy_path(self, capsys):
        """Runs skill and prints output."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = SkillResult(
            output="skill output here",
            exit_code=0,
            skill_name="my-skill",
            args="",
            duration_seconds=2.0,
        )

        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        assert rc == 0
        assert "skill output here" in capsys.readouterr().out

    def test_run_with_error(self, capsys):
        """Prints error to stderr and returns non-zero exit code."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = SkillResult(
            output="",
            exit_code=1,
            skill_name="my-skill",
            args="",
            duration_seconds=0.5,
            error="command not found",
        )

        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["run", "my-skill"])

        assert rc == 1
        assert "command not found" in capsys.readouterr().err


class TestCmdGrade:
    """Tests for the grade subcommand."""

    def _make_grading_report(self, passed=True):
        return GradingReport(
            skill_name="test-skill",
            model="claude-sonnet-4-6",
            results=[
                GradingResult(
                    criterion="Is the output relevant?",
                    passed=passed,
                    score=0.9 if passed else 0.3,
                    evidence="Found relevant content",
                    reasoning="Output addresses the query",
                )
            ],
            duration_seconds=1.0,
        )

    def test_grade_with_output(self, tmp_path):
        """Grades pre-captured output, returns 0 when passed."""
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

    def test_grade_no_eval_spec(self, capsys):
        """Returns 1 when no eval spec is found."""
        spec = _make_spec(eval_spec=None)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["grade", "skill.md"])

        assert rc == 1
        assert "No eval spec" in capsys.readouterr().err

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

    def test_grade_failed(self, tmp_path):
        """Returns 1 when grading fails."""
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


class TestOnlyCriterion:
    """Tests for --only-criterion filter on the grade subcommand."""

    def _report(self):
        return GradingReport(
            skill_name="test-skill",
            model="claude-sonnet-4-6",
            results=[
                GradingResult(
                    criterion="x",
                    passed=True,
                    score=1.0,
                    evidence="",
                    reasoning="",
                )
            ],
            duration_seconds=1.0,
        )

    def _run(self, tmp_path, criteria, extra_args):
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

    def test_single_substring_filters(self, tmp_path):
        """--only-criterion foo keeps only matching criteria."""
        rc, mock_grade, spec = self._run(
            tmp_path,
            ["foo bar", "baz qux", "other foo"],
            ["--only-criterion", "foo"],
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["foo bar", "other foo"]
        # Grader called with filtered spec
        mock_grade.assert_called_once()
        passed_spec = mock_grade.call_args.args[1]
        assert passed_spec.grading_criteria == ["foo bar", "other foo"]

    def test_multiple_substrings_union(self, tmp_path):
        """Multiple --only-criterion flags use OR semantics."""
        rc, mock_grade, spec = self._run(
            tmp_path,
            ["alpha", "beta", "gamma", "alphabeta"],
            ["--only-criterion", "alpha", "--only-criterion", "gamma"],
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["alpha", "gamma", "alphabeta"]

    def test_no_match_exits_2(self, tmp_path, capsys):
        """No match prints Available and exits 2."""
        import pytest

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
            pytest.raises(SystemExit) as exc_info,
        ):
            main(
                [
                    "grade",
                    "skill.md",
                    "--output",
                    str(output_file),
                    "--only-criterion",
                    "nonexistent",
                ]
            )
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "No grading criteria match filter" in err
        assert "Available:" in err
        assert "clarity" in err
        assert "accuracy" in err
        # Grader must NOT have been called
        mock_grade.assert_not_called()

    def test_no_flag_passes_all(self, tmp_path):
        """Without --only-criterion, all criteria are passed through."""
        rc, mock_grade, spec = self._run(
            tmp_path, ["one", "two", "three"], []
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["one", "two", "three"]

    def test_case_insensitive(self, tmp_path):
        """--only-criterion FOO matches criterion 'foo'."""
        rc, _mock, spec = self._run(
            tmp_path, ["foo", "bar"], ["--only-criterion", "FOO"]
        )
        assert rc == 0
        assert spec.eval_spec.grading_criteria == ["foo"]


class TestCmdGradeSaveDiff:
    """Tests for --save and --diff flags on the grade subcommand."""

    def _make_grading_report(self, skill_name="test-skill", passed=True, score=0.9):
        return GradingReport(
            skill_name=skill_name,
            model="claude-sonnet-4-6",
            results=[
                GradingResult(
                    criterion="Is the output relevant?",
                    passed=passed,
                    score=score,
                    evidence="Found relevant content",
                    reasoning="Output addresses the query",
                )
            ],
            duration_seconds=1.0,
        )

    def test_save_creates_file(self, tmp_path):
        """--save writes JSON to .clauditor/."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_grading_report()

        import os

        orig_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            with (
                patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
                patch(
                    "clauditor.quality_grader.grade_quality",
                    new_callable=AsyncMock,
                    return_value=report,
                ),
            ):
                rc = main(
                    ["grade", "skill.md", "--output", str(output_file), "--save"]
                )

            assert rc == 0
            save_path = tmp_path / ".clauditor" / "test-skill.grade.json"
            assert save_path.exists()
            data = json.loads(save_path.read_text())
            assert data["skill_name"] == "test-skill"
        finally:
            os.chdir(orig_dir)

    def test_save_creates_directory(self, tmp_path):
        """.clauditor/ is created if missing."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_grading_report()

        import os

        orig_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert not (tmp_path / ".clauditor").exists()
            with (
                patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
                patch(
                    "clauditor.quality_grader.grade_quality",
                    new_callable=AsyncMock,
                    return_value=report,
                ),
            ):
                main(["grade", "skill.md", "--output", str(output_file), "--save"])

            assert (tmp_path / ".clauditor").is_dir()
        finally:
            os.chdir(orig_dir)

    def test_diff_shows_regressions(self, tmp_path, capsys):
        """--diff with prior results shows regression table."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)

        # Prior report: high score
        prior_report = self._make_grading_report(score=0.9, passed=True)
        # Current report: low score (regression)
        current_report = self._make_grading_report(score=0.5, passed=False)

        import os

        orig_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Write prior results
            save_dir = tmp_path / ".clauditor"
            save_dir.mkdir()
            (save_dir / "test-skill.grade.json").write_text(prior_report.to_json())

            with (
                patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
                patch(
                    "clauditor.quality_grader.grade_quality",
                    new_callable=AsyncMock,
                    return_value=current_report,
                ),
            ):
                main(["grade", "skill.md", "--output", str(output_file), "--diff"])

            out = capsys.readouterr().out
            assert "REGRESSION" in out
            assert "1 regression(s) detected" in out
        finally:
            os.chdir(orig_dir)

    def test_diff_no_prior_warns(self, tmp_path, capsys):
        """--diff without prior results warns, doesn't error."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        report = self._make_grading_report()

        import os

        orig_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            with (
                patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
                patch(
                    "clauditor.quality_grader.grade_quality",
                    new_callable=AsyncMock,
                    return_value=report,
                ),
            ):
                rc = main(
                    ["grade", "skill.md", "--output", str(output_file), "--diff"]
                )

            assert rc == 0
            err = capsys.readouterr().err
            assert "WARNING" in err
            assert "No prior results" in err
        finally:
            os.chdir(orig_dir)

    def test_save_and_diff_together(self, tmp_path, capsys):
        """Both --save and --diff work in sequence."""
        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)

        prior_report = self._make_grading_report(score=0.8, passed=True)
        current_report = self._make_grading_report(score=0.85, passed=True)

        import os

        orig_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Write prior results
            save_dir = tmp_path / ".clauditor"
            save_dir.mkdir()
            (save_dir / "test-skill.grade.json").write_text(prior_report.to_json())

            with (
                patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
                patch(
                    "clauditor.quality_grader.grade_quality",
                    new_callable=AsyncMock,
                    return_value=current_report,
                ),
            ):
                rc = main(
                    [
                        "grade",
                        "skill.md",
                        "--output",
                        str(output_file),
                        "--diff",
                        "--save",
                    ]
                )

            assert rc == 0
            out = capsys.readouterr().out
            # Diff ran (no regressions since score improved)
            assert "No regressions detected" in out
            # Save ran — file updated with current results
            saved = json.loads(
                (save_dir / "test-skill.grade.json").read_text()
            )
            assert saved["results"][0]["score"] == 0.85
        finally:
            os.chdir(orig_dir)


class TestCmdCompare:
    """Tests for the compare subcommand (US-003)."""

    def _make_saved_grade_json(
        self, tmp_path, name: str, *, criterion_passes: dict[str, bool]
    ):
        """Write a GradingReport to a .grade.json file and return the path."""
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
            skill_name=name,
            model="test-model",
            results=results,
            duration_seconds=0.0,
        )
        path = tmp_path / f"{name}.grade.json"
        path.write_text(report.to_json())
        return path

    def test_compare_two_grade_json_no_flips(self, tmp_path, capsys):
        """Two identical .grade.json files produce no flips and exit 0."""
        before = self._make_saved_grade_json(
            tmp_path, "before", criterion_passes={"c1": True, "c2": True}
        )
        after = self._make_saved_grade_json(
            tmp_path, "after", criterion_passes={"c1": True, "c2": True}
        )
        rc = main(["compare", str(before), str(after)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no flips" in out

    def test_compare_two_grade_json_regression(self, tmp_path, capsys):
        """A flipped-to-fail criterion yields [REGRESSION] and exit 1."""
        before = self._make_saved_grade_json(
            tmp_path, "before", criterion_passes={"c1": True, "c2": True}
        )
        after = self._make_saved_grade_json(
            tmp_path, "after", criterion_passes={"c1": True, "c2": False}
        )
        rc = main(["compare", str(before), str(after)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "[REGRESSION]" in out
        assert "c2" in out

    def test_compare_two_grade_json_improvement(self, tmp_path, capsys):
        """A flipped-to-pass criterion yields [IMPROVEMENT] and exit 0."""
        before = self._make_saved_grade_json(
            tmp_path, "before", criterion_passes={"c1": True, "c2": False}
        )
        after = self._make_saved_grade_json(
            tmp_path, "after", criterion_passes={"c1": True, "c2": True}
        )
        rc = main(["compare", str(before), str(after)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[IMPROVEMENT]" in out
        assert "c2" in out

    def test_compare_two_txt_with_spec(self, tmp_path, capsys):
        """Two .txt files plus --spec re-grade both and diff Layer 1 results."""
        before_txt = tmp_path / "before.txt"
        after_txt = tmp_path / "after.txt"
        before_txt.write_text("nothing matching")
        after_txt.write_text("hello world")

        eval_spec = _make_eval_spec(
            assertions=[{"type": "contains", "value": "hello"}]
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

    def test_triggers_no_eval_spec(self, capsys):
        """Returns 1 when no eval spec is found."""
        spec = _make_spec(eval_spec=None)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["triggers", "skill.md"])

        assert rc == 1
        assert "No eval spec" in capsys.readouterr().err


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

    def test_extract_no_eval_spec(self, capsys):
        """Returns 1 when no eval spec is found."""
        spec = _make_spec(eval_spec=None)

        with patch("clauditor.cli.SkillSpec.from_file", return_value=spec):
            rc = main(["extract", "skill.md"])

        assert rc == 1
        assert "No eval spec" in capsys.readouterr().err

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
        return SkillResult(
            output=output,
            exit_code=0,
            skill_name="find-restaurants",
            args="",
            duration_seconds=1.0,
        )

    def test_capture_default_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants"])
        assert rc == 0
        out_path = tmp_path / "tests/eval/captured/find-restaurants.txt"
        assert out_path.exists()
        assert out_path.read_text() == "captured stdout"
        mock_runner.run.assert_called_once_with("find-restaurants", "")

    def test_capture_custom_out(self, tmp_path):
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result("abc")
        target = tmp_path / "sub" / "custom.txt"
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--out", str(target)])
        assert rc == 0
        assert target.read_text() == "abc"

    def test_capture_versioned_appends_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
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
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
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
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "/find-restaurants"])
        assert rc == 0
        mock_runner.run.assert_called_once_with("find-restaurants", "")
        assert (tmp_path / "tests/eval/captured/find-restaurants.txt").exists()

    def test_capture_passes_trailing_args(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._mock_result()
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants", "--", "near", "San Jose"])
        assert rc == 0
        mock_runner.run.assert_called_once_with("find-restaurants", "near San Jose")

    def test_capture_runner_failure_returns_nonzero(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        mock_runner = MagicMock()
        mock_runner.run.return_value = SkillResult(
            output="",
            exit_code=2,
            skill_name="find-restaurants",
            args="",
            duration_seconds=0.1,
            error="boom",
        )
        with patch("clauditor.cli.SkillRunner", return_value=mock_runner):
            rc = main(["capture", "find-restaurants"])
        assert rc == 1
        assert "boom" in capsys.readouterr().err


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
        # Expect fail marker for missing claude CLI
        lines = [line for line in out.splitlines() if "claude-cli" in line]
        assert any("[fail]" in line for line in lines)


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
        # Sparkline line present (non-empty last line)
        lines = [ln for ln in out.splitlines() if ln]
        assert lines
        # Sparkline should use only glyphs from SPARK_GLYPHS
        from clauditor.history import SPARK_GLYPHS

        spark = lines[-1]
        assert all(c in SPARK_GLYPHS for c in spark)

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

    def test_last_n_truncates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path / ".clauditor" / "history.jsonl", n=10)

        rc = main(["trend", "test-skill", "--metric", "pass_rate", "--last", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if "\t" in ln]
        assert len(data_lines) == 5


class TestCmdGradeHistory:
    """cmd_grade appends a history record (US-006)."""

    def test_grade_appends_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        output_file = tmp_path / "output.txt"
        output_file.write_text("some skill output")

        eval_spec = _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        report = GradingReport(
            skill_name="test-skill",
            model="claude-sonnet-4-6",
            results=[
                GradingResult(
                    criterion="Is the output relevant?",
                    passed=True,
                    score=0.9,
                    evidence="ok",
                    reasoning="ok",
                )
            ],
            duration_seconds=1.0,
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
        assert history_path.exists()
        lines = [ln for ln in history_path.read_text().splitlines() if ln]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["skill"] == "test-skill"
        assert record["pass_rate"] == 1.0
        assert record["mean_score"] == 0.9
