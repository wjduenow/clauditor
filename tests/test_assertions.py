"""Tests for Layer 1 deterministic assertions."""

from clauditor.assertions import (
    AssertionResult,
    AssertionSet,
    assert_contains,
    assert_has_entries,
    assert_has_urls,
    assert_max_length,
    assert_min_count,
    assert_min_length,
    assert_not_contains,
    assert_regex,
    run_assertions,
)

SAMPLE_OUTPUT = """
🎯 **Top 5 kids' activities near Cupertino, CA** (ages 4-6, Free to $$, 25mi)

---
**Venues** (open on target dates):

**1. Children's Discovery Museum** — Museum (Indoor), $$
📍 180 Woz Way, San Jose, CA 95110 (~11mi)
🕐 9:30am-4:30pm daily (spring break hours)
👶 Best for ages: 0-10
🌟 Hands-on exhibits including mammoth bones, water play, and art studio.
🌐 [cdm.org](https://www.cdm.org/) | 📞 (408) 298-5437

**2. Deer Hollow Farm** — Nature/Farm, Free
📍 22500 Cristo Rey Dr, Cupertino, CA 94040 (~4mi)
🕐 Tue 8am-4pm; Wed 8am-1pm
👶 Best for ages: 2-8
🌟 Free working farm with goats, chickens, sheep.
🌐 [deerhollowfarm.org](https://www.deerhollowfarm.org/) | 📞 (650) 903-6331

**3. Happy Hollow Park & Zoo** — Zoo/Amusement, $$
📍 748 Story Rd, San Jose, CA 95112 (~13mi)
🕐 Wed-Sun 10am-4pm
👶 Best for ages: 2-8
🌟 Kid-friendly rides, petting zoo, animal encounters.
🌐 [happyhollow.org](https://happyhollow.org/) | 📞 (408) 794-6400

---
**Events** (happening on target dates):

**4. Police Read Along** — EVENT, Free
📍 Cupertino Library, 10800 Torre Ave (~1mi)
📅 Wednesday, April 8 • 10:30am-11:00am
🌟 Sheriff's Deputy reads books and shares safety tips.
🎟️ [sccld.org](https://sccld.org/locations/cupertino/)
"""


class TestContains:
    def test_found(self):
        result = assert_contains(SAMPLE_OUTPUT, "Venues")
        assert result.passed

    def test_missing(self):
        result = assert_contains(SAMPLE_OUTPUT, "Nonexistent Section")
        assert not result.passed


class TestNotContains:
    def test_absent(self):
        result = assert_not_contains(SAMPLE_OUTPUT, "ERROR")
        assert result.passed

    def test_present(self):
        result = assert_not_contains(SAMPLE_OUTPUT, "Venues")
        assert not result.passed


class TestRegex:
    def test_match(self):
        result = assert_regex(SAMPLE_OUTPUT, r"\*\*\d+\.\s+")
        assert result.passed
        assert result.evidence is not None

    def test_no_match(self):
        result = assert_regex(SAMPLE_OUTPUT, r"ZZZZZ\d+")
        assert not result.passed


class TestMinCount:
    def test_enough(self):
        result = assert_min_count(SAMPLE_OUTPUT, r"\*\*\d+\.", 3)
        assert result.passed

    def test_not_enough(self):
        result = assert_min_count(SAMPLE_OUTPUT, r"\*\*\d+\.", 10)
        assert not result.passed


class TestMinLength:
    def test_long_enough(self):
        result = assert_min_length(SAMPLE_OUTPUT, 100)
        assert result.passed

    def test_too_short(self):
        result = assert_min_length("short", 100)
        assert not result.passed


class TestHasUrls:
    def test_has_urls(self):
        result = assert_has_urls(SAMPLE_OUTPUT, minimum=3)
        assert result.passed

    def test_not_enough_urls(self):
        result = assert_has_urls("no urls here", minimum=1)
        assert not result.passed


class TestHasEntries:
    def test_has_entries(self):
        result = assert_has_entries(SAMPLE_OUTPUT, minimum=3)
        assert result.passed

    def test_not_enough(self):
        result = assert_has_entries(SAMPLE_OUTPUT, minimum=10)
        assert not result.passed


class TestRunAssertions:
    def test_all_pass(self):
        assertions = [
            {"type": "contains", "value": "Venues"},
            {"type": "contains", "value": "Events"},
            {"type": "has_urls", "value": "3"},
            {"type": "has_entries", "value": "3"},
            {"type": "not_contains", "value": "ERROR"},
            {"type": "min_length", "value": "500"},
        ]
        results = run_assertions(SAMPLE_OUTPUT, assertions)
        assert results.passed
        assert results.pass_rate == 1.0

    def test_mixed_results(self):
        assertions = [
            {"type": "contains", "value": "Venues"},
            {"type": "contains", "value": "Nonexistent"},
        ]
        results = run_assertions(SAMPLE_OUTPUT, assertions)
        assert not results.passed
        assert results.pass_rate == 0.5
        assert len(results.failed) == 1

    def test_unknown_type(self):
        results = run_assertions(SAMPLE_OUTPUT, [{"type": "bogus", "value": "x"}])
        assert not results.passed

    def test_summary(self):
        assertions = [
            {"type": "contains", "value": "Venues"},
            {"type": "contains", "value": "Missing"},
        ]
        results = run_assertions(SAMPLE_OUTPUT, assertions)
        summary = results.summary()
        assert "1/2" in summary
        assert "FAIL" in summary


class TestAssertionSet:
    def test_empty(self):
        s = AssertionSet()
        assert s.pass_rate == 0.0
        assert s.passed  # no assertions = nothing failed (vacuous truth)

    def test_all_passed(self):
        s = AssertionSet(
            results=[
                assert_contains("hello world", "hello"),
                assert_contains("hello world", "world"),
            ]
        )
        assert s.passed
        assert s.pass_rate == 1.0
        assert len(s.failed) == 0

    def test_pass_rate_mixed(self):
        s = AssertionSet(
            results=[
                assert_contains("hello", "hello"),
                assert_contains("hello", "missing"),
                assert_contains("hello", "also_missing"),
            ]
        )
        assert not s.passed
        assert abs(s.pass_rate - 1 / 3) < 0.01

    def test_failed_returns_only_failures(self):
        s = AssertionSet(
            results=[
                assert_contains("hello", "hello"),
                assert_contains("hello", "nope"),
            ]
        )
        failed = s.failed
        assert len(failed) == 1
        assert not failed[0].passed

    def test_summary_format(self):
        s = AssertionSet(
            results=[
                assert_contains("hello", "hello"),
                assert_contains("hello", "nope"),
            ]
        )
        summary = s.summary()
        assert "1/2" in summary
        assert "50%" in summary
        assert "FAIL" in summary
        assert "nope" in summary


class TestMaxLength:
    def test_pass(self):
        result = assert_max_length("short", 100)
        assert result.passed
        assert "5" in result.message

    def test_fail(self):
        result = assert_max_length("this is too long", 5)
        assert not result.passed

    def test_exact(self):
        result = assert_max_length("12345", 5)
        assert result.passed


class TestAssertionResultBool:
    def test_bool_true(self):
        r = AssertionResult(name="test", passed=True, message="ok")
        assert bool(r) is True

    def test_bool_false(self):
        r = AssertionResult(name="test", passed=False, message="fail")
        assert bool(r) is False


class TestRunAssertionsEdgeCases:
    def test_empty_assertions(self):
        result = run_assertions("anything", [])
        assert isinstance(result, AssertionSet)
        assert result.passed  # vacuous truth
        assert len(result.results) == 0

    def test_unknown_type_message(self):
        result = run_assertions("text", [{"type": "bogus", "value": "x"}])
        assert not result.passed
        assert len(result.results) == 1
        assert "Unknown assertion type" in result.results[0].message
        assert result.results[0].name == "unknown:bogus"

    def test_max_length_via_run(self):
        result = run_assertions("short", [{"type": "max_length", "value": "100"}])
        assert result.passed

    def test_regex_via_run(self):
        result = run_assertions("hello 123", [{"type": "regex", "value": r"\d+"}])
        assert result.passed

    def test_min_count_via_run(self):
        assertion = {"type": "min_count", "value": "a", "minimum": 3}
        result = run_assertions("aaa", [assertion])
        assert result.passed

    def test_has_urls_via_run(self):
        result = run_assertions(
            "visit https://example.com",
            [{"type": "has_urls", "value": "1"}],
        )
        assert result.passed

    def test_has_entries_via_run(self):
        result = run_assertions(
            "**1. Item** **2. Item**",
            [{"type": "has_entries", "value": "2"}],
        )
        assert result.passed
