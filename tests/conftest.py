"""Shared test fixtures for clauditor tests.

Provides reusable fixtures for eval data, specs, temp skill files, and mock runners.
IMPORTANT: Do NOT define fixtures named clauditor_runner, clauditor_spec,
clauditor_grader, or clauditor_triggers -- those are defined by the pytest plugin.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clauditor.runner import SkillResult, SkillRunner
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    SectionRequirement,
)


@pytest.fixture(autouse=True)
def _isolate_clauditor_history(tmp_path, monkeypatch):
    """Redirect history.jsonl writes to a per-test tmp dir so running the
    suite never writes ``.clauditor/history.jsonl`` in the real cwd.

    ``history.append_record`` uses a default-arg ``path=_DEFAULT_PATH`` that
    is frozen at import time, so monkeypatching ``_DEFAULT_PATH`` alone is a
    no-op. Replace the module function with a thin wrapper that injects a
    tmp path whenever callers omit one.
    """
    from clauditor import history as _history

    real_append = _history.append_record
    real_read = _history.read_records
    tmp_default = tmp_path / ".clauditor" / "history.jsonl"

    def _append(skill, pass_rate, mean_score, metrics, path=None):
        return real_append(
            skill,
            pass_rate,
            mean_score,
            metrics,
            path=tmp_default if path is None else path,
        )

    def _read(skill=None, path=None):
        return real_read(skill=skill, path=tmp_default if path is None else path)

    monkeypatch.setattr(_history, "append_record", _append)
    monkeypatch.setattr(_history, "read_records", _read)
    monkeypatch.setattr(_history, "_DEFAULT_PATH", tmp_default)


@pytest.fixture
def sample_eval_data() -> dict:
    """Return a dict matching eval.json format with all fields populated."""
    return {
        "skill_name": "find-kid-activities",
        "description": "Eval for /find-kid-activities",
        "test_args": '"Cupertino, CA" --dates today --cost Free --depth quick',
        "assertions": [
            {"type": "contains", "value": "Venues"},
            {"type": "has_entries", "value": "3"},
            {"type": "has_urls", "value": "2"},
            {"type": "not_contains", "value": "ERROR"},
            {"type": "min_length", "value": "500"},
        ],
        "sections": [
            {
                "name": "Venues",
                "min_entries": 3,
                "fields": [
                    {"name": "name", "required": True},
                    {"name": "address", "required": True},
                    {"name": "hours", "required": True},
                    {"name": "website", "required": True},
                    {"name": "phone", "required": False},
                ],
            },
            {
                "name": "Events",
                "min_entries": 0,
                "fields": [
                    {"name": "name", "required": True},
                    {"name": "date", "required": True},
                    {"name": "event_url", "required": True},
                ],
            },
        ],
        "grading_criteria": [
            "Are venues within the specified distance?",
            "Are hours accurate for the requested dates?",
        ],
        "grading_model": "claude-sonnet-4-6",
        "trigger_tests": {
            "should_trigger": [
                "find kid activities in Cupertino",
                "things to do with kids near me",
            ],
            "should_not_trigger": [
                "what is the weather today",
                "write me a poem",
            ],
        },
        "variance": {
            "n_runs": 5,
            "min_stability": 0.8,
        },
    }


@pytest.fixture
def make_eval_spec():
    """Factory fixture that creates EvalSpec instances from optional overrides.

    Usage:
        def test_something(make_eval_spec):
            spec = make_eval_spec(skill_name="my-skill")
    """

    def _factory(**overrides) -> EvalSpec:
        defaults = {
            "skill_name": "test-skill",
            "description": "A test eval spec",
            "test_args": "--depth quick",
            "assertions": [{"type": "contains", "value": "test"}],
            "sections": [
                SectionRequirement(
                    name="Results",
                    min_entries=1,
                    fields=[
                        FieldRequirement(name="name", required=True),
                        FieldRequirement(name="url", required=False),
                    ],
                )
            ],
            "grading_criteria": ["Is the output relevant?"],
            "grading_model": "claude-sonnet-4-6",
            "trigger_tests": None,
            "variance": None,
        }
        defaults.update(overrides)
        return EvalSpec(**defaults)

    return _factory


@pytest.fixture
def tmp_skill_file(tmp_path):
    """Factory fixture that creates a temporary .md skill file.

    Optionally creates a sibling .eval.json file.

    Usage:
        def test_something(tmp_skill_file):
            skill_path = tmp_skill_file("my-skill", content="# My Skill")
            skill_path, eval_path = tmp_skill_file(
                "my-skill",
                content="# My Skill",
                eval_data={"skill_name": "my-skill", "assertions": []},
            )
    """

    def _factory(
        name: str = "test-skill",
        content: str = "# Test Skill\n\nA test skill for unit tests.",
        eval_data: dict | None = None,
    ) -> Path | tuple[Path, Path]:
        skill_path = tmp_path / f"{name}.md"
        skill_path.write_text(content)

        if eval_data is not None:
            eval_path = tmp_path / f"{name}.eval.json"
            eval_path.write_text(json.dumps(eval_data, indent=2))
            return skill_path, eval_path

        return skill_path

    return _factory


@pytest.fixture
def mock_runner():
    """Factory fixture returning a MagicMock SkillRunner.

    The mock's .run() returns a configurable SkillResult.

    Usage:
        def test_something(mock_runner):
            runner = mock_runner(output="some output", exit_code=0)
            result = runner.run("my-skill")
            assert result.output == "some output"
    """

    def _factory(
        output: str = "mock output",
        exit_code: int = 0,
        skill_name: str = "test-skill",
        args: str = "",
        duration_seconds: float = 1.0,
        error: str | None = None,
    ) -> MagicMock:
        mock = MagicMock(spec=SkillRunner)
        mock.project_dir = Path.cwd()
        result = SkillResult(
            output=output,
            exit_code=exit_code,
            skill_name=skill_name,
            args=args,
            duration_seconds=duration_seconds,
            error=error,
        )
        mock.run.return_value = result
        mock.run_raw.return_value = result
        return mock

    return _factory
