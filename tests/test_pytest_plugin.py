"""Tests for the clauditor pytest plugin using pytester."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import clauditor.pytest_plugin as _plugin_mod
from clauditor.pytest_plugin import (
    clauditor_asserter,
    clauditor_blind_compare,
    clauditor_capture,
    clauditor_grader,
    clauditor_runner,
    clauditor_spec,
    clauditor_triggers,
    pytest_addoption,
    pytest_collection_modifyitems,
    pytest_configure,
)

# Re-import to ensure coverage sees the module code
importlib.reload(_plugin_mod)

pytest_plugins = ["pytester"]


class TestPytestPlugin:
    def test_marker_registered(self, pytester):
        """Check that clauditor_grade marker appears in help."""
        result = pytester.runpytest_inprocess("--markers")
        result.stdout.fnmatch_lines(["*clauditor_grade*"])

    def test_grade_marker_skipped_by_default(self, pytester):
        """Grade-marked tests are skipped without --clauditor-grade."""
        pytester.makepyfile("""
            import pytest
            @pytest.mark.clauditor_grade
            def test_graded():
                pass
            def test_normal():
                pass
        """)
        result = pytester.runpytest_inprocess("-v")
        result.assert_outcomes(passed=1, skipped=1)

    def test_grade_marker_runs_with_flag(self, pytester):
        """Grade-marked tests run when --clauditor-grade is passed."""
        pytester.makepyfile("""
            import pytest
            @pytest.mark.clauditor_grade
            def test_graded():
                pass
        """)
        result = pytester.runpytest_inprocess("--clauditor-grade", "-v")
        result.assert_outcomes(passed=1)

    def test_runner_fixture_available(self, pytester):
        """The clauditor_runner fixture is available and non-None."""
        pytester.makepyfile("""
            def test_runner(clauditor_runner):
                assert clauditor_runner is not None
        """)
        result = pytester.runpytest_inprocess()
        result.assert_outcomes(passed=1)

    def test_cli_options_passed(self, pytester):
        """CLI options are forwarded to fixture-created objects."""
        pytester.makepyfile("""
            def test_timeout(clauditor_runner):
                assert clauditor_runner.timeout == 42
        """)
        result = pytester.runpytest_inprocess("--clauditor-timeout=42")
        result.assert_outcomes(passed=1)

    def test_spec_fixture_available(self, pytester):
        """The clauditor_spec fixture is available and callable."""
        pytester.makepyfile("""
            def test_spec(clauditor_spec):
                assert callable(clauditor_spec)
        """)
        result = pytester.runpytest_inprocess()
        result.assert_outcomes(passed=1)


class TestPluginFunctionsDirect:
    """Direct unit tests for plugin functions to ensure coverage."""

    def test_pytest_addoption_registers_options(self):
        """pytest_addoption adds the expected CLI options."""
        parser = MagicMock()
        group = MagicMock()
        parser.getgroup.return_value = group
        pytest_addoption(parser)
        parser.getgroup.assert_called_once_with(
            "clauditor", "Claude Code skill testing"
        )
        assert group.addoption.call_count == 6
        # Verify option names
        option_names = [call.args[0] for call in group.addoption.call_args_list]
        assert "--clauditor-project-dir" in option_names
        assert "--clauditor-timeout" in option_names
        assert "--clauditor-claude-bin" in option_names
        assert "--clauditor-grade" in option_names
        assert "--clauditor-model" in option_names
        assert "--clauditor-no-api-key" in option_names

    def test_pytest_configure_adds_marker(self):
        """pytest_configure registers the clauditor_grade, network, and slow markers."""
        config = MagicMock()
        pytest_configure(config)
        assert config.addinivalue_line.call_count == 3
        registered = [
            call.args[1] for call in config.addinivalue_line.call_args_list
        ]
        assert all(
            call.args[0] == "markers"
            for call in config.addinivalue_line.call_args_list
        )
        assert any("clauditor_grade" in line for line in registered)
        # Pin the exact marker description strings so a typo in the
        # registration (empty description, wrong suffix, etc.) fails loud.
        assert (
            "network: real HTTP; deselect with -m 'not network'" in registered
        )
        assert (
            "slow: slow-running tests; deselect with -m 'not slow'" in registered
        )

    def test_collection_modifyitems_skips_grade_tests(self):
        """Grade-marked items are skipped when flag is not set."""
        config = MagicMock()
        config.getoption.return_value = False
        item = MagicMock()
        item.keywords = {"clauditor_grade": True}
        pytest_collection_modifyitems(config, [item])
        item.add_marker.assert_called_once()

    def test_collection_modifyitems_no_skip_with_flag(self):
        """Grade-marked items are NOT skipped when flag is set."""
        config = MagicMock()
        config.getoption.return_value = True
        item = MagicMock()
        item.keywords = {"clauditor_grade": True}
        pytest_collection_modifyitems(config, [item])
        item.add_marker.assert_not_called()

    def test_collection_modifyitems_ignores_normal_tests(self):
        """Non-grade items are never skipped."""
        config = MagicMock()
        config.getoption.return_value = False
        item = MagicMock()
        item.keywords = {}
        pytest_collection_modifyitems(config, [item])
        item.add_marker.assert_not_called()

    def test_runner_fixture_returns_skill_runner(self):
        """clauditor_runner fixture returns a configured SkillRunner."""
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 60,
                "--clauditor-claude-bin": "claude",
            }[opt]
        )
        runner = clauditor_runner.__wrapped__(request)
        assert runner.timeout == 60
        # ``claude_bin`` moved from ``SkillRunner`` to the harness in
        # US-004 of issue #148; the default ``ClaudeCodeHarness``
        # exposes it as ``harness.claude_bin``.
        assert runner.harness.claude_bin == "claude"

    def test_spec_fixture_returns_callable(self, tmp_path):
        """clauditor_spec fixture returns a callable factory."""
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-no-api-key": False,
            }[opt]
        )
        factory = clauditor_spec.__wrapped__(request, tmp_path)
        assert callable(factory)

    def test_capture_fixture_returns_path(self, tmp_path):
        """clauditor_capture factory returns a pathlib.Path."""
        from pathlib import Path

        request = MagicMock()
        request.config.rootdir = str(tmp_path)
        factory = clauditor_capture.__wrapped__(request)
        result = factory("find-restaurants")
        assert isinstance(result, Path)

    def test_capture_default_path(self, tmp_path):
        """Default base_dir is tests/eval/captured relative to rootdir."""
        request = MagicMock()
        request.config.rootdir = str(tmp_path)
        factory = clauditor_capture.__wrapped__(request)
        result = factory("find-restaurants")
        expected = (
            tmp_path / "tests" / "eval" / "captured" / "find-restaurants.txt"
        )
        assert result == expected
        assert result.name == "find-restaurants.txt"

    def test_capture_custom_base_dir(self, tmp_path):
        """Custom base_dir is respected."""
        request = MagicMock()
        request.config.rootdir = str(tmp_path)
        factory = clauditor_capture.__wrapped__(request)
        custom = tmp_path / "custom"
        result = factory("my-skill", base_dir=custom)
        assert result == custom / "my-skill.txt"

    def test_capture_missing_file_lazy(self, tmp_path):
        """Missing file does not raise at fixture call; only on read_text()."""
        import pytest as _pytest

        request = MagicMock()
        request.config.rootdir = str(tmp_path)
        factory = clauditor_capture.__wrapped__(request)
        # Does not raise:
        path = factory("nonexistent")
        # But reading it does:
        with _pytest.raises(FileNotFoundError):
            path.read_text()

    def test_spec_factory_calls_from_file(self, tmp_path):
        """clauditor_spec factory delegates to SkillSpec.from_file."""
        from unittest.mock import patch

        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-no-api-key": False,
            }[opt]
        )
        factory = clauditor_spec.__wrapped__(request, tmp_path)
        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file"
        ) as mock_from_file:
            mock_spec = MagicMock()
            mock_spec.eval_spec = None
            mock_from_file.return_value = mock_spec
            result = factory("some/skill.md")
            mock_from_file.assert_called_once()
            assert result is mock_spec


class TestClauditorSpecInputFiles:
    """US-005: clauditor_spec fixture auto-stages input_files."""

    def test_clauditor_spec_fixture_without_input_files_unchanged(self, tmp_path):
        """When input_files is empty, spec.run is NOT wrapped."""
        from unittest.mock import patch

        from clauditor.spec import SkillSpec

        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-no-api-key": False,
            }[opt]
        )
        factory = clauditor_spec.__wrapped__(request, tmp_path)

        mock_spec = MagicMock(spec=SkillSpec)
        mock_eval_spec = MagicMock()
        mock_eval_spec.input_files = []
        mock_spec.eval_spec = mock_eval_spec
        original_run = mock_spec.run

        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file",
            return_value=mock_spec,
        ):
            result = factory("some/skill.md")

        # spec.run was NOT replaced
        assert result.run is original_run

    def test_clauditor_spec_fixture_wraps_run_when_input_files_present(
        self, tmp_path
    ):
        """When input_files is non-empty, spec.run is wrapped with default run_dir."""
        from unittest.mock import patch

        from clauditor.spec import SkillSpec

        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-no-api-key": False,
            }[opt]
        )
        factory = clauditor_spec.__wrapped__(request, tmp_path)

        mock_spec = MagicMock(spec=SkillSpec)
        mock_eval_spec = MagicMock()
        mock_eval_spec.input_files = ["/abs/path/sales.csv"]
        mock_spec.eval_spec = mock_eval_spec
        original_run = mock_spec.run
        original_run.return_value = "RESULT"

        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file",
            return_value=mock_spec,
        ):
            result = factory("some/skill.md")

        # spec.run WAS replaced
        assert result.run is not original_run

        # Calling with no run_dir injects the default + creates the dir
        ret = result.run()
        assert ret == "RESULT"
        original_run.assert_called_once()
        call_kwargs = original_run.call_args.kwargs
        expected_dir = tmp_path / f"clauditor_run_{id(result)}"
        assert call_kwargs["run_dir"] == expected_dir
        assert expected_dir.exists()

        # Explicit run_dir passed by caller overrides the default
        original_run.reset_mock()
        custom = tmp_path / "custom_dir"
        result.run("arg", run_dir=custom)
        original_run.assert_called_once_with("arg", run_dir=custom)

    def test_clauditor_spec_fixture_stages_input_files_transparently(
        self, pytester
    ):
        """End-to-end: pytester test uses clauditor_spec and input_files get staged."""
        skill_dir = pytester.path / "skill_pkg"
        (skill_dir / ".claude" / "commands").mkdir(parents=True)
        skill_md = skill_dir / ".claude" / "commands" / "my-skill.md"
        skill_md.write_text("# My Skill\n\nDoes things.\n")

        (skill_dir / ".claude" / "commands" / "sales.csv").write_text(
            "a,b\n1,2\n"
        )

        eval_json = skill_dir / ".claude" / "commands" / "my-skill.eval.json"
        eval_json.write_text(
            '{"test_args": "--depth quick",'
            ' "input_files": ["sales.csv"],'
            ' "assertions": []}'
        )

        pytester.makepyfile(f"""
            from unittest.mock import patch

            from clauditor.runner import SkillResult

            SKILL = r"{skill_md}"

            def test_staging(clauditor_spec):
                spec = clauditor_spec(SKILL)
                assert spec.eval_spec is not None
                assert spec.eval_spec.input_files
                from clauditor.spec import SkillSpec
                assert not (
                    getattr(spec.run, '__func__', None) is SkillSpec.run
                )

                fake_result = SkillResult(
                    output="Staged 1 input file(s) into /tmp/x\\nOK",
                    exit_code=0,
                    skill_name="my-skill",
                    args="--depth quick",
                )
                with patch.object(
                    spec.runner, "run", return_value=fake_result,
                ):
                    result = spec.run()
                assert result.exit_code == 0
        """)
        result = pytester.runpytest_inprocess("-v")
        result.assert_outcomes(passed=1)


def _make_blind_report(**overrides):
    from clauditor.quality_grader import BlindReport

    defaults = dict(
        preference="a",
        confidence=0.9,
        score_a=0.85,
        score_b=0.65,
        reasoning="a is more complete",
        model="claude-sonnet-4-6",
        position_agreement=True,
        input_tokens=100,
        output_tokens=50,
        duration_seconds=1.25,
    )
    defaults.update(overrides)
    return BlindReport(**defaults)


def _blind_compare_factory(tmp_path: Path, *, cli_model: str | None = None):
    """Build the clauditor_blind_compare factory directly (no pytest injection).

    ``cli_model`` simulates the value of the ``--clauditor-model`` plugin
    option as if a user had passed it on the pytest CLI. Defaults to None
    so existing tests are unaffected.
    """
    request = MagicMock()
    request.config.getoption.side_effect = (
        lambda opt: {
            "--clauditor-project-dir": None,
            "--clauditor-timeout": 300,
            "--clauditor-claude-bin": "claude",
            "--clauditor-model": cli_model,
            "--clauditor-no-api-key": False,
        }[opt]
    )
    spec_factory = clauditor_spec.__wrapped__(request, tmp_path)
    return clauditor_blind_compare.__wrapped__(request, spec_factory)


def _write_skill_with_eval(
    tmp_path: Path,
    name: str = "sushi",
    user_prompt: str | None = "What's the best sushi?",
    criteria: list[str] | None = None,
    eval_filename: str | None = None,
    grading_model: str = "claude-sonnet-4-6",
) -> tuple[Path, Path]:
    skill_path = tmp_path / f"{name}.md"
    skill_path.write_text(f"# {name}\n\nA skill.")
    eval_data: dict = {
        "skill_name": name,
        "description": f"Eval for {name}",
        "assertions": [],
        "grading_criteria": [
            {"id": f"c{i}", "criterion": c}
            for i, c in enumerate(
                criteria or [
                    "Is the recommendation specific?",
                    "Does it cite prices?",
                ]
            )
        ],
        "grading_model": grading_model,
    }
    if user_prompt is not None:
        eval_data["user_prompt"] = user_prompt
    eval_path = tmp_path / (eval_filename or f"{name}.eval.json")
    eval_path.write_text(json.dumps(eval_data))
    return skill_path, eval_path


class TestClauditorBlindCompare:
    """US-003: clauditor_blind_compare pytest fixture."""

    def test_clauditor_blind_compare_happy_path(self, tmp_path):
        """Factory loads spec, invokes blind_compare, returns the report."""
        skill_path, _ = _write_skill_with_eval(tmp_path)
        factory = _blind_compare_factory(tmp_path)
        canned = _make_blind_report()

        with patch(
            "clauditor.quality_grader.blind_compare",
            new=AsyncMock(return_value=canned),
        ) as mock_bc:
            result = factory(skill_path, "output A", "output B")

        assert result is canned
        mock_bc.assert_awaited_once()
        call = mock_bc.await_args
        # Positional args: user_prompt, output_a, output_b, rubric_hint
        assert call.args[0] == "What's the best sushi?"
        assert call.args[1] == "output A"
        assert call.args[2] == "output B"
        rubric_hint = call.args[3]
        assert "Is the recommendation specific?" in rubric_hint
        assert "Does it cite prices?" in rubric_hint
        assert call.kwargs["model"] == "claude-sonnet-4-6"

    def test_clauditor_blind_compare_eval_path_override(self, tmp_path):
        """Explicit eval_path overrides sibling auto-discovery."""
        # Sibling eval: different user_prompt than the override we'll pass.
        skill_path, _ = _write_skill_with_eval(
            tmp_path,
            name="ramen",
            user_prompt="sibling prompt",
            criteria=["sibling criterion"],
        )
        # Override eval in a different location with distinct content.
        override_dir = tmp_path / "overrides"
        override_dir.mkdir()
        override_eval = override_dir / "ramen.eval.json"
        override_eval.write_text(
            json.dumps({
                "skill_name": "ramen",
                "description": "override",
                "user_prompt": "override prompt",
                "assertions": [],
                "grading_criteria": [
                    {"id": "ov1", "criterion": "override criterion"},
                ],
                "grading_model": "claude-sonnet-4-6",
            })
        )

        factory = _blind_compare_factory(tmp_path)
        canned = _make_blind_report()

        with patch(
            "clauditor.quality_grader.blind_compare",
            new=AsyncMock(return_value=canned),
        ) as mock_bc:
            factory(skill_path, "a", "b", eval_path=override_eval)

        call = mock_bc.await_args
        assert call.args[0] == "override prompt"
        assert "override criterion" in call.args[3]
        assert "sibling criterion" not in call.args[3]

    def test_clauditor_blind_compare_model_override(self, tmp_path):
        """Explicit model kwarg beats the spec's grading_model."""
        skill_path, _ = _write_skill_with_eval(
            tmp_path,
            name="udon",
            grading_model="WRONG-SHOULD-NOT-BE-USED",
        )
        factory = _blind_compare_factory(tmp_path)
        canned = _make_blind_report(model="claude-opus-4-6")

        with patch(
            "clauditor.quality_grader.blind_compare",
            new=AsyncMock(return_value=canned),
        ) as mock_bc:
            factory(skill_path, "a", "b", model="claude-opus-4-6")

        call = mock_bc.await_args
        assert call.kwargs["model"] == "claude-opus-4-6"
        assert call.kwargs["model"] != "WRONG-SHOULD-NOT-BE-USED"

    def test_clauditor_blind_compare_respects_cli_model_option(self, tmp_path):
        """--clauditor-model CLI option beats the spec's grading_model.

        Precedence: explicit kwarg > --clauditor-model > spec.grading_model.
        Matches the behavior of clauditor_grader / clauditor_triggers, which
        also default to the CLI option when no explicit model is supplied.
        """
        skill_path, _ = _write_skill_with_eval(
            tmp_path,
            name="cli_model",
            grading_model="spec-model-should-lose",
        )
        factory = _blind_compare_factory(
            tmp_path, cli_model="claude-opus-4-6"
        )
        canned = _make_blind_report(model="claude-opus-4-6")

        with patch(
            "clauditor.quality_grader.blind_compare",
            new=AsyncMock(return_value=canned),
        ) as mock_bc:
            factory(skill_path, "a", "b")  # no explicit model kwarg

        call = mock_bc.await_args
        assert call.kwargs["model"] == "claude-opus-4-6"
        assert call.kwargs["model"] != "spec-model-should-lose"

    def test_clauditor_blind_compare_kwarg_beats_cli_model_option(self, tmp_path):
        """Explicit model= kwarg still wins over --clauditor-model CLI option."""
        skill_path, _ = _write_skill_with_eval(
            tmp_path,
            name="kwarg_wins",
            grading_model="spec-model-should-lose",
        )
        factory = _blind_compare_factory(
            tmp_path, cli_model="cli-model-should-also-lose"
        )
        canned = _make_blind_report(model="claude-opus-4-6")

        with patch(
            "clauditor.quality_grader.blind_compare",
            new=AsyncMock(return_value=canned),
        ) as mock_bc:
            factory(skill_path, "a", "b", model="claude-opus-4-6")

        call = mock_bc.await_args
        assert call.kwargs["model"] == "claude-opus-4-6"

    def test_clauditor_blind_compare_raises_on_missing_user_prompt(self, tmp_path):
        """Missing user_prompt in the spec propagates as ValueError."""
        skill_path, _ = _write_skill_with_eval(
            tmp_path, name="empty", user_prompt=None
        )
        factory = _blind_compare_factory(tmp_path)

        with patch(
            "clauditor.quality_grader.blind_compare",
            new=AsyncMock(return_value=_make_blind_report()),
        ):
            with pytest.raises(ValueError, match="user_prompt"):
                factory(skill_path, "a", "b")

    def test_clauditor_blind_compare_reserved_fixture_name(self):
        """Regression guard: fixture name is documented as plugin-reserved."""
        conftest_text = (
            Path(__file__).parent / "conftest.py"
        ).read_text()
        assert "clauditor_blind_compare" in conftest_text
        # And the fixture itself is a pytest fixture callable
        assert callable(clauditor_blind_compare)
        assert hasattr(clauditor_blind_compare, "__wrapped__")


class TestClauditorAsserterFactory:
    """Direct coverage of the clauditor_asserter fixture factory body."""

    def test_factory_returns_skill_asserter_wrapping_result(self):
        """The factory callable wraps a SkillResult in a SkillAsserter."""
        from clauditor.asserters import SkillAsserter
        from clauditor.runner import SkillResult

        factory = clauditor_asserter.__wrapped__()
        result = SkillResult(
            output="hello world",
            exit_code=0,
            skill_name="s",
            args="",
        )
        asserter = factory(result)
        assert isinstance(asserter, SkillAsserter)
        # assert_contains delegates to the real assertion helper.
        asserter.assert_contains("hello")


class TestClauditorGraderFactory:
    """Direct coverage of clauditor_grader error + output=None branches."""

    def _request_with_model(self, model="claude-sonnet-4-6"):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {"--clauditor-model": model}.get(opt)
        )
        return request

    def test_raises_value_error_when_spec_lacks_eval(self, tmp_path):
        """Grader factory raises ValueError if spec.eval_spec is None."""
        request = self._request_with_model()

        def fake_clauditor_spec(skill_path, eval_path=None):
            mock_spec = MagicMock()
            mock_spec.eval_spec = None
            return mock_spec

        factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(ValueError, match="No eval spec found"):
            factory(tmp_path / "skill.md")

    def test_output_none_triggers_spec_run(self, tmp_path, monkeypatch):
        """output=None path calls spec.run() and feeds its output into
        grade_quality — covers the branch that tests typically bypass
        by passing output= directly.
        """
        # US-001 of #162: provider resolves from
        # ``eval_spec.grading_provider``; an unconstrained MagicMock
        # would yield a non-None ``grading_provider`` MagicMock and
        # trip the dispatcher's unknown-provider ``ValueError``. Pin
        # the attribute to ``None`` so resolution falls through to
        # ``"anthropic"``.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        request = self._request_with_model()
        mock_eval_spec = MagicMock()
        mock_eval_spec.grading_provider = None

        mock_run_result = MagicMock()
        mock_run_result.output = "captured skill output"
        mock_spec = MagicMock()
        mock_spec.eval_spec = mock_eval_spec
        mock_spec.run.return_value = mock_run_result

        def fake_clauditor_spec(skill_path, eval_path=None):
            return mock_spec

        # Patch BEFORE __wrapped__ is called: the outer fixture body
        # does ``from clauditor.quality_grader import grade_quality`` and
        # captures it into the factory closure, so the patch target must
        # be live at __wrapped__ time (not just at factory-call time).
        canned = MagicMock()
        with patch(
            "clauditor.quality_grader.grade_quality",
            new=AsyncMock(return_value=canned),
        ) as mock_grade:
            factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
            result = factory(tmp_path / "skill.md")

        assert result is canned
        mock_spec.run.assert_called_once()
        # grade_quality was called with the spec.run() output.
        call = mock_grade.await_args
        assert call.args[0] == "captured skill output"


class TestClauditorTriggersFactory:
    """Direct coverage of clauditor_triggers error branch."""

    def test_raises_value_error_when_spec_lacks_eval(self, tmp_path):
        """Triggers factory raises ValueError if spec.eval_spec is None."""
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {"--clauditor-model": "claude-sonnet-4-6"}.get(opt)
        )

        def fake_clauditor_spec(skill_path, eval_path=None):
            mock_spec = MagicMock()
            mock_spec.eval_spec = None
            return mock_spec

        factory = clauditor_triggers.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(ValueError, match="No eval spec found"):
            factory(tmp_path / "skill.md")


class TestClauditorBlindCompareFactory:
    """Direct coverage of clauditor_blind_compare error branch."""

    def test_raises_value_error_when_spec_lacks_eval(self, tmp_path):
        """Blind-compare factory raises ValueError if spec.eval_spec
        is None — validates the spec shape BEFORE the auth dispatch
        per CodeRabbit fix on PR #163, so a missing eval.json
        surfaces as the actual problem rather than being masked by
        an auth-missing error.
        """
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {"--clauditor-model": "claude-sonnet-4-6"}.get(opt)
        )

        def fake_clauditor_spec(skill_path, eval_path=None):
            mock_spec = MagicMock()
            mock_spec.eval_spec = None
            return mock_spec

        factory = clauditor_blind_compare.__wrapped__(
            request, fake_clauditor_spec
        )
        with pytest.raises(ValueError, match="No eval spec found"):
            factory(tmp_path / "skill.md", "output A", "output B")


class TestClauditorNoApiKeyOption:
    """US-007: ``--clauditor-no-api-key`` pytest option parity.

    DEC-006: when set, the ``clauditor_spec`` fixture threads
    ``env_override=env_without_api_key()`` through ``SkillSpec.run``;
    otherwise ``env_override`` stays unset and the existing call shape
    is preserved for back-compat with the ``--clauditor-timeout``
    wiring tests.
    """

    def _request(self, *, no_api_key: bool):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-no-api-key": no_api_key,
            }[opt]
        )
        return request

    def test_no_api_key_option_reaches_spec_run(self, tmp_path):
        """Setting ``--clauditor-no-api-key`` threads ``env_override`` to spec.run.

        The fixture must call ``original_run(..., env_override=<dict>)``
        where the dict has both auth env vars stripped.
        """
        from clauditor.spec import SkillSpec

        request = self._request(no_api_key=True)
        factory = clauditor_spec.__wrapped__(request, tmp_path)

        mock_spec = MagicMock(spec=SkillSpec)
        mock_eval_spec = MagicMock()
        mock_eval_spec.input_files = []
        mock_spec.eval_spec = mock_eval_spec
        original_run = mock_spec.run
        original_run.return_value = "RESULT"

        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file",
            return_value=mock_spec,
        ):
            result = factory("some/skill.md")

        # Wrapping happened because the option is set (even with no input_files).
        assert result.run is not original_run

        ret = result.run()
        assert ret == "RESULT"
        original_run.assert_called_once()
        call_kwargs = original_run.call_args.kwargs
        assert "env_override" in call_kwargs
        env = call_kwargs["env_override"]
        assert isinstance(env, dict)
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_timeout_option_still_works(self, tmp_path):
        """Regression guard: ``--clauditor-timeout`` still reaches SkillRunner.

        Adding ``--clauditor-no-api-key`` must not disturb the existing
        timeout wiring from ``--clauditor-timeout`` → ``SkillRunner.timeout``.
        """
        request = self._request(no_api_key=False)
        # Override the timeout to a distinct non-default value so a
        # regression that reverts to the default 300 would be visible.
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 99,
                "--clauditor-claude-bin": "claude",
                "--clauditor-no-api-key": False,
            }[opt]
        )
        runner = clauditor_runner.__wrapped__(request)
        assert runner.timeout == 99
        # And clauditor_spec must not wrap spec.run when the option is
        # off AND input_files is empty (pre-US-007 behavior preserved).
        from clauditor.spec import SkillSpec

        factory = clauditor_spec.__wrapped__(request, tmp_path)
        mock_spec = MagicMock(spec=SkillSpec)
        mock_eval_spec = MagicMock()
        mock_eval_spec.input_files = []
        mock_spec.eval_spec = mock_eval_spec
        original_run = mock_spec.run

        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file",
            return_value=mock_spec,
        ):
            result = factory("some/skill.md")

        assert result.run is original_run

    def test_wrapper_accepts_timeout_override_kwarg(self, tmp_path):
        """#64 QG: the fixture wrapper must accept ``timeout_override``.

        Before the fix, ``_run_with_overrides`` only accepted
        ``args`` + ``run_dir``, so a caller doing
        ``spec.run(timeout_override=60)`` hit ``TypeError``. The
        wrapper must now forward timeout_override to original_run.
        """
        from clauditor.spec import SkillSpec

        request = self._request(no_api_key=True)
        factory = clauditor_spec.__wrapped__(request, tmp_path)

        mock_spec = MagicMock(spec=SkillSpec)
        mock_eval_spec = MagicMock()
        mock_eval_spec.input_files = []
        mock_spec.eval_spec = mock_eval_spec
        original_run = mock_spec.run
        original_run.return_value = "RESULT"

        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file",
            return_value=mock_spec,
        ):
            result = factory("some/skill.md")

        ret = result.run(timeout_override=60)
        assert ret == "RESULT"
        assert original_run.call_args.kwargs["timeout_override"] == 60

    def test_caller_env_override_wins_over_fixture(self, tmp_path):
        """#64 QG: when the caller passes env_override, it should win
        over the fixture-level default (principle of least surprise)."""
        from clauditor.spec import SkillSpec

        request = self._request(no_api_key=True)
        factory = clauditor_spec.__wrapped__(request, tmp_path)

        mock_spec = MagicMock(spec=SkillSpec)
        mock_eval_spec = MagicMock()
        mock_eval_spec.input_files = []
        mock_spec.eval_spec = mock_eval_spec
        original_run = mock_spec.run
        original_run.return_value = "RESULT"

        caller_env = {"CUSTOM": "value"}
        with patch(
            "clauditor.pytest_plugin.SkillSpec.from_file",
            return_value=mock_spec,
        ):
            result = factory("some/skill.md")
        result.run(env_override=caller_env)

        assert original_run.call_args.kwargs["env_override"] is caller_env


class TestClauditorFixturesAuthGuard:
    """US-004: auth guard fires at factory-invocation time for the three
    grading fixtures.

    Per DEC-005 the fixtures raise (not skip) so a CI run under
    subscription-only auth surfaces a config regression instead of
    silently skipping. Per DEC-013 the raised class is
    ``AnthropicAuthMissingError`` — the same class the CLI catches — so
    tests and CLI users see a byte-identical message shape.

    Per DEC-012 the message must contain three durable substrings:
    ``"ANTHROPIC_API_KEY"``, ``"Claude Pro"``, and
    ``"console.anthropic.com"``. Each test also asserts the
    per-fixture command-name substring (e.g. ``"clauditor grader"``) so
    a future rename of the cmd_name argument trips the test.
    """

    def _request(self, model: str = "claude-sonnet-4-6"):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {"--clauditor-model": model}.get(opt)
        )
        return request

    def _stub_spec_factory(self, *, grading_provider=None):
        """Build a fake ``clauditor_spec`` that returns a stub spec.

        Per US-001 of #162 the fixture loads the spec BEFORE running the
        auth guard so it can resolve ``provider =
        eval_spec.grading_provider or "anthropic"``. Existing tests that
        expect Anthropic-path behavior pass ``grading_provider=None``
        (or ``"anthropic"``) so resolution falls through to the
        Anthropic auth branch and the legacy assertions still hold.
        """
        eval_spec = MagicMock()
        eval_spec.grading_provider = grading_provider

        def factory(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            return spec

        return factory

    def test_clauditor_grader_raises_on_missing_key(
        self, tmp_path, monkeypatch
    ):
        """clauditor_grader factory raises AnthropicAuthMissingError when
        ANTHROPIC_API_KEY is unset — before any SDK call happens.
        """
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        request = self._request()
        factory = clauditor_grader.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        assert "ANTHROPIC_API_KEY" in msg
        assert "Claude Pro" in msg
        assert "console.anthropic.com" in msg
        assert "clauditor grader" in msg

    def test_clauditor_triggers_raises_on_missing_key(
        self, tmp_path, monkeypatch
    ):
        """clauditor_triggers factory raises AnthropicAuthMissingError
        when ANTHROPIC_API_KEY is unset — before any SDK call happens.
        """
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        request = self._request()
        factory = clauditor_triggers.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        assert "ANTHROPIC_API_KEY" in msg
        assert "Claude Pro" in msg
        assert "console.anthropic.com" in msg
        assert "clauditor triggers" in msg

    def test_clauditor_blind_compare_raises_on_missing_key(
        self, tmp_path, monkeypatch
    ):
        """clauditor_blind_compare factory raises AnthropicAuthMissingError
        when ANTHROPIC_API_KEY is unset — before any SDK call happens.
        """
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-model": None,
                "--clauditor-no-api-key": False,
            }.get(opt)
        )
        factory = clauditor_blind_compare.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md", "a", "b")
        msg = str(excinfo.value)
        assert "ANTHROPIC_API_KEY" in msg
        assert "Claude Pro" in msg
        assert "console.anthropic.com" in msg
        assert "clauditor blind_compare" in msg


class TestPytestFixturesStrictMode:
    """US-005 / DEC-009 (#86): fixtures stay strict unless opt-in env set.

    The three grading fixtures default to :func:`check_api_key_only`
    (strict: only ``ANTHROPIC_API_KEY`` passes). Users who deliberately
    want the CLI transport to participate in fixture tests set
    ``CLAUDITOR_FIXTURE_ALLOW_CLI=1``, which routes the guard through
    :func:`check_any_auth_available` (relaxed: CLI-on-PATH also passes).

    Each fixture is verified across three auth scenarios:

    - **Default strict mode, CLI on PATH, no key**: raises — CLI
      availability does NOT rescue a missing key without the opt-in.
    - **Opt-in mode, CLI on PATH, no key**: passes the guard — the
      relaxed check treats CLI presence as sufficient.
    - **Opt-in mode, no key, no CLI**: still raises — both paths absent.
    """

    def _request(self, model: str = "claude-sonnet-4-6"):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {"--clauditor-model": model}.get(opt)
        )
        return request

    def _blind_compare_request(self):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-model": None,
                "--clauditor-no-api-key": False,
            }.get(opt)
        )
        return request

    def _patch_which(self, monkeypatch, path):
        import clauditor._anthropic as _anthropic

        monkeypatch.setattr(
            _anthropic.shutil, "which", lambda name: path
        )

    def _stub_spec_factory(self, *, grading_provider=None):
        """Build a fake ``clauditor_spec`` for the strict-mode tests.

        Per US-001 of #162 the fixture loads the spec FIRST and reads
        ``spec.eval_spec.grading_provider`` to pick the auth helper.
        ``grading_provider=None`` exercises the default-to-anthropic
        path; passing ``"openai"`` (or another provider string)
        exercises the dispatcher branch.
        """
        eval_spec = MagicMock()
        eval_spec.grading_provider = grading_provider

        def factory(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            return spec

        return factory

    def test_grader_strict_default_cli_on_path_still_raises(
        self, tmp_path, monkeypatch
    ):
        """DEC-009: strict-by-default — CLI presence alone does NOT pass."""
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # CLAUDITOR_FIXTURE_ALLOW_CLI auto-cleared by conftest.
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._request()
        factory = clauditor_grader.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)

    def test_triggers_strict_default_cli_on_path_still_raises(
        self, tmp_path, monkeypatch
    ):
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._request()
        factory = clauditor_triggers.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)

    def test_blind_compare_strict_default_cli_on_path_still_raises(
        self, tmp_path, monkeypatch
    ):
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._blind_compare_request()
        factory = clauditor_blind_compare.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md", "a", "b")
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)

    def test_grader_allow_cli_opt_in_cli_on_path_passes_guard(
        self, tmp_path, monkeypatch
    ):
        """DEC-009 opt-in: CLAUDITOR_FIXTURE_ALLOW_CLI=1 lets CLI presence pass."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._request()

        # If the guard passes, the factory proceeds past the auth check.
        # Per US-001 of #162 the spec is loaded BEFORE the guard, so we
        # raise the sentinel from spec.run() to prove the guard did NOT
        # raise: the test reaches the post-guard ``spec.run()`` call.
        _sentinel = RuntimeError("guard passed; spec.run reached")
        eval_spec = MagicMock()
        eval_spec.grading_provider = None

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            spec.run.side_effect = _sentinel
            return spec

        factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(RuntimeError) as excinfo:
            factory(tmp_path / "skill.md")
        assert excinfo.value is _sentinel

    def test_triggers_allow_cli_opt_in_cli_on_path_passes_guard(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._request()

        # Guard passes → factory reaches ``run_triggers`` which fires
        # the sentinel via the patched ``test_triggers`` import below.
        _sentinel = RuntimeError("guard passed; run_triggers reached")
        eval_spec = MagicMock()
        eval_spec.grading_provider = None

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            return spec

        with patch(
            "clauditor.triggers.test_triggers",
            side_effect=_sentinel,
        ):
            factory = clauditor_triggers.__wrapped__(
                request, fake_clauditor_spec
            )
            with pytest.raises(RuntimeError) as excinfo:
                factory(tmp_path / "skill.md")
        assert excinfo.value is _sentinel

    def test_blind_compare_allow_cli_opt_in_cli_on_path_passes_guard(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._blind_compare_request()

        _sentinel = RuntimeError("guard passed; blind_compare_from_spec reached")
        eval_spec = MagicMock()
        eval_spec.grading_provider = None

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            return spec

        with patch(
            "clauditor.quality_grader.blind_compare_from_spec",
            side_effect=_sentinel,
        ):
            factory = clauditor_blind_compare.__wrapped__(
                request, fake_clauditor_spec
            )
            with pytest.raises(RuntimeError) as excinfo:
                factory(tmp_path / "skill.md", "a", "b")
        assert excinfo.value is _sentinel

    def test_grader_allow_cli_opt_in_no_cli_still_raises(
        self, tmp_path, monkeypatch
    ):
        """Opt-in + no key + no CLI → relaxed guard still raises (both absent)."""
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        self._patch_which(monkeypatch, None)
        request = self._request()
        factory = clauditor_grader.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        # Relaxed-guard message — DEC-015 four anchors.
        assert "claude CLI" in msg
        assert "ANTHROPIC_API_KEY" in msg

    def test_grader_allow_cli_false_value_stays_strict(
        self, tmp_path, monkeypatch
    ):
        """CLAUDITOR_FIXTURE_ALLOW_CLI=0 does NOT opt in — strict still applies."""
        from clauditor._anthropic import AnthropicAuthMissingError

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "0")
        self._patch_which(monkeypatch, "/usr/local/bin/claude")
        request = self._request()
        factory = clauditor_grader.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(AnthropicAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        # Strict-guard message — preserves #83 DEC-012 three anchors.
        assert "ANTHROPIC_API_KEY" in msg
        assert "Claude Pro" in msg
        assert "console.anthropic.com" in msg


class TestClauditorFixturesAuthGuardOpenAI:
    """US-001 of #162: provider-aware auth in pytest fixtures.

    Spec loads FIRST in each fixture; ``provider =
    eval_spec.grading_provider or "anthropic"`` resolves to OpenAI
    when the spec declares it; the auth dispatcher then routes to
    :func:`check_openai_auth`, raising :class:`OpenAIAuthMissingError`
    (a sibling of :class:`AnthropicAuthMissingError`, NOT a subclass)
    when ``OPENAI_API_KEY`` is missing — even if ``ANTHROPIC_API_KEY``
    is set.

    DEC-004 of #162: ``CLAUDITOR_FIXTURE_ALLOW_CLI=1`` is silently
    no-op for the OpenAI branch (OpenAI has no CLI transport).

    All tests use ``__wrapped__`` direct calls per
    ``.claude/rules/pytester-inprocess-coverage-hazard.md`` (DEC-006
    of #162) — no ``pytester.runpytest_inprocess`` + ``mock.patch``
    combinations under coverage.
    """

    def _request(self, model: str = "gpt-5.4"):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {"--clauditor-model": model}.get(opt)
        )
        return request

    def _blind_compare_request(self):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 300,
                "--clauditor-claude-bin": "claude",
                "--clauditor-model": None,
                "--clauditor-no-api-key": False,
            }.get(opt)
        )
        return request

    def _stub_spec_factory(self, *, grading_provider="openai"):
        """Build a fake ``clauditor_spec`` returning an OpenAI-graded spec."""
        eval_spec = MagicMock()
        eval_spec.grading_provider = grading_provider

        def factory(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            return spec

        return factory

    # ------------------------------------------------------------------
    # T1: provider="openai" + OPENAI_API_KEY unset + ANTHROPIC_API_KEY set
    #     → OpenAIAuthMissingError raised (no Anthropic fallback).
    # ------------------------------------------------------------------

    def test_grader_openai_provider_missing_key_raises_openai_error(
        self, tmp_path, monkeypatch
    ):
        from clauditor._providers import OpenAIAuthMissingError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # ANTHROPIC_API_KEY is set — proves no fallback.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        request = self._request()
        factory = clauditor_grader.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(OpenAIAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        assert "OPENAI_API_KEY" in msg
        assert "platform.openai.com" in msg
        assert "clauditor grader" in msg

    def test_triggers_openai_provider_missing_key_raises_openai_error(
        self, tmp_path, monkeypatch
    ):
        from clauditor._providers import OpenAIAuthMissingError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        request = self._request()
        factory = clauditor_triggers.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(OpenAIAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        assert "OPENAI_API_KEY" in msg
        assert "platform.openai.com" in msg
        assert "clauditor triggers" in msg

    def test_blind_compare_openai_provider_missing_key_raises_openai_error(
        self, tmp_path, monkeypatch
    ):
        from clauditor._providers import OpenAIAuthMissingError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        request = self._blind_compare_request()
        factory = clauditor_blind_compare.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(OpenAIAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md", "a", "b")
        msg = str(excinfo.value)
        assert "OPENAI_API_KEY" in msg
        assert "platform.openai.com" in msg
        assert "clauditor blind_compare" in msg

    # ------------------------------------------------------------------
    # T2: provider="openai" + OPENAI_API_KEY set → guard passes,
    #     factory proceeds past the auth seam.
    # ------------------------------------------------------------------

    def test_grader_openai_provider_with_key_passes_guard(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        request = self._request()

        # Guard passes → factory reaches ``spec.run()``. Sentinel proves
        # auth check did NOT raise.
        _sentinel = RuntimeError("guard passed; spec.run reached")
        eval_spec = MagicMock()
        eval_spec.grading_provider = "openai"

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            spec.run.side_effect = _sentinel
            return spec

        factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(RuntimeError) as excinfo:
            factory(tmp_path / "skill.md")
        assert excinfo.value is _sentinel

    # ------------------------------------------------------------------
    # T3: provider="openai" + CLAUDITOR_FIXTURE_ALLOW_CLI=1 → strict
    #     OpenAI key check still applied (env var is no-op for OpenAI
    #     per DEC-004 of #162).
    # ------------------------------------------------------------------

    def test_grader_openai_provider_allow_cli_env_is_noop(
        self, tmp_path, monkeypatch
    ):
        from clauditor._providers import OpenAIAuthMissingError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        # CLAUDITOR_FIXTURE_ALLOW_CLI=1 is honored only for the
        # Anthropic branch — OpenAI must still raise.
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        # Patch ``which`` so a ``claude`` binary appears available; if
        # the fixture were (incorrectly) routing through the relaxed
        # Anthropic guard this test would silently pass under it.
        import clauditor._anthropic as _anthropic

        monkeypatch.setattr(
            _anthropic.shutil, "which", lambda name: "/usr/local/bin/claude"
        )
        request = self._request()
        factory = clauditor_grader.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(OpenAIAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        # Confirm we surface the OPENAI message, not the Anthropic one.
        msg = str(excinfo.value)
        assert "OPENAI_API_KEY" in msg
        assert "platform.openai.com" in msg

    def test_triggers_openai_provider_allow_cli_env_is_noop(
        self, tmp_path, monkeypatch
    ):
        from clauditor._providers import OpenAIAuthMissingError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        import clauditor._anthropic as _anthropic

        monkeypatch.setattr(
            _anthropic.shutil, "which", lambda name: "/usr/local/bin/claude"
        )
        request = self._request()
        factory = clauditor_triggers.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(OpenAIAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md")
        msg = str(excinfo.value)
        assert "OPENAI_API_KEY" in msg

    def test_blind_compare_openai_provider_allow_cli_env_is_noop(
        self, tmp_path, monkeypatch
    ):
        from clauditor._providers import OpenAIAuthMissingError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        import clauditor._anthropic as _anthropic

        monkeypatch.setattr(
            _anthropic.shutil, "which", lambda name: "/usr/local/bin/claude"
        )
        request = self._blind_compare_request()
        factory = clauditor_blind_compare.__wrapped__(
            request, self._stub_spec_factory()
        )
        with pytest.raises(OpenAIAuthMissingError) as excinfo:
            factory(tmp_path / "skill.md", "a", "b")
        msg = str(excinfo.value)
        assert "OPENAI_API_KEY" in msg

    # ------------------------------------------------------------------
    # T4: provider unset (None in spec) + ANTHROPIC_API_KEY set →
    #     existing Anthropic happy path.
    # ------------------------------------------------------------------

    def test_grader_provider_none_falls_back_to_anthropic(
        self, tmp_path, monkeypatch
    ):
        """Unset ``grading_provider`` defaults to ``"anthropic"``."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        request = self._request()

        _sentinel = RuntimeError("anthropic happy path reached")
        eval_spec = MagicMock()
        eval_spec.grading_provider = None

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            spec.run.side_effect = _sentinel
            return spec

        factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(RuntimeError) as excinfo:
            factory(tmp_path / "skill.md")
        assert excinfo.value is _sentinel

    def test_grader_eval_spec_none_raises_value_error_before_auth(
        self, tmp_path, monkeypatch
    ):
        """Spec with ``eval_spec=None`` raises ``ValueError`` BEFORE
        the auth guard fires (CodeRabbit fix on PR #163). Otherwise a
        missing/invalid auth key would mask the more useful "no eval
        spec found" error for users whose underlying problem is a
        missing eval.json — they'd debug auth instead of fixing the
        spec.
        """
        # Both keys unset — proves the auth guard was NOT reached
        # (otherwise we'd see ``AnthropicAuthMissingError``).
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        request = self._request()

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = None
            return spec

        factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(ValueError) as excinfo:
            factory(tmp_path / "skill.md")
        assert "No eval spec found" in str(excinfo.value)

    # ------------------------------------------------------------------
    # T5: provider="anthropic" explicit + CLAUDITOR_FIXTURE_ALLOW_CLI=1
    #     → existing relaxed-guard behavior preserved (CLI on PATH
    #     passes the guard).
    # ------------------------------------------------------------------

    def test_grader_anthropic_provider_explicit_allow_cli_relaxed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDITOR_FIXTURE_ALLOW_CLI", "1")
        import clauditor._anthropic as _anthropic

        monkeypatch.setattr(
            _anthropic.shutil, "which", lambda name: "/usr/local/bin/claude"
        )
        request = self._request()

        # Guard passes → spec.run sentinel proves the relaxed check
        # accepted CLI presence as auth.
        _sentinel = RuntimeError("relaxed guard accepted CLI")
        eval_spec = MagicMock()
        eval_spec.grading_provider = "anthropic"

        def fake_clauditor_spec(skill_path, eval_path=None):
            spec = MagicMock()
            spec.eval_spec = eval_spec
            spec.run.side_effect = _sentinel
            return spec

        factory = clauditor_grader.__wrapped__(request, fake_clauditor_spec)
        with pytest.raises(RuntimeError) as excinfo:
            factory(tmp_path / "skill.md")
        assert excinfo.value is _sentinel
