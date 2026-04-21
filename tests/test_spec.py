"""Tests for SkillSpec: from_file, run, evaluate."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import clauditor.spec as _spec_mod

importlib.reload(_spec_mod)

from clauditor.spec import SkillSpec, _failed_run_result  # noqa: E402

# ── Minimal eval data for fixture ──────────────────────────────────────────

MINIMAL_EVAL = {
    "skill_name": "test-skill",
    "description": "test eval",
    "test_args": "--depth quick",
    "assertions": [{"id": "a_hello", "type": "contains", "value": "hello"}],
}


class TestFromFile:
    """SkillSpec.from_file factory method."""

    def test_missing_skill_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Skill file not found"):
            SkillSpec.from_file(tmp_path / "nonexistent.md")

    def test_loads_skill_without_eval(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("bare-skill")
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_path == skill_path
        assert spec.skill_name == "bare-skill"
        assert spec.eval_spec is None

    def test_auto_discovers_sibling_eval(self, tmp_skill_file, mock_runner):
        skill_path, eval_path = tmp_skill_file("my-skill", eval_data=MINIMAL_EVAL)
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.eval_spec is not None
        assert spec.eval_spec.skill_name == "test-skill"

    def test_explicit_eval_path(self, tmp_skill_file, tmp_path, mock_runner):
        skill_path = tmp_skill_file("a-skill")
        # Write eval json at a non-sibling location
        custom_eval = tmp_path / "custom.eval.json"
        custom_eval.write_text(json.dumps(MINIMAL_EVAL))
        spec = SkillSpec.from_file(
            skill_path, eval_path=custom_eval, runner=mock_runner()
        )
        assert spec.eval_spec is not None
        assert spec.eval_spec.test_args == "--depth quick"

    # ── Layout-aware identity derivation (DEC-001, DEC-002, DEC-009) ──

    def test_from_file_modern_layout_matching_frontmatter(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout, frontmatter ``name:`` matches parent dir → silent."""
        skill_path = tmp_skill_file(
            "foo",
            content="---\nname: foo\n---\n# Foo\n",
            layout="modern",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_from_file_modern_layout_disagreement_warns(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout, frontmatter ``name:`` disagrees → frontmatter
        wins, stderr warning emitted per DEC-002 / DEC-009."""
        skill_path = tmp_skill_file(
            "foo",
            content="---\nname: bar\n---\n# Bar\n",
            layout="modern",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "bar"
        captured = capsys.readouterr()
        assert (
            "frontmatter name 'bar' overrides filesystem name 'foo'"
            in captured.err
        )

    def test_from_file_modern_layout_missing_name_silent(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout with no frontmatter → falls back to parent dir
        name silently (DEC-001)."""
        skill_path = tmp_skill_file(
            "foo",
            content="# Foo\n\nNo frontmatter here.\n",
            layout="modern",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_from_file_legacy_layout_matching_frontmatter(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Legacy layout, frontmatter ``name:`` matches stem → silent."""
        skill_path = tmp_skill_file(
            "foo",
            content="---\nname: foo\n---\n# Foo\n",
            layout="legacy",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_from_file_legacy_layout_missing_name_silent(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Legacy layout with no frontmatter → falls back to stem
        silently (DEC-001). This mirrors today's default legacy skill
        shape (no frontmatter)."""
        skill_path = tmp_skill_file(
            "my-skill",
            content="# My Skill\n\nNo frontmatter here.\n",
            layout="legacy",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "my-skill"
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_init_with_nonexistent_path_uses_layout_fallback(self):
        """Direct ``SkillSpec(...)`` construction with a non-existent path
        must not call ``read_text`` and must derive ``skill_name`` from
        the layout (modern → parent dir, legacy → stem). Regression guard
        for DEC-006 — the path taken by
        ``tests/test_quality_grader.py`` when building a spec with a
        placeholder ``Path("dummy.md")``.
        """
        # Modern fallback: path is a named dir / SKILL.md.
        spec_modern = SkillSpec(skill_path=Path("/nonexistent/foo/SKILL.md"))
        assert spec_modern.skill_name == "foo"

        # Legacy fallback: path is <stem>.md.
        spec_legacy = SkillSpec(skill_path=Path("/nonexistent/bar.md"))
        assert spec_legacy.skill_name == "bar"


class TestRun:
    """SkillSpec.run method."""

    def test_run_with_explicit_args(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("run-skill")
        runner = mock_runner(output="explicit output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run(args="--custom flag")
        runner.run.assert_called_once_with(
            "run-skill",
            "--custom flag",
            cwd=None,
            allow_hang_heuristic=True,
        )
        assert result.output == "explicit output"

    def test_run_uses_eval_test_args_when_no_args(self, tmp_skill_file, mock_runner):
        skill_path, _ = tmp_skill_file("run-skill", eval_data=MINIMAL_EVAL)
        runner = mock_runner(output="eval args output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        runner.run.assert_called_once_with(
            "run-skill",
            "--depth quick",
            cwd=None,
            allow_hang_heuristic=True,
        )

    def test_run_uses_empty_string_when_no_eval_no_args(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("run-skill")
        runner = mock_runner(output="empty args output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        runner.run.assert_called_once_with(
            "run-skill",
            "",
            cwd=None,
            allow_hang_heuristic=True,
        )


class TestEvaluate:
    """SkillSpec.evaluate method."""

    def test_raises_when_no_eval_spec(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("no-eval")
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        with pytest.raises(ValueError, match="No eval spec found"):
            spec.evaluate()

    def test_happy_path_with_explicit_output(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "eval-skill",
            "assertions": [{"id": "a_hello", "type": "contains", "value": "hello"}],
        }
        skill_path, _ = tmp_skill_file("eval-skill", eval_data=eval_data)
        runner = mock_runner()
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate(output="hello world")
        assert result.passed
        # Runner should NOT have been called since we provided output
        runner.run.assert_not_called()

    def test_evaluate_runs_skill_when_no_output(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "auto-skill",
            "assertions": [{"id": "a_mock", "type": "contains", "value": "mock"}],
        }
        skill_path, _ = tmp_skill_file("auto-skill", eval_data=eval_data)
        runner = mock_runner(output="mock output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert result.passed
        runner.run.assert_called_once()

    def test_evaluate_returns_error_on_failed_run(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "fail-skill",
            "assertions": [{"id": "a_any", "type": "contains", "value": "anything"}],
        }
        skill_path, _ = tmp_skill_file("fail-skill", eval_data=eval_data)
        runner = mock_runner(output="", exit_code=1, error="boom")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert not result.passed
        assert len(result.results) == 1
        assert "failed to run" in result.results[0].message
        assert "boom" in result.results[0].message


class TestFileBasedOutput:
    """SkillSpec.run with file-based output (output_file / output_files)."""

    def test_output_file_reads_file_content(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "file-skill",
            "test_args": "",
            "assertions": [],
            "output_file": "results/output.txt",
        }
        skill_path, _ = tmp_skill_file("file-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout content")
        # runner.project_dir must point to tmp_path so we can create the file
        project_dir = skill_path.parent
        runner.project_dir = project_dir
        # Create the output file
        (project_dir / "results").mkdir()
        (project_dir / "results" / "output.txt").write_text("file content here")

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.output == "file content here"

    def test_output_file_missing_keeps_stdout(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "file-skill",
            "test_args": "",
            "assertions": [],
            "output_file": "nonexistent.txt",
        }
        skill_path, _ = tmp_skill_file("file-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout fallback")
        runner.project_dir = skill_path.parent
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.output == "stdout fallback"

    def test_output_files_glob_populates_outputs(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "glob-skill",
            "test_args": "",
            "assertions": [],
            "output_files": ["out/*.txt"],
        }
        skill_path, _ = tmp_skill_file("glob-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout content")
        project_dir = skill_path.parent
        runner.project_dir = project_dir
        # Create matching files
        (project_dir / "out").mkdir()
        (project_dir / "out" / "a.txt").write_text("alpha")
        (project_dir / "out" / "b.txt").write_text("beta")

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert len(result.outputs) == 2
        assert result.outputs["out/a.txt"] == "alpha"
        assert result.outputs["out/b.txt"] == "beta"
        # result.output should be set to the first file read
        assert result.output == "alpha"

    def test_no_output_file_fields_keeps_stdout(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "plain-skill",
            "test_args": "",
            "assertions": [],
        }
        skill_path, _ = tmp_skill_file("plain-skill", eval_data=eval_data)
        runner = mock_runner(output="just stdout")
        runner.project_dir = skill_path.parent
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.output == "just stdout"
        assert result.outputs == {}


class TestOutputFilesResolutionWithStagedInputs:
    """When input_files is staged, output_files must glob from the staging CWD,
    not the runner's project_dir — otherwise mutated outputs are lost."""

    def test_output_files_resolves_against_staging_cwd(
        self, tmp_path, mock_runner
    ):
        skill_dir = tmp_path / ".claude" / "commands"
        skill_dir.mkdir(parents=True)
        (skill_dir / "csv-cleaner.md").write_text("# CSV cleaner\n")
        (skill_dir / "sales.csv").write_text("a,b\n1,2\n")
        (skill_dir / "csv-cleaner.eval.json").write_text(
            json.dumps(
                {
                    "skill_name": "csv-cleaner",
                    "test_args": "",
                    "assertions": [],
                    "input_files": ["sales.csv"],
                    "output_files": ["cleaned.csv"],
                }
            )
        )

        runner = mock_runner(output="stdout transcript")
        runner.project_dir = tmp_path  # repo root, NOT the staging dir

        run_dir = tmp_path / "iter-tmp" / "csv-cleaner" / "run-0"
        run_dir.mkdir(parents=True)
        cleaned_text = "header\nclean,row\n"

        # Side-effect: the "skill" writes cleaned.csv into its staging CWD.
        base_result = runner.run.return_value

        def side_effect(skill_name, args, *, cwd=None, allow_hang_heuristic=True):
            assert cwd == run_dir / "inputs"
            (cwd / "cleaned.csv").write_text(cleaned_text)
            return base_result

        runner.run.side_effect = side_effect

        spec = SkillSpec.from_file(skill_dir / "csv-cleaner.md", runner=runner)
        result = spec.run(run_dir=run_dir)

        assert "cleaned.csv" in result.outputs
        assert result.outputs["cleaned.csv"] == cleaned_text
        assert result.output == cleaned_text

    def test_output_files_without_input_files_still_uses_project_dir(
        self, tmp_skill_file, mock_runner
    ):
        # Regression guard: pre-existing output_files behavior is unchanged
        # when no input_files are declared.
        eval_data = {
            "skill_name": "glob-skill",
            "test_args": "",
            "assertions": [],
            "output_files": ["out/*.txt"],
        }
        skill_path, _ = tmp_skill_file("glob-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout content")
        project_dir = skill_path.parent
        runner.project_dir = project_dir
        (project_dir / "out").mkdir()
        (project_dir / "out" / "a.txt").write_text("alpha")

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.outputs["out/a.txt"] == "alpha"


class TestSkillSpecRunWithInputFiles:
    """US-003: run_dir staging hook for EvalSpec.input_files."""

    def test_spec_run_without_run_dir_uses_project_dir(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("no-rd-skill")
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        # cwd kwarg defaults to None (runner falls back to project_dir)
        assert runner.run.call_args.kwargs.get("cwd") is None

    def test_spec_run_with_empty_input_files_does_not_stage(
        self, tmp_skill_file, mock_runner, tmp_path
    ):
        eval_data = {
            "skill_name": "empty-inputs",
            "test_args": "",
            "assertions": [],
            "input_files": [],
        }
        skill_path, _ = tmp_skill_file("empty-inputs", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        with patch("clauditor.spec.stage_inputs") as mock_stage:
            spec.run(run_dir=tmp_path / "run-0")
        mock_stage.assert_not_called()
        assert runner.run.call_args.kwargs.get("cwd") is None

    def test_spec_run_with_input_files_stages_and_sets_cwd(
        self, tmp_skill_file, mock_runner, tmp_path
    ):
        # Create sibling input files next to the skill
        (tmp_path / "data1.txt").write_text("one")
        (tmp_path / "data2.txt").write_text("two")
        eval_data = {
            "skill_name": "staging-skill",
            "test_args": "",
            "assertions": [],
            "input_files": ["data1.txt", "data2.txt"],
        }
        skill_path, _ = tmp_skill_file("staging-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)

        run_dir = tmp_path / "run-0"
        run_dir.mkdir()
        spec.run(run_dir=run_dir)

        assert (run_dir / "inputs" / "data1.txt").read_text() == "one"
        assert (run_dir / "inputs" / "data2.txt").read_text() == "two"
        assert runner.run.call_args.kwargs.get("cwd") == run_dir / "inputs"

    def test_spec_run_with_input_files_emits_staged_log_line(
        self, tmp_skill_file, mock_runner, tmp_path, capsys
    ):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        eval_data = {
            "skill_name": "log-skill",
            "test_args": "",
            "assertions": [],
            "input_files": ["a.txt", "b.txt"],
        }
        skill_path, _ = tmp_skill_file("log-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)

        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        spec.run(run_dir=run_dir)

        captured = capsys.readouterr()
        assert "Staged 2 input file(s)" in captured.out


class TestFailedRunResult:
    """_failed_run_result helper."""

    def test_returns_failed_assertion_result(self):
        r = _failed_run_result("my-skill", "timeout")
        assert r.passed is False
        assert "my-skill" in r.message
        assert "timeout" in r.message
        assert r.name == "skill_execution"


class TestAllowHangHeuristicThreading:
    """DEC-005 / US-003: the ``allow_hang_heuristic`` flag threads from the
    EvalSpec through ``SkillSpec.run`` into ``SkillRunner.run(...)``.
    """

    def test_eval_spec_false_threads_to_runner(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {
            "skill_name": "off-skill",
            "test_args": "",
            "assertions": [],
            "allow_hang_heuristic": False,
        }
        skill_path, _ = tmp_skill_file("off-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert (
            runner.run.call_args.kwargs.get("allow_hang_heuristic") is False
        )

    def test_eval_spec_default_threads_true(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {
            "skill_name": "on-skill",
            "test_args": "",
            "assertions": [],
        }
        skill_path, _ = tmp_skill_file("on-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert (
            runner.run.call_args.kwargs.get("allow_hang_heuristic") is True
        )

    def test_no_eval_spec_threads_true(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("bare-skill")
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert (
            runner.run.call_args.kwargs.get("allow_hang_heuristic") is True
        )
