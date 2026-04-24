"""Tests for Layer 3 quality grading (rubric-based Sonnet grading)."""

from __future__ import annotations

import json
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor.quality_grader import (
    BlindReport,
    GradingReport,
    GradingResult,
    VarianceReport,
    _build_blind_prompt_for_mapping,
    _pick_blind_mappings,
    _slots_for_mapping,
    _translate_blind_result,
    _validate_blind_inputs,
    blind_compare,
    build_blind_prompt,
    build_grading_prompt,
    build_grading_report,
    combine_blind_results,
    grade_quality,
    measure_variance,
    parse_blind_response,
    parse_grading_response,
)
from clauditor.schemas import EvalSpec, GradeThresholds, VarianceConfig, criterion_text
from clauditor.spec import SkillSpec


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
    def test_thresholds_and_metrics_required(self):
        """GradingReport() without thresholds/metrics raises TypeError (DEC-005)."""
        with pytest.raises(TypeError):
            GradingReport(
                skill_name="test",
                results=[],
                model="claude-sonnet-4-6",
            )

    def test_passed_all_true(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, True, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        assert report.passed is True

    def test_passed_one_false(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        assert report.passed is False

    def test_pass_rate_all_pass(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, True, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        assert report.pass_rate == pytest.approx(1.0)

    def test_pass_rate_partial(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        assert report.pass_rate == pytest.approx(2 / 3)

    def test_pass_rate_empty(self):
        report = GradingReport(
            skill_name="test", results=[], model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        assert report.pass_rate == 0.0

    def test_mean_score(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        # Two at 0.9, one at 0.2 => (0.9 + 0.2 + 0.9) / 3
        assert report.mean_score == pytest.approx(2.0 / 3)

    def test_mean_score_empty(self):
        report = GradingReport(
            skill_name="test", results=[], model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        assert report.mean_score == 0.0

    def test_summary_contains_counts(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        s = report.summary()
        assert "2/3 criteria passed" in s
        assert "67%" in s

    def test_summary_lists_each_criterion(self):
        report = GradingReport(
            skill_name="test",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        s = report.summary()
        assert "PASS: Output contains actionable" in s
        assert "FAIL: Tone is professional" in s


class TestGradingReportSerialization:
    def test_to_json_basic(self):
        report = GradingReport(
            skill_name="test-skill",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            duration_seconds=1.5,
            thresholds=GradeThresholds(),
            metrics={},
        )
        raw = report.to_json()
        data = json.loads(raw)
        assert data["skill_name"] == "test-skill"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["duration_seconds"] == 1.5
        assert len(data["results"]) == 3
        assert (
            data["results"][0]["criterion"]
            == "Output contains actionable recommendations"
        )
        assert data["results"][0]["passed"] is True
        assert "timestamp" in data

    def test_from_json_roundtrip(self):
        original = GradingReport(
            skill_name="roundtrip-skill",
            results=_make_results([True, False, True]),
            model="claude-sonnet-4-6",
            duration_seconds=2.3,
            thresholds=GradeThresholds(),
            metrics={},
        )
        raw = original.to_json()
        restored = GradingReport.from_json(raw)
        assert restored.skill_name == original.skill_name
        assert restored.model == original.model
        assert restored.duration_seconds == pytest.approx(original.duration_seconds)
        assert len(restored.results) == len(original.results)
        for orig_r, rest_r in zip(original.results, restored.results):
            assert rest_r.criterion == orig_r.criterion
            assert rest_r.passed == orig_r.passed
            assert rest_r.score == pytest.approx(orig_r.score)
            assert rest_r.evidence == orig_r.evidence
            assert rest_r.reasoning == orig_r.reasoning

    def test_from_json_preserves_floats(self):
        report = GradingReport(
            skill_name="float-test",
            results=[
                GradingResult(
                    criterion="precision",
                    passed=True,
                    score=0.8765,
                    evidence="e",
                    reasoning="r",
                )
            ],
            model="claude-sonnet-4-6",
            duration_seconds=3.14159,
            thresholds=GradeThresholds(),
            metrics={},
        )
        restored = GradingReport.from_json(report.to_json())
        assert restored.results[0].score == pytest.approx(0.8765)
        assert restored.duration_seconds == pytest.approx(3.14159)

    def test_from_json_handles_missing_duration(self):
        data = json.dumps({
            "skill_name": "no-duration",
            "model": "claude-sonnet-4-6",
            "results": [
                {
                    "criterion": "c1",
                    "passed": True,
                    "score": 0.9,
                    "evidence": "e",
                    "reasoning": "r",
                }
            ],
        })
        restored = GradingReport.from_json(data)
        assert restored.duration_seconds == 0.0

    def test_to_json_includes_timestamp(self):
        report = GradingReport(
            skill_name="ts-test",
            results=[],
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        raw = report.to_json()
        data = json.loads(raw)
        # ISO 8601 timestamp should contain a 'T' separator
        assert "T" in data["timestamp"]

    def test_thresholds_roundtrip(self):
        """Thresholds survive JSON round-trip."""
        thresholds = GradeThresholds(min_pass_rate=0.8, min_mean_score=0.6)
        original = GradingReport(
            skill_name="threshold-test",
            results=_make_results([True, False]),
            model="claude-sonnet-4-6",
            thresholds=thresholds,
            metrics={},
        )
        raw = original.to_json()
        data = json.loads(raw)
        assert data["thresholds"]["min_pass_rate"] == 0.8
        assert data["thresholds"]["min_mean_score"] == 0.6

        restored = GradingReport.from_json(raw)
        assert restored.thresholds is not None
        assert restored.thresholds.min_pass_rate == pytest.approx(0.8)
        assert restored.thresholds.min_mean_score == pytest.approx(0.6)
        assert restored.passed == original.passed

    def test_token_fields_roundtrip(self):
        """input_tokens/output_tokens survive JSON round-trip."""
        original = GradingReport(
            skill_name="tokens",
            results=_make_results([True]),
            model="claude-sonnet-4-6",
            input_tokens=500,
            output_tokens=200,
            thresholds=GradeThresholds(),
            metrics={},
        )
        raw = original.to_json()
        data = json.loads(raw)
        assert data["input_tokens"] == 500
        assert data["output_tokens"] == 200

        restored = GradingReport.from_json(raw)
        assert restored.input_tokens == 500
        assert restored.output_tokens == 200

    def test_token_fields_default_when_missing(self):
        """Missing token fields in legacy grade.json files default to 0."""
        data = json.dumps({
            "skill_name": "legacy",
            "model": "claude-sonnet-4-6",
            "results": [],
        })
        restored = GradingReport.from_json(data)
        assert restored.input_tokens == 0
        assert restored.output_tokens == 0

    def test_default_thresholds_roundtrip(self):
        """Default thresholds survive JSON round-trip."""
        original = GradingReport(
            skill_name="default-thresh",
            results=_make_results([True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        raw = original.to_json()
        data = json.loads(raw)
        assert data["thresholds"]["min_pass_rate"] == 0.7
        assert data["thresholds"]["min_mean_score"] == 0.5

        restored = GradingReport.from_json(raw)
        assert restored.thresholds.min_pass_rate == pytest.approx(0.7)
        assert restored.thresholds.min_mean_score == pytest.approx(0.5)


class TestGradingReportTransport:
    """US-006 (#86): GradingReport carries ``transport_source`` and
    ``schema_version=2``. Legacy v1 sidecars without ``transport_source``
    load with ``transport_source="api"`` defaulted."""

    def _report(self, **overrides) -> GradingReport:
        defaults = dict(
            skill_name="transport-test",
            results=_make_results([True]),
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            metrics={},
        )
        defaults.update(overrides)
        return GradingReport(**defaults)

    def test_default_transport_source_is_api(self):
        report = self._report()
        assert report.transport_source == "api"

    def test_to_json_schema_version_bumped_to_2(self):
        report = self._report(transport_source="cli")
        data = json.loads(report.to_json())
        assert data["schema_version"] == 2

    def test_schema_version_is_first_key(self):
        """Per ``.claude/rules/json-schema-version.md``, schema_version
        must be the first key in the serialized payload."""
        report = self._report(transport_source="cli")
        raw = report.to_json()
        data = json.loads(raw)
        assert next(iter(data)) == "schema_version"

    def test_to_json_includes_transport_source_cli(self):
        report = self._report(transport_source="cli")
        data = json.loads(report.to_json())
        assert data["transport_source"] == "cli"

    def test_to_json_includes_transport_source_api(self):
        report = self._report(transport_source="api")
        data = json.loads(report.to_json())
        assert data["transport_source"] == "api"

    def test_from_json_v1_defaults_transport_source_to_api(self):
        """Legacy v1 sidecars (no ``transport_source``) default to
        ``"api"`` so pre-#86 iterations load cleanly."""
        legacy_payload = json.dumps({
            "schema_version": 1,
            "skill_name": "legacy",
            "model": "claude-sonnet-4-6",
            "results": [],
        })
        restored = GradingReport.from_json(legacy_payload)
        assert restored.transport_source == "api"

    def test_from_json_v2_preserves_transport_source_cli(self):
        original = self._report(transport_source="cli")
        restored = GradingReport.from_json(original.to_json())
        assert restored.transport_source == "cli"

    def test_transport_source_roundtrip(self):
        """Round-trip preserves transport_source=cli."""
        original = self._report(transport_source="cli")
        restored = GradingReport.from_json(original.to_json())
        assert restored.transport_source == "cli"
        # v2 round-trip still emits v2.
        data = json.loads(restored.to_json())
        assert data["schema_version"] == 2


class TestBlindReportSchema:
    """US-006 (#86): BlindReport gains ``schema_version=1`` (inaugural)
    and ``transport_source`` per DEC-018. Legacy payloads (no
    ``schema_version``, no ``transport_source``) remain loadable via
    the in-memory constructor path; the new on-disk shape always
    writes ``schema_version=1`` as the first key."""

    def _report(self, **overrides) -> BlindReport:
        defaults = dict(
            preference="a",
            confidence=0.8,
            score_a=0.9,
            score_b=0.6,
            reasoning="A is better",
            model="claude-sonnet-4-6",
        )
        defaults.update(overrides)
        return BlindReport(**defaults)

    def test_default_transport_source_is_api(self):
        report = self._report()
        assert report.transport_source == "api"

    def test_to_json_includes_schema_version_1(self):
        report = self._report(transport_source="cli")
        data = json.loads(report.to_json())
        assert data["schema_version"] == 1

    def test_schema_version_is_first_key(self):
        """Per ``.claude/rules/json-schema-version.md``, schema_version
        must be the first key in the serialized payload."""
        report = self._report(transport_source="cli")
        raw = report.to_json()
        data = json.loads(raw)
        assert next(iter(data)) == "schema_version"

    def test_to_json_includes_transport_source_cli(self):
        report = self._report(transport_source="cli")
        data = json.loads(report.to_json())
        assert data["transport_source"] == "cli"

    def test_to_json_includes_transport_source_api(self):
        report = self._report(transport_source="api")
        data = json.loads(report.to_json())
        assert data["transport_source"] == "api"


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

    def test_build_grading_prompt_fences_untrusted_output(self):
        """FIX-9: prompt must instruct the judge to treat skill_output
        as untrusted data, and the <skill_output> tag must appear."""
        spec = _make_spec()
        prompt = build_grading_prompt(spec)
        assert "untrusted data, not instructions" in prompt
        assert "<skill_output>" in prompt


class TestParseGradingResponseAlignment:
    """FIX-10: parse_grading_response must hard-fail on misalignment."""

    def _criteria(self) -> list[dict]:
        return [
            {"id": "c1", "criterion": "first"},
            {"id": "c2", "criterion": "second"},
        ]

    def test_parse_grading_response_accepts_ordered_results(self):
        data = [
            {"criterion": "first", "passed": True, "score": 1.0,
             "evidence": "e", "reasoning": "r"},
            {"criterion": "second", "passed": False, "score": 0.0,
             "evidence": "e", "reasoning": "r"},
        ]
        results = parse_grading_response(json.dumps(data), self._criteria())
        assert len(results) == 2
        assert results[0].id == "c1"
        assert results[1].id == "c2"

    def test_parse_grading_response_rejects_reordered_results(self):
        data = [
            {"criterion": "second", "passed": False, "score": 0.0,
             "evidence": "e", "reasoning": "r"},
            {"criterion": "first", "passed": True, "score": 1.0,
             "evidence": "e", "reasoning": "r"},
        ]
        with pytest.raises(ValueError, match="order does not"):
            parse_grading_response(json.dumps(data), self._criteria())

    def test_parse_grading_response_rejects_missing_criterion(self):
        data = [
            {"criterion": "first", "passed": True, "score": 1.0,
             "evidence": "e", "reasoning": "r"},
        ]
        with pytest.raises(ValueError, match="result"):
            parse_grading_response(json.dumps(data), self._criteria())


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

        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [
            MagicMock(type="text", text=json.dumps(grading_data))
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

        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [
            MagicMock(type="text", text="I cannot parse this as valid output")
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
    async def test_grade_quality_handles_empty_content(self):
        """Copilot fix (PR #34): ``grade_quality`` must defensively unpack
        ``response.content`` the same way ``extract_and_report`` does.
        Empty content / non-text-first-block crashes are now a graceful
        failed report instead of an uncaught ``IndexError``."""
        spec = _make_spec()
        mock_response = MagicMock(
            usage=MagicMock(input_tokens=5, output_tokens=1)
        )
        mock_response.content = []
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)

        assert report.passed is False
        assert len(report.results) == 1
        assert report.results[0].criterion == "parse_response"
        assert (
            "no text" in report.results[0].reasoning.lower()
        )

    @pytest.mark.asyncio
    async def test_grade_quality_catches_parse_misalignment(self):
        """FIX-10 / PR #34: when ``parse_grading_response`` raises
        ValueError on criterion reorder/drop, ``grade_quality`` must
        catch it and return a graceful misalignment report (not crash)."""
        spec = _make_spec()
        # Return criteria in swapped order — triggers text-match
        # mismatch inside parse_grading_response, which raises ValueError.
        grading_data = [
            {
                "criterion": "Tone is professional and clear",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "All requested topics are covered",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
        ]
        mock_response = MagicMock(
            usage=MagicMock(input_tokens=1, output_tokens=1)
        )
        mock_response.content = [
            MagicMock(type="text", text=json.dumps(grading_data))
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)

        assert report.passed is False
        assert len(report.results) == 1
        assert report.results[0].criterion == "parse_response"
        assert "misalignment" in report.results[0].reasoning.lower()

    @pytest.mark.asyncio
    async def test_grade_quality_coerces_unparseable_score_to_zero(self):
        """Covers ``parse_grading_response`` ``score = float(item.get)``
        exception branch — non-numeric score is coerced to 0.0 instead of
        crashing."""
        spec = _make_spec()
        grading_data = [
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True,
                "score": "not a number",
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "Tone is professional and clear",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "All requested topics are covered",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
        ]
        mock_response = MagicMock(
            usage=MagicMock(input_tokens=1, output_tokens=1)
        )
        mock_response.content = [
            MagicMock(type="text", text=json.dumps(grading_data))
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)

        assert report.results[0].score == 0.0

    @pytest.mark.asyncio
    async def test_grade_quality_skips_non_text_blocks(self):
        """Non-text blocks (tool_use, refusal) before a text block must not
        be indexed as ``.text``. The defensive filter selects the first
        block with ``type == "text"``."""
        spec = _make_spec()
        grading_data = [
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "Tone is professional and clear",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "All requested topics are covered",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
        ]
        tool_use_block = MagicMock(spec=["type"])
        tool_use_block.type = "tool_use"
        text_block = MagicMock(type="text", text=json.dumps(grading_data))
        mock_response = MagicMock(
            usage=MagicMock(input_tokens=1, output_tokens=1)
        )
        mock_response.content = [tool_use_block, text_block]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)

        assert report.passed is True
        assert len(report.results) == len(spec.grading_criteria)

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
            {
                "criterion": "Tone is professional and clear",
                "passed": True,
                "score": 0.9,
                "evidence": "prose",
                "reasoning": "pro",
            },
            {
                "criterion": "All requested topics are covered",
                "passed": True,
                "score": 0.9,
                "evidence": "covered",
                "reasoning": "yes",
            },
        ]

        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [
            MagicMock(type="text", text=f"```json\n{json.dumps(grading_data)}\n```")
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await grade_quality("Some output", spec)

        assert len(report.results) == 3
        assert report.results[0].passed is True

    @pytest.mark.asyncio
    async def test_sends_output_in_message(self):
        spec = EvalSpec(
            skill_name="test-skill",
            description="A",
            grading_criteria=["A"],
        )
        grading_data = [
            {
                "criterion": "A",
                "passed": True,
                "score": 1.0,
                "evidence": "e",
                "reasoning": "r",
            },
        ]

        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [
            MagicMock(type="text", text=json.dumps(grading_data))
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

    @pytest.mark.asyncio
    async def test_captures_token_usage(self):
        """grade_quality propagates SDK usage into the GradingReport."""
        spec = _make_spec()
        grading_data = [
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True,
                "score": 0.9,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "Tone is professional and clear",
                "passed": True,
                "score": 0.9,
                "evidence": "e",
                "reasoning": "r",
            },
            {
                "criterion": "All requested topics are covered",
                "passed": True,
                "score": 0.9,
                "evidence": "e",
                "reasoning": "r",
            },
        ]
        mock_response = MagicMock(
            usage=MagicMock(input_tokens=500, output_tokens=200)
        )
        mock_response.content = [MagicMock(type="text", text=json.dumps(grading_data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("some output", spec)
        assert report.input_tokens == 500
        assert report.output_tokens == 200

    @pytest.mark.asyncio
    async def test_grade_quality_retries_on_malformed_json(self, capsys):
        """Parse retry (clauditor-6cf / #94): first call returns malformed
        JSON, second returns valid JSON → final report is the passing
        verdict, tokens sum across both attempts, stderr records the
        retry."""
        spec = _make_spec()
        good_data = [
            {
                "criterion": criterion_text(c), "passed": True,
                "score": 0.9, "evidence": "e", "reasoning": "r",
            }
            for c in spec.grading_criteria
        ]
        bad = MagicMock(usage=MagicMock(input_tokens=10, output_tokens=5))
        bad.content = [MagicMock(type="text", text="not json at all")]
        good = MagicMock(usage=MagicMock(input_tokens=100, output_tokens=50))
        good.content = [
            MagicMock(type="text", text=json.dumps(good_data))
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[bad, good])
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)
        assert report.passed is True
        assert len(report.results) == 3
        # Tokens accumulated across both attempts.
        assert report.input_tokens == 110
        assert report.output_tokens == 55
        # Stderr recorded the retry.
        captured = capsys.readouterr()
        assert "grade_quality" in captured.err
        assert "retrying" in captured.err.lower()

    @pytest.mark.asyncio
    async def test_grade_quality_no_retry_on_success(self):
        """Parse retry (clauditor-6cf / #94): a clean first response must
        NOT trigger a retry — only one API call is made."""
        spec = _make_spec()
        good_data = [
            {
                "criterion": criterion_text(c), "passed": True,
                "score": 0.9, "evidence": "e", "reasoning": "r",
            }
            for c in spec.grading_criteria
        ]
        good = MagicMock(usage=MagicMock(input_tokens=100, output_tokens=50))
        good.content = [
            MagicMock(type="text", text=json.dumps(good_data))
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=good)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            await grade_quality("output", spec)
        assert mock_client.messages.create.await_count == 1

    @pytest.mark.asyncio
    async def test_grade_quality_does_not_retry_on_alignment_failure(self):
        """Parse retry (clauditor-6cf / #94): alignment failures (wrong
        number of results) indicate a prompt-design bug and must NOT be
        retried. Regression guard so we don't waste API calls on a
        non-transient failure."""
        spec = _make_spec()  # 3 criteria
        # Only 1 result returned — raises ValueError in the verbose
        # parser, which the orchestrator treats as "don't retry".
        data = [
            {
                "criterion": "Output contains actionable recommendations",
                "passed": True, "score": 0.9,
                "evidence": "e", "reasoning": "r",
            }
        ]
        resp = MagicMock(usage=MagicMock(input_tokens=50, output_tokens=10))
        resp.content = [MagicMock(type="text", text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)
        # Exactly one API call despite the failure.
        assert mock_client.messages.create.await_count == 1
        assert report.results[0].criterion == "parse_response"
        assert "misalignment" in report.results[0].reasoning.lower()

    @pytest.mark.asyncio
    async def test_grade_quality_does_not_retry_on_shape_failure(self):
        """Parse retry (clauditor-6cf / #94, Copilot feedback on PR #98):
        a response that is valid JSON but has the wrong top-level type
        (e.g. a dict where a list was expected) is a model-protocol bug,
        not a transient hiccup — must NOT be retried. Mirrors
        ``_call_blind_side_with_retry``'s shape-vs-decode split."""
        spec = _make_spec()
        # Valid JSON, top-level dict instead of list → verbose parser
        # returns ``([], None)`` (shape failure, no decode error).
        resp = MagicMock(usage=MagicMock(input_tokens=50, output_tokens=10))
        resp.content = [
            MagicMock(type="text", text='{"not": "a list"}')
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)
        # Exactly one API call — shape failure must not retry.
        assert mock_client.messages.create.await_count == 1
        assert report.results[0].criterion == "parse_response"

    @pytest.mark.asyncio
    async def test_grade_quality_both_attempts_fail(self):
        """Parse retry (clauditor-6cf / #94): both attempts return
        malformed JSON → final failure report with cumulative tokens
        and the detailed parse-error reasoning from C."""
        spec = _make_spec()
        bad_a = MagicMock(usage=MagicMock(input_tokens=10, output_tokens=5))
        bad_a.content = [MagicMock(type="text", text="not json")]
        bad_b = MagicMock(usage=MagicMock(input_tokens=11, output_tokens=6))
        bad_b.content = [MagicMock(type="text", text="still not")]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[bad_a, bad_b])
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            report = await grade_quality("output", spec)
        assert report.passed is False
        assert report.results[0].criterion == "parse_response"
        assert report.input_tokens == 21
        assert report.output_tokens == 11
        # C: reasoning carries the JSONDecodeError details from the
        # LAST attempt, not a generic "Failed to parse" message.
        assert "at line" in report.results[0].reasoning
        assert "ends with" in report.results[0].reasoning


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
        skill_name="test-skill", results=results, model="claude-sonnet-4-6",
        thresholds=GradeThresholds(),
        metrics={},
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
                    thresholds=GradeThresholds(),
                    metrics={},
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
            thresholds=GradeThresholds(),
            metrics={},
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
            thresholds=GradeThresholds(),
            metrics={},
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
    async def test_measure_variance_aggregates_tokens(self):
        """Tokens across the internal grade_quality runs are summed."""
        mock_report = GradingReport(
            skill_name="test-skill",
            results=[
                GradingResult(
                    criterion="Is clear",
                    passed=True,
                    score=0.9,
                    evidence="e",
                    reasoning="r",
                )
            ],
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            thresholds=GradeThresholds(),
            metrics={},
        )

        # Use real attrs (not MagicMock defaults) so skill-side token
        # aggregation is actually verifiable — MagicMock would silently
        # return MagicMock instances for arithmetic and the test would
        # pass while storing nonsense.
        mock_result = MagicMock()
        mock_result.output = "some output"
        mock_result.succeeded = True
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_result.duration_seconds = 1.5

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

        # Grader (Layer 3) tokens aggregated across the 3 internal runs
        assert vr.input_tokens == 300
        assert vr.output_tokens == 150
        # Skill (subprocess) tokens aggregated across the 3 skill runs
        assert vr.skill_input_tokens == 30
        assert vr.skill_output_tokens == 15
        assert vr.skill_duration_seconds == pytest.approx(4.5)

    @pytest.mark.asyncio
    async def test_measure_variance_no_eval_spec(self):
        """Raises ValueError when spec has no eval_spec."""
        mock_spec = MagicMock()
        mock_spec.skill_name = "test-skill"
        mock_spec.eval_spec = None

        with pytest.raises(ValueError, match="No eval spec found"):
            await measure_variance(mock_spec, n_runs=3)


class TestGradeThresholdsOnReport:
    """Tests for GradeThresholds logic in GradingReport.passed."""

    def _make_report(
        self,
        passed_flags: list[bool],
        scores: list[float],
        thresholds: GradeThresholds | None = None,
    ) -> GradingReport:
        thresholds = thresholds if thresholds is not None else GradeThresholds()
        results = [
            GradingResult(
                criterion=f"c{i}",
                passed=p,
                score=s,
                evidence="e",
                reasoning="r",
            )
            for i, (p, s) in enumerate(zip(passed_flags, scores))
        ]
        return GradingReport(
            skill_name="test",
            results=results,
            model="claude-sonnet-4-6",
            thresholds=thresholds,
            metrics={},
        )

    def test_all_above_threshold_passes(self):
        """All criteria pass and scores are above threshold -> passed=True."""
        report = self._make_report(
            [True, True, True], [0.8, 0.9, 0.7],
            thresholds=GradeThresholds(min_pass_rate=0.7, min_mean_score=0.5),
        )
        assert report.pass_rate == pytest.approx(1.0)
        assert report.mean_score == pytest.approx(0.8)
        assert report.passed is True

    def test_pass_rate_below_threshold_fails(self):
        """pass_rate below min_pass_rate -> passed=False."""
        # 1 of 3 pass -> pass_rate = 0.333
        report = self._make_report(
            [True, False, False], [0.9, 0.6, 0.6],
            thresholds=GradeThresholds(min_pass_rate=0.7, min_mean_score=0.5),
        )
        assert report.pass_rate == pytest.approx(1 / 3)
        assert report.mean_score == pytest.approx(0.7)
        assert report.passed is False

    def test_mean_score_below_threshold_fails(self):
        """mean_score below min_mean_score -> passed=False."""
        # All pass but scores are low
        report = self._make_report(
            [True, True, True], [0.3, 0.4, 0.2],
            thresholds=GradeThresholds(min_pass_rate=0.7, min_mean_score=0.5),
        )
        assert report.pass_rate == pytest.approx(1.0)
        assert report.mean_score == pytest.approx(0.3)
        assert report.passed is False

    def test_no_thresholds_uses_defaults(self):
        """When thresholds is None, uses defaults (0.7/0.5)."""
        # 3/4 pass -> pass_rate = 0.75 >= 0.7, mean_score = 0.6 >= 0.5
        report = self._make_report(
            [True, True, True, False], [0.7, 0.7, 0.6, 0.4],
            thresholds=None,
        )
        assert report.pass_rate == pytest.approx(0.75)
        assert report.mean_score == pytest.approx(0.6)
        assert report.passed is True

    def test_custom_thresholds_from_eval_spec(self):
        """Custom strict thresholds cause failure even when defaults would pass."""
        # pass_rate=0.75, mean_score=0.6 — passes with defaults, fails with strict
        report = self._make_report(
            [True, True, True, False], [0.7, 0.7, 0.6, 0.4],
            thresholds=GradeThresholds(min_pass_rate=0.9, min_mean_score=0.8),
        )
        assert report.passed is False

    def test_at_exact_threshold_boundary(self):
        """Values exactly at thresholds should pass."""
        report = self._make_report(
            [True, True, True, False, False, False, True, True, True, True],
            [0.5] * 10,
            thresholds=GradeThresholds(min_pass_rate=0.7, min_mean_score=0.5),
        )
        assert report.pass_rate == pytest.approx(0.7)
        assert report.mean_score == pytest.approx(0.5)
        assert report.passed is True


class TestBlindReport:
    def test_blind_report_to_json_roundtrip(self):
        report = BlindReport(
            preference="a",
            confidence=0.82,
            score_a=0.9,
            score_b=0.6,
            reasoning="A is more concise and correct.",
            position_agreement=False,
            model="claude-sonnet-4-6",
            input_tokens=123,
            output_tokens=45,
            duration_seconds=1.25,
        )
        raw = report.to_json()
        data = json.loads(raw)
        assert data["preference"] == "a"
        assert data["confidence"] == pytest.approx(0.82)
        assert data["score_a"] == pytest.approx(0.9)
        assert data["score_b"] == pytest.approx(0.6)
        assert data["reasoning"] == "A is more concise and correct."
        assert data["position_agreement"] is False
        assert data["model"] == "claude-sonnet-4-6"
        assert data["input_tokens"] == 123
        assert data["output_tokens"] == 45
        assert data["duration_seconds"] == pytest.approx(1.25)

    def test_blind_report_defaults(self):
        report = BlindReport(
            preference="tie",
            confidence=0.5,
            score_a=0.7,
            score_b=0.7,
            reasoning="Equivalent quality.",
            model="claude-sonnet-4-6",
        )
        assert report.position_agreement is True
        assert report.input_tokens == 0
        assert report.output_tokens == 0
        assert report.duration_seconds == 0.0


class TestBuildBlindPrompt:
    def test_build_blind_prompt_includes_user_prompt(self):
        prompt = build_blind_prompt(
            "How do I center a div?", "Use flexbox.", "Use grid."
        )
        assert "How do I center a div?" in prompt

    def test_build_blind_prompt_labels_outputs_1_and_2(self):
        prompt = build_blind_prompt(
            "Q?", "first output text", "second output text"
        )
        assert "<response_1>" in prompt
        assert "</response_1>" in prompt
        assert "<response_2>" in prompt
        assert "</response_2>" in prompt
        lowered = prompt.lower()
        assert "output a" not in lowered
        assert "output b" not in lowered
        assert "option a" not in lowered
        assert "option b" not in lowered

    def test_build_blind_prompt_empty_string_hint_matches_none(self):
        a = build_blind_prompt("q", "o1", "o2", rubric_hint="")
        b = build_blind_prompt("q", "o1", "o2", rubric_hint=None)
        assert a == b

    def test_build_blind_prompt_no_rubric_hint_when_none(self):
        prompt = build_blind_prompt("Q?", "a-out", "b-out", rubric_hint=None)
        lowered = prompt.lower()
        assert "pay extra attention" not in lowered
        assert "extra attention" not in lowered
        assert "rubric hint" not in lowered

    def test_build_blind_prompt_injects_rubric_hint_when_given(self):
        prompt = build_blind_prompt(
            "Q?", "a-out", "b-out", rubric_hint="code clarity"
        )
        assert "code clarity" in prompt

    def test_build_blind_prompt_requests_json_schema(self):
        prompt = build_blind_prompt("Q?", "a-out", "b-out")
        for field in ("preference", "confidence", "score_1", "score_2", "reasoning"):
            assert field in prompt


def _blind_response(
    preference: str,
    confidence: float = 0.8,
    score_1: float = 0.7,
    score_2: float = 0.5,
    reasoning: str = "because",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    resp = MagicMock(
        usage=MagicMock(
            input_tokens=input_tokens, output_tokens=output_tokens
        )
    )
    # After bead clauditor-24h.3 the helper's _extract_result filters
    # content blocks by ``type == "text"``. Real Anthropic responses
    # always carry the type tag, so the fixture does too.
    resp.content = [
        MagicMock(
            type="text",
            text=json.dumps(
                {
                    "preference": preference,
                    "confidence": confidence,
                    "score_1": score_1,
                    "score_2": score_2,
                    "reasoning": reasoning,
                }
            ),
        )
    ]
    return resp


class TestBlindCompare:
    # Seed 42: random.Random(42).random() < 0.5 is False → run1_mapping
    # is "ab->21" → output_a lands in slot 2, output_b in slot 1.
    # Seed 1: random.Random(1).random() < 0.5 is True → "ab->12".

    @pytest.mark.asyncio
    async def test_blind_compare_rejects_empty_user_prompt(self):
        with pytest.raises(ValueError, match="user_prompt must be non-empty"):
            await blind_compare("", "a-out", "b-out")

    @pytest.mark.asyncio
    async def test_blind_compare_rejects_whitespace_user_prompt(self):
        with pytest.raises(ValueError, match="user_prompt must be non-empty"):
            await blind_compare("   \n", "a-out", "b-out")

    @pytest.mark.asyncio
    async def test_blind_compare_rejects_empty_output_a(self):
        with pytest.raises(ValueError, match="non-empty"):
            await blind_compare("q", "", "b-out")

    @pytest.mark.asyncio
    async def test_blind_compare_rejects_whitespace_output_b(self):
        with pytest.raises(ValueError, match="non-empty"):
            await blind_compare("q", "a-out", "   \n")

    @pytest.mark.asyncio
    async def test_blind_compare_agreement_picks_winner_a(self):
        # Seed 1 → run-1 is "ab->12" (a=slot1, b=slot2).
        # Run-1: judge says "1" → a wins.
        # Run-2 is "ab->21" (a=slot2, b=slot1). Judge says "2" → a wins.
        r1 = _blind_response("1", confidence=0.9, score_1=0.85, score_2=0.3)
        r2 = _blind_response("2", confidence=0.7, score_1=0.3, score_2=0.85)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "a"
        assert report.position_agreement is True
        assert report.confidence == pytest.approx(0.8)
        assert report.score_a == pytest.approx(0.85)
        assert report.score_b == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_blind_compare_agreement_picks_winner_b(self):
        # Seed 1 → run-1 mapping "ab->12"; judge says "2" → b wins.
        # Run-2 mapping "ab->21"; judge says "1" → b wins.
        r1 = _blind_response("2", confidence=0.8, score_1=0.4, score_2=0.9)
        r2 = _blind_response("1", confidence=0.6, score_1=0.9, score_2=0.4)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "b"
        assert report.position_agreement is True
        assert report.confidence == pytest.approx(0.7)
        assert report.score_b == pytest.approx(0.9)
        assert report.score_a == pytest.approx(0.4)

    @pytest.mark.asyncio
    async def test_blind_compare_disagreement_returns_tie(self):
        # Seed 1 → run-1 "ab->12", judge says "1" → a wins.
        # Run-2 "ab->21", judge says "2" → a wins again normally, so
        # to get disagreement we make run-2 say "1" (→ b wins).
        r1 = _blind_response("1", confidence=0.9, score_1=0.9, score_2=0.4)
        r2 = _blind_response("1", confidence=0.3, score_1=0.8, score_2=0.5)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "tie"
        assert report.position_agreement is False
        # min of confidences on disagreement
        assert report.confidence == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_blind_compare_disagreement_takes_min_confidence(self):
        # Explicit named test for the min-not-mean invariant on disagreement.
        # Seed 1 → run-1 "ab->12" says "1" → a wins (conf 0.9).
        # Run-2 "ab->21" says "1" → b wins (conf 0.7). Disagreement → min.
        r1 = _blind_response("1", confidence=0.9, score_1=0.9, score_2=0.4)
        r2 = _blind_response("1", confidence=0.7, score_1=0.8, score_2=0.5)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "tie"
        assert report.position_agreement is False
        assert report.confidence == pytest.approx(0.7)  # min(0.9, 0.7)

    @pytest.mark.asyncio
    async def test_blind_compare_explicit_tie_verdict(self):
        r1 = _blind_response("tie", confidence=0.5, score_1=0.7, score_2=0.7)
        r2 = _blind_response("tie", confidence=0.5, score_1=0.7, score_2=0.7)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "tie"
        assert report.position_agreement is True

    @pytest.mark.asyncio
    async def test_blind_compare_tracks_tokens_across_both_calls(self):
        r1 = _blind_response(
            "1", input_tokens=120, output_tokens=40
        )
        r2 = _blind_response(
            "2", input_tokens=130, output_tokens=55
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.input_tokens == 250
        assert report.output_tokens == 95

    @pytest.mark.asyncio
    async def test_blind_compare_tracks_duration(self):
        r1 = _blind_response("1")
        r2 = _blind_response("2")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ), patch(
            "clauditor.quality_grader._monotonic",
            side_effect=[0.0, 1.25],
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.duration_seconds == pytest.approx(1.25)

    @pytest.mark.asyncio
    async def test_blind_compare_malformed_json_returns_graceful_tie(self):
        # Parse-retry (clauditor-6cf / #94): each side retries once on
        # JSON decode failure, so 4 bad responses total (2 per side).
        bad1a = MagicMock(usage=MagicMock(input_tokens=10, output_tokens=5))
        bad1a.content = [MagicMock(type="text", text="not json at all")]
        bad1b = MagicMock(usage=MagicMock(input_tokens=11, output_tokens=6))
        bad1b.content = [MagicMock(type="text", text="still not json")]
        bad2a = MagicMock(usage=MagicMock(input_tokens=12, output_tokens=7))
        bad2a.content = [MagicMock(type="text", text="also not json")]
        bad2b = MagicMock(usage=MagicMock(input_tokens=13, output_tokens=8))
        bad2b.content = [MagicMock(type="text", text="nope still bad")]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[bad1a, bad2a, bad1b, bad2b]
        )
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "tie"
        assert report.confidence == 0.0
        assert report.position_agreement is False
        assert report.score_a == 0.0
        assert report.score_b == 0.0
        assert (
            "parse" in report.reasoning.lower()
            or "not json at all" in report.reasoning
            or "nope still bad" in report.reasoning
        )
        # Tokens accumulate across both sides and both retry attempts.
        assert report.input_tokens == 10 + 11 + 12 + 13
        assert report.output_tokens == 5 + 6 + 7 + 8

    @pytest.mark.asyncio
    async def test_blind_compare_seeded_rng_is_deterministic(self):
        # Seed 42 → run-1 mapping "ab->21" (a goes to slot 2).
        r1 = _blind_response("1")
        r2 = _blind_response("2")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await blind_compare(
                "Qtext",
                "A_UNIQUE",
                "B_UNIQUE",
                rng=random.Random(42),
            )
        first_prompt = mock_client.messages.create.call_args_list[0].kwargs[
            "messages"
        ][0]["content"]
        # Under seed 42, run-1 is "ab->21": a should appear after
        # "Response 2" and b after "Response 1".
        # rindex to skip over the warning sentence mention and find the
        # actual section tags (which appear last in the prompt).
        idx_r1 = first_prompt.rindex("<response_1>")
        idx_r2 = first_prompt.rindex("<response_2>")
        idx_a = first_prompt.index("A_UNIQUE")
        idx_b = first_prompt.index("B_UNIQUE")
        assert idx_r1 < idx_b < idx_r2 < idx_a

        # Re-run with a fresh seed 42 and assert same mapping.
        r1b = _blind_response("1")
        r2b = _blind_response("2")
        mock_client2 = AsyncMock()
        mock_client2.messages.create = AsyncMock(side_effect=[r1b, r2b])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client2,
        ):
            await blind_compare(
                "Qtext",
                "A_UNIQUE",
                "B_UNIQUE",
                rng=random.Random(42),
            )
        second_prompt = mock_client2.messages.create.call_args_list[0].kwargs[
            "messages"
        ][0]["content"]
        assert first_prompt == second_prompt

    @pytest.mark.asyncio
    async def test_blind_compare_uses_custom_model_arg(self):
        r1 = _blind_response("1")
        r2 = _blind_response("2")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await blind_compare(
                "q",
                "a-text",
                "b-text",
                model="claude-haiku-4-5-20251001",
                rng=random.Random(1),
            )
        for call in mock_client.messages.create.call_args_list:
            assert call.kwargs["model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_blind_compare_score_fields_translated_to_original_space(
        self,
    ):
        # Seed 42 → run-1 mapping "ab->21": slot 1 = b, slot 2 = a.
        # Run-1: score_1=0.9, score_2=0.3 → score_b=0.9, score_a=0.3.
        # Run-2 mapping "ab->12": slot 1 = a, slot 2 = b.
        # Run-2 symmetric: score_1=0.3, score_2=0.9 → score_a=0.3,
        # score_b=0.9. Means: score_a=0.3, score_b=0.9.
        # Winner: run-1 says "1" → b (under ab->21). Run-2 says "2" → b.
        r1 = _blind_response(
            "1", confidence=0.9, score_1=0.9, score_2=0.3
        )
        r2 = _blind_response(
            "2", confidence=0.9, score_1=0.3, score_2=0.9
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(42)
            )
        assert report.preference == "b"
        assert report.position_agreement is True
        assert report.score_a == pytest.approx(0.3)
        assert report.score_b == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_blind_compare_partial_parse_failure_keeps_good_run(self):
        # Seed 1 → run-1 "ab->12"; judge says "1" → a wins in run-1.
        # Run-2 returns garbage twice → parse retry exhausts; cannot
        # verify position agreement. See clauditor-6cf / #94.
        good = _blind_response(
            "1", confidence=0.9, score_1=0.85, score_2=0.3,
            input_tokens=100, output_tokens=40,
        )
        bad_a = MagicMock(usage=MagicMock(input_tokens=20, output_tokens=10))
        bad_a.content = [MagicMock(type="text", text="garbage not json")]
        bad_b = MagicMock(usage=MagicMock(input_tokens=21, output_tokens=11))
        bad_b.content = [MagicMock(type="text", text="garbage not json v2")]
        mock_client = AsyncMock()
        # Order: side1 attempt 1 (good), side2 attempt 1 (bad_a),
        # side2 attempt 2 (bad_b). Side1 parsed so no retry.
        mock_client.messages.create = AsyncMock(
            side_effect=[good, bad_a, bad_b]
        )
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "a"
        assert report.position_agreement is False
        assert report.score_a == pytest.approx(0.85)
        assert report.score_b == pytest.approx(0.3)
        assert report.confidence == pytest.approx(0.9)
        assert "parse" in report.reasoning.lower()
        assert "run-1" in report.reasoning
        assert report.input_tokens == 100 + 20 + 21
        assert report.output_tokens == 40 + 10 + 11

    @pytest.mark.asyncio
    async def test_blind_compare_partial_parse_run1_bad_keeps_run2(self):
        # Symmetric to test_blind_compare_partial_parse_failure_keeps_good_run:
        # run-1 is garbage (twice, retry exhausted), run-2 parses.
        # Verdict must come from run-2 and the reasoning must credit
        # run-2 (not "run-1"). Regression test for the hardcoded-run-
        # number bug flagged by CodeRabbit. Parse retry behavior tracked
        # in clauditor-6cf / #94.
        #
        # Use a prompt-content dispatcher: under seed 1, run-1's
        # mapping places a-text in <response_1>; run-2's mapping
        # places b-text in <response_1>. Route by this marker so the
        # test is independent of asyncio scheduling order.
        bad_a = MagicMock(usage=MagicMock(input_tokens=20, output_tokens=10))
        bad_a.content = [MagicMock(type="text", text="garbage not json")]
        # Seed 1 → run-2 "ab->21"; judge says "2" → a wins in original space.
        good = _blind_response(
            "2", confidence=0.8, score_1=0.4, score_2=0.85,
            input_tokens=100, output_tokens=40,
        )

        async def dispatcher(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            # run-1 mapping under seed 1 places a-text in response_1.
            r1_block = prompt.split("<response_1>", 1)[1].split(
                "</response_1>", 1
            )[0]
            if "a-text" in r1_block:
                return bad_a  # run-1: always garbage
            return good  # run-2: parses cleanly

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=dispatcher)
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.preference == "a"
        assert report.position_agreement is False
        assert "run-2" in report.reasoning
        assert "run-1" not in report.reasoning or report.reasoning.count("run-") == 1

    @pytest.mark.asyncio
    async def test_blind_compare_default_rng_works(self):
        r1 = _blind_response("1")
        r2 = _blind_response("2")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare("q", "a-text", "b-text")
        assert isinstance(report, BlindReport)

    @pytest.mark.asyncio
    async def test_blind_compare_malformed_score_fields_fall_back_to_zero(self):
        # Covers the translate() fallback branches for non-numeric confidence,
        # score_1, and score_2 — where the judge returns strings/null/etc.
        payload = json.dumps({
            "preference": "1",
            "confidence": "high",  # non-numeric → 0.0
            "score_1": None,        # None → 0.0
            "score_2": "lots",      # non-numeric → 0.0
            "reasoning": "broken floats",
        })
        r1 = MagicMock(usage=MagicMock(input_tokens=10, output_tokens=5))
        r1.content = [MagicMock(type="text", text=payload)]
        r2 = MagicMock(usage=MagicMock(input_tokens=10, output_tokens=5))
        r2.content = [MagicMock(type="text", text=payload)]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[r1, r2])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        assert report.confidence == 0.0
        assert report.score_a == 0.0
        assert report.score_b == 0.0

    @pytest.mark.asyncio
    async def test_blind_compare_empty_content_response(self):
        # Covers the text_of() branch where response.content is empty —
        # the function returns "", which then fails JSON parse → graceful tie.
        # Parse retry (clauditor-6cf / #94): run-1 side retries after the
        # empty response; run-2 side parses cleanly on first attempt.
        # Route responses by prompt content to avoid asyncio-scheduling
        # fragility.
        empty = MagicMock(usage=MagicMock(input_tokens=5, output_tokens=2))
        empty.content = []
        ok = _blind_response("1", confidence=0.8, score_1=0.9, score_2=0.3)

        async def dispatcher(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            r1_block = prompt.split("<response_1>", 1)[1].split(
                "</response_1>", 1
            )[0]
            # Seed 1, run-1 "ab->12": a-text in response_1 slot → empty.
            if "a-text" in r1_block:
                return empty
            return ok

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=dispatcher)
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            report = await blind_compare(
                "q", "a-text", "b-text", rng=random.Random(1)
            )
        # Partial parse failure path: run-2 is the good run.
        assert report.position_agreement is False
        assert "run-2" in report.reasoning


class TestParseBlindResponse:
    def _full(self, **overrides):
        data = {
            "preference": "1",
            "confidence": 0.8,
            "score_1": 0.7,
            "score_2": 0.5,
            "reasoning": "because",
        }
        data.update(overrides)
        return data

    def test_parses_plain_json(self):
        from clauditor.quality_grader import _parse_blind_response

        data = self._full()
        result = _parse_blind_response(json.dumps(data))
        assert result == data

    def test_parses_markdown_fenced_json(self):
        from clauditor.quality_grader import _parse_blind_response

        data = self._full()
        text = "```json\n" + json.dumps(data) + "\n```"
        result = _parse_blind_response(text)
        assert result == data

    def test_returns_none_on_non_dict(self):
        from clauditor.quality_grader import _parse_blind_response

        assert _parse_blind_response("[1, 2, 3]") is None
        assert _parse_blind_response('"a string"') is None

    def test_returns_none_on_missing_preference(self):
        from clauditor.quality_grader import _parse_blind_response

        data = self._full()
        del data["preference"]
        assert _parse_blind_response(json.dumps(data)) is None

    def test_returns_none_on_missing_score_1(self):
        from clauditor.quality_grader import _parse_blind_response

        data = self._full()
        del data["score_1"]
        assert _parse_blind_response(json.dumps(data)) is None

    def test_returns_none_on_missing_score_2(self):
        from clauditor.quality_grader import _parse_blind_response

        data = self._full()
        del data["score_2"]
        assert _parse_blind_response(json.dumps(data)) is None

    def test_returns_none_on_garbage_text(self):
        from clauditor.quality_grader import _parse_blind_response

        assert _parse_blind_response("not json at all") is None

    def test_accepts_extra_fields(self):
        from clauditor.quality_grader import _parse_blind_response

        data = self._full(extra="field", more=42)
        result = _parse_blind_response(json.dumps(data))
        assert result is not None
        assert result["extra"] == "field"

    def test_parses_bare_markdown_fence(self):
        # Covers the fallback branch when the fence has no 'json' language tag.
        from clauditor.quality_grader import _parse_blind_response

        data = self._full()
        text = "```\n" + json.dumps(data) + "\n```"
        result = _parse_blind_response(text)
        assert result == data


class TestBlindCompareFromSpec:
    """Tests for the pure blind_compare_from_spec helper (clauditor-5x5.1)."""

    def _canned_report(self) -> BlindReport:
        return BlindReport(
            preference="a",
            confidence=0.9,
            score_a=0.9,
            score_b=0.3,
            reasoning="canned",
            position_agreement=True,
            model="claude-sonnet-4-6",
            input_tokens=10,
            output_tokens=5,
            duration_seconds=0.1,
        )

    def _make_skill_spec(
        self,
        *,
        eval_spec: EvalSpec | None = None,
    ):
        return SkillSpec(skill_path=Path("dummy.md"), eval_spec=eval_spec)

    def _make_eval_spec(
        self,
        *,
        user_prompt: str | None = "What's the best sushi in Tokyo?",
        test_args: str = "",
        grading_criteria=None,
        grading_model: str = "claude-sonnet-4-6",
    ) -> EvalSpec:
        if grading_criteria is None:
            grading_criteria = [
                "Output contains actionable recommendations",
                "Tone is professional and clear",
            ]
        return EvalSpec(
            skill_name="test-skill",
            description="test",
            test_args=test_args,
            user_prompt=user_prompt,
            grading_criteria=grading_criteria,
            grading_model=grading_model,
        )

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_happy_path(self):
        from clauditor.quality_grader import blind_compare_from_spec

        eval_spec = self._make_eval_spec()
        spec = self._make_skill_spec(eval_spec=eval_spec)
        canned = self._canned_report()
        mock_bc = AsyncMock(return_value=canned)

        with patch("clauditor.quality_grader.blind_compare", mock_bc):
            result = await blind_compare_from_spec(spec, "A", "B")

        assert result is canned
        assert mock_bc.await_count == 1
        call = mock_bc.await_args
        # blind_compare(user_prompt, output_a, output_b, rubric_hint, *,
        # model=..., rng=...)
        assert call.args[0] == "What's the best sushi in Tokyo?"
        assert call.args[1] == "A"
        assert call.args[2] == "B"
        assert call.args[3] == (
            "- Output contains actionable recommendations\n"
            "- Tone is professional and clear"
        )
        assert call.kwargs["model"] == "claude-sonnet-4-6"
        assert call.kwargs["rng"] is None

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_model_override(self):
        from clauditor.quality_grader import blind_compare_from_spec

        eval_spec = self._make_eval_spec(grading_model="claude-sonnet-4-6")
        spec = self._make_skill_spec(eval_spec=eval_spec)
        mock_bc = AsyncMock(return_value=self._canned_report())

        with patch("clauditor.quality_grader.blind_compare", mock_bc):
            await blind_compare_from_spec(
                spec, "A", "B", model="claude-opus-4-6"
            )

        assert mock_bc.await_args.kwargs["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_raises_on_missing_eval_spec(self):
        from clauditor.quality_grader import blind_compare_from_spec

        spec = self._make_skill_spec(eval_spec=None)
        with pytest.raises(ValueError, match="No eval spec"):
            await blind_compare_from_spec(spec, "A", "B")

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_raises_on_missing_user_prompt(self):
        from clauditor.quality_grader import blind_compare_from_spec

        spec = self._make_skill_spec(
            eval_spec=self._make_eval_spec(user_prompt=None)
        )
        with pytest.raises(ValueError, match="user_prompt"):
            await blind_compare_from_spec(spec, "A", "B")

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_raises_on_whitespace_user_prompt(self):
        from clauditor.quality_grader import blind_compare_from_spec

        spec = self._make_skill_spec(
            eval_spec=self._make_eval_spec(user_prompt="   \n  ")
        )
        with pytest.raises(ValueError, match="user_prompt"):
            await blind_compare_from_spec(spec, "A", "B")

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_requires_user_prompt_even_if_test_args_set(
        self,
    ):
        """Core behavior change: test_args no longer substitutes for user_prompt.

        A spec with runner CLI args set but no user_prompt must still
        raise — the two fields now have distinct semantics.
        """
        from clauditor.quality_grader import blind_compare_from_spec

        spec = self._make_skill_spec(
            eval_spec=self._make_eval_spec(
                user_prompt=None, test_args="--depth quick --limit 10"
            )
        )
        with pytest.raises(ValueError, match="user_prompt"):
            await blind_compare_from_spec(spec, "A", "B")

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_forwards_user_prompt_not_test_args(
        self,
    ):
        """Forwards eval_spec.user_prompt (not test_args) to blind_compare."""
        from clauditor.quality_grader import blind_compare_from_spec

        eval_spec = self._make_eval_spec(
            user_prompt="Is sushi good?",
            test_args="--depth quick",
        )
        spec = self._make_skill_spec(eval_spec=eval_spec)
        mock_bc = AsyncMock(return_value=self._canned_report())

        with patch("clauditor.quality_grader.blind_compare", mock_bc):
            await blind_compare_from_spec(spec, "A", "B")

        assert mock_bc.await_args.args[0] == "Is sushi good?"
        assert mock_bc.await_args.args[0] != "--depth quick"

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_rejects_non_string_user_prompt(
        self,
    ):
        """In-memory construction cannot smuggle a non-string user_prompt
        past the validator — it raises ValueError, not AttributeError."""
        from clauditor.quality_grader import blind_compare_from_spec

        eval_spec = self._make_eval_spec(user_prompt=None)
        eval_spec.user_prompt = 42  # type: ignore[assignment]
        spec = self._make_skill_spec(eval_spec=eval_spec)
        with pytest.raises(ValueError, match="must be a string, got int"):
            await blind_compare_from_spec(spec, "A", "B")

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_no_criteria_passes_none_rubric(self):
        from clauditor.quality_grader import blind_compare_from_spec

        spec = self._make_skill_spec(
            eval_spec=self._make_eval_spec(grading_criteria=[])
        )
        mock_bc = AsyncMock(return_value=self._canned_report())

        with patch("clauditor.quality_grader.blind_compare", mock_bc):
            await blind_compare_from_spec(spec, "A", "B")

        # Positional rubric_hint arg (index 3) must be None, not "".
        assert mock_bc.await_args.args[3] is None

    @pytest.mark.asyncio
    async def test_blind_compare_from_spec_rng_pass_through(self):
        from clauditor.quality_grader import blind_compare_from_spec

        spec = self._make_skill_spec(eval_spec=self._make_eval_spec())
        rng = random.Random(42)
        mock_bc = AsyncMock(return_value=self._canned_report())

        with patch("clauditor.quality_grader.blind_compare", mock_bc):
            await blind_compare_from_spec(spec, "A", "B", rng=rng)

        assert mock_bc.await_args.kwargs["rng"] is rng

    def test_validate_blind_compare_spec_raises_on_missing_eval_spec(self):
        """validate_blind_compare_spec raises with a clear message when
        eval_spec is missing — same shape as blind_compare_from_spec, but
        sync and network-free so callers can fail-fast."""
        from clauditor.quality_grader import validate_blind_compare_spec

        spec = self._make_skill_spec(eval_spec=None)
        with pytest.raises(ValueError, match="No eval spec"):
            validate_blind_compare_spec(spec)

    def test_validate_blind_compare_spec_raises_on_missing_user_prompt(self):
        """validate_blind_compare_spec raises when user_prompt is missing/whitespace."""
        from clauditor.quality_grader import validate_blind_compare_spec

        spec = self._make_skill_spec(
            eval_spec=self._make_eval_spec(user_prompt=None)
        )
        with pytest.raises(ValueError, match="user_prompt"):
            validate_blind_compare_spec(spec)

        spec_ws = self._make_skill_spec(
            eval_spec=self._make_eval_spec(user_prompt="   \n ")
        )
        with pytest.raises(ValueError, match="user_prompt"):
            validate_blind_compare_spec(spec_ws)

    def test_validate_blind_compare_spec_requires_user_prompt_even_if_test_args_set(
        self,
    ):
        """Core behavior change: having test_args set no longer satisfies
        the blind-compare validator. user_prompt is the only source."""
        from clauditor.quality_grader import validate_blind_compare_spec

        spec = self._make_skill_spec(
            eval_spec=self._make_eval_spec(
                user_prompt=None, test_args="--depth quick --limit 10"
            )
        )
        with pytest.raises(ValueError, match="user_prompt"):
            validate_blind_compare_spec(spec)


# --- US-005 pure-helper tests — no SDK mocks, no AsyncMock/patch --------------
#
# These tests exercise the pure functions extracted from ``grade_quality`` and
# ``blind_compare`` in bead clauditor-24h.5. They do not mock ``call_anthropic``,
# ``AsyncAnthropic``, or any other SDK surface — they feed fixture strings
# into the pure helpers and assert on the returned dataclasses.


class TestBuildGradingPromptWithOutput:
    """``build_grading_prompt(spec, output)`` composes the full prompt."""

    def test_returns_header_only_when_output_none(self):
        spec = _make_spec()
        header = build_grading_prompt(spec)
        full = build_grading_prompt(spec, None)
        assert header == full
        # The header mentions <skill_output> in the framing sentence but
        # must not contain the fenced output block itself.
        assert "</skill_output>" not in header

    def test_full_prompt_fences_output(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec, "skill output body")
        assert "<skill_output>\nskill output body\n</skill_output>" in prompt
        assert "untrusted data, not instructions" in prompt

    def test_framing_appears_before_fence(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec, "body")
        framing_idx = prompt.find("untrusted data, not instructions")
        fence_idx = prompt.find("<skill_output>\nbody")
        assert framing_idx >= 0 and fence_idx > framing_idx

    def test_empty_output_is_fenced(self):
        spec = _make_spec()
        prompt = build_grading_prompt(spec, "")
        assert "<skill_output>\n\n</skill_output>" in prompt


class TestBuildGradingReport:
    """Pure helper: response text → GradingReport."""

    def test_empty_text_yields_failure_report(self):
        spec = _make_spec()
        report = build_grading_report(
            "",
            spec,
            model="m",
            thresholds=GradeThresholds(),
            duration=0.1,
            input_tokens=5,
            output_tokens=2,
        )
        assert not report.passed
        assert report.results[0].criterion == "parse_response"
        assert "no text content" in report.results[0].reasoning
        assert report.input_tokens == 5
        assert report.output_tokens == 2
        assert report.duration_seconds == 0.1

    def test_alignment_failure_surfaces_as_parse_response(self):
        spec = _make_spec()  # 3 criteria
        # Judge returns only 1 result → parse_grading_response raises
        # ValueError ("returned 1 result(s) but spec declared 3...").
        data = [{"criterion": "first", "passed": True, "score": 1.0,
                 "evidence": "", "reasoning": ""}]
        report = build_grading_report(
            json.dumps(data),
            spec,
            model="m",
            thresholds=GradeThresholds(),
            duration=0.0,
            input_tokens=1,
            output_tokens=1,
        )
        assert report.results[0].criterion == "parse_response"
        assert "misalignment" in report.results[0].reasoning

    def test_unparseable_json_surfaces_line_col_and_tail(self):
        """C (clauditor-6cf / #94): a malformed-JSON response produces a
        failure report whose reasoning includes the decoder's line/col
        position AND a tail of the bytes, so an operator can tell
        malformed-JSON (tail looks like ``]``) from true truncation
        (tail is mid-content). Regression guard against reverting to
        the generic "Failed to parse grader response as JSON" string."""
        spec = _make_spec()
        # Real-world failure pattern from the #94 repro: unescaped
        # double-quote inside an evidence string. The JSON array is
        # closed (ends with "]") so the tail should make that visible.
        bad_text = (
            '[{"criterion":"c","passed":true,"score":1.0,'
            '"evidence":"outer "inner" quote","reasoning":"r"}]'
        )
        report = build_grading_report(
            bad_text,
            spec,
            model="m",
            thresholds=GradeThresholds(),
            duration=0.0,
            input_tokens=1,
            output_tokens=1,
        )
        reasoning = report.results[0].reasoning
        assert report.results[0].criterion == "parse_response"
        assert "Failed to parse" in reasoning
        assert "at line" in reasoning
        assert "col" in reasoning
        # Tail is rendered so the reader can distinguish truncation
        # from malformed-but-complete JSON.
        assert "ends with" in reasoning
        # The closing bracket should be visible in the tail — proof the
        # response was NOT truncated.
        assert "]" in reasoning

    def test_unparseable_json_yields_failure_report(self):
        spec = _make_spec()
        report = build_grading_report(
            "not valid json",
            spec,
            model="m",
            thresholds=GradeThresholds(),
            duration=0.0,
            input_tokens=1,
            output_tokens=1,
        )
        assert report.results[0].criterion == "parse_response"
        assert "Failed to parse" in report.results[0].reasoning
        assert report.results[0].evidence == "not valid json"

    def test_happy_path_produces_passing_report(self):
        spec = _make_spec()
        data = [
            {
                "criterion": criterion_text(c),
                "passed": True,
                "score": 0.9,
                "evidence": "e",
                "reasoning": "r",
            }
            for c in spec.grading_criteria
        ]
        report = build_grading_report(
            json.dumps(data),
            spec,
            model="claude-sonnet-4-6",
            thresholds=GradeThresholds(),
            duration=0.25,
            input_tokens=100,
            output_tokens=50,
        )
        assert report.passed
        assert report.duration_seconds == 0.25
        assert report.input_tokens == 100
        assert len(report.results) == 3


class TestParseBlindResponsePublic:
    """The public ``parse_blind_response`` name (US-005 rename)."""

    def test_public_alias_parses_same_as_legacy(self):
        # The legacy _parse_blind_response alias must still exist and
        # return the same values as the public name.
        from clauditor.quality_grader import _parse_blind_response

        text = json.dumps({
            "preference": "1",
            "confidence": 0.8,
            "score_1": 0.7,
            "score_2": 0.5,
            "reasoning": "r",
        })
        assert parse_blind_response(text) == _parse_blind_response(text)


class TestValidateBlindInputs:
    """Pure guard: ``_validate_blind_inputs``."""

    def test_all_non_empty_is_ok(self):
        _validate_blind_inputs("q", "a", "b")  # no raise

    def test_empty_user_prompt_raises(self):
        with pytest.raises(ValueError, match="user_prompt"):
            _validate_blind_inputs("", "a", "b")

    def test_whitespace_user_prompt_raises(self):
        with pytest.raises(ValueError, match="user_prompt"):
            _validate_blind_inputs("   \n", "a", "b")

    def test_empty_output_a_raises(self):
        with pytest.raises(ValueError, match="output_a"):
            _validate_blind_inputs("q", "", "b")

    def test_whitespace_output_b_raises(self):
        with pytest.raises(ValueError, match="output_b"):
            _validate_blind_inputs("q", "a", "  \n ")


class TestPickBlindMappings:
    """Pure: ``_pick_blind_mappings`` picks a pair of distinct mappings."""

    def test_mappings_always_differ(self):
        for seed in range(10):
            m1, m2 = _pick_blind_mappings(random.Random(seed))
            assert m1 != m2
            assert {m1, m2} == {"ab->12", "ab->21"}

    def test_deterministic_with_seed(self):
        # Seed 1 → random() < 0.5 True → m1="ab->12".
        m1, m2 = _pick_blind_mappings(random.Random(1))
        assert m1 == "ab->12"
        assert m2 == "ab->21"

    def test_seed_42_yields_opposite(self):
        # Seed 42 → random() < 0.5 False → m1="ab->21".
        m1, m2 = _pick_blind_mappings(random.Random(42))
        assert m1 == "ab->21"
        assert m2 == "ab->12"

    def test_none_rng_works(self):
        m1, m2 = _pick_blind_mappings(None)
        assert {m1, m2} == {"ab->12", "ab->21"}


class TestSlotsForMapping:
    """Pure: ``_slots_for_mapping``."""

    def test_ab_to_12_assigns_a_first(self):
        assert _slots_for_mapping("ab->12", "A", "B") == ("A", "B")

    def test_ab_to_21_swaps(self):
        assert _slots_for_mapping("ab->21", "A", "B") == ("B", "A")


class TestBuildBlindPromptForMapping:
    """Pure: ``_build_blind_prompt_for_mapping``."""

    def test_ab_to_12_puts_a_in_response_1(self):
        prompt = _build_blind_prompt_for_mapping(
            "ab->12", "Q?", "AAA", "BBB", None
        )
        # The prompt header mentions <response_1>/<response_2> in its
        # framing sentence, so split on the fenced variants that actually
        # carry the payload.
        r1 = prompt.split("<response_1>\n")[1].split("\n</response_1>")[0]
        r2 = prompt.split("<response_2>\n")[1].split("\n</response_2>")[0]
        assert r1 == "AAA"
        assert r2 == "BBB"

    def test_ab_to_21_swaps(self):
        prompt = _build_blind_prompt_for_mapping(
            "ab->21", "Q?", "AAA", "BBB", None
        )
        r1 = prompt.split("<response_1>\n")[1].split("\n</response_1>")[0]
        r2 = prompt.split("<response_2>\n")[1].split("\n</response_2>")[0]
        assert r1 == "BBB"
        assert r2 == "AAA"

    def test_rubric_hint_flows_through(self):
        prompt = _build_blind_prompt_for_mapping(
            "ab->12", "Q?", "A", "B", "focus on clarity"
        )
        assert "focus on clarity" in prompt


class TestTranslateBlindResult:
    """Pure: ``_translate_blind_result``."""

    def _parsed(self, **overrides):
        data = {
            "preference": "1",
            "confidence": 0.8,
            "score_1": 0.7,
            "score_2": 0.5,
            "reasoning": "r",
        }
        data.update(overrides)
        return data

    def test_ab_to_12_pref_1_is_a(self):
        winner, conf, sa, sb, reason = _translate_blind_result(
            self._parsed(preference="1"), "ab->12"
        )
        assert winner == "a"
        assert sa == pytest.approx(0.7)
        assert sb == pytest.approx(0.5)
        assert conf == pytest.approx(0.8)
        assert reason == "r"

    def test_ab_to_12_pref_2_is_b(self):
        w, _, _, _, _ = _translate_blind_result(
            self._parsed(preference="2"), "ab->12"
        )
        assert w == "b"

    def test_ab_to_12_tie_stays_tie(self):
        w, _, _, _, _ = _translate_blind_result(
            self._parsed(preference="tie"), "ab->12"
        )
        assert w == "tie"

    def test_ab_to_21_pref_1_is_b(self):
        # Slot 1 is b when mapping flips.
        w, _, sa, sb, _ = _translate_blind_result(
            self._parsed(preference="1"), "ab->21"
        )
        assert w == "b"
        # score_1 in the response maps to b; score_2 to a.
        assert sb == pytest.approx(0.7)
        assert sa == pytest.approx(0.5)

    def test_ab_to_21_pref_2_is_a(self):
        w, _, _, _, _ = _translate_blind_result(
            self._parsed(preference="2"), "ab->21"
        )
        assert w == "a"

    def test_ab_to_21_tie_stays_tie(self):
        w, _, _, _, _ = _translate_blind_result(
            self._parsed(preference="tie"), "ab->21"
        )
        assert w == "tie"

    def test_non_float_scores_coerced_to_zero(self):
        parsed = self._parsed(
            confidence="high", score_1="a lot", score_2=None
        )
        _, conf, sa, sb, _ = _translate_blind_result(parsed, "ab->12")
        assert conf == 0.0
        assert sa == 0.0
        assert sb == 0.0


class TestCombineBlindResults:
    """Pure: ``combine_blind_results`` combines two parsed verdicts."""

    def _parsed(self, preference="1", conf=0.8, s1=0.7, s2=0.5, reason="r"):
        return {
            "preference": preference,
            "confidence": conf,
            "score_1": s1,
            "score_2": s2,
            "reasoning": reason,
        }

    def test_both_fail_returns_tie(self):
        report = combine_blind_results(
            parsed1=None,
            parsed2=None,
            text1="garbage1",
            text2="garbage2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=10,
            output_tokens=5,
            duration_seconds=1.0,
        )
        assert report.preference == "tie"
        assert report.position_agreement is False
        assert report.confidence == 0.0
        assert report.score_a == 0.0
        assert report.score_b == 0.0
        assert "garbage1" in report.reasoning
        assert "garbage2" in report.reasoning
        assert report.input_tokens == 10
        assert report.duration_seconds == 1.0

    def test_only_first_parsed_uses_run1_verdict(self):
        report = combine_blind_results(
            parsed1=self._parsed(preference="1"),
            parsed2=None,
            text1="ok",
            text2="garbage",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        assert report.preference == "a"
        assert report.position_agreement is False
        assert "run-1" in report.reasoning

    def test_only_second_parsed_uses_run2_verdict(self):
        report = combine_blind_results(
            parsed1=None,
            parsed2=self._parsed(preference="1"),
            text1="garbage",
            text2="ok",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        # Run-2 mapping "ab->21" + preference "1" → winner "b".
        assert report.preference == "b"
        assert report.position_agreement is False
        assert "run-2" in report.reasoning

    def test_agreement_averages_confidence(self):
        # Run-1 "ab->12" pref "1" → a. Run-2 "ab->21" pref "2" → a.
        report = combine_blind_results(
            parsed1=self._parsed(preference="1", conf=0.9, s1=0.85, s2=0.3),
            parsed2=self._parsed(preference="2", conf=0.7, s1=0.3, s2=0.85),
            text1="t1",
            text2="t2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        assert report.preference == "a"
        assert report.position_agreement is True
        assert report.confidence == pytest.approx(0.8)
        # score_a averaged: (0.85 + 0.85) / 2
        assert report.score_a == pytest.approx(0.85)

    def test_disagreement_returns_tie_with_min_confidence(self):
        # Run-1 "ab->12" pref "1" → a. Run-2 "ab->21" pref "1" → b.
        report = combine_blind_results(
            parsed1=self._parsed(preference="1", conf=0.9),
            parsed2=self._parsed(preference="1", conf=0.7),
            text1="t1",
            text2="t2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        assert report.preference == "tie"
        assert report.position_agreement is False
        assert report.confidence == pytest.approx(0.7)  # min
        assert "Position disagreement" in report.reasoning

    def test_tokens_and_duration_threaded_through(self):
        report = combine_blind_results(
            parsed1=self._parsed(),
            parsed2=self._parsed(),
            text1="t1",
            text2="t2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="claude-sonnet-4-6",
            input_tokens=1234,
            output_tokens=567,
            duration_seconds=3.14,
        )
        assert report.input_tokens == 1234
        assert report.output_tokens == 567
        assert report.duration_seconds == pytest.approx(3.14)
        assert report.model == "claude-sonnet-4-6"

    def test_transport_source_default_api(self):
        """Default ``transport_source`` is ``"api"``."""
        report = combine_blind_results(
            parsed1=self._parsed(),
            parsed2=self._parsed(),
            text1="t1",
            text2="t2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        assert report.transport_source == "api"

    def test_transport_source_mixed_is_propagated(self):
        """``transport_source="mixed"`` stamps the BlindReport (DEC-018)."""
        report = combine_blind_results(
            parsed1=self._parsed(),
            parsed2=self._parsed(),
            text1="t1",
            text2="t2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
            transport_source="mixed",
        )
        assert report.transport_source == "mixed"

    def test_transport_source_cli_is_propagated(self):
        """``transport_source="cli"`` stamps the BlindReport."""
        report = combine_blind_results(
            parsed1=self._parsed(),
            parsed2=self._parsed(),
            text1="t1",
            text2="t2",
            run1_mapping="ab->12",
            run2_mapping="ab->21",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
            transport_source="cli",
        )
        assert report.transport_source == "cli"


