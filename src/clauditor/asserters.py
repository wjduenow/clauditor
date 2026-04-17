"""Test-helper assertion wrapper for :class:`SkillResult`.

:class:`SkillAsserter` is a thin composition wrapper around a
:class:`~clauditor.runner.SkillResult` that exposes the Layer 1
``assert_*`` helpers used by tests. Keeping the wrapper separate from
``SkillResult`` preserves the dataclass's pure-data contract: non-test
callers get a data container with no test-only API surface, while tests
opt into the helpers by constructing ``SkillAsserter(result)``.

Usage in tests::

    asserter = SkillAsserter(result)
    asserter.assert_contains("Expected Section")
    asserter.assert_has_entries(minimum=3)

Or via the :func:`assert_from` convenience factory::

    assert_from(result).assert_contains("Expected Section")
"""

from __future__ import annotations

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
from clauditor.runner import SkillResult


class SkillAsserter:
    """Composition wrapper exposing Layer 1 assertions against a ``SkillResult``.

    Each ``assert_*`` method delegates to the corresponding pure function
    in :mod:`clauditor.assertions`, raising :class:`AssertionError` on
    failure with the assertion's own ``message``.
    """

    __slots__ = ("result",)

    def __init__(self, result: SkillResult) -> None:
        self.result = result

    # --- Layer 1: Deterministic assertions ---

    def assert_contains(self, value: str) -> None:
        """Assert output contains a substring. Raises AssertionError on failure."""
        res = assert_contains(self.result.output, value)
        if not res:
            raise AssertionError(res.message)

    def assert_not_contains(self, value: str) -> None:
        """Assert output does NOT contain a substring."""
        res = assert_not_contains(self.result.output, value)
        if not res:
            raise AssertionError(res.message)

    def assert_matches(self, pattern: str) -> None:
        """Assert output matches a regex pattern."""
        res = assert_regex(self.result.output, pattern)
        if not res:
            raise AssertionError(res.message)

    def assert_min_count(self, pattern: str, minimum: int) -> None:
        """Assert a pattern appears at least N times."""
        res = assert_min_count(self.result.output, pattern, minimum)
        if not res:
            raise AssertionError(res.message)

    def assert_min_length(self, minimum: int) -> None:
        """Assert output is at least N characters."""
        res = assert_min_length(self.result.output, minimum)
        if not res:
            raise AssertionError(res.message)

    def assert_has_urls(self, minimum: int = 1) -> None:
        """Assert output contains at least N URLs."""
        res = assert_has_urls(self.result.output, minimum)
        if not res:
            raise AssertionError(res.message)

    def assert_has_entries(self, minimum: int = 1) -> None:
        """Assert output contains at least N numbered entries."""
        res = assert_has_entries(self.result.output, minimum)
        if not res:
            raise AssertionError(res.message)

    def run_assertions(self, assertions: list[dict]) -> AssertionSet:
        """Run a list of assertion dicts against this output."""
        return run_assertions(self.result.output, assertions)


def assert_from(result: SkillResult) -> SkillAsserter:
    """Convenience factory: ``assert_from(result).assert_contains(...)``."""
    return SkillAsserter(result)
