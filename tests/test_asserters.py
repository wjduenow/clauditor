"""Tests for :mod:`clauditor.asserters` — the SkillAsserter test-helper wrapper.

The module is imported by the pytest plugin before coverage starts, so we
``importlib.reload`` it here (same pattern as ``tests/test_schemas.py``) to
make coverage observe the class body definitions.
"""

import importlib

import pytest

import clauditor.asserters as _asserters_mod

importlib.reload(_asserters_mod)

from clauditor.asserters import SkillAsserter, assert_from  # noqa: E402
from clauditor.runner import SkillResult  # noqa: E402


def _result(output: str) -> SkillResult:
    return SkillResult(output=output, exit_code=0, skill_name="t", args="")


class TestSkillAsserterConstruction:
    def test_stores_result_reference(self):
        result = _result("hi")
        asserter = SkillAsserter(result)
        assert asserter.result is result

    def test_uses_slots(self):
        """__slots__ prevents accidental attribute drift on the wrapper."""
        asserter = SkillAsserter(_result("hi"))
        with pytest.raises(AttributeError):
            asserter.extra = "nope"  # type: ignore[attr-defined]


class TestAssertContains:
    def test_pass(self):
        SkillAsserter(_result("hello world")).assert_contains("world")

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("hello world")).assert_contains("missing")


class TestAssertNotContains:
    def test_pass(self):
        SkillAsserter(_result("hello world")).assert_not_contains("missing")

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("hello world")).assert_not_contains("hello")


class TestAssertMatches:
    def test_pass(self):
        SkillAsserter(_result("order 12345 confirmed")).assert_matches(r"\d{5}")

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("no digits here")).assert_matches(r"\d{5}")


class TestAssertMinCount:
    def test_pass(self):
        SkillAsserter(_result("a a a")).assert_min_count("a", 3)

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("a a")).assert_min_count("a", 5)


class TestAssertMinLength:
    def test_pass(self):
        SkillAsserter(_result("x" * 100)).assert_min_length(100)

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("short")).assert_min_length(1000)


class TestAssertHasUrls:
    def test_pass(self):
        SkillAsserter(
            _result("Visit https://example.com today")
        ).assert_has_urls(1)

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("no urls here")).assert_has_urls(1)


class TestAssertHasEntries:
    def test_pass(self):
        SkillAsserter(
            _result("**1. First**\n**2. Second**\n**3. Third**")
        ).assert_has_entries(3)

    def test_fail(self):
        with pytest.raises(AssertionError):
            SkillAsserter(_result("no numbered entries")).assert_has_entries(3)


class TestRunAssertions:
    def test_delegates_to_assertions_module(self):
        asserter = SkillAsserter(_result("hello world"))
        assertion_set = asserter.run_assertions(
            [{"type": "contains", "needle": "hello"}]
        )
        assert assertion_set.passed


class TestAssertFromFactory:
    def test_wraps_result_in_asserter(self):
        result = _result("hello world")
        asserter = assert_from(result)
        assert isinstance(asserter, SkillAsserter)
        assert asserter.result is result

    def test_chainable_usage(self):
        """``assert_from(result).assert_contains(...)`` is the concise shape."""
        assert_from(_result("hello world")).assert_contains("hello")
