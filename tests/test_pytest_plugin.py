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
        assert runner.claude_bin == "claude"

    def test_spec_fixture_returns_callable(self, tmp_path):
        """clauditor_spec fixture returns a callable factory."""
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 180,
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
                "--clauditor-timeout": 180,
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
                "--clauditor-timeout": 180,
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
                "--clauditor-timeout": 180,
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
            "--clauditor-timeout": 180,
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

    def test_output_none_triggers_spec_run(self, tmp_path):
        """output=None path calls spec.run() and feeds its output into
        grade_quality — covers the branch that tests typically bypass
        by passing output= directly.
        """
        request = self._request_with_model()
        mock_eval_spec = MagicMock()

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


class TestClauditorNoApiKeyOption:
    """US-007: ``--clauditor-no-api-key`` pytest option parity.

    DEC-006: when set, the ``clauditor_spec`` fixture threads
    ``env_override=_env_without_api_key()`` through ``SkillSpec.run``;
    otherwise ``env_override`` stays unset and the existing call shape
    is preserved for back-compat with the ``--clauditor-timeout``
    wiring tests.
    """

    def _request(self, *, no_api_key: bool):
        request = MagicMock()
        request.config.getoption.side_effect = (
            lambda opt: {
                "--clauditor-project-dir": None,
                "--clauditor-timeout": 180,
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
        # regression that reverts to the default 180 would be visible.
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
