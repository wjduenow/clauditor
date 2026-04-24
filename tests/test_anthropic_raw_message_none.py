"""US-002 regression guard: raw_message=None must not AttributeError.

Bead ``clauditor-9a4.2`` (#86). In US-003 the CLI-transport branch of
``call_anthropic`` will return :class:`AnthropicResult` with
``raw_message=None`` (the subprocess output carries no SDK
``Message`` object). This test module is a defensive audit that
passes a ``raw_message=None`` result through every ``call_anthropic``
consumer and asserts none of them raises ``AttributeError``.

The audit grep (from the bead's acceptance criteria)::

    grep -rn "raw_message\\." src/clauditor/ \\
        | grep -v "raw_message is\\|raw_message =\\|raw_message:\\|raw_message,"

returns zero hits today: no caller currently dereferences
``raw_message.<attr>``. These tests are the regression guard that
locks that property in place so a future author cannot quietly add
a ``.raw_message.stop_reason`` access without tripping a failure.

Consumers covered (one test per caller, per the bead's acceptance
criteria):

- ``clauditor.quality_grader.grade_quality``
- ``clauditor.quality_grader.blind_compare``
- ``clauditor.grader.extract_and_grade``
- ``clauditor.grader.extract_and_report``
- ``clauditor.suggest.propose_edits``
- ``clauditor.propose_eval.propose_eval``
- ``clauditor.triggers.classify_query`` (the single-query seam
  ``test_triggers`` fans out over, same ``call_anthropic`` call path)

Each test mocks ``clauditor._anthropic.call_anthropic`` at the one
seam the target module imports it from, per
``.claude/rules/centralized-sdk-call.md``. No pytester, no
``runpytest_inprocess`` — this is a plain in-process coverage test
so the hazard from
``.claude/rules/pytester-inprocess-coverage-hazard.md`` does not
apply.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clauditor._anthropic import AnthropicResult
from clauditor.assertions import AssertionResult
from clauditor.grader import extract_and_grade, extract_and_report
from clauditor.propose_eval import ProposeEvalInput, propose_eval
from clauditor.quality_grader import (
    GradeThresholds,
    blind_compare,
    grade_quality,
)
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    SectionRequirement,
    TierRequirement,
)
from clauditor.suggest import SuggestInput, propose_edits
from clauditor.triggers import classify_query


def _anthropic_result_with_none_raw(
    text: str,
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> AnthropicResult:
    """Build the canonical raw_message=None result for these tests.

    Equivalent to the ``_mock_anthropic_result`` helpers scattered
    across the suite (``tests/test_suggest.py``,
    ``tests/test_triggers.py``, ``tests/test_propose_eval.py``), but
    lifted to module scope here because this file's whole purpose
    is auditing the ``raw_message=None`` branch — spelling the
    construction out at the call site would obscure the intent.
    """
    return AnthropicResult(
        response_text=text,
        text_blocks=[text] if text else [],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_message=None,
    )


class TestGradeQualityRawMessageNone:
    """quality_grader.grade_quality must tolerate raw_message=None."""

    @pytest.mark.asyncio
    async def test_grade_quality_does_not_access_raw_message(self) -> None:
        spec = EvalSpec(
            skill_name="test-skill",
            grading_criteria=["friendly tone"],
        )
        # Response shape matches parse_grading_response's positional
        # criterion-text alignment (DEC-001 / #25).
        response = json.dumps(
            [
                {
                    "criterion": "friendly tone",
                    "passed": True,
                    "score": 0.9,
                    "evidence": "hello there",
                    "reasoning": "warm greeting",
                }
            ]
        )
        fake_result = _anthropic_result_with_none_raw(response)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            # No AttributeError — the call completes and returns a
            # full report built from the fake_result's text_blocks /
            # token counts, never touching .raw_message.
            report = await grade_quality(
                "Hello there, friend!",
                spec,
                thresholds=GradeThresholds(),
            )
        assert report.skill_name == "test-skill"
        assert len(report.results) == 1
        assert report.results[0].passed is True


class TestBlindCompareRawMessageNone:
    """quality_grader.blind_compare must tolerate raw_message=None.

    Both parallel ``call_anthropic`` calls return raw_message=None;
    the asserter relies on the verdict-JSON in text_blocks only.
    """

    @pytest.mark.asyncio
    async def test_blind_compare_does_not_access_raw_message(self) -> None:
        response = json.dumps(
            {
                "preference": "1",
                "confidence": 0.8,
                "score_1": 0.8,
                "score_2": 0.4,
                "reasoning": "output 1 was clearer",
            }
        )
        fake_result = _anthropic_result_with_none_raw(response)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            report = await blind_compare(
                "summarize this",
                "output a text",
                "output b text",
            )
        # Preference translation worked even without raw_message;
        # both judge runs agree (same canned response) so the winner
        # resolves deterministically.
        assert report.preference in ("a", "b", "tie")
        assert report.model  # set by blind_compare


class TestExtractAndGradeRawMessageNone:
    """grader.extract_and_grade must tolerate raw_message=None."""

    @pytest.mark.asyncio
    async def test_extract_and_grade_does_not_access_raw_message(
        self,
    ) -> None:
        spec = EvalSpec(
            skill_name="test-skill",
            sections=[
                SectionRequirement(
                    name="Items",
                    tiers=[
                        TierRequirement(
                            label="default",
                            min_entries=1,
                            fields=[
                                FieldRequirement(name="name", required=True),
                            ],
                        ),
                    ],
                ),
            ],
        )
        extraction = json.dumps(
            {"Items": {"default": [{"name": "foo"}]}}
        )
        fake_result = _anthropic_result_with_none_raw(extraction)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            result = await extract_and_grade("some output", spec)
        assert result.passed


class TestExtractAndReportRawMessageNone:
    """grader.extract_and_report must tolerate raw_message=None.

    ``build_extraction_report`` (inside ``extract_and_report``) enforces
    a stable ``FieldRequirement.id`` per DEC-001 / #25, so the in-memory
    spec fixture here must supply one — the raw_message=None path is
    still exercised verbatim.
    """

    @pytest.mark.asyncio
    async def test_extract_and_report_does_not_access_raw_message(
        self,
    ) -> None:
        spec = EvalSpec(
            skill_name="test-skill",
            sections=[
                SectionRequirement(
                    name="Items",
                    tiers=[
                        TierRequirement(
                            label="default",
                            min_entries=1,
                            fields=[
                                FieldRequirement(
                                    name="name",
                                    required=True,
                                    id="items-name",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        extraction = json.dumps(
            {"Items": {"default": [{"name": "foo"}]}}
        )
        fake_result = _anthropic_result_with_none_raw(extraction)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            report = await extract_and_report(
                "some output",
                spec,
                skill_name="test-skill",
            )
        assert report.skill_name == "test-skill"
        assert not report.parse_errors


class TestProposeEditsRawMessageNone:
    """suggest.propose_edits must tolerate raw_message=None."""

    @pytest.mark.asyncio
    async def test_propose_edits_does_not_access_raw_message(self) -> None:
        suggest_input = SuggestInput(
            skill_name="find",
            source_iteration=3,
            source_grading_path=".clauditor/iteration-3/find/grading.json",
            skill_md_text="# Skill\n\nDo the thing.\n",
            failing_assertions=[
                AssertionResult(
                    id="a1",
                    name="needs-fence",
                    passed=False,
                    message="missing fence",
                    kind="presence",
                ),
            ],
        )
        envelope = json.dumps(
            {
                "summary_rationale": "tighten the prompt",
                "edits": [
                    {
                        "anchor": "Do the thing.",
                        "replacement": "Do the better thing.",
                        "rationale": "improves clarity",
                        "confidence": 0.8,
                        "motivated_by": ["a1"],
                    }
                ],
            }
        )
        fake_result = _anthropic_result_with_none_raw(envelope)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            report = await propose_edits(suggest_input)
        assert report.api_error is None
        assert report.parse_error is None
        assert len(report.edit_proposals) == 1


class TestProposeEvalRawMessageNone:
    """propose_eval.propose_eval must tolerate raw_message=None."""

    @pytest.mark.asyncio
    async def test_propose_eval_does_not_access_raw_message(
        self, tmp_path: Path
    ) -> None:
        propose_input = ProposeEvalInput(
            skill_name="greeter",
            skill_md_text="---\nname: greeter\n---\n# Greeter\n\nSay hello.\n",
            frontmatter={"name": "greeter"},
            skill_body="# Greeter\n\nSay hello.\n",
        )
        spec_dict = {
            "test_args": "hello world",
            "assertions": [
                {
                    "id": "greets-user",
                    "type": "contains",
                    "name": "greets the user",
                    "needle": "hello",
                }
            ],
            "grading_criteria": [
                {"id": "is-friendly", "criterion": "friendly tone"}
            ],
        }
        fake_result = _anthropic_result_with_none_raw(json.dumps(spec_dict))
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            report = await propose_eval(propose_input, spec_dir=tmp_path)
        assert report.api_error is None
        assert report.proposed_spec  # non-empty dict


class TestClassifyQueryRawMessageNone:
    """triggers.classify_query must tolerate raw_message=None.

    ``test_triggers`` fans out over ``classify_query`` via
    ``asyncio.gather``, so the per-query seam is the one that
    actually touches ``call_anthropic`` — covering it covers the
    test_triggers code path too.
    """

    @pytest.mark.asyncio
    async def test_classify_query_does_not_access_raw_message(
        self,
    ) -> None:
        response = json.dumps(
            {
                "triggered": True,
                "confidence": 0.9,
                "reasoning": "matches skill description",
            }
        )
        fake_result = _anthropic_result_with_none_raw(response)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=fake_result),
        ):
            result = await classify_query(
                skill_name="greeter",
                description="greets users",
                query="say hello",
                expected=True,
                model="claude-sonnet-4-6",
            )
        assert result.passed is True
        assert result.predicted_trigger is True
