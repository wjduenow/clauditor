"""Tests for SkillRunner."""

import importlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import clauditor.runner as _runner_mod

importlib.reload(_runner_mod)

from clauditor.runner import SkillResult, SkillRunner  # noqa: E402


class TestRunRaw:
    def test_run_raw_returns_baseline_skill_name(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="echo")
        result = runner.run_raw("test prompt")
        assert result.skill_name == "__baseline__"

    def test_run_raw_passes_prompt_directly(self):
        """Verify run_raw sends the prompt without a skill prefix."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="some output", stderr="", returncode=0
            )
            result = runner.run_raw("find me activities in Seattle")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == [
                "claude",
                "-p",
                "find me activities in Seattle",
                "--no-input",
            ]
            assert result.skill_name == "__baseline__"
            assert result.args == "find me activities in Seattle"
            assert result.output == "some output"

    def test_run_raw_handles_timeout(self):
        runner = SkillRunner(project_dir="/tmp", timeout=1, claude_bin="claude")
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("claude", 1),
        ):
            result = runner.run_raw("test prompt")
            assert result.exit_code == -1
            assert result.skill_name == "__baseline__"
            assert "Timed out" in result.error

    def test_run_raw_handles_missing_binary(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="nonexistent-binary")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = runner.run_raw("test prompt")
            assert result.exit_code == -1
            assert result.skill_name == "__baseline__"
            assert "not found" in result.error


# ---------------------------------------------------------------------------
# SkillResult.succeeded
# ---------------------------------------------------------------------------


class TestSkillResultSucceeded:
    def _make(self, exit_code: int = 0, output: str = "some output") -> SkillResult:
        return SkillResult(
            output=output,
            exit_code=exit_code,
            skill_name="test",
            args="",
        )

    def test_succeeded_true(self):
        assert self._make(exit_code=0, output="hello").succeeded is True

    def test_succeeded_false_empty_output(self):
        assert self._make(exit_code=0, output="").succeeded is False

    def test_succeeded_false_whitespace(self):
        assert self._make(exit_code=0, output="  \n").succeeded is False

    def test_succeeded_false_nonzero_exit(self):
        assert self._make(exit_code=1, output="hello").succeeded is False


# ---------------------------------------------------------------------------
# SkillResult assertion helpers
# ---------------------------------------------------------------------------


class TestSkillResultAssertions:
    """Each assertion method should pass or raise AssertionError."""

    def _make(self, output: str) -> SkillResult:
        return SkillResult(
            output=output, exit_code=0, skill_name="test", args=""
        )

    # assert_contains
    def test_assert_contains_pass(self):
        self._make("hello world").assert_contains("world")

    def test_assert_contains_fail(self):
        with pytest.raises(AssertionError):
            self._make("hello world").assert_contains("missing")

    # assert_not_contains
    def test_assert_not_contains_pass(self):
        self._make("hello world").assert_not_contains("missing")

    def test_assert_not_contains_fail(self):
        with pytest.raises(AssertionError):
            self._make("hello world").assert_not_contains("hello")

    # assert_matches
    def test_assert_matches_pass(self):
        self._make("order 12345 confirmed").assert_matches(r"\d{5}")

    def test_assert_matches_fail(self):
        with pytest.raises(AssertionError):
            self._make("no digits here").assert_matches(r"\d{5}")

    # assert_min_count
    def test_assert_min_count_pass(self):
        self._make("a a a").assert_min_count("a", 3)

    def test_assert_min_count_fail(self):
        with pytest.raises(AssertionError):
            self._make("a a").assert_min_count("a", 5)

    # assert_min_length
    def test_assert_min_length_pass(self):
        self._make("x" * 100).assert_min_length(100)

    def test_assert_min_length_fail(self):
        with pytest.raises(AssertionError):
            self._make("short").assert_min_length(1000)

    # assert_has_urls
    def test_assert_has_urls_pass(self):
        self._make("Visit https://example.com today").assert_has_urls(1)

    def test_assert_has_urls_fail(self):
        with pytest.raises(AssertionError):
            self._make("no urls here").assert_has_urls(1)

    # assert_has_entries
    def test_assert_has_entries_pass(self):
        self._make("**1. First**\n**2. Second**\n**3. Third**").assert_has_entries(3)

    def test_assert_has_entries_fail(self):
        with pytest.raises(AssertionError):
            self._make("no numbered entries").assert_has_entries(3)

    # run_assertions delegates
    def test_run_assertions_delegates(self):
        result = self._make("hello world")
        assertion_set = result.run_assertions(
            [{"type": "contains", "value": "hello"}]
        )
        assert assertion_set.passed


# ---------------------------------------------------------------------------
# SkillRunner.run()
# ---------------------------------------------------------------------------


class TestSkillRunnerRun:
    def test_runner_run_success(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="skill output", stderr="", returncode=0
            )
            result = runner.run("my-skill", "some args")

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["claude", "-p", "/my-skill some args", "--no-input"]
            assert result.output == "skill output"
            assert result.exit_code == 0
            assert result.skill_name == "my-skill"
            assert result.args == "some args"
            assert result.error is None

    def test_runner_run_success_no_args(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="output", stderr="", returncode=0
            )
            runner.run("my-skill")
            cmd = mock_run.call_args[0][0]
            assert cmd == ["claude", "-p", "/my-skill", "--no-input"]

    def test_runner_run_timeout(self):
        runner = SkillRunner(project_dir="/tmp", timeout=5, claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired("claude", 5),
        ):
            result = runner.run("my-skill")
            assert result.exit_code == -1
            assert "Timed out" in result.error
            assert result.output == ""

    def test_runner_run_not_found(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="missing-bin")
        with patch(
            "clauditor.runner.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = runner.run("my-skill")
            assert result.exit_code == -1
            assert "not found" in result.error
            assert result.output == ""


# ---------------------------------------------------------------------------
# SkillResult.outputs dict
# ---------------------------------------------------------------------------


class TestSkillResultOutputs:
    def _make(self, **kwargs) -> SkillResult:
        defaults = dict(output="some output", exit_code=0, skill_name="test", args="")
        defaults.update(kwargs)
        return SkillResult(**defaults)

    def test_outputs_defaults_to_empty_dict(self):
        result = self._make()
        assert result.outputs == {}

    def test_outputs_can_be_populated_with_multiple_files(self):
        files = {"report.md": "# Report", "data.csv": "a,b\n1,2"}
        result = self._make(outputs=files)
        assert result.outputs == files
        assert result.outputs["report.md"] == "# Report"
        assert result.outputs["data.csv"] == "a,b\n1,2"

    def test_succeeded_works_when_outputs_populated(self):
        result = self._make(
            output="primary output",
            exit_code=0,
            outputs={"file.txt": "content"},
        )
        assert result.succeeded is True

    def test_succeeded_works_with_empty_outputs(self):
        result = self._make(output="primary output", exit_code=0)
        assert result.outputs == {}
        assert result.succeeded is True

    def test_assertion_methods_use_output_not_outputs(self):
        result = self._make(
            output="hello world",
            outputs={"other.txt": "completely different text"},
        )
        result.assert_contains("hello world")
        result.assert_not_contains("completely different text")
