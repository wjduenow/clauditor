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
    group.addoption(
        "--clauditor-grade",
        action="store_true",
        default=False,
        help="Enable Layer 3 LLM-graded quality tests (requires API key, costs money)",
    )
    group.addoption(
        "--clauditor-model",
        default=None,
        help="Override grading model for Layer 3 tests (default: claude-sonnet-4-6)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "clauditor_grade: mark test as requiring Layer 3 LLM grading "
        "(skipped without --clauditor-grade)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if not config.getoption("--clauditor-grade"):
        skip_grade = pytest.mark.skip(
            reason="need --clauditor-grade to run Layer 3 tests"
        )
        for item in items:
            if "clauditor_grade" in item.keywords:
                item.add_marker(skip_grade)


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


@pytest.fixture
def clauditor_grader(request: pytest.FixtureRequest, clauditor_spec):
    """Fixture factory for quality grading. Returns a callable that grades a skill."""
    import asyncio

    from clauditor.quality_grader import grade_quality

    model = request.config.getoption("--clauditor-model") or "claude-sonnet-4-6"

    def _factory(
        skill_path: str | Path,
        eval_path: str | Path | None = None,
        output: str | None = None,
    ):
        spec = clauditor_spec(skill_path, eval_path)
        if spec.eval_spec is None:
            raise ValueError(f"No eval spec found for {skill_path}")
        if output is None:
            result = spec.run()
            output = result.output
        return asyncio.run(grade_quality(output, spec.eval_spec, model))

    return _factory


@pytest.fixture
def clauditor_triggers(request: pytest.FixtureRequest, clauditor_spec):
    """Fixture factory for trigger precision testing."""
    import asyncio

    from clauditor.triggers import test_triggers as run_triggers

    model = request.config.getoption("--clauditor-model") or "claude-sonnet-4-6"

    def _factory(
        skill_path: str | Path, eval_path: str | Path | None = None
    ):
        spec = clauditor_spec(skill_path, eval_path)
        if spec.eval_spec is None:
            raise ValueError(f"No eval spec found for {skill_path}")
        return asyncio.run(run_triggers(spec.eval_spec, model))

    return _factory
