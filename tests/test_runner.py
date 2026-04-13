"""Tests for SkillRunner."""

import importlib
import json
import subprocess
from unittest.mock import patch

import pytest

import clauditor.runner as _runner_mod

importlib.reload(_runner_mod)

from clauditor.runner import SkillResult, SkillRunner  # noqa: E402
from tests.conftest import _FakePopen, make_fake_skill_stream  # noqa: E402

# ---------------------------------------------------------------------------
# SkillRunner.run_raw
# ---------------------------------------------------------------------------


class TestRunRaw:
    def test_run_raw_returns_baseline_skill_name(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=make_fake_skill_stream("hi"),
        ):
            result = runner.run_raw("test prompt")
        assert result.skill_name == "__baseline__"

    def test_run_raw_passes_prompt_directly(self):
        """Verify run_raw sends the prompt without a skill prefix."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("some output")
            result = runner.run_raw("find me activities in Seattle")
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd == [
                "claude",
                "-p",
                "find me activities in Seattle",
                "--output-format",
                "stream-json",
                "--verbose",
            ]
            assert result.skill_name == "__baseline__"
            assert result.args == "find me activities in Seattle"
            assert result.output == "some output"

    def test_run_raw_handles_timeout(self):
        runner = SkillRunner(project_dir="/tmp", timeout=1, claude_bin="claude")
        fake = make_fake_skill_stream("partial")
        fake.wait = lambda timeout=None: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("claude", 1)
        )
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run_raw("test prompt")
        assert result.exit_code == -1
        assert result.skill_name == "__baseline__"
        assert result.error == "timeout"

    def test_run_raw_handles_missing_binary(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="nonexistent-binary")
        with patch(
            "clauditor.runner.subprocess.Popen", side_effect=FileNotFoundError
        ):
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
# SkillRunner.run() — covered more thoroughly in TestStreamJsonRunner
# ---------------------------------------------------------------------------


class TestSkillRunnerRun:
    def test_runner_run_success(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("skill output")
            result = runner.run("my-skill", "some args")

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd == [
                "claude",
                "-p",
                "/my-skill some args",
                "--output-format",
                "stream-json",
                "--verbose",
            ]
            assert result.output == "skill output"
            assert result.exit_code == 0
            assert result.skill_name == "my-skill"
            assert result.args == "some args"
            assert result.error is None

    def test_runner_run_success_no_args(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("output")
            runner.run("my-skill")
            cmd = mock_popen.call_args[0][0]
            assert cmd[:3] == ["claude", "-p", "/my-skill"]

    def test_runner_run_timeout(self):
        runner = SkillRunner(project_dir="/tmp", timeout=5, claude_bin="claude")
        fake = make_fake_skill_stream("partial")
        fake.wait = lambda timeout=None: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("claude", 5)
        )
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("my-skill")
        assert result.exit_code == -1
        assert result.error == "timeout"

    def test_runner_run_not_found(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="missing-bin")
        with patch(
            "clauditor.runner.subprocess.Popen",
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


# ---------------------------------------------------------------------------
# Stream-JSON runner — new Popen-based behavior
# ---------------------------------------------------------------------------


class TestStreamJsonRunner:
    def test_single_assistant_message_single_text_block(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=make_fake_skill_stream(
                "hello", input_tokens=100, output_tokens=50
            ),
        ):
            result = runner.run("skill")
        assert result.output == "hello"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.exit_code == 0
        assert result.duration_seconds >= 0

    def test_two_assistant_messages_joined_with_newline(self):
        extra = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "second"}],
                },
            }
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=make_fake_skill_stream("first", extra_messages=extra),
        ):
            result = runner.run("skill")
        assert result.output == "first\nsecond"

    def test_assistant_text_and_tool_use_only_text_included(self):
        lines = [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "visible"},
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            },
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 5, "output_tokens": 7},
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=_FakePopen(lines)
        ):
            result = runner.run("skill")
        assert result.output == "visible"
        assert result.input_tokens == 5
        assert result.output_tokens == 7

    def test_missing_result_message_defaults_tokens_to_zero(self, capsys):
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "only text"}],
                    },
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=_FakePopen(lines)
        ):
            result = runner.run("skill")
        assert isinstance(result, SkillResult)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.output == "only text"
        captured = capsys.readouterr()
        assert "without a 'result'" in captured.err

    def test_malformed_json_line_skipped_with_warning(self, capsys):
        lines = [
            "this is not json",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "survived"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=_FakePopen(lines)
        ):
            result = runner.run("skill")
        assert result.output == "survived"
        assert result.input_tokens == 1
        assert result.output_tokens == 2
        captured = capsys.readouterr()
        assert "malformed" in captured.err

    def test_timeout_sets_duration_and_kills_process(self):
        runner = SkillRunner(project_dir="/tmp", timeout=5, claude_bin="claude")
        fake = make_fake_skill_stream("partial")

        def _raise(timeout=None):  # noqa: ARG001
            raise subprocess.TimeoutExpired("claude", 5)

        fake.wait = _raise
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")
        assert result.error == "timeout"
        assert result.exit_code == -1
        assert result.duration_seconds >= 0
        assert fake.kill_called is True

    def test_file_not_found_sets_duration(self):
        """DEC-005: duration must be set even on FileNotFoundError."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="missing")
        with patch(
            "clauditor.runner.subprocess.Popen", side_effect=FileNotFoundError
        ):
            result = runner.run("skill")
        assert result.exit_code == -1
        assert "not found" in result.error
        assert result.duration_seconds >= 0

    def test_raw_messages_populated(self):
        extra = [
            {"type": "system", "subtype": "ping"},
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=make_fake_skill_stream("x", extra_messages=extra),
        ):
            result = runner.run("skill")
        # assistant + system + result == 3 messages
        assert len(result.raw_messages) == 3
        types = [m.get("type") for m in result.raw_messages]
        assert types == ["assistant", "system", "result"]
