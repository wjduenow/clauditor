"""Tests for SkillSpec: from_file, run, evaluate."""

from __future__ import annotations

import json

import pytest

from clauditor.spec import SkillSpec, _failed_run_result

# ── Minimal eval data for fixture ──────────────────────────────────────────

MINIMAL_EVAL = {
    "skill_name": "test-skill",
    "description": "test eval",
    "test_args": "--depth quick",
    "assertions": [{"type": "contains", "value": "hello"}],
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


class TestRun:
    """SkillSpec.run method."""

    def test_run_with_explicit_args(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("run-skill")
        runner = mock_runner(output="explicit output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run(args="--custom flag")
        runner.run.assert_called_once_with("run-skill", "--custom flag")
        assert result.output == "explicit output"

    def test_run_uses_eval_test_args_when_no_args(self, tmp_skill_file, mock_runner):
        skill_path, _ = tmp_skill_file("run-skill", eval_data=MINIMAL_EVAL)
        runner = mock_runner(output="eval args output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        runner.run.assert_called_once_with("run-skill", "--depth quick")

    def test_run_uses_empty_string_when_no_eval_no_args(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("run-skill")
        runner = mock_runner(output="empty args output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        runner.run.assert_called_once_with("run-skill", "")


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
            "assertions": [{"type": "contains", "value": "hello"}],
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
            "assertions": [{"type": "contains", "value": "mock"}],
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
            "assertions": [{"type": "contains", "value": "anything"}],
        }
        skill_path, _ = tmp_skill_file("fail-skill", eval_data=eval_data)
        runner = mock_runner(output="", exit_code=1, error="boom")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert not result.passed
        assert len(result.results) == 1
        assert "failed to run" in result.results[0].message
        assert "boom" in result.results[0].message


class TestFailedRunResult:
    """_failed_run_result helper."""

    def test_returns_failed_assertion_result(self):
        r = _failed_run_result("my-skill", "timeout")
        assert r.passed is False
        assert "my-skill" in r.message
        assert "timeout" in r.message
        assert r.name == "skill_execution"
