"""Tests for the A/B baseline comparator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.comparator import (
    ABReport,
    ABResult,
    Flip,
    compare_ab,
    diff_assertion_sets,
)
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult


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


def _make_grade(criterion: str, passed: bool, score: float = 0.8) -> GradingResult:
    return GradingResult(
        criterion=criterion,
        passed=passed,
        score=score,
        evidence="test evidence",
        reasoning="test reasoning",
    )


def _make_report(
    results: list[GradingResult], skill_name: str = "test-skill"
) -> GradingReport:
    return GradingReport(
        skill_name=skill_name,
        results=results,
        model="test-model",
    )


class TestABResult:
    def test_regression_when_baseline_passes_skill_fails(self):
        r = ABResult(
            criterion="quality",
            skill_grade=_make_grade("quality", passed=False),
            baseline_grade=_make_grade("quality", passed=True),
            regression=True,
        )
        assert r.regression is True

    def test_no_regression_when_both_pass(self):
        r = ABResult(
            criterion="quality",
            skill_grade=_make_grade("quality", passed=True),
            baseline_grade=_make_grade("quality", passed=True),
            regression=False,
        )
        assert r.regression is False

    def test_no_regression_when_both_fail(self):
        r = ABResult(
            criterion="quality",
            skill_grade=_make_grade("quality", passed=False),
            baseline_grade=_make_grade("quality", passed=False),
            regression=False,
        )
        assert r.regression is False

    def test_no_regression_when_skill_passes_baseline_fails(self):
        r = ABResult(
            criterion="quality",
            skill_grade=_make_grade("quality", passed=True),
            baseline_grade=_make_grade("quality", passed=False),
            regression=False,
        )
        assert r.regression is False


class TestABReport:
    def _build_report(self, results: list[ABResult]) -> ABReport:
        skill_grades = [r.skill_grade for r in results]
        baseline_grades = [r.baseline_grade for r in results]
        return ABReport(
            skill_name="test-skill",
            skill_report=_make_report(skill_grades),
            baseline_report=_make_report(baseline_grades),
            results=results,
            model="test-model",
        )

    def test_passed_when_no_regressions(self):
        results = [
            ABResult(
                criterion="c1",
                skill_grade=_make_grade("c1", True),
                baseline_grade=_make_grade("c1", True),
                regression=False,
            ),
            ABResult(
                criterion="c2",
                skill_grade=_make_grade("c2", True),
                baseline_grade=_make_grade("c2", False),
                regression=False,
            ),
        ]
        report = self._build_report(results)
        assert report.passed is True
        assert report.regressions == []

    def test_failed_when_has_regression(self):
        results = [
            ABResult(
                criterion="c1",
                skill_grade=_make_grade("c1", True),
                baseline_grade=_make_grade("c1", True),
                regression=False,
            ),
            ABResult(
                criterion="c2",
                skill_grade=_make_grade("c2", False),
                baseline_grade=_make_grade("c2", True),
                regression=True,
            ),
        ]
        report = self._build_report(results)
        assert report.passed is False
        assert len(report.regressions) == 1
        assert report.regressions[0].criterion == "c2"

    def test_passed_with_empty_results(self):
        report = self._build_report([])
        assert report.passed is True
        assert report.regressions == []

    def test_multiple_regressions(self):
        results = [
            ABResult(
                criterion="c1",
                skill_grade=_make_grade("c1", False),
                baseline_grade=_make_grade("c1", True),
                regression=True,
            ),
            ABResult(
                criterion="c2",
                skill_grade=_make_grade("c2", False),
                baseline_grade=_make_grade("c2", True),
                regression=True,
            ),
        ]
        report = self._build_report(results)
        assert report.passed is False
        assert len(report.regressions) == 2

    def test_summary_contains_key_info(self):
        results = [
            ABResult(
                criterion="has_urls",
                skill_grade=_make_grade("has_urls", True),
                baseline_grade=_make_grade("has_urls", True),
                regression=False,
            ),
            ABResult(
                criterion="formatted_output",
                skill_grade=_make_grade("formatted_output", False),
                baseline_grade=_make_grade("formatted_output", True),
                regression=True,
            ),
        ]
        report = self._build_report(results)
        summary = report.summary()
        assert "test-skill" in summary
        assert "FAIL" in summary
        assert "1 regression" in summary
        assert "has_urls" in summary
        assert "formatted_output" in summary
        assert "REGRESSION" in summary

    def test_summary_pass_when_no_regressions(self):
        results = [
            ABResult(
                criterion="c1",
                skill_grade=_make_grade("c1", True),
                baseline_grade=_make_grade("c1", True),
                regression=False,
            ),
        ]
        report = self._build_report(results)
        summary = report.summary()
        assert "PASS" in summary
        assert "REGRESSION" not in summary


def _mock_eval_spec(test_args="find kid activities near me"):
    """Create a mock eval spec with test_args and grading criteria."""
    es = MagicMock()
    es.test_args = test_args
    return es


def _mock_spec(
    eval_spec=None,
    skill_output="skill output",
    baseline_output="baseline output",
):
    """Create a mock SkillSpec with a mock runner."""
    spec = MagicMock()
    spec.skill_name = "test-skill"
    spec.eval_spec = eval_spec
    spec.run.return_value = SkillResult(
        output=skill_output, exit_code=0, skill_name="test-skill", args="test"
    )
    spec.runner.run_raw.return_value = SkillResult(
        output=baseline_output, exit_code=0, skill_name="__baseline__", args="test"
    )
    return spec


class TestCompareAB:
    """Tests for the compare_ab async function."""

    @pytest.mark.asyncio
    async def test_success_no_regressions(self):
        eval_spec = _mock_eval_spec()
        spec = _mock_spec(eval_spec=eval_spec)

        skill_grades = [
            _make_grade("clarity", True, 0.9),
            _make_grade("accuracy", True, 0.85),
        ]
        baseline_grades = [
            _make_grade("clarity", True, 0.8),
            _make_grade("accuracy", True, 0.75),
        ]

        skill_report = _make_report(skill_grades)
        baseline_report = _make_report(baseline_grades)

        mock_grade = AsyncMock(side_effect=[skill_report, baseline_report])

        with patch("clauditor.comparator.grade_quality", mock_grade):
            report = await compare_ab(spec)

        assert report.passed is True
        assert len(report.results) == 2
        assert report.regressions == []
        assert report.skill_name == "test-skill"
        assert mock_grade.call_count == 2

    @pytest.mark.asyncio
    async def test_regression_detected(self):
        eval_spec = _mock_eval_spec()
        spec = _mock_spec(eval_spec=eval_spec)

        skill_grades = [
            _make_grade("clarity", False, 0.3),
            _make_grade("accuracy", True, 0.9),
        ]
        baseline_grades = [
            _make_grade("clarity", True, 0.9),
            _make_grade("accuracy", True, 0.8),
        ]

        skill_report = _make_report(skill_grades)
        baseline_report = _make_report(baseline_grades)

        mock_grade = AsyncMock(side_effect=[skill_report, baseline_report])

        with patch("clauditor.comparator.grade_quality", mock_grade):
            report = await compare_ab(spec)

        assert report.passed is False
        assert len(report.regressions) == 1
        assert report.regressions[0].criterion == "clarity"

    @pytest.mark.asyncio
    async def test_no_eval_spec_raises(self):
        spec = _mock_spec(eval_spec=None)

        with pytest.raises(ValueError, match="No eval spec found"):
            await compare_ab(spec)

    @pytest.mark.asyncio
    async def test_empty_test_args_raises(self):
        eval_spec = _mock_eval_spec(test_args="")
        spec = _mock_spec(eval_spec=eval_spec)

        with pytest.raises(ValueError, match="non-empty test_args"):
            await compare_ab(spec)

    @pytest.mark.asyncio
    async def test_whitespace_only_test_args_raises(self):
        eval_spec = _mock_eval_spec(test_args="   ")
        spec = _mock_spec(eval_spec=eval_spec)

        with pytest.raises(ValueError, match="non-empty test_args"):
            await compare_ab(spec)

    @pytest.mark.asyncio
    async def test_baseline_fewer_criteria_pads_with_synthetic(self):
        eval_spec = _mock_eval_spec()
        spec = _mock_spec(eval_spec=eval_spec)

        skill_grades = [
            _make_grade("clarity", True, 0.9),
            _make_grade("accuracy", True, 0.8),
            _make_grade("format", False, 0.4),
        ]
        baseline_grades = [
            _make_grade("clarity", True, 0.85),
        ]

        skill_report = _make_report(skill_grades)
        baseline_report = _make_report(baseline_grades)

        mock_grade = AsyncMock(side_effect=[skill_report, baseline_report])

        with patch("clauditor.comparator.grade_quality", mock_grade):
            report = await compare_ab(spec)

        assert len(report.results) == 3
        assert report.results[0].baseline_grade.passed is True
        assert report.results[1].baseline_grade.passed is False
        assert report.results[1].baseline_grade.score == 0.0
        assert "not evaluated" in report.results[1].baseline_grade.reasoning
        assert report.results[2].baseline_grade.passed is False
        assert report.results[1].regression is False
        assert report.results[2].regression is False

    @pytest.mark.asyncio
    async def test_custom_model_passed_through(self):
        eval_spec = _mock_eval_spec()
        spec = _mock_spec(eval_spec=eval_spec)

        grades = [_make_grade("clarity", True, 0.9)]
        report_obj = _make_report(grades)
        mock_grade = AsyncMock(side_effect=[report_obj, report_obj])

        with patch("clauditor.comparator.grade_quality", mock_grade):
            report = await compare_ab(spec, model="claude-haiku-4-5")

        assert report.model == "claude-haiku-4-5"
        for call in mock_grade.call_args_list:
            assert call.kwargs.get("model") == "claude-haiku-4-5"
