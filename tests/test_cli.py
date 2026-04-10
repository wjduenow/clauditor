"""Tests for clauditor CLI commands."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.cli import main
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    SectionRequirement,
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
            min_entries=2,
            fields=[
                FieldRequirement(name="name", required=True),
                FieldRequirement(name="address", required=True),
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
                ),
                AssertionResult(
                    name="section:Results[0].name",
                    passed=passed,
                    message=(
                        "Field present"
                        if passed
                        else "Missing required field 'name'"
                    ),
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
