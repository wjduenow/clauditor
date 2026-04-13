"""Skill runner — executes Claude Code skills and captures output.

Stream-JSON parser notes
------------------------
This module invokes the Claude CLI with
``--output-format stream-json --verbose`` and parses its NDJSON output
line-by-line. Each line is a JSON object with a ``type`` field. The schema
below reflects the Anthropic CLI streaming format (verified live against
``claude`` 2.1.x):

- ``{"type": "system", ...}``                 — init / hook / misc events
- ``{"type": "assistant", "message": {..., "content": [blocks]}}``
    Each block in ``message.content`` has a ``type``. For ``type == "text"``
    the block carries a ``text`` field. Tool-use blocks and other block
    types are ignored for the purposes of ``SkillResult.output``.
- ``{"type": "result", ..., "usage": {"input_tokens": N, "output_tokens": M}}``
    The final line of a successful run. Carries aggregate token usage.

Malformed lines (``json.JSONDecodeError``) are logged to stderr and
skipped, never aborting the run. A missing ``result`` message leaves
token counts at 0 and emits a warning but still yields a ``SkillResult``.

Every exit path from ``_invoke`` is wrapped in ``try/finally`` so that
``SkillResult.duration_seconds`` is set for success, timeout, missing
binary, and any other error path (DEC-005).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
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
    outputs: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_messages: list[dict] = field(default_factory=list)

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
        return self._invoke(prompt=prompt, skill_name=skill_name, args=args)

    def run_raw(self, prompt: str) -> SkillResult:
        """Run a raw prompt without skill prefix for baseline comparison.

        Args:
            prompt: The raw prompt to send to Claude (no /{skill} prefix).

        Returns:
            SkillResult with skill_name="__baseline__"
        """
        return self._invoke(prompt=prompt, skill_name="__baseline__", args=prompt)

    # ------------------------------------------------------------------ #
    # Stream-json Popen implementation                                    #
    # ------------------------------------------------------------------ #

    def _invoke(self, *, prompt: str, skill_name: str, args: str) -> SkillResult:
        """Run ``claude`` with stream-json output and parse the NDJSON stream.

        Uses ``try/finally`` so ``duration_seconds`` is populated on every
        exit path (success, timeout, CalledProcessError, FileNotFoundError).
        """
        start = time.monotonic()
        raw_messages: list[dict] = []
        text_chunks: list[str] = []
        input_tokens = 0
        output_tokens = 0
        saw_result = False
        result: SkillResult | None = None
        proc: subprocess.Popen | None = None
        try:
            try:
                proc = subprocess.Popen(
                    [
                        self.claude_bin,
                        "-p",
                        prompt,
                        "--output-format",
                        "stream-json",
                        "--verbose",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(self.project_dir),
                )
            except FileNotFoundError:
                result = SkillResult(
                    output="",
                    exit_code=-1,
                    skill_name=skill_name,
                    args=args,
                    error=f"Claude CLI not found: {self.claude_bin}",
                )
                return result

            try:
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError as exc:
                            print(
                                "clauditor.runner: skipping malformed "
                                f"stream-json line: {exc}",
                                file=sys.stderr,
                            )
                            continue

                        raw_messages.append(msg)
                        mtype = msg.get("type")
                        if mtype == "assistant":
                            message = msg.get("message") or {}
                            for block in message.get("content", []) or []:
                                if (
                                    isinstance(block, dict)
                                    and block.get("type") == "text"
                                ):
                                    text_chunks.append(block.get("text", ""))
                        elif mtype == "result":
                            saw_result = True
                            usage = msg.get("usage") or {}
                            input_tokens = int(usage.get("input_tokens", 0) or 0)
                            output_tokens = int(
                                usage.get("output_tokens", 0) or 0
                            )

                returncode = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                result = SkillResult(
                    output="\n".join(text_chunks),
                    exit_code=-1,
                    skill_name=skill_name,
                    args=args,
                    error="timeout",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    raw_messages=raw_messages,
                )
                return result

            stderr_text = ""
            if proc.stderr is not None:
                try:
                    stderr_text = proc.stderr.read() or ""
                except Exception:
                    stderr_text = ""

            if not saw_result:
                print(
                    "clauditor.runner: stream-json ended without a 'result' "
                    "message; token usage unavailable",
                    file=sys.stderr,
                )

            result = SkillResult(
                output="\n".join(text_chunks),
                exit_code=returncode,
                skill_name=skill_name,
                args=args,
                error=stderr_text if returncode != 0 and stderr_text else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_messages=raw_messages,
            )
            return result
        finally:
            duration = time.monotonic() - start
            if result is not None:
                result.duration_seconds = duration
