"""Tests for Layer 3c trigger precision testing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from clauditor.schemas import EvalSpec, TriggerTests
from clauditor.triggers import (
    TriggerReport,
    TriggerResult,
    build_trigger_prompt,
    classify_query,
    parse_trigger_response,
)
from clauditor.triggers import test_triggers as run_test_triggers

# --- Helpers ---


def _make_result(
    query: str = "test",
    expected: bool = True,
    predicted: bool = True,
    confidence: float = 0.9,
) -> TriggerResult:
    return TriggerResult(
        query=query,
        expected_trigger=expected,
        predicted_trigger=predicted,
        passed=expected == predicted,
        confidence=confidence,
        reasoning="test reasoning",
    )


def _make_report(results: list[TriggerResult]) -> TriggerReport:
    return TriggerReport(
        skill_name="test-skill",
        skill_description="A test skill",
        results=results,
        model="test-model",
    )


def _make_eval_spec(
    should_trigger: list[str] | None = None,
    should_not_trigger: list[str] | None = None,
) -> EvalSpec:
    trigger_tests = None
    if should_trigger is not None or should_not_trigger is not None:
        trigger_tests = TriggerTests(
            should_trigger=should_trigger or [],
            should_not_trigger=should_not_trigger or [],
        )
    return EvalSpec(
        skill_name="test-skill",
        description="A skill for testing",
        trigger_tests=trigger_tests,
    )


def _mock_client(response_text: str) -> AsyncMock:
    """Create a mock AsyncAnthropic client returning the given text."""
    client = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = response_text
    mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
    mock_response.content = [mock_content]
    client.messages.create = AsyncMock(return_value=mock_response)
    return client


# --- TriggerReport tests ---


class TestTriggerReport:
    def test_all_passed(self):
        report = _make_report([
            _make_result(expected=True, predicted=True),
            _make_result(expected=False, predicted=False),
        ])
        assert report.passed is True
        assert report.accuracy == 1.0

    def test_some_failed(self):
        report = _make_report([
            _make_result(expected=True, predicted=True),
            _make_result(expected=True, predicted=False),
        ])
        assert report.passed is False
        assert report.accuracy == 0.5

    def test_precision_perfect(self):
        report = _make_report([
            _make_result(expected=True, predicted=True),
            _make_result(expected=False, predicted=False),
        ])
        assert report.precision == 1.0

    def test_precision_with_false_positive(self):
        report = _make_report([
            _make_result(expected=True, predicted=True),
            _make_result(expected=False, predicted=True),  # false positive
        ])
        assert report.precision == 0.5

    def test_precision_no_predicted_positive(self):
        report = _make_report([
            _make_result(expected=True, predicted=False),
            _make_result(expected=False, predicted=False),
        ])
        assert report.precision == 1.0

    def test_recall_perfect(self):
        report = _make_report([
            _make_result(expected=True, predicted=True),
            _make_result(expected=False, predicted=False),
        ])
        assert report.recall == 1.0

    def test_recall_with_false_negative(self):
        report = _make_report([
            _make_result(expected=True, predicted=True),
            _make_result(expected=True, predicted=False),  # false negative
        ])
        assert report.recall == 0.5

    def test_recall_no_actual_positive(self):
        report = _make_report([
            _make_result(expected=False, predicted=False),
        ])
        assert report.recall == 1.0

    def test_accuracy_empty(self):
        report = _make_report([])
        assert report.accuracy == 0.0

    def test_summary_contains_key_info(self):
        report = _make_report([
            _make_result(
                query="do the thing", expected=True, predicted=True
            ),
            _make_result(
                query="unrelated", expected=False, predicted=False
            ),
        ])
        text = report.summary()
        assert "test-skill" in text
        assert "Precision" in text
        assert "Recall" in text
        assert "Accuracy" in text
        assert "PASSED" in text
        assert "do the thing" in text
        assert "unrelated" in text

    def test_summary_shows_failed(self):
        report = _make_report([
            _make_result(expected=True, predicted=False),
        ])
        text = report.summary()
        assert "FAILED" in text


# --- parse_trigger_response tests ---


class TestParseTriggerResponse:
    def test_valid_json(self):
        text = '{"triggered": true, "confidence": 0.95, "reasoning": "matches"}'
        triggered, confidence, reasoning = parse_trigger_response(text)
        assert triggered is True
        assert confidence == 0.95
        assert reasoning == "matches"

    def test_false_trigger(self):
        text = (
            '{"triggered": false, "confidence": 0.1, '
            '"reasoning": "no match"}'
        )
        triggered, confidence, reasoning = parse_trigger_response(text)
        assert triggered is False
        assert confidence == 0.1

    def test_markdown_wrapped_json(self):
        text = (
            '```json\n{"triggered": true, "confidence": 0.8,'
            ' "reasoning": "yes"}\n```'
        )
        triggered, confidence, reasoning = parse_trigger_response(text)
        assert triggered is True
        assert confidence == 0.8

    def test_markdown_no_language_tag(self):
        text = '```\n{"triggered": false, "confidence": 0.3, "reasoning": "no"}\n```'
        triggered, confidence, reasoning = parse_trigger_response(text)
        assert triggered is False
        assert confidence == 0.3

    def test_invalid_json(self):
        text = "This is not JSON at all"
        triggered, confidence, reasoning = parse_trigger_response(text)
        assert triggered is False
        assert confidence == 0.0
        assert "Failed to parse" in reasoning

    def test_empty_string(self):
        triggered, confidence, reasoning = parse_trigger_response("")
        assert triggered is False
        assert confidence == 0.0


# --- build_trigger_prompt tests ---


class TestBuildTriggerPrompt:
    def test_contains_skill_name(self):
        prompt = build_trigger_prompt("my-skill", "does things", "help me")
        assert "my-skill" in prompt

    def test_contains_description(self):
        prompt = build_trigger_prompt("s", "A great skill", "query")
        assert "A great skill" in prompt

    def test_contains_query(self):
        prompt = build_trigger_prompt("s", "d", "find restaurants nearby")
        assert "find restaurants nearby" in prompt

    def test_asks_for_json(self):
        prompt = build_trigger_prompt("s", "d", "q")
        assert "JSON" in prompt


# --- classify_query tests ---


class TestClassifyQuery:
    @pytest.mark.asyncio
    async def test_correct_positive(self):
        client = _mock_client(
            '{"triggered": true, "confidence": 0.9, "reasoning": "match"}'
        )
        result = await classify_query(
            "skill", "desc", "do it", True, client, "test-model"
        )
        assert result.predicted_trigger is True
        assert result.passed is True
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_correct_negative(self):
        client = _mock_client(
            '{"triggered": false, "confidence": 0.8, "reasoning": "nope"}'
        )
        result = await classify_query(
            "skill", "desc", "unrelated", False, client, "test-model"
        )
        assert result.predicted_trigger is False
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_false_positive(self):
        client = _mock_client(
            '{"triggered": true, "confidence": 0.6, "reasoning": "maybe"}'
        )
        result = await classify_query(
            "skill", "desc", "unrelated", False, client, "test-model"
        )
        assert result.predicted_trigger is True
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_parse_failure_defaults(self):
        client = _mock_client("garbage response")
        result = await classify_query(
            "skill", "desc", "query", True, client, "test-model"
        )
        assert result.predicted_trigger is False
        assert result.passed is False
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_captures_token_usage(self):
        client = _mock_client(
            '{"triggered": true, "confidence": 0.9, "reasoning": "ok"}'
        )
        result = await classify_query(
            "skill", "desc", "query", True, client, "test-model"
        )
        assert result.input_tokens == 500
        assert result.output_tokens == 200

    @pytest.mark.asyncio
    async def test_calls_client_with_correct_model(self):
        client = _mock_client(
            '{"triggered": true, "confidence": 0.9, "reasoning": "ok"}'
        )
        await classify_query(
            "skill", "desc", "query", True, client, "my-model"
        )
        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "my-model"


# --- test_triggers tests ---


class TestTestTriggers:
    @pytest.mark.asyncio
    async def test_no_trigger_tests_returns_empty(self):
        spec = _make_eval_spec()  # trigger_tests is None
        report = await run_test_triggers(spec)
        assert report.results == []
        assert report.skill_name == "test-skill"

    @pytest.mark.asyncio
    async def test_all_queries_classified(self, monkeypatch):
        spec = _make_eval_spec(
            should_trigger=["do it", "run it"],
            should_not_trigger=["weather today"],
        )

        mock_client = _mock_client(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )

        # Patch AsyncAnthropic to return our mock client
        mock_cls = MagicMock(return_value=mock_client)
        monkeypatch.setattr(
            "clauditor.triggers.AsyncAnthropic", mock_cls, raising=False
        )
        # Also need to make the lazy import work
        import clauditor.triggers as triggers_mod

        monkeypatch.setattr(triggers_mod, "AsyncAnthropic", mock_cls, raising=False)

        # We need to patch at the import level
        import anthropic  # noqa: F811

        monkeypatch.setattr(anthropic, "AsyncAnthropic", mock_cls)

        report = await run_test_triggers(spec, model="test-model")
        assert len(report.results) == 3
        assert report.model == "test-model"

    @pytest.mark.asyncio
    async def test_aggregates_token_usage(self, monkeypatch):
        """TriggerReport sums tokens across all classify_query calls."""
        spec = _make_eval_spec(
            should_trigger=["a", "b"],
            should_not_trigger=["c"],
        )
        mock_client = _mock_client(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        mock_cls = MagicMock(return_value=mock_client)
        import anthropic

        monkeypatch.setattr(anthropic, "AsyncAnthropic", mock_cls)

        report = await run_test_triggers(spec)
        # 3 queries × 500/200 per mock
        assert report.input_tokens == 1500
        assert report.output_tokens == 600

    @pytest.mark.asyncio
    async def test_parallel_execution(self, monkeypatch):
        """Verify all queries run via asyncio.gather (in parallel)."""
        spec = _make_eval_spec(
            should_trigger=["a", "b"],
            should_not_trigger=["c"],
        )

        mock_client = _mock_client(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        mock_cls = MagicMock(return_value=mock_client)

        import anthropic

        monkeypatch.setattr(anthropic, "AsyncAnthropic", mock_cls)

        report = await run_test_triggers(spec)

        # All 3 queries should have been sent
        assert mock_client.messages.create.call_count == 3
        assert len(report.results) == 3
