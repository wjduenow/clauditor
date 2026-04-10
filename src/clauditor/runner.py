"""Skill runner — executes Claude Code skills and captures output."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from clauditor.assertions import (
    AssertionSet,
    assert_contains,
    assert_has_entries,
    assert_has_urls,
    assert_min_count,
    assert_min_length,
    assert_not_contains,
    assert_regex,
    run_assertions,
)


@dataclass
class SkillResult:
    """Captured output from a skill run."""

    output: str
    exit_code: int
    skill_name: str
    args: str
    duration_seconds: float = 0.0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.output.strip() != ""

    # --- Layer 1: Deterministic assertions ---

    def assert_contains(self, value: str) -> None:
        """Assert output contains a substring. Raises AssertionError on failure."""
        result = assert_contains(self.output, value)
        if not result:
            raise AssertionError(result.message)

    def assert_not_contains(self, value: str) -> None:
        """Assert output does NOT contain a substring."""
        result = assert_not_contains(self.output, value)
        if not result:
            raise AssertionError(result.message)

    def assert_matches(self, pattern: str) -> None:
        """Assert output matches a regex pattern."""
        result = assert_regex(self.output, pattern)
        if not result:
            raise AssertionError(result.message)

    def assert_min_count(self, pattern: str, minimum: int) -> None:
        """Assert a pattern appears at least N times."""
        result = assert_min_count(self.output, pattern, minimum)
        if not result:
            raise AssertionError(result.message)

    def assert_min_length(self, minimum: int) -> None:
        """Assert output is at least N characters."""
        result = assert_min_length(self.output, minimum)
        if not result:
            raise AssertionError(result.message)

    def assert_has_urls(self, minimum: int = 1) -> None:
        """Assert output contains at least N URLs."""
        result = assert_has_urls(self.output, minimum)
        if not result:
            raise AssertionError(result.message)

    def assert_has_entries(self, minimum: int = 1) -> None:
        """Assert output contains at least N numbered entries."""
        result = assert_has_entries(self.output, minimum)
        if not result:
            raise AssertionError(result.message)

    def run_assertions(self, assertions: list[dict]) -> AssertionSet:
        """Run a list of assertion dicts against this output."""
        return run_assertions(self.output, assertions)


class SkillRunner:
    """Executes Claude Code skills via the CLI and captures output."""

    def __init__(
        self,
        project_dir: str | Path | None = None,
        timeout: int = 180,
        claude_bin: str = "claude",
    ):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.timeout = timeout
        self.claude_bin = claude_bin

    def run(self, skill_name: str, args: str = "") -> SkillResult:
        """Run a skill and capture its output.

        Args:
            skill_name: Name of the skill (e.g., "find-kid-activities")
            args: Pre-filled arguments to skip interactive prompts

        Returns:
            SkillResult with captured output
        """
        prompt = f"/{skill_name}"
        if args:
            prompt += f" {args}"

        import time

        start = time.monotonic()
        try:
            result = subprocess.run(
                [self.claude_bin, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.project_dir),
            )
            duration = time.monotonic() - start
            return SkillResult(
                output=result.stdout,
                exit_code=result.returncode,
                skill_name=skill_name,
                args=args,
                duration_seconds=duration,
                error=result.stderr if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return SkillResult(
                output="",
                exit_code=-1,
                skill_name=skill_name,
                args=args,
                duration_seconds=duration,
                error=f"Timed out after {self.timeout}s",
            )
        except FileNotFoundError:
            return SkillResult(
                output="",
                exit_code=-1,
                skill_name=skill_name,
                args=args,
                error=f"Claude CLI not found: {self.claude_bin}",
            )

    def run_raw(self, prompt: str) -> SkillResult:
        """Run a raw prompt without skill prefix for baseline comparison.

        Args:
            prompt: The raw prompt to send to Claude (no /{skill} prefix).

        Returns:
            SkillResult with skill_name="__baseline__"
        """
        import time

        start = time.monotonic()
        try:
            result = subprocess.run(
                [self.claude_bin, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.project_dir),
            )
            duration = time.monotonic() - start
            return SkillResult(
                output=result.stdout,
                exit_code=result.returncode,
                skill_name="__baseline__",
                args=prompt,
                duration_seconds=duration,
                error=result.stderr if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return SkillResult(
                output="",
                exit_code=-1,
                skill_name="__baseline__",
                args=prompt,
                duration_seconds=duration,
                error=f"Timed out after {self.timeout}s",
            )
        except FileNotFoundError:
            return SkillResult(
                output="",
                exit_code=-1,
                skill_name="__baseline__",
                args=prompt,
                error=f"Claude CLI not found: {self.claude_bin}",
            )
