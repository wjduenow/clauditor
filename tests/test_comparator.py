"""Tests for the A/B baseline comparator."""

from clauditor.comparator import ABReport, ABResult
from clauditor.quality_grader import GradingReport, GradingResult


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
