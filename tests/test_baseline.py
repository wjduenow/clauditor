"""Tests for ``clauditor.baseline`` — pure baseline compute.

Covers the compute_baseline pure function and the BaselineReports
serialization to JSON sidecars. No tmp_path, no subprocess — pure
unit tests that construct fixtures and assert on return values.

Traces: DEC-006.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.baseline import _SCHEMA_VERSION, BaselineReports, compute_baseline
from clauditor.grader import ExtractionReport
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult
from clauditor.schemas import EvalSpec, GradeThresholds


def _make_skill_result(
    *,
    output: str = "baseline output",
    duration: float = 5.0,
    input_tokens: int = 200,
    output_tokens: int = 100,
) -> SkillResult:
    return SkillResult(
        output=output,
        exit_code=0,
        skill_name="test-skill",
        args="test args",
        duration_seconds=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_grading_report(
    *,
    skill_name: str = "test-skill",
    pass_fractions: tuple[bool, ...] = (True, True, False),
) -> GradingReport:
    results = [
        GradingResult(
            criterion=f"c{i}",
            passed=p,
            score=1.0 if p else 0.0,
            evidence="",
            reasoning="",
            id=f"c{i}",
        )
        for i, p in enumerate(pass_fractions)
    ]
    return GradingReport(
        skill_name=skill_name,
        results=results,
        model="test-model",
        duration_seconds=1.0,
        input_tokens=100,
        output_tokens=50,
        thresholds=GradeThresholds(),
        metrics={},
    )


def _make_eval_spec(*, with_sections: bool = False) -> EvalSpec:
    spec = EvalSpec(
        skill_name="test-skill",
        test_args="test args",
        assertions=[
            {"id": "a1", "type": "contains", "value": "baseline"},
        ],
        grading_criteria=[
            {"id": "c0", "criterion": "c0"},
            {"id": "c1", "criterion": "c1"},
            {"id": "c2", "criterion": "c2"},
        ],
    )
    return spec


class TestBaselineReportsToJsonMap:
    """Verify the to_json_map serialization contract."""

    def test_always_emits_four_files_without_sections(self) -> None:
        reports = BaselineReports(
            skill_name="test-skill",
            iteration=1,
            skill_result=_make_skill_result(),
            grading_report=_make_grading_report(),
            assertion_set=AssertionSet(results=[]),
            extraction_report=None,
        )
        files = reports.to_json_map()
        assert set(files.keys()) == {
            "baseline.json",
            "baseline_assertions.json",
            "baseline_grading.json",
        }

    def test_includes_extraction_when_present(self) -> None:
        extraction = ExtractionReport(
            skill_name="test-skill",
            results=[],
            model="test-model",
            input_tokens=10,
            output_tokens=5,
        )
        reports = BaselineReports(
            skill_name="test-skill",
            iteration=1,
            skill_result=_make_skill_result(),
            grading_report=_make_grading_report(),
            assertion_set=AssertionSet(results=[]),
            extraction_report=extraction,
        )
        files = reports.to_json_map()
        assert "baseline_extraction.json" in files

    def test_schema_version_is_first_key_in_meta(self) -> None:
        reports = BaselineReports(
            skill_name="test-skill",
            iteration=1,
            skill_result=_make_skill_result(),
            grading_report=_make_grading_report(),
            assertion_set=AssertionSet(results=[]),
            extraction_report=None,
        )
        files = reports.to_json_map()
        meta = json.loads(files["baseline.json"])
        assert list(meta.keys())[0] == "schema_version"
        assert meta["schema_version"] == _SCHEMA_VERSION

    def test_schema_version_is_first_key_in_assertions(self) -> None:
        reports = BaselineReports(
            skill_name="test-skill",
            iteration=1,
            skill_result=_make_skill_result(),
            grading_report=_make_grading_report(),
            assertion_set=AssertionSet(results=[]),
            extraction_report=None,
        )
        files = reports.to_json_map()
        assertions = json.loads(files["baseline_assertions.json"])
        assert list(assertions.keys())[0] == "schema_version"
        assert assertions["schema_version"] == _SCHEMA_VERSION

    def test_meta_contains_run_fields(self) -> None:
        sr = _make_skill_result(
            output="hello",
            duration=3.5,
            input_tokens=42,
            output_tokens=17,
        )
        reports = BaselineReports(
            skill_name="my-skill",
            iteration=7,
            skill_result=sr,
            grading_report=_make_grading_report(skill_name="my-skill"),
            assertion_set=AssertionSet(results=[]),
            extraction_report=None,
        )
        meta = json.loads(reports.to_json_map()["baseline.json"])
        assert meta["skill"] == "my-skill"
        assert meta["iteration"] == 7
        assert meta["output"] == "hello"
        assert meta["exit_code"] == 0
        assert meta["input_tokens"] == 42
        assert meta["output_tokens"] == 17
        assert meta["duration_seconds"] == 3.5

    def test_assertions_payload_merges_set(self) -> None:
        aset = AssertionSet(
            results=[
                AssertionResult(
                    id="a1",
                    name="check",
                    passed=True,
                    message="ok",
                    kind="contains",
                )
            ],
            input_tokens=5,
            output_tokens=3,
        )
        reports = BaselineReports(
            skill_name="test-skill",
            iteration=1,
            skill_result=_make_skill_result(),
            grading_report=_make_grading_report(),
            assertion_set=aset,
            extraction_report=None,
        )
        payload = json.loads(reports.to_json_map()["baseline_assertions.json"])
        assert payload["skill"] == "test-skill"
        assert payload["iteration"] == 1
        assert len(payload["results"]) == 1
        assert payload["results"][0]["id"] == "a1"


class TestComputeBaseline:
    """Verify the pure compute_baseline function.

    compute_baseline is sync (uses asyncio.run internally), so these
    tests are plain sync methods. The async LLM calls are patched with
    AsyncMock which asyncio.run handles correctly.
    """

    def test_computes_all_layers_without_sections(self) -> None:
        sr = _make_skill_result(output="baseline output")
        eval_spec = _make_eval_spec()
        grading_report = _make_grading_report()

        with patch(
            "clauditor.quality_grader.grade_quality",
            new=AsyncMock(return_value=grading_report),
        ):
            reports = compute_baseline(
                skill_result=sr,
                eval_spec=eval_spec,
                skill_name="test-skill",
                iteration=1,
                model="test-model",
            )

        assert reports.grading_report is grading_report
        assert reports.skill_result is sr
        assert reports.extraction_report is None
        # L1 assertions ran — "baseline" is in "baseline output"
        assert len(reports.assertion_set.results) == 1
        assert reports.assertion_set.results[0].passed is True

    def test_computes_extraction_when_sections_present(self) -> None:
        sr = _make_skill_result(output="some output")
        eval_spec = _make_eval_spec()
        from clauditor.schemas import (
            FieldRequirement,
            SectionRequirement,
            TierRequirement,
        )

        eval_spec.sections = [
            SectionRequirement(
                name="sec1",
                tiers=[
                    TierRequirement(
                        label="tier1",
                        fields=[FieldRequirement(id="f1", name="field1")],
                    )
                ],
            )
        ]
        grading_report = _make_grading_report()
        extraction_report = ExtractionReport(
            skill_name="test-skill",
            results=[],
            model="test-model",
            input_tokens=10,
            output_tokens=5,
        )

        with (
            patch(
                "clauditor.quality_grader.grade_quality",
                new=AsyncMock(return_value=grading_report),
            ),
            patch(
                "clauditor.grader.extract_and_report",
                new=AsyncMock(return_value=extraction_report),
            ),
        ):
            reports = compute_baseline(
                skill_result=sr,
                eval_spec=eval_spec,
                skill_name="test-skill",
                iteration=1,
                model="test-model",
            )

        assert reports.extraction_report is extraction_report

    def test_passes_model_and_thresholds_to_grade_quality(self) -> None:
        sr = _make_skill_result(output="text")
        eval_spec = _make_eval_spec()
        grading_report = _make_grading_report()
        mock_grade = AsyncMock(return_value=grading_report)

        with patch("clauditor.quality_grader.grade_quality", new=mock_grade):
            compute_baseline(
                skill_result=sr,
                eval_spec=eval_spec,
                skill_name="test-skill",
                iteration=2,
                model="claude-sonnet-4-20250514",
            )

        mock_grade.assert_called_once_with(
            "text",
            eval_spec,
            "claude-sonnet-4-20250514",
            thresholds=eval_spec.grade_thresholds,
        )

    def test_skill_name_and_iteration_propagated(self) -> None:
        sr = _make_skill_result()
        eval_spec = _make_eval_spec()
        grading_report = _make_grading_report()

        with patch(
            "clauditor.quality_grader.grade_quality",
            new=AsyncMock(return_value=grading_report),
        ):
            reports = compute_baseline(
                skill_result=sr,
                eval_spec=eval_spec,
                skill_name="my-skill",
                iteration=42,
                model="test-model",
            )

        assert reports.skill_name == "my-skill"
        assert reports.iteration == 42
