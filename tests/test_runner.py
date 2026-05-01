"""Tests for SkillRunner."""

import importlib
import json
import subprocess
from unittest.mock import patch

import pytest

import clauditor.runner as _runner_mod

importlib.reload(_runner_mod)

# Reload the harness package + module too so their bound ``InvokeResult``
# (imported from :mod:`clauditor.runner` at import time) re-resolves to the
# reloaded class. Without this, ``isinstance(harness.invoke(...).., InvokeResult)``
# checks fail because the harness still holds the pre-reload class
# reference. See US-004 of ``plans/super/148-extract-harness-protocol.md``.
#
# Reloading ``clauditor._harnesses`` (the package ``__init__.py``) is also
# what gives coverage instrumentation a chance to see the package's
# ``Harness`` Protocol definition: pytest-cov starts AFTER test collection,
# by which point ``__init__.py`` has already executed. Reloading it here
# (during test-module import, but post-collection) re-runs the body under
# coverage so the Protocol class lines register as covered.
import clauditor._harnesses as _harnesses_pkg  # noqa: E402
import clauditor._harnesses._claude_code as _claude_code_mod  # noqa: E402

importlib.reload(_harnesses_pkg)
importlib.reload(_claude_code_mod)

from clauditor._harnesses._claude_code import (  # noqa: E402
    _RESULT_TEXT_MAX_CHARS,
    ClaudeCodeHarness,
    _classify_result_message,
    _count_background_task_launches,
    _detect_background_task_noncompletion,
    _detect_interactive_hang,
    env_without_api_key,
)
from clauditor.asserters import SkillAsserter  # noqa: E402
from clauditor.runner import (  # noqa: E402
    _BACKGROUND_TASK_WARNING_PREFIX,
    _INTERACTIVE_HANG_WARNING_PREFIX,
    InvokeResult,
    SkillResult,
    SkillRunner,
    env_with_sync_tasks,
)
from tests.conftest import (  # noqa: E402
    _FakePopen,
    make_fake_background_task_stream,
    make_fake_interactive_hang_stream,
    make_fake_skill_stream,
)

# ---------------------------------------------------------------------------
# SkillRunner.run_raw
# ---------------------------------------------------------------------------


class TestRunRaw:
    def test_run_raw_returns_baseline_skill_name(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=make_fake_skill_stream("hi"),
        ):
            result = runner.run_raw("test prompt")
        assert result.skill_name == "__baseline__"

    def test_run_raw_passes_prompt_directly(self):
        """Verify run_raw sends the prompt without a skill prefix."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run_raw("test prompt")
        assert result.exit_code == -1
        assert result.skill_name == "__baseline__"
        assert result.error == "timeout"

    def test_run_raw_handles_missing_binary(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="nonexistent-binary")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=FileNotFoundError
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
# Harness protocol + InvokeResult.harness_metadata (US-001 / clauditor-3sm.1)
# ---------------------------------------------------------------------------


class TestHarnessProtocol:
    """Structural tests for the ``Harness`` protocol introduced in US-001.

    The protocol lives in ``clauditor._harnesses`` and is the seam future
    harnesses (Codex per #149) will satisfy. This test confirms the
    public shape: ``name`` ClassVar plus ``invoke``, ``strip_auth_keys``,
    and ``build_prompt`` methods. Per DEC-008, ``allow_hang_heuristic``
    is intentionally NOT on ``invoke`` — it's a Claude-Code-specific
    knob configured at harness construction time (US-004).
    """

    def test_stub_satisfies_harness_protocol(self):
        """A class with the four protocol members is structurally a
        ``Harness``. Verified at runtime via ``isinstance`` (the protocol
        is ``@runtime_checkable``) and at signature level via
        ``inspect.signature`` so adding a new required parameter to
        ``Harness.invoke`` without updating implementations fails this
        test rather than silently passing.
        """
        import inspect
        from pathlib import Path
        from typing import ClassVar

        from clauditor._harnesses import Harness
        from clauditor.runner import InvokeResult

        class StubHarness:
            name: ClassVar[str] = "stub"

            def invoke(
                self,
                prompt: str,
                *,
                cwd: Path | None,
                env: dict[str, str] | None,
                timeout: int,
                model: str | None = None,
                subject: str | None = None,
            ) -> InvokeResult:
                return InvokeResult(output="ok", exit_code=0)

            def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]:
                return dict(env)

            def build_prompt(
                self,
                skill_name: str,
                args: str,
                *,
                system_prompt: str | None,
            ) -> str:
                return f"/{skill_name}"

        stub = StubHarness()

        # Runtime member-presence check (the protocol is @runtime_checkable).
        assert isinstance(stub, Harness)

        # Signature drift-guard: the stub's invoke parameter set must be a
        # superset of the protocol's, so adding a new required kwarg to
        # ``Harness.invoke`` without updating implementations red-flags here.
        # ``Harness.invoke`` is referenced via the class so ``self`` appears;
        # ``stub.invoke`` is a bound method so ``self`` is absent. Compare
        # the user-facing parameter sets (without ``self``) on both sides.
        protocol_params = set(inspect.signature(Harness.invoke).parameters) - {"self"}
        stub_params = set(inspect.signature(stub.invoke).parameters)
        missing = protocol_params - stub_params
        assert not missing, (
            f"StubHarness.invoke missing protocol parameters: {missing}"
        )

    def test_harness_protocol_includes_build_prompt(self):
        """Drift-guard for ``Harness.build_prompt`` (US-001 of #150).

        Locks: (1) the method exists on the protocol; (2) ``system_prompt``
        is keyword-only; (3) the return annotation is ``str``. A signature
        change that drops keyword-only or changes the return type fails
        this test.
        """
        import inspect

        from clauditor._harnesses import Harness

        assert hasattr(Harness, "build_prompt"), (
            "Harness protocol missing build_prompt"
        )
        sig = inspect.signature(Harness.build_prompt)
        params = sig.parameters
        assert "system_prompt" in params, (
            "Harness.build_prompt missing system_prompt parameter"
        )
        assert params["system_prompt"].kind is inspect.Parameter.KEYWORD_ONLY, (
            "Harness.build_prompt.system_prompt must be keyword-only"
        )
        assert params["system_prompt"].annotation == "str | None", (
            "Harness.build_prompt.system_prompt must be annotated 'str | None'"
        )
        assert sig.return_annotation == "str", (
            "Harness.build_prompt return annotation must be 'str'"
        )


class TestInvokeResultHarnessMetadata:
    """``InvokeResult`` gains a ``harness_metadata`` dict in US-001 to
    let future harnesses surface harness-specific observability without
    a sidecar-schema bump (DEC-007)."""

    def test_default_is_empty_dict(self):
        result = InvokeResult(output="ok", exit_code=0)
        assert result.harness_metadata == {}

    def test_independent_per_instance(self):
        """``field(default_factory=dict)`` — not a shared mutable
        default — so mutating one instance does not bleed into another."""
        r1 = InvokeResult(output="ok", exit_code=0)
        r2 = InvokeResult(output="ok", exit_code=0)
        r1.harness_metadata["k"] = "v"
        assert "k" not in r2.harness_metadata


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
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
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
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("my-skill")
        assert result.exit_code == -1
        assert result.error == "timeout"

    def test_runner_run_not_found(self):
        runner = SkillRunner(project_dir="/tmp", claude_bin="missing-bin")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("out")
            runner.run("my-skill", "args")
            assert mock_popen.call_args.kwargs["cwd"] == "/tmp"

    def test_runner_cwd_override_passes_through_to_popen(self, tmp_path):
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("out")
            runner.run("my-skill", "args", cwd=tmp_path)
            assert mock_popen.call_args.kwargs["cwd"] == str(tmp_path)


class TestSkillRunnerEnvAndTimeout:
    """US-003: keyword-only ``env=`` and ``timeout=`` kwargs on ``run``.

    Traces to DEC-010 (move ``timeout`` from ``__init__`` to a per-call
    ``run()`` kwarg, with ``self.timeout`` as fallback) and DEC-013
    (``env=`` kwarg shape mirrors ``cwd``; ``None`` passes through to
    ``subprocess.Popen(env=None)`` which inherits ``os.environ``).
    """

    def test_run_env_none_popen_receives_none(self):
        """Default ``env=None`` reaches Popen unchanged."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("out")
            runner.run("my-skill", "args")
            assert mock_popen.call_args.kwargs["env"] is None

    def test_run_env_dict_popen_receives_dict(self):
        """``env={"KEY": "VAL"}`` is forwarded to Popen verbatim."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        env = {"KEY": "VAL", "PATH": "/usr/bin"}
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("out")
            runner.run("my-skill", "args", env=env)
            assert mock_popen.call_args.kwargs["env"] == env

    def test_run_timeout_override_used_in_watchdog(self):
        """Per-call ``timeout=`` overrides ``self.timeout`` for the watchdog."""
        runner = SkillRunner(project_dir="/tmp", timeout=180, claude_bin="claude")
        captured: dict[str, float] = {}

        class _CapturingTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                captured["interval"] = interval
                self.function = function
                self.daemon = True

            def start(self):
                pass

            def cancel(self):
                pass

        with (
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=make_fake_skill_stream("out"),
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _CapturingTimer),
        ):
            runner.run("my-skill", "args", timeout=60)

        assert captured["interval"] == 60

    def test_run_timeout_none_falls_back_to_self_timeout(self):
        """``timeout=None`` (default) uses ``self.timeout``."""
        runner = SkillRunner(project_dir="/tmp", timeout=42, claude_bin="claude")
        captured: dict[str, float] = {}

        class _CapturingTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                captured["interval"] = interval
                self.function = function
                self.daemon = True

            def start(self):
                pass

            def cancel(self):
                pass

        with (
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=make_fake_skill_stream("out"),
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _CapturingTimer),
        ):
            runner.run("my-skill", "args")

        assert captured["interval"] == 42

    def test_init_timeout_default_300(self):
        """``SkillRunner()`` with no kwargs defaults ``self.timeout == 300``.

        Bumped from 180 to 300 per #104 — real-world subscription-backed
        runs routinely exceed 180 s.
        """
        runner = SkillRunner()
        assert runner.timeout == 300

    def test_existing_call_site_unaffected(self):
        """A ``runner.run("skill", args="")`` call with no new kwargs still
        works and produces a normal ``SkillResult`` — back-compat check."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("hello")
            result = runner.run("my-skill", args="")
            # env kwarg is passed (as None), not omitted — Popen's default
            # inheritance path requires explicit ``env=None``.
            assert "env" in mock_popen.call_args.kwargs
            assert mock_popen.call_args.kwargs["env"] is None
            assert result.output == "hello"
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# US-005: MockHarness substitution + claude_bin deprecation
# ---------------------------------------------------------------------------


class TestSkillRunnerHarnessSubstitution:
    """US-005: a custom ``Harness`` swapped in via ``harness=`` kwarg
    fully replaces the default :class:`ClaudeCodeHarness` and its result
    is projected verbatim onto :class:`SkillResult`.
    """

    def test_skill_runner_projects_mock_harness_result(self):
        """``runner.run`` projects the harness's configured ``InvokeResult``
        onto a :class:`SkillResult` (field-copy per ``_invoke``)."""
        from clauditor._harnesses._mock import MockHarness

        configured = InvokeResult(
            output="mocked-output",
            exit_code=7,
            duration_seconds=1.25,
        )
        mock = MockHarness(result=configured)
        runner = SkillRunner(project_dir="/tmp", harness=mock)

        result = runner.run("foo")

        assert isinstance(result, SkillResult)
        assert result.output == "mocked-output"
        assert result.exit_code == 7
        assert result.duration_seconds == 1.25
        assert result.skill_name == "foo"

    def test_skill_runner_records_invoke_call_args(self):
        """The mock records prompt/cwd/env/timeout exactly as the runner
        forwards them to :meth:`Harness.invoke`. Per US-003 of issue #150
        the prompt is the string returned by ``MockHarness.build_prompt``,
        which has its own deterministic shape (``"[mock]|/foo arg"``)."""
        from pathlib import Path

        from clauditor._harnesses._mock import MockHarness

        mock = MockHarness()
        runner = SkillRunner(project_dir="/tmp", timeout=99, harness=mock)

        runner.run("foo", "arg")

        assert len(mock.invoke_calls) == 1
        call = mock.invoke_calls[0]
        assert call["prompt"] == "[mock]|/foo arg"
        assert call["cwd"] == Path("/tmp")
        assert call["env"] is None
        assert call["timeout"] == 99

    def test_skill_runner_default_harness_is_claude_code(self):
        """Constructing ``SkillRunner()`` with no ``harness=`` kwarg
        builds a default :class:`ClaudeCodeHarness`."""
        runner = SkillRunner()
        assert isinstance(runner.harness, ClaudeCodeHarness)


class TestSkillRunnerClaudeBinDeprecation:
    """US-005 / DEC-002: ``claude_bin=`` together with an explicit
    ``harness=`` is a soft-deprecation path — the harness wins and a
    :class:`DeprecationWarning` is emitted. ``claude_bin=`` alone is
    still the supported single-knob path and emits no warning.
    """

    def test_claude_bin_with_harness_emits_deprecation_warning(self):
        from clauditor._harnesses._mock import MockHarness

        with pytest.warns(
            DeprecationWarning, match=r"claude_bin via ClaudeCodeHarness"
        ):
            SkillRunner(harness=MockHarness(), claude_bin="custom")

    def test_claude_bin_without_harness_emits_no_warning(self):
        """``claude_bin=`` alone is still supported — no warning, and the
        constructed default harness honours the path."""
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runner = SkillRunner(claude_bin="custom")

        assert not any(
            issubclass(w.category, DeprecationWarning) for w in caught
        )
        assert isinstance(runner.harness, ClaudeCodeHarness)
        assert runner.harness.claude_bin == "custom"


class TestHarnessStripAuthKeys:
    """Direct coverage of ``Harness.strip_auth_keys`` on both implementers.

    Pinned by Quality Gate of issue #148: the protocol method had no
    direct unit-test coverage on either ``ClaudeCodeHarness`` or
    ``MockHarness`` (only one branch reached via integration paths).
    Locks the non-mutating-scrub contract per
    ``.claude/rules/non-mutating-scrub.md``.
    """

    def test_claude_code_strips_anthropic_keys(self):
        """``ClaudeCodeHarness.strip_auth_keys`` removes Anthropic auth env vars."""
        env = {
            "ANTHROPIC_API_KEY": "sk-...",
            "ANTHROPIC_AUTH_TOKEN": "tok",
            "PATH": "/usr/bin",
            "FOO": "bar",
        }
        scrubbed = ClaudeCodeHarness().strip_auth_keys(env)
        assert "ANTHROPIC_API_KEY" not in scrubbed
        assert "ANTHROPIC_AUTH_TOKEN" not in scrubbed
        assert scrubbed["PATH"] == "/usr/bin"
        assert scrubbed["FOO"] == "bar"
        # Input unchanged (non-mutating).
        assert "ANTHROPIC_API_KEY" in env

    def test_mock_strip_is_identity_copy(self):
        """``MockHarness.strip_auth_keys`` returns a verbatim copy (no scrubbing)."""
        from clauditor._harnesses._mock import MockHarness

        env = {"ANTHROPIC_API_KEY": "sk-...", "FOO": "bar"}
        scrubbed = MockHarness().strip_auth_keys(env)
        assert scrubbed == env
        # Returns a new dict (mutation-safe per Harness protocol contract).
        scrubbed["FOO"] = "mutated"
        assert env["FOO"] == "bar"


class TestHarnessBuildPrompt:
    """Direct coverage of ``Harness.build_prompt`` on both implementers.

    Pinned by US-001 of issue #150: the prompt-builder is a pure helper
    (no I/O) per ``.claude/rules/pure-compute-vs-io-split.md``. Locks:
    (1) ``ClaudeCodeHarness`` returns slash-style commands and ignores
    ``system_prompt``; (2) ``MockHarness`` records every call on
    ``build_prompt_calls`` for test assertions.
    """

    def test_claude_code_harness_build_prompt_with_args_and_no_system_prompt(self):
        """Args present → ``"/{skill_name} {args}"``; ``system_prompt`` ignored."""
        result = ClaudeCodeHarness().build_prompt("foo", "bar baz", system_prompt=None)
        assert result == "/foo bar baz"

    def test_claude_code_harness_build_prompt_no_args(self):
        """Empty args → ``"/{skill_name}"`` with no trailing space."""
        result = ClaudeCodeHarness().build_prompt("foo", "", system_prompt=None)
        assert result == "/foo"

    def test_claude_code_harness_build_prompt_ignores_system_prompt(self):
        """``system_prompt`` does not appear in the Claude Code slash command."""
        result = ClaudeCodeHarness().build_prompt("foo", "", system_prompt="anything")
        assert result == "/foo"

    def test_mock_harness_build_prompt_records_call(self):
        """``MockHarness.build_prompt`` records ``(skill_name, args, system_prompt)``
        on ``build_prompt_calls`` so unit tests can assert against it."""
        from clauditor._harnesses._mock import MockHarness

        mock = MockHarness()
        returned = mock.build_prompt("foo", "bar", system_prompt="hello")
        assert len(mock.build_prompt_calls) == 1
        entry = mock.build_prompt_calls[0]
        # Recorded entry must contain all three values; structure is the
        # mock's choice (dict or tuple) so long as the values are present.
        if isinstance(entry, dict):
            assert entry["skill_name"] == "foo"
            assert entry["args"] == "bar"
            assert entry["system_prompt"] == "hello"
        else:
            assert "foo" in entry
            assert "bar" in entry
            assert "hello" in entry
        # And the returned string is deterministic enough to surface
        # ``system_prompt`` when present.
        assert "hello" in returned
        assert "foo" in returned


class TestSkillRunnerRunBuildsPromptViaHarness:
    """US-003 of issue #150: ``SkillRunner.run`` composes its prompt via
    ``self.harness.build_prompt(...)`` rather than synthesizing the slash
    string inline. The ``system_prompt`` kwarg threads from
    :meth:`SkillRunner.run` through to ``build_prompt`` (see DEC-008 for
    why it is the LAST keyword argument on ``run``).
    """

    def test_runner_run_calls_harness_build_prompt_with_args(self):
        """``runner.run("foo", "bar")`` records a build_prompt call with
        ``skill_name="foo"``, ``args="bar"``, ``system_prompt=None``."""
        from clauditor._harnesses._mock import MockHarness

        mock = MockHarness()
        runner = SkillRunner(project_dir="/tmp", harness=mock)

        runner.run("foo", "bar")

        assert len(mock.build_prompt_calls) == 1
        call = mock.build_prompt_calls[-1]
        assert call["skill_name"] == "foo"
        assert call["args"] == "bar"
        assert call["system_prompt"] is None

    def test_runner_run_threads_system_prompt_kwarg_to_build_prompt(self):
        """``runner.run("foo", "bar", system_prompt="hello")`` threads
        ``system_prompt`` through to ``Harness.build_prompt``."""
        from clauditor._harnesses._mock import MockHarness

        mock = MockHarness()
        runner = SkillRunner(project_dir="/tmp", harness=mock)

        runner.run("foo", "bar", system_prompt="hello")

        assert len(mock.build_prompt_calls) == 1
        call = mock.build_prompt_calls[-1]
        assert call["skill_name"] == "foo"
        assert call["args"] == "bar"
        assert call["system_prompt"] == "hello"

    def test_runner_run_passes_built_prompt_to_invoke(self):
        """The string returned by ``Harness.build_prompt`` is exactly what
        reaches ``Harness.invoke`` as ``prompt``."""
        from clauditor._harnesses._mock import MockHarness

        mock = MockHarness()
        runner = SkillRunner(project_dir="/tmp", harness=mock)

        runner.run("foo", "bar", system_prompt="hello")

        # ``MockHarness.build_prompt`` returns
        # ``f"[mock]{system_prompt or ''}|/{skill_name} {args}".rstrip()``.
        expected_prompt = "[mock]hello|/foo bar"
        assert len(mock.invoke_calls) == 1
        assert mock.invoke_calls[-1]["prompt"] == expected_prompt

    def test_runner_run_back_compat_no_system_prompt_kwarg(self):
        """Calling ``runner.run("foo", "bar")`` with no ``system_prompt``
        kwarg yields the legacy ``"/foo bar"`` prompt when paired with
        :class:`ClaudeCodeHarness` (which ignores ``system_prompt``)."""
        runner = SkillRunner(project_dir="/tmp", harness=ClaudeCodeHarness())

        # Patch ``invoke`` to capture the prompt without spawning a subprocess.
        captured: dict[str, str] = {}

        def _fake_invoke(prompt, **_kwargs):
            captured["prompt"] = prompt
            return InvokeResult(output="", exit_code=0, duration_seconds=0.0)

        with patch.object(runner.harness, "invoke", side_effect=_fake_invoke):
            runner.run("foo", "bar")

        assert captured["prompt"] == "/foo bar"


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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=_FakePopen(lines)
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=_FakePopen(lines)
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=_FakePopen(lines)
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("skill")
        # _on_timeout was called but returned early (poll() was not None);
        # the run completes normally and we get a success result, not a
        # false timeout.
        assert result.error != "timeout"
        assert result.output == "done"

    def test_timeout_sets_timeout_category(self):
        """US-004 / DEC-009 / DEC-010: a watchdog-fired timeout must set
        ``error_category="timeout"`` alongside ``error="timeout"``."""
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("skill")
        assert result.error == "timeout"
        assert result.error_category == "timeout"

    def test_timeout_category_beats_stream_json_error(self):
        """US-004 / DEC-009 invariant guard: if the stream emits an
        ``is_error: true, result: "429 rate limit"`` payload AND the
        watchdog fires, the final ``error`` / ``error_category`` must
        reflect the timeout — NOT ``"rate_limit"``. The early return
        in the timeout branch is load-bearing: without it, the later
        normal-exit path would clobber the timeout classification with
        the stream-json's ``stream_json_error_text`` accumulator.
        """
        runner = SkillRunner(project_dir="/tmp", timeout=5, claude_bin="claude")
        # Stream-json carries is_error:true with a rate-limit phrase.
        # Under _ImmediateTimer, the watchdog fires before the stdout
        # loop runs, kills the fake (returncode=-9), then the loop
        # drains the buffered is_error result message. The timeout
        # branch must still win, because it returns early.
        fake = make_fake_skill_stream("partial", error_text="429 rate limit")

        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                self.function = function
                self.daemon = True

            def start(self):
                self.function()

            def cancel(self):
                pass

        with (
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("skill")
        assert result.error == "timeout"
        assert result.error_category == "timeout"
        # Regression guard: the rate-limit classification must NOT leak
        # into the final result, even though the stream-json contained
        # a recognized rate-limit phrase.
        assert result.error_category != "rate_limit"

    def test_timeout_preserves_stderr_as_warning(self):
        """QG Pass 2 guard: stderr captured before the watchdog fires must
        land in ``result.warnings`` (not silently dropped) so operators can
        see why the child ran past the deadline. ``error`` / ``error_category``
        stay ``"timeout"`` — stderr does NOT get promoted to the error slot."""
        runner = SkillRunner(project_dir="/tmp", timeout=5, claude_bin="claude")
        fake = make_fake_skill_stream("partial")
        # Inject a realistic stderr chunk so the drain loop captures it.
        fake.stderr = iter(["claude: retrying after 429...\n"])

        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                self.function = function
                self.daemon = True

            def start(self):
                self.function()

            def cancel(self):
                pass

        with (
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = runner.run("skill")
        assert result.error == "timeout"
        assert result.error_category == "timeout"
        assert any(
            "claude: retrying after 429" in w for w in result.warnings
        ), f"expected stderr preserved in warnings, got {result.warnings!r}"

    def test_file_not_found_sets_duration(self):
        """DEC-005: duration must be set even on FileNotFoundError."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="missing")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=FileNotFoundError
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
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
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            result = runner.run("skill")

        assert len(result.stream_events) == 3
        assert result.stream_events == messages

    def test_output_field_still_renders_text_blocks(self):
        """Regression: SkillResult.output is unchanged by stream_events work."""
        fake = make_fake_skill_stream("the quick brown fox")
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=_FakePopen(lines)
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
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=_FakePopen(lines)
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
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
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            result = runner.run("skill")
        assert result.output == "ok"
        assert any(
            "cleanup close(stdout)" in w and "OSError" in w
            for w in result.warnings
        ), f"expected stdout close warning, got {result.warnings!r}"


# ---------------------------------------------------------------------------
# SkillResult.error_category + succeeded_cleanly (US-001)
# ---------------------------------------------------------------------------


class TestSkillResultErrorCategory:
    """Covers the ``error_category`` field and ``succeeded_cleanly``
    property added in US-001 of ``plans/super/63-runner-error-surfacing.md``
    (DEC-010, DEC-015)."""

    def _make(
        self,
        *,
        output: str = "hello",
        exit_code: int = 0,
        error: str | None = None,
        error_category=None,
        warnings: list[str] | None = None,
    ) -> SkillResult:
        return SkillResult(
            output=output,
            exit_code=exit_code,
            skill_name="test",
            args="",
            error=error,
            error_category=error_category,
            warnings=warnings if warnings is not None else [],
        )

    def test_error_category_default_none_minimal_kwargs(self):
        """Constructing with only the currently-required kwargs leaves
        ``error_category`` at ``None``. Regression guard that the field
        addition stays additive for every existing call site."""
        result = SkillResult(
            output="anything",
            exit_code=0,
            skill_name="test",
            args="",
        )
        assert result.error_category is None

    def test_succeeded_cleanly_happy_path(self):
        """All signals clean → True."""
        result = self._make()
        assert result.succeeded is True
        assert result.succeeded_cleanly is True

    def test_succeeded_cleanly_false_when_error_set(self):
        """An ``error`` string disqualifies a clean success."""
        result = self._make(error="something went wrong")
        assert result.succeeded_cleanly is False

    @pytest.mark.parametrize(
        "category",
        ["rate_limit", "auth", "api", "interactive", "subprocess", "timeout"],
    )
    def test_succeeded_cleanly_false_for_each_error_category(self, category):
        """Every Literal value disqualifies ``succeeded_cleanly``; the
        parametrization also documents that each value assigns cleanly
        (Python does not enforce ``Literal`` at runtime, but a typo
        here would raise in the parametrize data)."""
        result = self._make(error_category=category)
        assert result.error_category == category
        assert result.succeeded_cleanly is False

    def test_succeeded_cleanly_false_on_interactive_hang_warning(self):
        """A ``warnings`` entry starting with the ``interactive-hang:``
        prefix disqualifies the result even with error=None and
        error_category=None. US-003 will wire real detection to this
        prefix; US-001 codifies the check."""
        result = self._make(
            warnings=["interactive-hang: assistant ended turn with a question"]
        )
        assert result.error is None
        assert result.error_category is None
        assert result.succeeded_cleanly is False

    def test_succeeded_cleanly_false_on_background_task_warning(self):
        """A ``warnings`` entry starting with the ``background-task:``
        prefix disqualifies the result even with error=None and
        error_category=None. Mirrors the interactive-hang check added
        in US-003. GitHub #97.
        """
        result = self._make(
            warnings=["background-task: skill launched Task(run_in_background=true)"]
        )
        assert result.error is None
        assert result.error_category is None
        assert result.succeeded_cleanly is False

    def test_succeeded_cleanly_tolerates_other_warnings(self):
        """Warnings that do NOT start with the interactive-hang prefix
        do not disqualify a clean success — only the prefixed tag does."""
        result = self._make(
            warnings=[
                "malformed stream-json line skipped: trailing garbage",
                "cleanup close(stderr) failed: OSError: EBADF",
            ]
        )
        assert result.succeeded_cleanly is True

    def test_succeeded_cleanly_false_when_not_succeeded(self):
        """If the underlying lenient ``succeeded`` is False (e.g. empty
        output), ``succeeded_cleanly`` must be False too."""
        result = self._make(output="")
        assert result.succeeded is False
        assert result.succeeded_cleanly is False

    def test_succeeded_cleanly_false_on_nonzero_exit(self):
        """Non-zero exit disqualifies via the underlying ``succeeded``."""
        result = self._make(exit_code=1)
        assert result.succeeded is False
        assert result.succeeded_cleanly is False

    def test_error_category_literal_values_assign_cleanly(self):
        """Each Literal value round-trips through the constructor. Python
        does not enforce ``Literal`` at runtime, so this is a self-
        documenting regression assertion — a future rename of one of
        the enum strings would fail here."""
        for category in (
            "rate_limit",
            "auth",
            "api",
            "interactive",
            "subprocess",
            "timeout",
        ):
            result = SkillResult(
                output="x",
                exit_code=0,
                skill_name="t",
                args="",
                error_category=category,
            )
            assert result.error_category == category


# ---------------------------------------------------------------------------
# _classify_result_message pure helper (US-002, DEC-010, DEC-013)
# ---------------------------------------------------------------------------


class TestClassifyResultMessage:
    """Pure-unit tests for :func:`clauditor.runner._classify_result_message`.

    Covers the keyword precedence (rate_limit before auth), the
    non-True / missing-key short-circuit, the missing/non-string
    ``result`` fallback, and the 4 KB truncation path — all without
    any subprocess mocking. Traces to US-002 of
    ``plans/super/63-runner-error-surfacing.md``.
    """

    def test_is_error_absent_returns_none_none(self):
        """A ``result`` message without an ``is_error`` key is benign."""
        assert _classify_result_message({"type": "result"}) == (None, None)

    def test_is_error_false_returns_none_none(self):
        """``is_error: False`` is the success path."""
        msg = {"type": "result", "is_error": False, "result": "anything"}
        assert _classify_result_message(msg) == (None, None)

    def test_is_error_string_true_not_treated_as_error(self):
        """Strict ``is True`` check — string ``"true"`` does NOT count."""
        msg = {"type": "result", "is_error": "true", "result": "boom"}
        assert _classify_result_message(msg) == (None, None)

    def test_is_error_int_1_not_treated_as_error(self):
        """Strict ``is True`` check — truthy int 1 does NOT count."""
        msg = {"type": "result", "is_error": 1, "result": "boom"}
        assert _classify_result_message(msg) == (None, None)

    def test_429_classifies_as_rate_limit(self):
        msg = {"type": "result", "is_error": True, "result": "got 429 back"}
        assert _classify_result_message(msg) == ("got 429 back", "rate_limit")

    def test_rate_limit_phrase_classifies_as_rate_limit(self):
        msg = {"type": "result", "is_error": True, "result": "Rate Limit exceeded"}
        text, category = _classify_result_message(msg)
        assert category == "rate_limit"
        assert text == "Rate Limit exceeded"

    def test_rate_hyphen_limit_phrase_classifies_as_rate_limit(self):
        msg = {
            "type": "result",
            "is_error": True,
            "result": "provider rate-limit hit",
        }
        assert _classify_result_message(msg) == (
            "provider rate-limit hit",
            "rate_limit",
        )

    def test_401_classifies_as_auth(self):
        msg = {"type": "result", "is_error": True, "result": "401 Unauthorized"}
        assert _classify_result_message(msg) == ("401 Unauthorized", "auth")

    def test_403_classifies_as_auth(self):
        msg = {"type": "result", "is_error": True, "result": "403 forbidden"}
        assert _classify_result_message(msg) == ("403 forbidden", "auth")

    def test_anthropic_api_key_classifies_as_auth(self):
        msg = {
            "type": "result",
            "is_error": True,
            "result": "Please check your ANTHROPIC_API_KEY",
        }
        text, category = _classify_result_message(msg)
        assert category == "auth"
        assert text == "Please check your ANTHROPIC_API_KEY"

    def test_anthropic_api_key_lowercase_classifies_as_auth(self):
        """Case-insensitive match — the classifier lowercases before probing,
        so ``anthropic_api_key`` in any casing is routed to ``auth``."""
        msg = {
            "type": "result",
            "is_error": True,
            "result": "check your anthropic_api_key env var",
        }
        _, category = _classify_result_message(msg)
        assert category == "auth"

    def test_authentication_phrase_classifies_as_auth(self):
        msg = {"type": "result", "is_error": True, "result": "Authentication failed"}
        assert _classify_result_message(msg) == ("Authentication failed", "auth")

    def test_generic_error_classifies_as_api(self):
        msg = {"type": "result", "is_error": True, "result": "Internal server error"}
        assert _classify_result_message(msg) == ("Internal server error", "api")

    def test_rate_limit_wins_over_auth_when_both_keywords_present(self):
        """Ordering: ``rate_limit`` is checked before ``auth`` so a message
        containing both 429 and an auth keyword is rate-limit."""
        msg = {
            "type": "result",
            "is_error": True,
            "result": "429 auth error mixed signal",
        }
        text, category = _classify_result_message(msg)
        assert category == "rate_limit"
        assert text == "429 auth error mixed signal"

    def test_missing_result_field_falls_back_to_api_error_no_detail(self):
        """``is_error: True`` without a ``result`` field → sentinel text."""
        msg = {"type": "result", "is_error": True}
        assert _classify_result_message(msg) == ("API error (no detail)", "api")

    def test_non_string_result_field_falls_back_to_api_error_no_detail(self):
        """``result: 123`` (number) triggers the isinstance guard."""
        msg = {"type": "result", "is_error": True, "result": 123}
        assert _classify_result_message(msg) == ("API error (no detail)", "api")

    def test_none_result_field_falls_back_to_api_error_no_detail(self):
        """``result: None`` explicitly (not missing) → sentinel text."""
        msg = {"type": "result", "is_error": True, "result": None}
        assert _classify_result_message(msg) == ("API error (no detail)", "api")

    def test_4kb_truncation_appends_suffix(self):
        """A huge payload is clipped at the soft cap plus the suffix."""
        big = "X" * 5000
        msg = {"type": "result", "is_error": True, "result": big}
        text, category = _classify_result_message(msg)
        assert text.endswith(" ... (truncated)")
        assert len(text) == _RESULT_TEXT_MAX_CHARS + len(" ... (truncated)")
        # Prefix is intact (for forensic value).
        assert text.startswith("X" * 100)
        # All X's → no classified keyword → "api" fallback.
        assert category == "api"

    def test_truncation_preserves_category_from_prefix(self):
        """Classification runs against the truncated text, so a keyword
        present in the surviving prefix is still detected."""
        # Keyword at position 0 survives truncation; trailing X's fill to
        # the 4 KB limit.
        msg = {
            "type": "result",
            "is_error": True,
            "result": "429 rate limit exceeded " + "X" * 5000,
        }
        text, category = _classify_result_message(msg)
        assert category == "rate_limit"
        assert text.endswith(" ... (truncated)")

    def test_short_text_not_truncated(self):
        """Text under the cap flows through verbatim."""
        msg = {"type": "result", "is_error": True, "result": "X" * 100}
        text, _ = _classify_result_message(msg)
        assert text == "X" * 100
        assert " ... (truncated)" not in text

    def test_exact_cap_not_truncated(self):
        """Text exactly ``_RESULT_TEXT_MAX_CHARS`` bytes is not clipped
        (the ``>`` comparison is strict)."""
        payload = "X" * _RESULT_TEXT_MAX_CHARS
        msg = {"type": "result", "is_error": True, "result": payload}
        text, _ = _classify_result_message(msg)
        assert text == payload
        assert " ... (truncated)" not in text


# ---------------------------------------------------------------------------
# Stream-json ``is_error: true`` integration (US-002, DEC-001, DEC-010)
# ---------------------------------------------------------------------------


class TestStreamJsonIsErrorResult:
    """End-to-end tests that feed an ``is_error: true`` stream through
    ``SkillRunner`` and assert the classification lands on
    ``SkillResult.error`` / ``error_category`` with DEC-001 stderr
    precedence honored. Traces to US-002 of
    ``plans/super/63-runner-error-surfacing.md``.
    """

    def _run_with_stream(self, fake: _FakePopen) -> SkillResult:
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            return runner.run("skill")

    def test_429_classified_as_rate_limit(self):
        fake = make_fake_skill_stream(
            "hello",
            error_text="API Error: Request rejected (429). Rate limit exceeded.",
        )
        result = self._run_with_stream(fake)
        assert result.error == (
            "API Error: Request rejected (429). Rate limit exceeded."
        )
        assert result.error_category == "rate_limit"

    def test_rate_limit_phrase_classified(self):
        fake = make_fake_skill_stream("hello", error_text="Rate limit exceeded")
        result = self._run_with_stream(fake)
        assert result.error == "Rate limit exceeded"
        assert result.error_category == "rate_limit"

    def test_401_classified_as_auth(self):
        fake = make_fake_skill_stream("hello", error_text="401 Unauthorized")
        result = self._run_with_stream(fake)
        assert result.error == "401 Unauthorized"
        assert result.error_category == "auth"

    def test_403_classified_as_auth(self):
        fake = make_fake_skill_stream("hello", error_text="403 Permission denied")
        result = self._run_with_stream(fake)
        assert result.error == "403 Permission denied"
        assert result.error_category == "auth"

    def test_anthropic_api_key_classified_as_auth(self):
        fake = make_fake_skill_stream(
            "hello", error_text="Check your ANTHROPIC_API_KEY"
        )
        result = self._run_with_stream(fake)
        assert result.error == "Check your ANTHROPIC_API_KEY"
        assert result.error_category == "auth"

    def test_generic_api_error_classified_as_api(self):
        fake = make_fake_skill_stream(
            "hello", error_text="Internal server error"
        )
        result = self._run_with_stream(fake)
        assert result.error == "Internal server error"
        assert result.error_category == "api"

    def test_is_error_false_no_classification(self):
        """Default stream (``is_error: False``) → no error, no category.
        Regression guard: the wiring must not misclassify clean streams."""
        fake = make_fake_skill_stream("hello")
        result = self._run_with_stream(fake)
        assert result.error is None
        assert result.error_category is None

    def test_is_error_absent_back_compat(self):
        """A stream where the ``result`` message has no ``is_error`` key
        at all (legacy shape) is treated as benign."""
        extra_messages = [
            {
                "type": "result",
                "subtype": "success",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ]
        # Build the stream manually: a single assistant text + a result
        # message that LACKS is_error entirely.
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hello"}],
                    },
                }
            ),
            json.dumps(extra_messages[0]),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error is None
        assert result.error_category is None

    def test_is_error_string_true_not_treated_as_error(self):
        """Result message with ``is_error: "true"`` (string) must not
        activate the classifier (strict ``is True`` check)."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hello"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "is_error": "true",
                    "result": "boom",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error is None
        assert result.error_category is None

    def test_missing_result_field_falls_back(self):
        """Result message with ``is_error: True`` but no ``result`` key
        → sentinel error text + ``api`` category."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hello"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error == "API error (no detail)"
        assert result.error_category == "api"

    def test_non_string_result_field_falls_back(self):
        """``is_error: True, result: 123`` → sentinel text + ``api``."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hello"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": 123,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error == "API error (no detail)"
        assert result.error_category == "api"

    def test_4kb_truncation(self):
        """A ~5 KB error payload is truncated to the soft cap + suffix,
        and the classifier still matches on the surviving prefix."""
        fake = make_fake_skill_stream("hello", error_text="X" * 5000)
        result = self._run_with_stream(fake)
        assert result.error is not None
        assert result.error.endswith(" ... (truncated)")
        assert len(result.error) == _RESULT_TEXT_MAX_CHARS + len(
            " ... (truncated)"
        )
        # All X's with no classification keyword → "api".
        assert result.error_category == "api"

    def test_short_text_not_truncated(self):
        """A 100-byte payload flows through verbatim."""
        fake = make_fake_skill_stream("hello", error_text="X" * 100)
        result = self._run_with_stream(fake)
        assert result.error == "X" * 100
        assert result.error is not None
        assert " ... (truncated)" not in result.error

    def test_stream_json_wins_over_stderr(self):
        """DEC-001: when stream-json reports an error, it takes
        precedence over stderr even on a clean exit code. Stderr is
        preserved by moving it into ``warnings``."""
        fake = make_fake_skill_stream("hello", error_text="429 rate limit")
        fake.stderr = iter(["some stderr diagnostic\n"])
        # Clean exit — the classifier still wins.
        fake.returncode = 0
        result = self._run_with_stream(fake)
        assert result.error == "429 rate limit"
        assert result.error_category == "rate_limit"
        # Stderr text is captured on warnings, not silently dropped.
        assert any(
            "some stderr diagnostic" in w for w in result.warnings
        ), f"expected stderr moved to warnings, got {result.warnings!r}"

    def test_stderr_precedence_preserved_when_no_stream_json_error(self):
        """When there is NO ``is_error: true`` payload, the pre-US-002
        precedence is preserved exactly: non-zero returncode + stderr
        becomes ``error`` with no category."""
        fake = make_fake_skill_stream("hello")
        fake.stderr = iter(["boom\n"])
        fake.returncode = 1
        result = self._run_with_stream(fake)
        assert result.error is not None
        assert "boom" in result.error
        assert result.error_category is None


# ---------------------------------------------------------------------------
# Fixture helpers (US-001, DEC-014)
# ---------------------------------------------------------------------------


class TestFixtureHelpers:
    """Covers the fixture-hybrid additions in ``tests/conftest.py``:

    - ``make_fake_skill_stream`` gains an ``error_text`` kwarg.
    - ``make_fake_interactive_hang_stream`` is a new sibling helper.

    The NDJSON is parsed line-by-line with ``json.loads`` so the
    assertions key on the actual fields, not the string layout.
    """

    @staticmethod
    def _parse_ndjson(fake: _FakePopen) -> list[dict]:
        """Drain the fake Popen's stdout and return the parsed messages."""
        body = fake.stdout.getvalue()
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def test_make_fake_skill_stream_default_is_error_false(self):
        """Back-compat: no ``error_text`` kwarg → ``is_error: False``."""
        fake = make_fake_skill_stream("hello")
        msgs = self._parse_ndjson(fake)
        result_msgs = [m for m in msgs if m.get("type") == "result"]
        assert len(result_msgs) == 1
        assert result_msgs[0]["is_error"] is False
        assert "result" not in result_msgs[0]

    def test_make_fake_skill_stream_error_text_sets_is_error_and_result(self):
        """``error_text="boom"`` → ``is_error: True`` and ``result: "boom"``."""
        fake = make_fake_skill_stream("hello", error_text="boom")
        msgs = self._parse_ndjson(fake)
        result_msgs = [m for m in msgs if m.get("type") == "result"]
        assert len(result_msgs) == 1
        assert result_msgs[0]["is_error"] is True
        assert result_msgs[0]["result"] == "boom"

    def test_interactive_hang_stream_default_shape(self):
        """Default hang stream: trailing ``?``, ``end_turn``, ``num_turns: 1``."""
        fake = make_fake_interactive_hang_stream()
        msgs = self._parse_ndjson(fake)

        assistants = [m for m in msgs if m.get("type") == "assistant"]
        assert len(assistants) == 1
        assistant = assistants[0]
        assert assistant["message"]["stop_reason"] == "end_turn"
        content = assistant["message"]["content"]
        # Default: no tool_use block, just a text block.
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"].endswith("?")

        results = [m for m in msgs if m.get("type") == "result"]
        assert len(results) == 1
        assert results[0]["num_turns"] == 1
        assert results[0]["is_error"] is False
        assert results[0]["subtype"] == "success"

    def test_interactive_hang_stream_with_tool_use_block(self):
        """``use_tool_use=True`` injects an ``AskUserQuestion`` tool_use block."""
        fake = make_fake_interactive_hang_stream(use_tool_use=True)
        msgs = self._parse_ndjson(fake)

        assistants = [m for m in msgs if m.get("type") == "assistant"]
        assert len(assistants) == 1
        content = assistants[0]["message"]["content"]

        text_blocks = [b for b in content if b.get("type") == "text"]
        tool_use_blocks = [b for b in content if b.get("type") == "tool_use"]
        assert len(text_blocks) == 1
        assert len(tool_use_blocks) == 1
        assert tool_use_blocks[0]["name"] == "AskUserQuestion"
        # The question should be carried in the standard input.questions shape.
        assert "questions" in tool_use_blocks[0]["input"]

    def test_interactive_hang_stream_custom_text(self):
        """The ``text`` kwarg flows to both the text block and the tool input."""
        fake = make_fake_interactive_hang_stream(
            text="Which city?", use_tool_use=True
        )
        msgs = self._parse_ndjson(fake)
        content = next(m for m in msgs if m.get("type") == "assistant")[
            "message"
        ]["content"]
        text_block = next(b for b in content if b.get("type") == "text")
        tool_use = next(b for b in content if b.get("type") == "tool_use")
        assert text_block["text"] == "Which city?"
        assert (
            tool_use["input"]["questions"][0]["question"] == "Which city?"
        )


# ---------------------------------------------------------------------------
# Interactive-hang detection (US-003, DEC-005, DEC-010, DEC-013)
# ---------------------------------------------------------------------------


def _assistant_event(
    *,
    text: str | None = None,
    stop_reason: str | None = "end_turn",
    tool_use_name: str | None = None,
) -> dict:
    """Build an assistant stream event for ``_detect_interactive_hang`` tests."""
    content: list[dict] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_use_name is not None:
        content.append(
            {
                "type": "tool_use",
                "id": "toolu_fake",
                "name": tool_use_name,
                "input": {},
            }
        )
    message: dict = {"role": "assistant", "content": content}
    if stop_reason is not None:
        message["stop_reason"] = stop_reason
    return {"type": "assistant", "message": message}


def _result_event(num_turns: int | None = 1) -> dict:
    event: dict = {"type": "result", "subtype": "success", "is_error": False}
    if num_turns is not None:
        event["num_turns"] = num_turns
    return event


class TestDetectInteractiveHang:
    """Pure-unit tests for ``_detect_interactive_hang``. No subprocess, no fixtures."""

    def test_empty_stream_events_returns_false(self):
        assert _detect_interactive_hang([], "hello?") is False

    def test_single_turn_trailing_question_triggers(self):
        events = [
            _assistant_event(text="What would you like?"),
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "What would you like?") is True

    def test_single_turn_ask_user_question_tool_use_triggers(self):
        events = [
            _assistant_event(
                text="Please clarify.",
                tool_use_name="AskUserQuestion",
            ),
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "Please clarify.") is True

    def test_single_turn_no_question_no_tool_use_returns_false(self):
        events = [
            _assistant_event(text="All done."),
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "All done.") is False

    def test_multi_turn_with_trailing_question_returns_false(self):
        events = [
            _assistant_event(text="Working..."),
            _assistant_event(text="Need more info?"),
            _result_event(num_turns=2),
        ]
        assert _detect_interactive_hang(events, "Need more info?") is False

    def test_missing_stop_reason_returns_false(self):
        events = [
            _assistant_event(text="Ends with?", stop_reason=None),
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "Ends with?") is False

    def test_missing_num_turns_returns_false(self):
        """Conservative: no num_turns means we cannot confirm single-turn."""
        events = [
            _assistant_event(text="Ends with?"),
            _result_event(num_turns=None),
        ]
        assert _detect_interactive_hang(events, "Ends with?") is False

    def test_different_tool_use_name_returns_false(self):
        events = [
            _assistant_event(
                text="All done.", tool_use_name="SomeOtherTool"
            ),
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "All done.") is False

    def test_malformed_events_degrade_to_false(self):
        """Events with missing/non-dict ``message`` or non-list ``content``
        must degrade cleanly without raising.
        """
        events = [
            {"type": "assistant"},  # no message key
            {"type": "assistant", "message": "not-a-dict"},  # non-dict message
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "not-a-list"},
            },
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "hello") is False

    def test_non_dict_event_skipped(self):
        """Top-level events that are not dicts must not raise."""
        events: list = ["not-a-dict", 42, _result_event(num_turns=1)]
        assert _detect_interactive_hang(events, "hello?") is False

    def test_non_string_stop_reason_treated_as_missing(self):
        events = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "stop_reason": 42,
                    "content": [{"type": "text", "text": "Ends with?"}],
                },
            },
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "Ends with?") is False

    def test_tool_use_loop_tolerates_noise(self):
        """Exercises defensive continues in the signal-(b) loop:
        non-dict events, non-assistant events, non-dict messages,
        non-list content, non-dict content blocks — all must be skipped
        without interfering with a real AskUserQuestion tool_use block
        reached later in the stream.
        """
        events: list = [
            "not-a-dict",  # top-level non-dict, skipped
            {"type": "system"},  # wrong type, skipped
            {"type": "assistant"},  # missing message, skipped
            {"type": "assistant", "message": "not-a-dict"},  # non-dict message
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "stop_reason": "end_turn",
                    "content": "not-a-list",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "stop_reason": "end_turn",
                    "content": [
                        "not-a-dict-block",
                        {"type": "text", "text": "Please."},
                        {
                            "type": "tool_use",
                            "name": "AskUserQuestion",
                            "input": {},
                        },
                    ],
                },
            },
            _result_event(num_turns=1),
        ]
        assert _detect_interactive_hang(events, "Please.") is True


class TestInteractiveHangDetection:
    """End-to-end: feed an interactive-hang stream through ``SkillRunner``
    and assert the warning prefix + ``error_category`` wiring.
    Traces to US-003 of ``plans/super/63-runner-error-surfacing.md``.
    """

    def _run_with_stream(
        self,
        fake: _FakePopen,
        *,
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            return runner.run("skill", allow_hang_heuristic=allow_hang_heuristic)

    def test_trailing_question_mark_triggers(self):
        fake = make_fake_interactive_hang_stream()
        result = self._run_with_stream(fake)
        assert result.error_category == "interactive"
        assert result.error is None
        assert any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings
        assert result.succeeded_cleanly is False

    def test_ask_user_question_tool_use_triggers(self):
        fake = make_fake_interactive_hang_stream(
            text="Please provide the target.",
            use_tool_use=True,
        )
        result = self._run_with_stream(fake)
        assert result.error_category == "interactive"
        assert any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings
        assert result.succeeded_cleanly is False

    def test_both_signals_triggers_once(self):
        """Trailing ``?`` AND tool_use present → one warning, not duplicated."""
        fake = make_fake_interactive_hang_stream(
            text="Which option?", use_tool_use=True
        )
        result = self._run_with_stream(fake)
        assert result.error_category == "interactive"
        hang_warnings = [
            w
            for w in result.warnings
            if w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
        ]
        assert len(hang_warnings) == 1

    def test_neither_signal_no_trigger(self):
        """Normal successful stream (no `?`, no tool_use) → no trigger."""
        fake = make_fake_skill_stream("All done.")
        result = self._run_with_stream(fake)
        assert result.error_category is None
        assert not any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        )

    def test_multi_turn_no_trigger(self):
        """``num_turns: 3`` + trailing ``?`` → does not trigger."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "stop_reason": "end_turn",
                        "content": [
                            {"type": "text", "text": "anything more?"}
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "num_turns": 3,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error_category is None
        assert not any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        )

    def test_missing_num_turns_no_trigger(self):
        """Result message with no ``num_turns`` at all → conservative False."""
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "stop_reason": "end_turn",
                        "content": [
                            {"type": "text", "text": "What next?"}
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error_category is None
        assert not any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        )

    def test_allow_hang_heuristic_false_suppresses(self):
        """Escape hatch off → no detection, no warning, no category."""
        fake = make_fake_interactive_hang_stream()
        result = self._run_with_stream(fake, allow_hang_heuristic=False)
        assert result.error_category is None
        assert not any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        )

    def test_api_error_wins_over_hang(self):
        """Stream with BOTH ``is_error: true`` AND trailing ``?`` →
        error_category reflects the API error, not the heuristic. The
        hang warning is NOT appended (detection is skipped when an API
        error has already been classified).
        """
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "stop_reason": "end_turn",
                        "content": [
                            {"type": "text", "text": "What should I do?"}
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "result": "429 rate limit",
                    "num_turns": 1,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
        ]
        fake = _FakePopen(lines)
        result = self._run_with_stream(fake)
        assert result.error_category == "rate_limit"
        assert result.error == "429 rate limit"
        assert not any(
            w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX)
            for w in result.warnings
        )


class TestApiKeySourceParsing:
    """Parse ``apiKeySource`` from the stream-json ``system/init`` message.

    Covers DEC-005 (stderr line + field), DEC-012 (suppress line when
    None), DEC-015 (first init wins), and DEC-017 (match on compound
    ``type=="system" AND subtype=="init"``). Traces to US-004 of
    ``plans/super/64-runner-auth-timeout.md``.
    """

    def _run_with_stream(self, fake: _FakePopen) -> SkillResult:
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            return runner.run("skill")

    def test_init_apikeysource_none(self):
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "session_id": "abc",
                "apiKeySource": "none",
            },
        )
        result = self._run_with_stream(fake)
        assert result.api_key_source == "none"

    def test_init_apikeysource_env_var(self):
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "session_id": "abc",
                "apiKeySource": "ANTHROPIC_API_KEY",
            },
        )
        result = self._run_with_stream(fake)
        assert result.api_key_source == "ANTHROPIC_API_KEY"

    def test_init_apikeysource_missing(self):
        # init present but no apiKeySource field → api_key_source is None,
        # no crash.
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "session_id": "abc",
            },
        )
        result = self._run_with_stream(fake)
        assert result.api_key_source is None

    def test_no_init_message(self):
        # Stream has no system/init message at all. Field stays None and
        # no crash occurs.
        fake = make_fake_skill_stream("hello")
        result = self._run_with_stream(fake)
        assert result.api_key_source is None

    def test_first_init_wins(self):
        # Two init messages; the first value is kept, the second ignored
        # (DEC-015).
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "first-value",
            },
            extra_messages=[
                {
                    "type": "system",
                    "subtype": "init",
                    "apiKeySource": "second-value",
                }
            ],
        )
        result = self._run_with_stream(fake)
        assert result.api_key_source == "first-value"

    def test_stderr_emits_info_line_when_present(self, capsys):
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "ANTHROPIC_API_KEY",
            },
        )
        self._run_with_stream(fake)
        captured = capsys.readouterr()
        # Exactly one matching line per run.
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        assert len(matching) == 1, captured.err
        assert "clauditor.runner:" in matching[0]
        assert "apiKeySource=ANTHROPIC_API_KEY" in matching[0]

    def test_stderr_line_suppressed_when_none(self, capsys):
        # No init message → no apiKeySource= line on stderr (DEC-012).
        fake = make_fake_skill_stream("hello")
        self._run_with_stream(fake)
        captured = capsys.readouterr()
        assert "apiKeySource=" not in captured.err

    def test_stderr_line_appends_subject_suffix(self, capsys):
        # Issue #107: when the caller threads a ``subject`` label
        # through ``ClaudeCodeHarness.invoke`` (as grader call sites do via
        # ``call_anthropic``), the stderr info line gains a
        # ``" (<subject>)"`` suffix so operators can attribute each
        # line to a specific internal LLM call.
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "none",
            },
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            ClaudeCodeHarness(claude_bin="claude").invoke(
                "prompt",
                cwd=None,
                env=None,
                timeout=180,
                subject="L2 extraction",
            )
        captured = capsys.readouterr()
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        assert len(matching) == 1, captured.err
        assert (
            matching[0]
            == "clauditor.runner: apiKeySource=none (L2 extraction)"
        )

    def test_stderr_line_omits_suffix_when_subject_none(self, capsys):
        # Issue #107 acceptance criterion 4: no regression in the
        # existing format when ``subject`` is not threaded through.
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "none",
            },
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            ClaudeCodeHarness(claude_bin="claude").invoke(
                "prompt",
                cwd=None,
                env=None,
                timeout=180,
            )
        captured = capsys.readouterr()
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        assert matching == ["clauditor.runner: apiKeySource=none"]

    def test_stderr_line_sanitizes_subject_newlines_and_length(self, capsys):
        # Copilot PR #114 review: ``subject`` is free-form, so a
        # caller that accidentally passes a multi-line string or an
        # unbounded value must not break the "one line per run"
        # invariant that log scrapers rely on. Sanitization replaces
        # CR/LF with spaces, strips, and caps at 200 chars.
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "none",
            },
        )
        hostile = "  L2\nextraction\rwith   " + ("x" * 500) + "  "
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            ClaudeCodeHarness(claude_bin="claude").invoke(
                "prompt",
                cwd=None,
                env=None,
                timeout=180,
                subject=hostile,
            )
        captured = capsys.readouterr()
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        # One line only — embedded \n did not split the output.
        assert len(matching) == 1, captured.err
        line = matching[0]
        assert "\n" not in line and "\r" not in line
        # Leading/trailing whitespace stripped; CR/LF replaced with spaces.
        expected_prefix = (
            "clauditor.runner: apiKeySource=none (L2 extraction with "
        )
        assert line.startswith(expected_prefix)
        # 200-char cap applied to the sanitized subject body.
        start = line.index("(") + 1
        end = line.rindex(")")
        assert end - start <= 200

    def test_stderr_line_omits_suffix_when_subject_whitespace_only(
        self, capsys
    ):
        # A whitespace-only subject strips to empty and must not emit
        # a trailing ``()`` suffix — degrade to the unlabeled format.
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "none",
            },
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            ClaudeCodeHarness(claude_bin="claude").invoke(
                "prompt",
                cwd=None,
                env=None,
                timeout=180,
                subject="   \n\r ",
            )
        captured = capsys.readouterr()
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        assert matching == ["clauditor.runner: apiKeySource=none"]

    def test_stderr_line_suppressed_when_init_missing_field(self, capsys):
        # init present but apiKeySource absent → no stderr line (DEC-012).
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "session_id": "abc",
            },
        )
        self._run_with_stream(fake)
        captured = capsys.readouterr()
        assert "apiKeySource=" not in captured.err

    def test_init_apikeysource_non_string_ignored(self):
        # Defensive: a non-string apiKeySource (e.g. a dict or int from a
        # buggy CLI build) is ignored rather than crashing.
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": 42,
            },
        )
        result = self._run_with_stream(fake)
        assert result.api_key_source is None

    def test_system_event_without_init_subtype_ignored(self):
        # type=="system" but subtype!="init" must not populate the field
        # (DEC-017).
        fake = make_fake_skill_stream(
            "hello",
            init_message={
                "type": "system",
                "subtype": "hook",
                "apiKeySource": "should-be-ignored",
            },
        )
        result = self._run_with_stream(fake)
        assert result.api_key_source is None


class TestEnvWithoutApiKey:
    """Pure-unit tests for :func:`clauditor.runner.env_without_api_key`.

    Covers DEC-007 (strip both auth vars), DEC-011 (non-mutating pure
    helper), and DEC-016 (preserve non-auth Anthropic env vars). No
    subprocess mocks — the helper is pure.
    """

    def test_strips_both_auth_vars(self):
        base = {
            "ANTHROPIC_API_KEY": "sk-key",
            "ANTHROPIC_AUTH_TOKEN": "tok-abc",
            "PATH": "/usr/bin",
        }
        result = env_without_api_key(base)
        assert "ANTHROPIC_API_KEY" not in result
        assert "ANTHROPIC_AUTH_TOKEN" not in result
        assert result["PATH"] == "/usr/bin"

    def test_preserves_other_vars(self):
        base = {
            "ANTHROPIC_API_KEY": "sk-key",
            "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            "PATH": "/usr/bin",
            "UNRELATED": "value",
        }
        result = env_without_api_key(base)
        assert result == {
            "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            "PATH": "/usr/bin",
            "UNRELATED": "value",
        }

    def test_default_reads_os_environ(self):
        fake_env = {
            "ANTHROPIC_API_KEY": "sk-key",
            "PATH": "/usr/bin",
            "MARKER": "present",
        }
        with patch.dict("os.environ", fake_env, clear=True):
            result = env_without_api_key()
        assert "ANTHROPIC_API_KEY" not in result
        assert result["PATH"] == "/usr/bin"
        assert result["MARKER"] == "present"

    def test_is_non_mutating(self):
        base = {
            "ANTHROPIC_API_KEY": "sk-key",
            "ANTHROPIC_AUTH_TOKEN": "tok-abc",
            "PATH": "/usr/bin",
        }
        original = dict(base)
        result = env_without_api_key(base)
        assert base == original
        assert result is not base

    def test_no_auth_vars_present(self):
        base = {"PATH": "/usr/bin", "HOME": "/home/user"}
        result = env_without_api_key(base)
        assert result == base
        assert result is not base

    def test_strips_openai_api_key(self):
        """DEC-008 of plans/super/145-openai-provider.md (US-008).

        ``OPENAI_API_KEY`` is stripped alongside the Anthropic auth
        env vars so that under ``--transport cli`` an untrusted skill
        subprocess cannot silently spend the operator's OpenAI quota.
        Non-mutating per ``.claude/rules/non-mutating-scrub.md``.
        """
        base = {
            "OPENAI_API_KEY": "secret",
            "ANTHROPIC_API_KEY": "anth",
            "FOO": "bar",
        }
        original = dict(base)
        result = env_without_api_key(base)
        # Both API keys stripped, unrelated key preserved.
        assert "OPENAI_API_KEY" not in result
        assert "ANTHROPIC_API_KEY" not in result
        assert result["FOO"] == "bar"
        # Non-mutating: input dict still has all three original keys.
        assert base == original
        assert "OPENAI_API_KEY" in base
        assert "ANTHROPIC_API_KEY" in base
        assert base["FOO"] == "bar"


class TestEnvWithSyncTasks:
    """Pure-unit tests for :func:`clauditor.runner.env_with_sync_tasks`.

    Tier 1.5 of GitHub #103. Covers: sets
    ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1``, preserves every other
    key, reads ``os.environ`` when ``base_env`` is ``None``, is
    non-mutating, and composes cleanly with
    :func:`env_without_api_key` (both directions).
    """

    def test_sets_disable_background_tasks(self):
        base = {"PATH": "/usr/bin"}
        result = env_with_sync_tasks(base)
        assert result["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
        assert result["PATH"] == "/usr/bin"

    def test_preserves_other_vars(self):
        base = {
            "ANTHROPIC_API_KEY": "sk-key",
            "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            "PATH": "/usr/bin",
        }
        result = env_with_sync_tasks(base)
        assert result["ANTHROPIC_API_KEY"] == "sk-key"
        assert result["ANTHROPIC_BASE_URL"] == "https://proxy.example.com"
        assert result["PATH"] == "/usr/bin"
        assert result["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"

    def test_default_reads_os_environ(self):
        fake_env = {"PATH": "/usr/bin", "MARKER": "present"}
        with patch.dict("os.environ", fake_env, clear=True):
            result = env_with_sync_tasks()
        assert result["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
        assert result["PATH"] == "/usr/bin"
        assert result["MARKER"] == "present"

    def test_is_non_mutating(self):
        base = {"PATH": "/usr/bin"}
        original = dict(base)
        result = env_with_sync_tasks(base)
        assert base == original
        assert result is not base

    def test_overrides_preexisting_value(self):
        """If the caller's env already has the var set to something
        unexpected (e.g. ``"0"``), the helper still forces ``"1"``."""
        base = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "0", "PATH": "/usr/bin"}
        result = env_with_sync_tasks(base)
        assert result["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"

    def test_composes_with_env_without_api_key(self):
        """Both effects apply when the helpers are chained — the order
        does not matter because neither touches the other's concern."""
        base = {
            "ANTHROPIC_API_KEY": "sk-key",
            "ANTHROPIC_AUTH_TOKEN": "tok",
            "PATH": "/usr/bin",
        }
        left = env_with_sync_tasks(env_without_api_key(base))
        right = env_without_api_key(env_with_sync_tasks(base))
        for result in (left, right):
            assert "ANTHROPIC_API_KEY" not in result
            assert "ANTHROPIC_AUTH_TOKEN" not in result
            assert result["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
            assert result["PATH"] == "/usr/bin"


# ---------------------------------------------------------------------------
# US-001 (#86): Regression smoke + fixture-replay for the extraction
# ---------------------------------------------------------------------------


class TestSkillRunnerInvokeRegressionSmoke:
    """Smoke tests for ``SkillRunner.run`` invariants preserved by
    the US-001 extraction of ``_invoke_claude_cli`` (now
    ``ClaudeCodeHarness.invoke`` per issue #148).

    These tests were authored BEFORE the extraction to lock in the
    current field-population shape of ``SkillResult``. Every existing
    field is asserted populated (or defaulted correctly) so the
    projection from :class:`InvokeResult` back onto :class:`SkillResult`
    is exhaustive.
    """

    def test_skillresult_every_field_populated_on_success(self):
        """Full-field smoke: all observable SkillResult fields reflect
        the stream-json input, with ``skill_name``/``args`` threaded
        from the caller and ``duration_seconds`` populated by the
        try/finally measurement."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=make_fake_skill_stream(
                "canonical success",
                input_tokens=123,
                output_tokens=45,
                init_message={
                    "type": "system",
                    "subtype": "init",
                    "apiKeySource": "ANTHROPIC_API_KEY",
                },
            ),
        ):
            result = runner.run("canonical-skill", "some-args")
        assert result.output == "canonical success"
        assert result.exit_code == 0
        assert result.skill_name == "canonical-skill"
        assert result.args == "some-args"
        assert result.duration_seconds >= 0.0
        assert result.error is None
        assert result.error_category is None
        assert result.outputs == {}
        assert result.input_tokens == 123
        assert result.output_tokens == 45
        # Init (+ apiKeySource) + assistant + result = 3 events.
        assert len(result.raw_messages) == 3
        assert len(result.stream_events) == 3
        assert result.warnings == []
        assert result.api_key_source == "ANTHROPIC_API_KEY"

    def test_fixture_replay_output_matches_pinned_value(self):
        """Canonical success stream-json → pinned ``output`` text."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=make_fake_skill_stream(
                "pinned-output-text-abc123",
                input_tokens=10,
                output_tokens=20,
            ),
        ):
            result = runner.run("skill", "")
        assert result.output == "pinned-output-text-abc123"
        assert result.exit_code == 0
        assert result.input_tokens == 10
        assert result.output_tokens == 20

    def test_fixture_replay_rate_limit_category(self):
        """429-style stream-json result → ``error_category=="rate_limit"``."""
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=make_fake_skill_stream(
                "partial",
                error_text="429 Too Many Requests: rate limit exceeded",
            ),
        ):
            result = runner.run("skill", "")
        assert result.error_category == "rate_limit"
        assert "429" in (result.error or "")


# ---------------------------------------------------------------------------
# ClaudeCodeHarness.invoke direct tests
# (originally _invoke_claude_cli per #86 US-001; migrated to the harness
# protocol in #148 US-004)
# ---------------------------------------------------------------------------


class TestInvokeClaudeCli:
    """Direct tests for ``ClaudeCodeHarness.invoke``.

    Originally extracted as the module-private ``_invoke_claude_cli``
    helper per US-001 of ``plans/super/86-claude-cli-transport.md``;
    migrated to ``ClaudeCodeHarness.invoke`` per #148 US-004 (the body
    moved verbatim into the harness method, function deleted, no
    compatibility shim).

    The harness's ``invoke`` is the transport-level primitive: it takes
    a pre-built prompt plus explicit ``cwd`` / ``env`` / ``timeout`` and
    returns a lean :class:`InvokeResult` with no skill-name / args
    context. ``call_anthropic``'s CLI transport branch is the second caller
    transport branch) that needs exactly this raw-prompt shape.
    """

    def _call(self, fake, **overrides):
        """Run ``ClaudeCodeHarness.invoke`` with the fake Popen + defaults."""
        # ``claude_bin`` and ``allow_hang_heuristic`` are
        # construction-time knobs on the harness now (US-004 of
        # ``plans/super/148-extract-harness-protocol.md``); pull them
        # out of ``overrides`` before forwarding the rest as ``invoke``
        # call kwargs.
        claude_bin = overrides.pop("claude_bin", "claude")
        allow_hang_heuristic = overrides.pop("allow_hang_heuristic", True)
        kwargs = dict(
            cwd=None,
            env=None,
            timeout=180,
        )
        kwargs.update(overrides)
        harness = ClaudeCodeHarness(
            claude_bin=claude_bin,
            allow_hang_heuristic=allow_hang_heuristic,
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            return harness.invoke("hi there prompt", **kwargs)

    # --------------------------------------------------------------- #
    # Success + field-population                                       #
    # --------------------------------------------------------------- #

    def test_success_returns_invokeresult_with_every_field(self):
        """InvokeResult is populated identically to SkillResult minus
        skill_name / args (which live only on the higher-level dataclass)."""
        result = self._call(
            make_fake_skill_stream(
                "the quick brown fox",
                input_tokens=7,
                output_tokens=11,
                init_message={
                    "type": "system",
                    "subtype": "init",
                    "apiKeySource": "ANTHROPIC_API_KEY",
                },
            )
        )
        assert isinstance(result, InvokeResult)
        assert result.output == "the quick brown fox"
        assert result.exit_code == 0
        assert result.duration_seconds >= 0.0
        assert result.error is None
        assert result.error_category is None
        assert result.input_tokens == 7
        assert result.output_tokens == 11
        # init + assistant + result = 3 entries.
        assert len(result.raw_messages) == 3
        assert len(result.stream_events) == 3
        assert result.warnings == []
        assert result.api_key_source == "ANTHROPIC_API_KEY"

    def test_success_no_skill_name_or_args_on_invokeresult(self):
        """InvokeResult is the transport primitive — no slash-command
        context. SkillResult adds those on top."""
        result = self._call(make_fake_skill_stream("ok"))
        # InvokeResult does NOT carry skill_name / args — those belong
        # to the SkillResult projection. Guard the dataclass surface.
        assert not hasattr(result, "skill_name")
        assert not hasattr(result, "args")

    # --------------------------------------------------------------- #
    # Error-category classification (US-002 branches reused verbatim)  #
    # --------------------------------------------------------------- #

    def test_rate_limit_category(self):
        """``429`` in the result text → ``error_category == "rate_limit"``."""
        result = self._call(
            make_fake_skill_stream(
                "partial",
                error_text="429 Too Many Requests: rate limit exceeded",
            )
        )
        assert result.error_category == "rate_limit"
        assert "429" in (result.error or "")

    def test_auth_category(self):
        """``401`` / ``unauthorized`` → ``error_category == "auth"``."""
        result = self._call(
            make_fake_skill_stream(
                "partial",
                error_text="401 Unauthorized: check your ANTHROPIC_API_KEY",
            )
        )
        assert result.error_category == "auth"

    def test_api_5xx_category_fallback(self):
        """A 5xx result text with no rate-limit / auth keyword falls
        through to the ``api`` category (the default per DEC-010)."""
        result = self._call(
            make_fake_skill_stream(
                "partial",
                error_text="500 Internal Server Error: upstream timeout",
            )
        )
        assert result.error_category == "api"

    # --------------------------------------------------------------- #
    # Defensive parsing + observability                                #
    # --------------------------------------------------------------- #

    def test_malformed_ndjson_line_skipped_and_warned(self, capsys):
        """A malformed JSON line is skipped, a stderr warning is
        printed, and ``InvokeResult.warnings`` records the skip."""
        lines = [
            "this is not json at all",
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
                {"type": "result", "usage": {"input_tokens": 2, "output_tokens": 3}}
            ),
        ]
        result = self._call(_FakePopen(lines))
        assert result.output == "survived"
        assert result.input_tokens == 2
        assert result.output_tokens == 3
        assert any("malformed stream-json" in w for w in result.warnings)
        captured = capsys.readouterr()
        assert "malformed" in captured.err

    def test_missing_result_message_warning(self, capsys):
        """Stream without a ``result`` message: warnings records the
        EOF condition and tokens default to 0."""
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
        result = self._call(_FakePopen(lines))
        assert result.output == "no result"
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert any(
            "without a 'result' message" in w for w in result.warnings
        )
        captured = capsys.readouterr()
        assert "without a 'result'" in captured.err

    # --------------------------------------------------------------- #
    # Timeout + binary-missing terminal paths                          #
    # --------------------------------------------------------------- #

    def test_timeout_kills_process_and_reports_timeout_category(self):
        """Watchdog firing → exit_code=-1, error="timeout",
        error_category="timeout", and the child is killed."""
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
            patch(
                "clauditor._harnesses._claude_code.subprocess.Popen",
                return_value=fake,
            ),
            patch("clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer),
        ):
            result = ClaudeCodeHarness(
                claude_bin="claude", allow_hang_heuristic=True
            ).invoke(
                "hi",
                cwd=None,
                env=None,
                timeout=1,
            )
        assert result.exit_code == -1
        assert result.error == "timeout"
        assert result.error_category == "timeout"
        assert result.duration_seconds >= 0.0
        assert fake.kill_called is True

    def test_filenotfounderror_on_missing_binary(self):
        """Popen raising FileNotFoundError produces a clean InvokeResult
        with a descriptive error and no leaked stream state."""
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=FileNotFoundError
        ):
            result = ClaudeCodeHarness(
                claude_bin="nonexistent-claude-binary",
                allow_hang_heuristic=True,
            ).invoke(
                "hi",
                cwd=None,
                env=None,
                timeout=180,
            )
        assert isinstance(result, InvokeResult)
        assert result.exit_code == -1
        assert result.output == ""
        assert "not found" in (result.error or "")
        assert "nonexistent-claude-binary" in (result.error or "")
        assert result.duration_seconds >= 0.0
        assert result.raw_messages == []
        assert result.stream_events == []

    # --------------------------------------------------------------- #
    # Env + cwd thread-through (transport primitive has no defaults)   #
    # --------------------------------------------------------------- #

    def test_env_none_passes_through_to_popen_verbatim(self):
        """``env=None`` reaches Popen unchanged (Popen's own default:
        inherit ``os.environ``)."""
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("ok")
            ClaudeCodeHarness(
                claude_bin="claude", allow_hang_heuristic=True
            ).invoke(
                "hi",
                cwd=None,
                env=None,
                timeout=180,
            )
            assert mock_popen.call_args.kwargs["env"] is None
            # cwd=None → Popen.cwd=None (helper has no self.project_dir
            # fallback — that lives on SkillRunner._invoke).
            assert mock_popen.call_args.kwargs["cwd"] is None

    def test_env_dict_and_cwd_threaded_to_popen(self, tmp_path):
        """A non-None ``env`` / ``cwd`` reaches Popen verbatim. The
        CLI-transport caller (US-003) uses this path with
        ``env=env_without_api_key(os.environ)``."""
        env = {"PATH": "/usr/bin", "MARKER": "x"}
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("ok")
            ClaudeCodeHarness(
                claude_bin="claude", allow_hang_heuristic=True
            ).invoke(
                "hi",
                cwd=tmp_path,
                env=env,
                timeout=180,
            )
            assert mock_popen.call_args.kwargs["env"] == env
            assert mock_popen.call_args.kwargs["cwd"] == str(tmp_path)

    def test_model_kwarg_added_to_argv(self):
        """``model=`` kwarg is inserted into the subprocess argv as
        ``["--model", model]`` so CLI transport honours the requested model.
        """
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("ok")
            ClaudeCodeHarness(
                claude_bin="claude", allow_hang_heuristic=True
            ).invoke(
                "hi",
                cwd=None,
                env=None,
                timeout=180,
                model="claude-haiku-4-5-20251001",
            )
            argv = mock_popen.call_args.args[0]
            assert "--model" in argv
            idx = argv.index("--model")
            assert argv[idx + 1] == "claude-haiku-4-5-20251001"

    def test_model_none_omits_flag(self):
        """When ``model=None`` (the default), ``--model`` is not added to argv."""
        with patch("clauditor._harnesses._claude_code.subprocess.Popen") as mock_popen:
            mock_popen.return_value = make_fake_skill_stream("ok")
            ClaudeCodeHarness(
                claude_bin="claude", allow_hang_heuristic=True
            ).invoke(
                "hi",
                cwd=None,
                env=None,
                timeout=180,
            )
            argv = mock_popen.call_args.args[0]
            assert "--model" not in argv

    # --------------------------------------------------------------- #
    # Single-caller invariant (US-001 done-when criterion)             #
    # --------------------------------------------------------------- #

    def test_invoke_claude_cli_helper_is_gone(self):
        """Drift guard: the ``_invoke_claude_cli`` module-private helper
        was deleted in US-004 of issue #148 (no compatibility shim per
        DEC-001 / Q1 → A). All transport-level calls go through
        :class:`ClaudeCodeHarness.invoke` now. A re-introduced helper
        would re-fragment the harness seam, so this test fails loudly.
        """
        import pathlib

        import clauditor

        src_root = pathlib.Path(clauditor.__file__).parent
        hits: list[pathlib.Path] = []
        for py_file in src_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if "_invoke_claude_cli" in text:
                hits.append(py_file)
        assert hits == [], (
            "Unexpected reference to _invoke_claude_cli — the helper "
            f"was deleted in US-004 of issue #148; got: {hits!r}"
        )

        # Direct import attempt should now fail.
        from clauditor import runner as _runner

        assert not hasattr(_runner, "_invoke_claude_cli")


# ---------------------------------------------------------------------------
# Background-task non-completion detection (GitHub #97)
# ---------------------------------------------------------------------------


def _bg_task_tool_use(
    *, run_in_background: bool = True, name: str = "Task", idx: int = 0
) -> dict:
    """Build a ``tool_use`` block for background-task detector tests."""
    return {
        "type": "tool_use",
        "id": f"toolu_bg_{idx}",
        "name": name,
        "input": {
            "description": "background agent",
            "prompt": "do work",
            "run_in_background": run_in_background,
        },
    }


def _bg_assistant_event(
    *,
    text: str | None = None,
    launches: int = 1,
    run_in_background: bool = True,
    name: str = "Task",
    stop_reason: str | None = "end_turn",
) -> dict:
    """Build an assistant event carrying Task tool_use blocks + optional text."""
    content: list[dict] = []
    for i in range(launches):
        content.append(
            _bg_task_tool_use(
                run_in_background=run_in_background, name=name, idx=i
            )
        )
    if text is not None:
        content.append({"type": "text", "text": text})
    message: dict = {"role": "assistant", "content": content}
    if stop_reason is not None:
        message["stop_reason"] = stop_reason
    return {"type": "assistant", "message": message}


class TestCountBackgroundTaskLaunches:
    """Pure-unit tests for ``_count_background_task_launches``."""

    def test_empty_stream_returns_zero(self):
        assert _count_background_task_launches([]) == 0

    def test_single_task_with_run_in_background(self):
        events = [_bg_assistant_event(launches=1)]
        assert _count_background_task_launches(events) == 1

    def test_three_tasks_with_run_in_background(self):
        events = [_bg_assistant_event(launches=3)]
        assert _count_background_task_launches(events) == 3

    def test_task_without_run_in_background_not_counted(self):
        events = [_bg_assistant_event(launches=1, run_in_background=False)]
        assert _count_background_task_launches(events) == 0

    def test_non_task_tool_use_not_counted(self):
        events = [_bg_assistant_event(launches=1, name="WebFetch")]
        assert _count_background_task_launches(events) == 0

    def test_truthy_non_true_run_in_background_not_counted(self):
        """Strict ``is True`` check — ``"true"``, ``1``, etc. are rejected."""
        events: list[dict] = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "x",
                            "name": "Task",
                            "input": {"run_in_background": "true"},
                        },
                        {
                            "type": "tool_use",
                            "id": "y",
                            "name": "Task",
                            "input": {"run_in_background": 1},
                        },
                    ],
                },
            }
        ]
        assert _count_background_task_launches(events) == 0

    def test_tasks_across_multiple_assistant_messages(self):
        events = [
            _bg_assistant_event(launches=2),
            _bg_assistant_event(launches=1),
        ]
        assert _count_background_task_launches(events) == 3

    def test_malformed_events_degrade_to_zero(self):
        events: list = [
            "not-a-dict",
            {"type": "assistant"},  # missing message
            {"type": "assistant", "message": "not-a-dict"},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "not-a-list"},
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        "not-a-dict-block",
                        {"type": "tool_use", "name": "Task"},  # no input
                        {
                            "type": "tool_use",
                            "name": "Task",
                            "input": "not-a-dict",
                        },
                    ],
                },
            },
        ]
        assert _count_background_task_launches(events) == 0


class TestDetectBackgroundTaskNoncompletion:
    """Pure-unit tests for ``_detect_background_task_noncompletion``."""

    def test_empty_stream_returns_false(self):
        assert (
            _detect_background_task_noncompletion([], "Waiting on agent.")
            is False
        )

    def test_no_background_tasks_returns_false(self):
        """Final text mentions waiting but no bg task launched → False."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Waiting on X."}],
                },
            },
            {"type": "result", "num_turns": 1},
        ]
        assert (
            _detect_background_task_noncompletion(events, "Waiting on X.")
            is False
        )

    def test_bg_task_with_waiting_regex_triggers(self):
        events = [
            _bg_assistant_event(text="Waiting on editorial agent.", launches=1),
            {"type": "result", "num_turns": 5},
        ]
        assert (
            _detect_background_task_noncompletion(
                events, "Waiting on editorial agent."
            )
            is True
        )

    def test_bg_task_with_few_turns_triggers(self):
        """3 launches + num_turns=3 < 3+2=5 → triggers via turn-count signal."""
        events = [
            _bg_assistant_event(text="All done.", launches=3),
            {"type": "result", "num_turns": 3},
        ]
        assert (
            _detect_background_task_noncompletion(events, "All done.") is True
        )

    def test_bg_task_with_enough_turns_and_clean_text_returns_false(self):
        """Skill that polls properly (turns >= launches+2) and synthesizes
        clean output must not trigger — legitimate coordination pattern.
        """
        events = [
            _bg_assistant_event(text="All done.", launches=3),
            {"type": "result", "num_turns": 10},  # 10 >= 3 + 2
        ]
        assert (
            _detect_background_task_noncompletion(events, "All done.") is False
        )

    def test_waiting_regex_variants(self):
        cases = [
            "waiting on X",
            "Waiting on X",
            "still waiting for the agent",
            "continuing to gather results",
            "work is in progress",
            "running in the background",
        ]
        for final_text in cases:
            events = [
                _bg_assistant_event(text=final_text, launches=1),
                {"type": "result", "num_turns": 10},
            ]
            assert (
                _detect_background_task_noncompletion(events, final_text)
                is True
            ), f"should trigger on {final_text!r}"

    def test_waiting_regex_word_boundary(self):
        """'in progress bar' must NOT match 'in progress'."""
        events = [
            _bg_assistant_event(text="rendering progress bar", launches=1),
            {"type": "result", "num_turns": 10},
        ]
        assert (
            _detect_background_task_noncompletion(
                events, "rendering progress bar"
            )
            is False
        )

    def test_missing_num_turns_falls_back_to_text_signal(self):
        """No num_turns → turn-count signal can't fire, but regex still can."""
        events = [
            _bg_assistant_event(text="Waiting on agent.", launches=1),
            {"type": "result"},  # no num_turns
        ]
        assert (
            _detect_background_task_noncompletion(events, "Waiting on agent.")
            is True
        )

    def test_missing_num_turns_and_clean_text_returns_false(self):
        events = [
            _bg_assistant_event(text="All done.", launches=1),
            {"type": "result"},
        ]
        assert (
            _detect_background_task_noncompletion(events, "All done.") is False
        )

    def test_non_background_task_not_counted(self):
        """Task without run_in_background=True → no launches → False."""
        events = [
            _bg_assistant_event(
                text="Waiting on agent.",
                launches=1,
                run_in_background=False,
            ),
            {"type": "result", "num_turns": 1},
        ]
        assert (
            _detect_background_task_noncompletion(events, "Waiting on agent.")
            is False
        )

    def test_malformed_events_degrade_to_false(self):
        events: list = [
            "not-a-dict",
            {"type": "assistant"},
            {"type": "assistant", "message": "nope"},
        ]
        assert (
            _detect_background_task_noncompletion(events, "Waiting") is False
        )

    def test_non_dict_event_during_num_turns_scan_is_skipped(self):
        """Covers the ``if not isinstance(event, dict): continue`` guard
        in the num_turns scanner when launches > 0 forces the scan to
        run. Non-dict event must be skipped without raising, and the
        detector still decides correctly based on real events.
        """
        events: list = [
            _bg_assistant_event(text="All done.", launches=1),
            "not-a-dict-event-during-result-scan",
            42,  # numeric event
            {"type": "result", "num_turns": 10},  # enough turns → no trigger
        ]
        # launches=1, num_turns=10 >= 1+2, no waiting regex → False,
        # but we had to walk past the non-dict events to get num_turns.
        assert (
            _detect_background_task_noncompletion(events, "All done.") is False
        )


class TestBackgroundTaskNoncompletionIntegration:
    """End-to-end: feed a background-task stream through ``SkillRunner``
    and assert warning + category wiring.
    """

    def _run_with_stream(
        self,
        fake: _FakePopen,
        *,
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            return runner.run("skill", allow_hang_heuristic=allow_hang_heuristic)

    def test_waiting_text_triggers_warning_and_category(self):
        fake = make_fake_background_task_stream(
            text="Waiting on editorial agent.",
            launches=3,
            num_turns=3,
        )
        result = self._run_with_stream(fake)
        assert result.error_category == "background-task"
        assert result.error is None
        assert any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings
        assert result.succeeded_cleanly is False

    def test_legitimate_coordination_does_not_trigger(self):
        """Skill that launched bg tasks AND properly polled (num_turns
        high, clean final text) must still pass succeeded_cleanly.
        """
        fake = make_fake_background_task_stream(
            text="All three restaurants found with full source lists.",
            launches=3,
            num_turns=10,
        )
        result = self._run_with_stream(fake)
        assert result.error_category is None
        assert result.error is None
        assert not any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings
        assert result.succeeded_cleanly is True

    def test_no_background_tasks_does_not_trigger(self):
        """Plain skill run (no bg tasks) must never fire this detector."""
        from tests.conftest import make_fake_skill_stream

        fake = make_fake_skill_stream("Clean output with no tasks.")
        result = self._run_with_stream(fake)
        assert result.error_category is None
        assert not any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings

    def test_allow_hang_heuristic_false_skips_detector(self):
        """``allow_hang_heuristic=False`` disables BOTH heuristics
        (interactive-hang + background-task) — they share the same
        opt-out switch per the per-skill escape-hatch contract.
        """
        fake = make_fake_background_task_stream(
            text="Waiting on editorial agent.",
            launches=3,
            num_turns=3,
        )
        result = self._run_with_stream(fake, allow_hang_heuristic=False)
        assert result.error_category is None
        assert not any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings
        assert result.succeeded_cleanly is True

    def test_sync_tasks_env_var_suppresses_warning(self):
        """Tier 1.5 of GitHub #103: when the caller set
        ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` in the subprocess
        env (typically via ``--sync-tasks``), the background-task
        detector is suppressed — spawning a Task with
        ``run_in_background=True`` under that env forces it sync, so
        warning the user is spurious.
        """
        fake = make_fake_background_task_stream(
            text="Waiting on editorial agent.",
            launches=3,
            num_turns=3,
        )
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        env = {
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "PATH": "/usr/bin",
        }
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            result = runner.run("skill", env=env)
        assert result.error_category is None
        assert not any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings
        assert result.succeeded_cleanly is True

    def test_sync_tasks_env_var_zero_does_not_suppress(self):
        """Only the literal string ``"1"`` suppresses — any other
        value (``"0"``, ``"false"``, missing) leaves the detector on.
        """
        fake = make_fake_background_task_stream(
            text="Waiting on editorial agent.",
            launches=3,
            num_turns=3,
        )
        runner = SkillRunner(project_dir="/tmp", claude_bin="claude")
        env = {
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "0",
            "PATH": "/usr/bin",
        }
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            result = runner.run("skill", env=env)
        assert result.error_category == "background-task"
        assert any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings

    def test_interactive_hang_wins_when_both_would_fire(self):
        """Precedence guard: if a stream matches both interactive-hang
        (trailing ``?``, num_turns=1, end_turn) AND background-task
        (Task tool_use with run_in_background=True), the interactive
        category wins — the detectors are mutually exclusive and
        interactive runs first.
        """
        fake = make_fake_background_task_stream(
            text="What should I do next?",
            launches=1,
            num_turns=1,
        )
        result = self._run_with_stream(fake)
        assert result.error_category == "interactive"
        # Background-task warning must NOT be appended when
        # interactive-hang already fired.
        assert not any(
            w.startswith(_BACKGROUND_TASK_WARNING_PREFIX)
            for w in result.warnings
        ), result.warnings

    def test_stderr_moved_to_warnings_when_bg_task_fires(self):
        """Parallel to the interactive-hang branch: when the bg-task
        heuristic sets ``error_category`` without an error text, any
        captured stderr is preserved in ``warnings`` (not silently
        dropped). Covers the shared
        ``("interactive", "background-task")`` branch in
        ``ClaudeCodeHarness.invoke`` that moves stderr to warnings so the
        caller can still observe subprocess diagnostics.
        """
        fake = make_fake_background_task_stream(
            text="Waiting on editorial agent.",
            launches=3,
            num_turns=3,
        )
        fake.stderr = iter(["retry notice from subprocess\n"])
        result = self._run_with_stream(fake)
        assert result.error_category == "background-task"
        assert result.error is None
        assert any(
            "retry notice from subprocess" in w for w in result.warnings
        ), result.warnings
