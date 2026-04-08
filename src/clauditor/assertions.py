"""Layer 1: Deterministic assertions against skill output.

No API calls, no LLM — just regex, string matching, and counting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    name: str
    passed: bool
    message: str
    evidence: str | None = None

    def __bool__(self) -> bool:
        return self.passed


@dataclass
class AssertionSet:
    """A collection of assertion results from checking skill output."""

    results: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[AssertionResult]:
        return [r for r in self.results if not r.passed]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        lines = [f"{passed}/{total} assertions passed ({self.pass_rate:.0%})"]
        for r in self.failed:
            lines.append(f"  FAIL: {r.name} — {r.message}")
        return "\n".join(lines)


def assert_contains(output: str, value: str) -> AssertionResult:
    """Check that output contains a substring."""
    found = value in output
    return AssertionResult(
        name=f"contains:{value[:40]}",
        passed=found,
        message=f"Found '{value[:40]}'" if found else f"Missing '{value[:40]}'",
    )


def assert_not_contains(output: str, value: str) -> AssertionResult:
    """Check that output does NOT contain a substring."""
    found = value in output
    return AssertionResult(
        name=f"not_contains:{value[:40]}",
        passed=not found,
        message="Correctly absent" if not found else f"Unexpected '{value[:40]}' found",
    )


def assert_regex(output: str, pattern: str) -> AssertionResult:
    """Check that output matches a regex pattern."""
    match = re.search(pattern, output)
    return AssertionResult(
        name=f"regex:{pattern[:40]}",
        passed=match is not None,
        message="Pattern matched" if match else f"Pattern not found: {pattern[:40]}",
        evidence=match.group(0)[:100] if match else None,
    )


def assert_min_count(output: str, pattern: str, minimum: int) -> AssertionResult:
    """Check that a pattern appears at least N times."""
    matches = re.findall(pattern, output)
    count = len(matches)
    return AssertionResult(
        name=f"min_count:{pattern[:30]}≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} matches (need ≥{minimum})",
    )


def assert_min_length(output: str, minimum: int) -> AssertionResult:
    """Check that output is at least N characters."""
    length = len(output)
    return AssertionResult(
        name=f"min_length≥{minimum}",
        passed=length >= minimum,
        message=f"Length {length} (need ≥{minimum})",
    )


def assert_max_length(output: str, maximum: int) -> AssertionResult:
    """Check that output is at most N characters."""
    length = len(output)
    return AssertionResult(
        name=f"max_length≤{maximum}",
        passed=length <= maximum,
        message=f"Length {length} (need ≤{maximum})",
    )


def assert_has_urls(output: str, minimum: int = 1) -> AssertionResult:
    """Check that output contains at least N URLs."""
    urls = re.findall(r"https?://[^\s\)\"'>]+", output)
    count = len(urls)
    return AssertionResult(
        name=f"has_urls≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} URLs (need ≥{minimum})",
        evidence="; ".join(urls[:5]) if urls else None,
    )


def assert_has_entries(output: str, minimum: int = 1) -> AssertionResult:
    """Check that output contains numbered entries (e.g., **1. Name**)."""
    entries = re.findall(r"\*\*\d+\.\s+", output)
    count = len(entries)
    return AssertionResult(
        name=f"has_entries≥{minimum}",
        passed=count >= minimum,
        message=f"Found {count} numbered entries (need ≥{minimum})",
    )


def run_assertions(output: str, assertions: list[dict]) -> AssertionSet:
    """Run a list of assertion dicts against output.

    Each dict has: {"type": "contains", "value": "Venues"} etc.
    Supported types: contains, not_contains, regex, min_count,
    min_length, max_length, has_urls, has_entries.
    """
    results = AssertionSet()
    for a in assertions:
        atype = a["type"]
        value = a.get("value", "")

        if atype == "contains":
            results.results.append(assert_contains(output, value))
        elif atype == "not_contains":
            results.results.append(assert_not_contains(output, value))
        elif atype == "regex":
            results.results.append(assert_regex(output, value))
        elif atype == "min_count":
            results.results.append(assert_min_count(output, value, a.get("minimum", 1)))
        elif atype == "min_length":
            results.results.append(assert_min_length(output, int(value)))
        elif atype == "max_length":
            results.results.append(assert_max_length(output, int(value)))
        elif atype == "has_urls":
            results.results.append(assert_has_urls(output, int(value) if value else 1))
        elif atype == "has_entries":
            results.results.append(
                assert_has_entries(output, int(value) if value else 1)
            )
        else:
            results.results.append(
                AssertionResult(
                    name=f"unknown:{atype}",
                    passed=False,
                    message=f"Unknown assertion type: {atype}",
                )
            )

    return results
