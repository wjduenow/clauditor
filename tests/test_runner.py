"""Tests for SkillRunner."""

import importlib
import json
import subprocess
from unittest.mock import patch

import pytest

import clauditor.runner as _runner_mod

importlib.reload(_runner_mod)

from clauditor.asserters import SkillAsserter  # noqa: E402
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

        # Simulate the watchdog firing immediately by patching threading.Timer
        # to invoke the callback on .start() before any stdout is read.
        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                self.function = function
                self.daemon = True

            def start(self):
                self.function()

            def cancel(self):
                pass

        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            patch("clauditor.runner.threading.Timer", _ImmediateTimer),
        ):
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
# SkillAsserter — Layer 1 test-helper wrapper (US-006)
# ---------------------------------------------------------------------------


class TestSkillAsserter:
    """Each assertion method should pass or raise AssertionError."""

    def _make(self, output: str) -> SkillAsserter:
        return SkillAsserter(
            SkillResult(output=output, exit_code=0, skill_name="test", args="")
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
        asserter = self._make("hello world")
        assertion_set = asserter.run_assertions(
            [{"type": "contains", "needle": "hello"}]
        )
        assert assertion_set.passed

    def test_asserter_stores_result_reference(self):
        """SkillAsserter should expose the wrapped result for introspection."""
        result = SkillResult(
            output="hi", exit_code=0, skill_name="t", args=""
        )
        asserter = SkillAsserter(result)
        assert asserter.result is result

    def test_assert_from_convenience_factory(self):
        """``assert_from(result)`` wraps a result in a SkillAsserter."""
        from clauditor.asserters import assert_from

        result = SkillResult(
            output="hello world", exit_code=0, skill_name="t", args=""
        )
        asserter = assert_from(result)
        assert isinstance(asserter, SkillAsserter)
        assert asserter.result is result
        # Sanity: the wrapper works end-to-end.
        asserter.assert_contains("hello")


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

        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                self.function = function
                self.daemon = True

            def start(self):
                self.function()

            def cancel(self):
                pass

        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            patch("clauditor.runner.threading.Timer", _ImmediateTimer),
        ):
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


class TestSkillRunnerCwd:
    """US-003: cwd override threads through to Popen."""

    def test_runner_default_cwd_is_project_dir(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("out")
            runner.run("my-skill", "args")
            assert mock_popen.call_args.kwargs["cwd"] == "/tmp"

    def test_runner_cwd_override_passes_through_to_popen(self, tmp_path):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("out")
            runner.run("my-skill", "args", cwd=tmp_path)
            assert mock_popen.call_args.kwargs["cwd"] == str(tmp_path)


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
        asserter = SkillAsserter(result)
        asserter.assert_contains("hello world")
        asserter.assert_not_contains("completely different text")


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

        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                self.function = function
                self.daemon = True

            def start(self):
                self.function()

            def cancel(self):
                pass

        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            patch("clauditor.runner.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("skill")
        assert result.error == "timeout"
        assert result.exit_code == -1
        assert result.duration_seconds >= 0
        assert fake.kill_called is True

    def test_timeout_watchdog_skips_if_child_already_exited(self):
        """Watchdog race: if the child exits cleanly before the timer fires,
        _on_timeout should bail out without setting timed_out[hit] and the
        run should return a success result, not a bogus timeout."""
        runner = SkillRunner(project_dir="/tmp", timeout=5, claude_bin="claude")
        fake = make_fake_skill_stream("done", input_tokens=1, output_tokens=2)
        # Simulate "child already exited" by pre-killing the fake. poll()
        # will return a non-None returncode so _on_timeout early-returns.
        fake._killed = True
        fake.returncode = 0

        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                self.function = function
                self.daemon = True

            def start(self):
                self.function()

            def cancel(self):
                pass

        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            patch("clauditor.runner.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("skill")
        # _on_timeout was called but returned early (poll() was not None);
        # the run completes normally and we get a success result, not a
        # false timeout.
        assert result.error != "timeout"
        assert result.output == "done"

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


class TestStreamJsonDefensiveBranches:
    """Cover defensive branches added in #21 so codecov/patch is green."""

    def test_non_dict_json_line_is_skipped(self):
        """runner.py:258 — JSON scalar/array lines are not stream-json
        messages; skip without crashing."""
        lines = [
            "123",  # bare scalar
            "[1, 2, 3]",  # bare array
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "survived"}]
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
            "clauditor.runner.subprocess.Popen",
            return_value=_FakePopen(lines),
        ):
            result = runner.run("skill")
        assert result.output == "survived"
        assert result.input_tokens == 1

    def test_assistant_content_non_list_is_skipped(self):
        """runner.py:266 — assistant message with non-list content."""
        lines = [
            json.dumps(
                {"type": "assistant", "message": {"content": "oops string"}}
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "real one"}]
                    },
                }
            ),
            json.dumps({"type": "result", "usage": {}}),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=_FakePopen(lines),
        ):
            result = runner.run("skill")
        assert result.output == "real one"

    def test_usage_input_tokens_non_numeric_defaults_to_zero(self):
        """runner.py:283-284 — ValueError on int() cast for input_tokens."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": "bogus", "output_tokens": 50},
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=_FakePopen(lines),
        ):
            result = runner.run("skill")
        assert result.input_tokens == 0
        assert result.output_tokens == 50

    def test_usage_output_tokens_non_numeric_defaults_to_zero(self):
        """runner.py:289-290 — ValueError on int() cast for output_tokens."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 10, "output_tokens": {"not": "int"}},
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=_FakePopen(lines),
        ):
            result = runner.run("skill")
        assert result.input_tokens == 10
        assert result.output_tokens == 0

    def test_stderr_drain_collects_chunks(self):
        """runner.py:211 — stderr chunks are accumulated by the drain thread."""
        fake = make_fake_skill_stream("ok")
        fake.stderr = iter(["warning: something\n", "more diagnostic\n"])
        # Return a nonzero exit code so stderr is surfaced in result.error.
        fake.returncode = 1
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=fake
        ):
            result = runner.run("skill")
        assert "warning: something" in (result.error or "")
        assert "more diagnostic" in (result.error or "")

    def test_stderr_drain_exception_is_swallowed(self):
        """runner.py:212-213 — exception while iterating stderr must not
        crash the run. Replace _FakePopen.stderr with a raising iterator."""

        class _RaisingIter:
            def __iter__(self):
                return self

            def __next__(self):
                raise RuntimeError("stderr blew up")

        fake = make_fake_skill_stream("ok")
        fake.stderr = _RaisingIter()
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=fake
        ):
            result = runner.run("skill")
        # The run completed successfully despite the stderr drain exception.
        assert result.output == "ok"
        assert result.exit_code == 0

    def test_outer_finally_reaps_leaked_subprocess(self):
        """runner.py:338-345, 353-354 — if an unexpected exception escapes
        the inner try, the outer finally must terminate/kill/close the
        child so no process leaks."""
        fake = make_fake_skill_stream("ok")
        # Force the wait step to raise an unexpected exception.
        original_wait = fake.wait
        call_count = {"n": 0}

        def _explode_once(timeout=None):  # noqa: ARG001
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom inside parsing")
            return original_wait(timeout=timeout)

        fake.wait = _explode_once
        # Track whether terminate + close were called.
        fake.terminate_called = False

        def _terminate():
            fake.terminate_called = True
            fake._killed = True
            fake.returncode = -15

        fake.terminate = _terminate

        stdout_closed = {"hit": False}
        # Capture the original bound close method BEFORE overriding so the
        # wrapper does not recurse into itself.
        original_close = fake.stdout.close

        def _close_stdout():
            stdout_closed["hit"] = True
            original_close()

        fake.stdout.close = _close_stdout

        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            pytest.raises(RuntimeError, match="boom"),
        ):
            runner.run("skill")
        # Defensive cleanup fired: terminate was called and stdout closed.
        assert fake.terminate_called is True
        assert stdout_closed["hit"] is True

    def test_blank_lines_in_stream_are_skipped(self):
        """runner.py:245 — blank lines between NDJSON messages are ignored."""
        lines = [
            "",
            "   ",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                }
            ),
            "",
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=_FakePopen(lines),
        ):
            result = runner.run("skill")
        assert result.output == "hi"
        assert result.input_tokens == 1

    def test_cleanup_terminate_succeeds_but_wait_raises(self):
        """runner.py — terminate() succeeds but proc.wait(timeout=1) raises
        TimeoutExpired, so the kill() + wait() fallback runs."""
        fake = make_fake_skill_stream("ok")

        call_log = {"terminate": 0, "kill": 0, "wait_during_cleanup": 0}

        def _boom_wait(timeout=None):  # noqa: ARG001
            # The first wait (in the read loop) raises the real error.
            # Subsequent waits are from cleanup — the first cleanup wait
            # raises TimeoutExpired so kill() runs; the second succeeds.
            call_log["wait_during_cleanup"] += 1
            if call_log["wait_during_cleanup"] == 1:
                raise RuntimeError("parse failure")
            if call_log["wait_during_cleanup"] == 2:
                # terminate() was called, but cleanup wait times out.
                raise subprocess.TimeoutExpired(cmd=["claude"], timeout=1)
            return 0

        fake.wait = _boom_wait

        def _terminate():
            call_log["terminate"] += 1
            fake._killed = False  # still alive after terminate

        def _kill():
            call_log["kill"] += 1
            fake._killed = True

        fake.terminate = _terminate
        fake.kill = _kill

        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            pytest.raises(RuntimeError, match="parse failure"),
        ):
            runner.run("skill")
        assert call_log["terminate"] == 1
        assert call_log["kill"] == 1  # kill fallback ran after terminate-wait raised

    def test_cleanup_wait_after_kill_also_raises(self):
        """runner.py — kill() + wait(timeout=1) cascade: if the wait AFTER
        kill also raises (TimeoutExpired), the innermost handler swallows
        it and the original exception still propagates."""
        fake = make_fake_skill_stream("ok")

        cleanup_wait_calls = {"n": 0}

        def _always_boom_wait(timeout=None):  # noqa: ARG001
            # First call = main run's wait in the read loop
            if cleanup_wait_calls["n"] == 0:
                cleanup_wait_calls["n"] = 1
                raise RuntimeError("parse failure")
            # Every subsequent call (cleanup waits) raises TimeoutExpired,
            # the realistic subprocess exception type the cleanup catches.
            cleanup_wait_calls["n"] += 1
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=1)

        fake.wait = _always_boom_wait
        # Don't let terminate mark the child as dead, so cleanup proceeds.
        fake.terminate = lambda: None
        fake.kill = lambda: None

        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            pytest.raises(RuntimeError, match="parse failure"),
        ):
            runner.run("skill")
        # Three wait() calls total: main loop + after terminate + after kill.
        assert cleanup_wait_calls["n"] >= 3

    def test_cleanup_terminate_exception_is_swallowed(self):
        """runner.py — if terminate() itself raises OSError, the cleanup
        chain still runs and the run's original exception propagates."""
        fake = make_fake_skill_stream("ok")

        def _boom_wait(timeout=None):  # noqa: ARG001
            raise RuntimeError("parse failure")

        fake.wait = _boom_wait

        def _terminate_raises():
            raise OSError("terminate failed")

        fake.terminate = _terminate_raises
        kill_called = {"hit": False}

        def _kill():
            kill_called["hit"] = True

        fake.kill = _kill

        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with (
            patch("clauditor.runner.subprocess.Popen", return_value=fake),
            pytest.raises(RuntimeError, match="parse failure"),
        ):
            runner.run("skill")
        # terminate raised, but the outer handler swallowed it; the
        # original RuntimeError from wait still propagated.


class TestStreamEvents:
    """Tests for SkillResult.stream_events capture (US-003 / DEC-010)."""

    def test_stream_events_populated(self):
        """Mock subprocess emits 3 JSON lines -> 3 dicts in order."""
        messages = [
            {"type": "system", "subtype": "init"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        ]
        fake = _FakePopen([json.dumps(m) for m in messages])
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")

        assert len(result.stream_events) == 3
        assert result.stream_events == messages
        # order preserved
        assert [e["type"] for e in result.stream_events] == [
            "system",
            "assistant",
            "result",
        ]

    def test_stream_events_skips_non_json(self):
        """Non-JSON lines are ignored; stream_events only contains dicts."""
        messages = [
            {"type": "system", "subtype": "init"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ]
        lines = [
            json.dumps(messages[0]),
            "this is not json at all",
            json.dumps(messages[1]),
            "{broken json",
            json.dumps(messages[2]),
        ]
        fake = _FakePopen(lines)
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")

        assert len(result.stream_events) == 3
        assert result.stream_events == messages

    def test_output_field_still_renders_text_blocks(self):
        """Regression: SkillResult.output is unchanged by stream_events work."""
        fake = make_fake_skill_stream("the quick brown fox")
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")

        assert result.output == "the quick brown fox"
        assert result.exit_code == 0
        # stream_events contains assistant + result (2 entries from the helper)
        assert len(result.stream_events) == 2
        assert result.stream_events[0]["type"] == "assistant"
        assert result.stream_events[-1]["type"] == "result"

    def test_stream_events_default_empty_list(self):
        """SkillResult default factory gives each instance its own list."""
        a = SkillResult(output="", exit_code=0, skill_name="x", args="")
        b = SkillResult(output="", exit_code=0, skill_name="y", args="")
        assert a.stream_events == []
        a.stream_events.append({"type": "foo"})
        assert b.stream_events == []


# ---------------------------------------------------------------------------
# US-007: SkillResult.warnings observability
# ---------------------------------------------------------------------------


class TestSkillResultWarnings:
    """Tests for the new warnings field added in US-007."""

    def test_warnings_default_empty_list(self):
        """SkillResult default factory gives each instance its own list."""
        a = SkillResult(output="", exit_code=0, skill_name="x", args="")
        b = SkillResult(output="", exit_code=0, skill_name="y", args="")
        assert a.warnings == []
        a.warnings.append("hello")
        assert b.warnings == []

    def test_happy_path_has_empty_warnings(self):
        """A clean run should produce zero warnings."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen",
            return_value=make_fake_skill_stream("fine"),
        ):
            result = runner.run("skill")
        assert result.output == "fine"
        assert result.warnings == []

    def test_malformed_line_appends_to_warnings(self):
        """Malformed stream-json line must ALSO show up in warnings, not just
        stderr (the stderr print is preserved per stream-json-schema.md)."""
        lines = [
            "this is not json",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                    },
                }
            ),
            json.dumps(
                {"type": "result", "usage": {"input_tokens": 1, "output_tokens": 1}}
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=_FakePopen(lines)
        ):
            result = runner.run("skill")
        assert result.output == "ok"
        assert any(
            "malformed stream-json line" in w for w in result.warnings
        ), f"expected malformed warning, got {result.warnings!r}"

    def test_missing_result_appends_to_warnings(self):
        """An EOF without a 'result' message must surface in warnings."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "no result"}],
                    },
                }
            ),
        ]
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor.runner.subprocess.Popen", return_value=_FakePopen(lines)
        ):
            result = runner.run("skill")
        assert result.output == "no result"
        assert any(
            "without a 'result' message" in w for w in result.warnings
        ), f"expected EOF warning, got {result.warnings!r}"

    def test_cleanup_kill_oserror_records_warning(self):
        """US-007 acceptance: simulate OSError from proc.kill() during the
        outer-finally cleanup chain and assert the resulting
        SkillResult.warnings contains a message naming the failing step."""
        fake = make_fake_skill_stream("ok")

        # Normal parse completes (wait returns the fake's returncode). Then
        # the outer-finally cleanup runs because we force poll() -> None
        # (child appears still alive). terminate() is a no-op; wait(timeout=1)
        # raises TimeoutExpired -> we fall through to kill(), which raises
        # OSError. Its message should land in SkillResult.warnings.
        # Save original wait (used by the main parse loop).
        original_wait = fake.wait
        cleanup_wait_count = {"n": 0}

        def _wait_shim(timeout=None):
            # First call: main parse wait — return normally.
            # Subsequent calls: cleanup waits — raise TimeoutExpired.
            if cleanup_wait_count["n"] == 0:
                cleanup_wait_count["n"] = 1
                return original_wait(timeout=timeout)
            cleanup_wait_count["n"] += 1
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=1)

        fake.wait = _wait_shim

        # Keep poll() reporting alive so the cleanup actually runs.
        # Override terminate + kill: terminate is a no-op; kill raises OSError.
        fake.terminate = lambda: None

        def _kill_raises():
            raise OSError("simulated kill failure")

        fake.kill = _kill_raises
        # Make _killed stay False so poll() keeps returning None.
        fake._killed = False

        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")

        assert result.output == "ok"
        assert any(
            "cleanup kill failed" in w and "simulated kill failure" in w
            for w in result.warnings
        ), (
            f"expected warning naming the failing step, got {result.warnings!r}"
        )

    def test_stderr_drainer_exception_records_warning(self):
        """An unexpected exception in the stderr drain thread must record
        a descriptive warning (drained into SkillResult.warnings) rather
        than silently vanishing."""

        class _RaisingIter:
            def __iter__(self):
                return self

            def __next__(self):
                raise RuntimeError("stderr blew up")

        fake = make_fake_skill_stream("ok")
        fake.stderr = _RaisingIter()
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")
        assert result.output == "ok"
        assert any(
            "stderr drainer" in w and "RuntimeError" in w for w in result.warnings
        ), f"expected stderr-drainer warning, got {result.warnings!r}"

    def test_stderr_drainer_oserror_records_warning(self):
        """An OSError (broken pipe, EBADF) in the stderr drain is the
        expected terminal state and must still record into warnings."""

        class _RaisingIter:
            def __iter__(self):
                return self

            def __next__(self):
                raise OSError("EBADF")

        fake = make_fake_skill_stream("ok")
        fake.stderr = _RaisingIter()
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")
        assert result.output == "ok"
        assert any(
            "stderr drainer stopped" in w and "OSError" in w
            for w in result.warnings
        ), f"expected stderr drainer OSError warning, got {result.warnings!r}"

    def test_cleanup_wait_oserror_records_warning(self):
        """OSError (not TimeoutExpired) from wait(timeout=1) after terminate
        takes the OSError branch and records a warning."""
        fake = make_fake_skill_stream("ok")
        original_wait = fake.wait
        cleanup_wait_count = {"n": 0}

        def _wait_shim(timeout=None):
            if cleanup_wait_count["n"] == 0:
                cleanup_wait_count["n"] = 1
                return original_wait(timeout=timeout)
            cleanup_wait_count["n"] += 1
            raise OSError("wait syscall blew up")

        fake.wait = _wait_shim
        # terminate() is a no-op: leaves _killed False so poll() keeps
        # reporting alive and the outer-finally cleanup runs.
        fake.terminate = lambda: None

        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")
        assert result.output == "ok"
        assert any(
            "cleanup wait after terminate failed" in w and "OSError" in w
            for w in result.warnings
        ), f"expected wait OSError warning, got {result.warnings!r}"

    def test_cleanup_close_oserror_records_warning(self):
        """OSError from stream.close() during cleanup records a warning
        naming the stream (stdout/stderr)."""
        fake = make_fake_skill_stream("ok")

        def _close_raises():
            raise OSError("close EBADF")

        fake.stdout.close = _close_raises
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor.runner.subprocess.Popen", return_value=fake):
            result = runner.run("skill")
        assert result.output == "ok"
        assert any(
            "cleanup close(stdout)" in w and "OSError" in w
            for w in result.warnings
        ), f"expected stdout close warning, got {result.warnings!r}"
