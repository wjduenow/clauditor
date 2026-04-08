"""Tests for SkillRunner."""

from unittest.mock import MagicMock, patch

from clauditor.runner import SkillRunner


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
        import subprocess

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
