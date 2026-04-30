"""Tests for Layer 3c trigger precision testing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clauditor._anthropic import AnthropicResult
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


def _mock_anthropic_result(
    response_text: str,
    *,
    input_tokens: int = 500,
    output_tokens: int = 200,
) -> AnthropicResult:
    """Return an :class:`AnthropicResult` shaped like a successful helper call.

    After bead ``clauditor-24h.3`` triggers.py routes through
    ``clauditor._providers.call_model`` instead of instantiating its
    own ``AsyncAnthropic`` client, so tests stub the helper and hand
    back an ``AnthropicResult`` directly.
    """
    text_blocks = [response_text] if response_text else []
    return AnthropicResult(
        response_text=response_text,
        text_blocks=text_blocks,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_message=None,
    )


def _mock_call_anthropic(response_text: str) -> AsyncMock:
    """Return an ``AsyncMock`` that yields a canned :class:`AnthropicResult`."""
    return AsyncMock(return_value=_mock_anthropic_result(response_text))


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
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "match"}'
        )
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "do it", True, "test-model"
            )
        assert result.predicted_trigger is True
        assert result.passed is True
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_correct_negative(self):
        call = _mock_call_anthropic(
            '{"triggered": false, "confidence": 0.8, "reasoning": "nope"}'
        )
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "unrelated", False, "test-model"
            )
        assert result.predicted_trigger is False
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_false_positive(self):
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.6, "reasoning": "maybe"}'
        )
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "unrelated", False, "test-model"
            )
        assert result.predicted_trigger is True
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_parse_failure_defaults(self):
        call = _mock_call_anthropic("garbage response")
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "query", True, "test-model"
            )
        assert result.predicted_trigger is False
        assert result.passed is False
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_captures_token_usage(self):
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "ok"}'
        )
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "query", True, "test-model"
            )
        assert result.input_tokens == 500
        assert result.output_tokens == 200

    @pytest.mark.asyncio
    async def test_calls_helper_with_correct_model(self):
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "ok"}'
        )
        with patch("clauditor._providers.call_model", call):
            await classify_query(
                "skill", "desc", "query", True, "my-model"
            )
        call.assert_awaited_once()
        assert call.await_args.kwargs["model"] == "my-model"
        assert call.await_args.kwargs["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_empty_text_blocks_falls_back_to_no_text(self):
        """A helper response with zero text blocks yields the 'no text' branch."""
        call = AsyncMock(return_value=_mock_anthropic_result(""))
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "query", True, "test-model"
            )
        assert result.predicted_trigger is False
        # expected=True so passed is False
        assert result.passed is False
        assert result.confidence == 0.0
        assert "no text content" in result.reasoning

    @pytest.mark.asyncio
    async def test_helper_error_yields_graceful_failure_row(self):
        """AnthropicHelperError does not abort the batch — records one
        row with ``triggered=False`` + ``passed=False`` regardless of
        expected (an API error is never a real pass), with reasoning
        that names the API error so sibling queries in ``test_triggers``
        still produce their rows.
        """
        from clauditor._anthropic import AnthropicHelperError

        call = AsyncMock(side_effect=AnthropicHelperError("rate limited"))
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "query", True, "test-model"
            )
        assert result.predicted_trigger is False
        assert result.passed is False
        assert result.confidence == 0.0
        assert "API error" in result.reasoning
        assert "rate limited" in result.reasoning
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    @pytest.mark.asyncio
    async def test_helper_error_fails_should_not_trigger_too(self):
        """An API error on a ``should_not_trigger`` query (expected=False)
        must still ``passed=False`` — otherwise systematic API failures
        would silently inflate pass rates on negative tests.
        """
        from clauditor._anthropic import AnthropicHelperError

        call = AsyncMock(side_effect=AnthropicHelperError("auth failure"))
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill", "desc", "unrelated query", False, "test-model"
            )
        assert result.expected_trigger is False
        assert result.predicted_trigger is False
        assert result.passed is False
        assert "API error" in result.reasoning

    @pytest.mark.asyncio
    async def test_classify_query_returns_failed_result_on_openai_helper_error(
        self,
    ):
        """#145 QG pass 1: an :class:`OpenAIHelperError` from the OpenAI
        backend must be caught with the same graceful-degradation contract
        as :class:`AnthropicHelperError`. Without this, a single OpenAI
        failure (auth, rate-limit exhaustion, conn error, 5xx) escapes
        ``asyncio.gather`` and aborts the whole ``test_triggers`` batch
        when ``provider="openai"``.
        """
        from clauditor._providers import OpenAIHelperError

        call = AsyncMock(side_effect=OpenAIHelperError("simulated openai"))
        with patch("clauditor._providers.call_model", call):
            result = await classify_query(
                "skill",
                "desc",
                "query",
                True,
                "test-model",
                provider="openai",
            )
        assert result.predicted_trigger is False
        assert result.passed is False
        assert result.confidence == 0.0
        assert "API error" in result.reasoning
        assert "simulated openai" in result.reasoning
        assert result.input_tokens == 0
        assert result.output_tokens == 0


# --- test_triggers tests ---


class TestTestTriggers:
    @pytest.mark.asyncio
    async def test_no_trigger_tests_returns_empty(self):
        spec = _make_eval_spec()  # trigger_tests is None
        report = await run_test_triggers(spec)
        assert report.results == []
        assert report.skill_name == "test-skill"

    @pytest.mark.asyncio
    async def test_all_queries_classified(self):
        spec = _make_eval_spec(
            should_trigger=["do it", "run it"],
            should_not_trigger=["weather today"],
        )

        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        with patch("clauditor._providers.call_model", call):
            report = await run_test_triggers(spec, model="test-model")
        assert len(report.results) == 3
        assert report.model == "test-model"

    @pytest.mark.asyncio
    async def test_aggregates_token_usage(self):
        """TriggerReport sums tokens across all classify_query calls."""
        spec = _make_eval_spec(
            should_trigger=["a", "b"],
            should_not_trigger=["c"],
        )
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        with patch("clauditor._providers.call_model", call):
            report = await run_test_triggers(spec)
        # 3 queries × 500/200 per mock
        assert report.input_tokens == 1500
        assert report.output_tokens == 600

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """Verify all queries run via asyncio.gather (in parallel)."""
        spec = _make_eval_spec(
            should_trigger=["a", "b"],
            should_not_trigger=["c"],
        )

        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        with patch("clauditor._providers.call_model", call):
            report = await run_test_triggers(spec)

        # All 3 queries should have been sent through the helper
        assert call.await_count == 3
        assert len(report.results) == 3


class TestTriggersGradingProviderOpenAI:
    """#145 US-010: when ``eval_spec.grading_provider == "openai"``,
    ``test_triggers`` resolves the provider once from the spec and
    threads it into every per-query ``classify_query`` call."""

    @pytest.mark.asyncio
    async def test_triggers_stamps_openai_when_grading_provider_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        spec = _make_eval_spec(
            should_trigger=["a", "b"],
            should_not_trigger=["c"],
        )
        spec.grading_provider = "openai"
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        with patch("clauditor._providers.call_model", call):
            await run_test_triggers(spec)
        # All 3 per-query call_model invocations must have received
        # ``provider="openai"``.
        assert call.await_count == 3
        for c in call.await_args_list:
            assert c.kwargs["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_triggers_defaults_to_anthropic_when_unset(self) -> None:
        """Back-compat regression: a spec with ``grading_provider=None``
        still routes through anthropic."""
        spec = _make_eval_spec(
            should_trigger=["a"],
        )
        # grading_provider default = None
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "yes"}'
        )
        with patch("clauditor._providers.call_model", call):
            await run_test_triggers(spec)
        assert call.await_count == 1
        assert call.await_args.kwargs["provider"] == "anthropic"

    @pytest.mark.asyncio
    async def test_classify_query_provider_kwarg_threaded(self) -> None:
        """``classify_query`` accepts a ``provider`` kwarg and threads
        it directly into ``call_model``."""
        call = _mock_call_anthropic(
            '{"triggered": true, "confidence": 0.9, "reasoning": "ok"}'
        )
        with patch("clauditor._providers.call_model", call):
            await classify_query(
                "skill", "desc", "q", True, "test-model",
                provider="openai",
            )
        call.assert_awaited_once()
        assert call.await_args.kwargs["provider"] == "openai"
