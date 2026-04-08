"""Pytest plugin for clauditor.

Registered via entry_points in pyproject.toml.
Provides fixtures and markers for testing Claude Code skills.

Usage in tests:
    def test_my_skill(clauditor_runner):
        result = clauditor_runner.run("my-skill", "--depth quick")
        result.assert_contains("Expected Section")
        result.assert_has_entries(minimum=3)

    def test_with_spec(clauditor_spec):
        spec = clauditor_spec(".claude/commands/my-skill.md")
        results = spec.evaluate()
        assert results.passed, results.summary()
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauditor.runner import SkillRunner
from clauditor.spec import SkillSpec


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("clauditor", "Claude Code skill testing")
    group.addoption(
        "--clauditor-project-dir",
        default=None,
        help="Project directory containing .claude/commands/ (default: cwd)",
    )
    group.addoption(
        "--clauditor-timeout",
        type=int,
        default=180,
        help="Timeout for skill execution in seconds (default: 180)",
    )
    group.addoption(
        "--clauditor-claude-bin",
        default="claude",
        help="Path to claude CLI binary (default: claude)",
    )


@pytest.fixture
def clauditor_runner(request: pytest.FixtureRequest) -> SkillRunner:
    """Fixture providing a SkillRunner configured from pytest options."""
    return SkillRunner(
        project_dir=request.config.getoption("--clauditor-project-dir"),
        timeout=request.config.getoption("--clauditor-timeout"),
        claude_bin=request.config.getoption("--clauditor-claude-bin"),
    )


@pytest.fixture
def clauditor_spec(request: pytest.FixtureRequest):
    """Fixture factory for loading SkillSpecs.

    Usage:
        def test_skill(clauditor_spec):
            spec = clauditor_spec(".claude/commands/my-skill.md")
    """
    runner = SkillRunner(
        project_dir=request.config.getoption("--clauditor-project-dir"),
        timeout=request.config.getoption("--clauditor-timeout"),
        claude_bin=request.config.getoption("--clauditor-claude-bin"),
    )

    def _factory(skill_path: str | Path, eval_path: str | Path | None = None):
        return SkillSpec.from_file(skill_path, eval_path=eval_path, runner=runner)

    return _factory
