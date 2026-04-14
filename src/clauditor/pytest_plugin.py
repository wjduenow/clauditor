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
    config.addinivalue_line(
        "markers",
        "network: real HTTP; deselect with -m 'not network'",
    )
    config.addinivalue_line(
        "markers",
        "slow: slow-running tests; deselect with -m 'not slow'",
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
def clauditor_spec(request: pytest.FixtureRequest, tmp_path: Path):
    """Fixture factory for loading SkillSpecs.

    Usage:
        def test_skill(clauditor_spec):
            spec = clauditor_spec(".claude/commands/my-skill.md")

    When the loaded spec declares non-empty ``eval_spec.input_files``, the
    returned ``SkillSpec`` has its ``.run`` method transparently wrapped so
    that calls without an explicit ``run_dir`` use a stable subdirectory
    under pytest's ``tmp_path``. This causes the declared input files to be
    staged automatically. Specs with no ``input_files`` are returned
    unmodified (zero behavior change).
    """
    runner = SkillRunner(
        project_dir=request.config.getoption("--clauditor-project-dir"),
        timeout=request.config.getoption("--clauditor-timeout"),
        claude_bin=request.config.getoption("--clauditor-claude-bin"),
    )

    def _factory(skill_path: str | Path, eval_path: str | Path | None = None):
        spec = SkillSpec.from_file(skill_path, eval_path=eval_path, runner=runner)
        if spec.eval_spec is not None and spec.eval_spec.input_files:
            original_run = spec.run
            default_run_dir = tmp_path / "clauditor_run"

            def _run_with_default_run_dir(
                args: str | None = None,
                *,
                run_dir: Path | None = None,
            ):
                if run_dir is None:
                    default_run_dir.mkdir(parents=True, exist_ok=True)
                    run_dir = default_run_dir
                return original_run(args, run_dir=run_dir)

            spec.run = _run_with_default_run_dir  # type: ignore[method-assign]
        return spec

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


@pytest.fixture(scope="session")
def clauditor_capture(request: pytest.FixtureRequest):
    """Fixture factory returning a Path to a captured skill output file.

    Usage:
        def test_my_skill(clauditor_capture):
            path = clauditor_capture("find-restaurants")
            output = path.read_text()  # raises FileNotFoundError if missing

    Default location: ``tests/eval/captured/<skill_name>.txt`` resolved
    relative to the pytest rootdir. Pass ``base_dir`` to override.
    The fixture does NOT run capture or skip on missing files — a missing
    file is the test's problem (DEC-006).
    """
    rootdir = Path(str(request.config.rootdir))

    def _factory(
        skill_name: str, base_dir: str | Path | None = None
    ) -> Path:
        if base_dir is None:
            base = rootdir / "tests" / "eval" / "captured"
        else:
            base = Path(base_dir)
        return base / f"{skill_name}.txt"

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
