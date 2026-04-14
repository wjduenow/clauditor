"""Tests for the clauditor pytest plugin using pytester."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import clauditor.pytest_plugin as _plugin_mod
from clauditor.pytest_plugin import (
    clauditor_capture,
    clauditor_runner,
    clauditor_spec,
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
        assert group.addoption.call_count == 5
        # Verify option names
        option_names = [call.args[0] for call in group.addoption.call_args_list]
        assert "--clauditor-project-dir" in option_names
        assert "--clauditor-timeout" in option_names
        assert "--clauditor-claude-bin" in option_names
        assert "--clauditor-grade" in option_names
        assert "--clauditor-model" in option_names

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
                with patch(
                    "clauditor.spec.SkillRunner.run",
                    return_value=fake_result,
                ):
                    result = spec.run()
                assert result.exit_code == 0
        """)
        result = pytester.runpytest_inprocess("-v")
        result.assert_outcomes(passed=1)
