"""Tests for the A/B baseline comparator."""

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.comparator import Flip, diff_assertion_sets


def _ar(name: str, passed: bool) -> AssertionResult:
    return AssertionResult(
        name=name,
        passed=passed,
        message="ok" if passed else "fail",
        kind="custom",
    )


class TestDiffAssertionSets:
    """Tests for diff_assertion_sets helper."""

    def test_only_in_before_is_removed(self):
        before = AssertionSet(results=[_ar("only_before", True)])
        after = AssertionSet(results=[])
        flips = diff_assertion_sets(before, after)
        assert len(flips) == 1
        assert flips[0].name == "only_before"
        assert flips[0].kind == "removed"

    def test_only_in_after_is_new(self):
        before = AssertionSet(results=[])
        after = AssertionSet(results=[_ar("only_after", True)])
        flips = diff_assertion_sets(before, after)
        assert len(flips) == 1
        assert flips[0].name == "only_after"
        assert flips[0].kind == "new"

    def test_both_same_passed_is_omitted(self):
        before = AssertionSet(results=[_ar("a", True), _ar("b", False)])
        after = AssertionSet(results=[_ar("a", True), _ar("b", False)])
        assert diff_assertion_sets(before, after) == []

    def test_flipped_pass_to_fail_is_regression(self):
        before = AssertionSet(results=[_ar("a", True)])
        after = AssertionSet(results=[_ar("a", False)])
        flips = diff_assertion_sets(before, after)
        assert len(flips) == 1
        assert flips[0].kind == "regression"
        assert flips[0].before_passed is True
        assert flips[0].after_passed is False

    def test_flipped_fail_to_pass_is_improvement(self):
        before = AssertionSet(results=[_ar("a", False)])
        after = AssertionSet(results=[_ar("a", True)])
        flips = diff_assertion_sets(before, after)
        assert len(flips) == 1
        assert flips[0].kind == "improvement"

    def test_result_sorted_by_name(self):
        before = AssertionSet(
            results=[_ar("zeta", True), _ar("alpha", True), _ar("mu", False)]
        )
        after = AssertionSet(
            results=[_ar("zeta", False), _ar("alpha", False), _ar("mu", True)]
        )
        flips = diff_assertion_sets(before, after)
        names = [f.name for f in flips]
        assert names == sorted(names)
        assert names == ["alpha", "mu", "zeta"]

    def test_flip_dataclass_fields(self):
        f = Flip(
            name="x", before_passed=True, after_passed=False, kind="regression"
        )
        assert f.name == "x"
        assert f.kind == "regression"
