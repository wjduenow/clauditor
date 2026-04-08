"""Tests for Layer 3 quality grading (rubric-based Sonnet grading)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor.quality_grader import (
    GradingReport,
    GradingResult,
    VarianceReport,
    build_grading_prompt,
    grade_quality,
    measure_variance,
    parse_grading_response,
)
from clauditor.schemas import EvalSpec, VarianceConfig


def _make_spec() -> EvalSpec:
    return EvalSpec(
        skill_name="test-skill",
        description="A test skill for unit tests",
        grading_criteria=[
            "Output contains actionable recommendations",
            "Tone is professional and clear",
            "All requested topics are covered",
        ],
    )


def _make_results(
    passed_flags: list[bool],
) -> list[GradingResult]:
    criteria = [
        "Output contains actionable recommendations",
        "Tone is professional and clear",
        "All requested topics are covered",
    ]
    return [
        GradingResult(
            criterion=criteria[i],
            passed=p,
            score=0.9 if p else 0.2,
            evidence="some text",
            reasoning="looks good" if p else "needs work",
        )
        for i, p in enumerate(passed_flags)
    ]


class TestGradingReport:
    def test_passed_all_true(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, True, True]),
            model="claude-sonnet-4-6",
        )
        assert report.passed is True

    def test_passed_one_false(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
        )
        assert report.passed is False

    def test_pass_rate_all_pass(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, True, True]),
            model="claude-sonnet-4-6",
        )
        assert report.pass_rate == pytest.approx(1.0)

    def test_pass_rate_partial(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
        )
        assert report.pass_rate == pytest.approx(2 / 3)

    def test_pass_rate_empty(self):
        report = GradingReport(
            skill_name="test", results=[], model="claude-sonnet-4-6"
        )
        assert report.pass_rate == 0.0

    def test_mean_score(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
        )
        # Two at 0.9, one at 0.2 => (0.9 + 0.2 + 0.9) / 3
        assert report.mean_score == pytest.approx(2.0 / 3)

    def test_mean_score_empty(self):
        report = GradingReport(
            skill_name="test", results=[], model="claude-sonnet-4-6"
        )
        assert report.mean_score == 0.0

    def test_summary_contains_counts(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
        )
        s = report.summary()
        assert "2/3 criteria passed" in s
        assert "67%" in s

    def test_summary_lists_each_criterion(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
        )
        s = report.summary()
        assert "PASS: Output contains actionable" in s
        assert "FAIL: Tone is professional" in s


class TestParseGradingResponse:
    def test_valid_json(self):
        data = [
            {
                "criterion": "Is clear",
                "passed": True,
                "score": 0.95,
                "evidence": "The text is well structured",
                "reasoning": "Clear headings and flow",
            }
        ]
        results = parse_grading_response(json.dumps(data), ["Is clear"])
        assert len(results) == 1
        assert results[0].criterion == "Is clear"
        assert results[0].passed is True
        assert results[0].score == pytest.approx(0.95)

    def test_markdown_wrapped_json(self):
        data = [
            {
                "criterion": "Has examples",
                "passed": False,
                "score": 0.3,
                "evidence": "No examples found",
                "reasoning": "Output lacks concrete examples",
            }
        ]
        text = f"```json\n{json.dumps(data)}\n```"
        results = parse_grading_response(text, ["Has examples"])
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].score == pytest.approx(0.3)

    def test_bare_markdown_wrapped_json(self):
        data = [
            {
                "criterion": "Is complete",
                "passed": True,
                "score": 0.8,
                "evidence": "All sections present",
                "reasoning": "Covers everything",
            }
        ]
        text = f"```\n{json.dumps(data)}\n```"
        results = parse_grading_response(text, ["Is complete"])
        assert len(results) == 1
        assert results[0].passed is True

    def test_invalid_json_returns_empty(self):
        results = parse_grading_response(
            "This is not JSON at all", ["criterion"]
        )
        assert results == []

    def test_non_array_json_returns_empty(self):
        results = parse_grading_response('{"not": "an array"}', ["c"])
        assert results == []

    def test_multiple_results(self):
        data = [
            {
                "criterion": "A",
                "passed": True,
                "score": 0.9,
                "evidence": "e1",
                "reasoning": "r1",
            },
            {
                "criterion": "B",
                "passed": False,
                "score": 0.1,
                "evidence": "e2",
                "reasoning": "r2",
            },
        ]
        results = parse_grading_response(json.dumps(data), ["A", "B"])
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False


class TestBuildGradingPrompt:
    def test_contains_skill_name(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec)
        assert "test-skill" in prompt

    def test_contains_all_criteria(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec)
        assert "actionable recommendations" in prompt
        assert "professional and clear" in prompt
        assert "requested topics are covered" in prompt

    def test_criteria_are_numbered(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec)
        assert "1. Output contains actionable" in prompt
        assert "2. Tone is professional" in prompt
        assert "3. All requested topics" in prompt

    def test_asks_for_json_response(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec)
        assert "JSON" in prompt


class TestGradeQuality:
    @pytest.mark.asyncio
    async def test_successful_grading(self):
        spec = _make_spec()
        grading_data = [
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True,
                "score": 0.9,
                "evidence": "Step 1: do this",
                "reasoning": "Clear action items",
            },
            {
                "criterion": "Tone is professional and clear",
                "passed": True,
                "score": 0.85,
                "evidence": "Well structured prose",
                "reasoning": "Professional tone throughout",
            },
            {
                "criterion": "All requested topics are covered",
                "passed": False,
                "score": 0.4,
                "evidence": "Missing section on testing",
                "reasoning": "Testing topic was not addressed",
            },
        ]

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text=json.dumps(grading_data))
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await grade_quality(
                "Some skill output text", spec, model="claude-sonnet-4-6"
            )

        assert report.skill_name == "test-skill"
        assert report.model == "claude-sonnet-4-6"
        assert len(report.results) == 3
        assert report.passed is False
        assert report.pass_rate == pytest.approx(2 / 3)
        assert report.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_parse_failure_returns_failed_report(self):
        spec = _make_spec()

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text="I cannot parse this as valid output")
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await grade_quality("Some output", spec)

        assert report.passed is False
        assert len(report.results) == 1
        assert report.results[0].criterion == "parse_response"
        assert report.results[0].score == 0.0
        assert "I cannot parse" in report.results[0].evidence

    @pytest.mark.asyncio
    async def test_markdown_wrapped_response(self):
        spec = _make_spec()
        grading_data = [
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True,
                "score": 0.95,
                "evidence": "Do X, then Y",
                "reasoning": "Very actionable",
            },
        ]

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text=f"```json\n{json.dumps(grading_data)}\n```")
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await grade_quality("Some output", spec)

        assert len(report.results) == 1
        assert report.results[0].passed is True

    @pytest.mark.asyncio
    async def test_sends_output_in_message(self):
        spec = _make_spec()
        grading_data = [
            {
                "criterion": "A",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
        ]

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text=json.dumps(grading_data))
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await grade_quality(
                "THE SKILL OUTPUT", spec, model="claude-sonnet-4-6"
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        user_message = call_kwargs["messages"][0]["content"]
        assert "THE SKILL OUTPUT" in user_message
        assert call_kwargs["model"] == "claude-sonnet-4-6"


def _make_grading_report(
    passed: bool, mean_score: float = 0.9, pass_rate: float = 1.0
) -> GradingReport:
    """Build a GradingReport with controlled properties."""
    if passed:
        results = [
            GradingResult(
                criterion="c1",
                passed=True,
                score=mean_score,
                evidence="e",
                reasoning="r",
            ),
        ]
    else:
        results = [
            GradingResult(
                criterion="c1",
                passed=True,
                score=mean_score * 2 - 0.2,
                evidence="e",
                reasoning="r",
            ),
            GradingResult(
                criterion="c2",
                passed=False,
                score=0.2,
                evidence="e",
                reasoning="r",
            ),
        ]
    return GradingReport(
        skill_name="test-skill", results=results, model="claude-sonnet-4-6"
    )


class TestVarianceReport:
    def test_all_passing_runs(self):
        """5 all-passing runs -> stability=1.0, passed=True."""
        reports = [
            _make_grading_report(passed=True, mean_score=0.9)
            for _ in range(5)
        ]
        vr = VarianceReport(
            skill_name="test-skill",
            n_runs=5,
            reports=reports,
            score_mean=0.9,
            score_stddev=0.0,
            pass_rate_mean=1.0,
            stability=1.0,
            min_stability=0.8,
            model="claude-sonnet-4-6",
        )
        assert vr.stability == 1.0
        assert vr.passed is True

    def test_partial_passing_runs(self):
        """3/5 passing -> stability=0.6, passed=False (below 0.8)."""
        reports = [
            _make_grading_report(passed=True, mean_score=0.9)
            for _ in range(3)
        ] + [
            _make_grading_report(passed=False, mean_score=0.5)
            for _ in range(2)
        ]
        vr = VarianceReport(
            skill_name="test-skill",
            n_runs=5,
            reports=reports,
            score_mean=0.74,
            score_stddev=0.16,
            pass_rate_mean=0.8,
            stability=0.6,
            min_stability=0.8,
            model="claude-sonnet-4-6",
        )
        assert vr.stability == 0.6
        assert vr.passed is False

    def test_stddev_computation(self):
        """Test stddev with known scores."""
        import math

        scores = [0.8, 0.9, 0.7, 0.9, 0.7]
        mean = sum(scores) / len(scores)
        stddev = math.sqrt(
            sum((s - mean) ** 2 for s in scores) / len(scores)
        )

        reports = []
        for score in scores:
            reports.append(
                GradingReport(
                    skill_name="test-skill",
                    results=[
                        GradingResult(
                            criterion="c1",
                            passed=True,
                            score=score,
                            evidence="e",
                            reasoning="r",
                        )
                    ],
                    model="claude-sonnet-4-6",
                )
            )

        vr = VarianceReport(
            skill_name="test-skill",
            n_runs=5,
            reports=reports,
            score_mean=mean,
            score_stddev=stddev,
            pass_rate_mean=1.0,
            stability=1.0,
            min_stability=0.8,
            model="claude-sonnet-4-6",
        )
        assert vr.score_mean == pytest.approx(0.8)
        assert vr.score_stddev == pytest.approx(math.sqrt(0.008))

    def test_passed_at_boundary(self):
        """Stability exactly at min_stability should pass."""
        vr = VarianceReport(
            skill_name="test",
            n_runs=5,
            reports=[],
            score_mean=0.8,
            score_stddev=0.1,
            pass_rate_mean=0.8,
            stability=0.8,
            min_stability=0.8,
        )
        assert vr.passed is True

    def test_summary_format(self):
        """Summary should include run count, mean score, stddev, stability, status."""
        vr = VarianceReport(
            skill_name="test-skill",
            n_runs=5,
            reports=[],
            score_mean=0.85,
            score_stddev=0.042,
            pass_rate_mean=0.9,
            stability=0.8,
            min_stability=0.8,
            model="claude-sonnet-4-6",
        )
        s = vr.summary()
        assert "5 runs" in s
        assert "0.85" in s
        assert "0.042" in s
        assert "80%" in s
        assert "PASS" in s

    def test_summary_fail(self):
        """Summary shows FAIL when stability below threshold."""
        vr = VarianceReport(
            skill_name="test-skill",
            n_runs=5,
            reports=[],
            score_mean=0.5,
            score_stddev=0.2,
            pass_rate_mean=0.5,
            stability=0.4,
            min_stability=0.8,
        )
        s = vr.summary()
        assert "FAIL" in s


class TestMeasureVariance:
    @pytest.mark.asyncio
    async def test_measure_variance_basic(self):
        """measure_variance runs skill N times and grades in parallel."""
        mock_report = GradingReport(
            skill_name="test-skill",
            results=[
                GradingResult(
                    criterion="Is clear",
                    passed=True,
                    score=0.9,
                    evidence="good",
                    reasoning="clear",
                )
            ],
            model="claude-sonnet-4-6",
        )

        mock_result = MagicMock()
        mock_result.output = "some output"
        mock_result.succeeded = True

        mock_spec = MagicMock()
        mock_spec.skill_name = "test-skill"
        mock_spec.run.return_value = mock_result
        mock_spec.eval_spec = EvalSpec(
            skill_name="test-skill",
            grading_criteria=["Is clear"],
            variance=VarianceConfig(n_runs=3, min_stability=0.8),
        )

        with patch(
            "clauditor.quality_grader.grade_quality",
            new_callable=AsyncMock,
            return_value=mock_report,
        ):
            vr = await measure_variance(mock_spec, n_runs=3)

        assert vr.skill_name == "test-skill"
        assert vr.n_runs == 3
        assert len(vr.reports) == 3
        assert vr.stability == 1.0
        assert vr.passed is True
        assert vr.score_mean == pytest.approx(0.9)
        assert vr.score_stddev == pytest.approx(0.0)
        assert vr.min_stability == 0.8
        assert mock_spec.run.call_count == 3

    @pytest.mark.asyncio
    async def test_measure_variance_default_min_stability(self):
        """Uses default 0.8 min_stability when variance config is None."""
        mock_report = GradingReport(
            skill_name="test-skill",
            results=[
                GradingResult(
                    criterion="A",
                    passed=True,
                    score=0.8,
                    evidence="e",
                    reasoning="r",
                )
            ],
            model="claude-sonnet-4-6",
        )

        mock_result = MagicMock()
        mock_result.output = "output"

        mock_spec = MagicMock()
        mock_spec.skill_name = "test-skill"
        mock_spec.run.return_value = mock_result
        mock_spec.eval_spec = EvalSpec(
            skill_name="test-skill",
            grading_criteria=["A"],
            variance=None,
        )

        with patch(
            "clauditor.quality_grader.grade_quality",
            new_callable=AsyncMock,
            return_value=mock_report,
        ):
            vr = await measure_variance(mock_spec, n_runs=2)

        assert vr.min_stability == 0.8

    @pytest.mark.asyncio
    async def test_measure_variance_no_eval_spec(self):
        """Raises ValueError when spec has no eval_spec."""
        mock_spec = MagicMock()
        mock_spec.skill_name = "test-skill"
        mock_spec.eval_spec = None

        with pytest.raises(ValueError, match="No eval spec found"):
            await measure_variance(mock_spec, n_runs=3)
