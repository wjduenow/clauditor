"""Tests for the OpenAI provider backend (bead clauditor-2pf, US-002).

Covers the happy path of :func:`clauditor._providers._openai.call_openai`:
``ModelResult`` field population, ``provider``/``source`` stamping,
``_monotonic`` indirection per
``.claude/rules/monotonic-time-indirection.md``, and ``raw_message``
shape (``response.model_dump()`` per DEC-001).

Retry branches, error categorization, and the rich
``response.output[]`` walker for refusal handling are out of scope —
US-003 / US-004 land them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor._providers._openai import (
    DEFAULT_MODEL_L2,
    DEFAULT_MODEL_L3,
    OpenAIHelperError,
    call_openai,
)


def _mock_response(
    *,
    output_text: str = "hello",
    input_tokens: int = 10,
    output_tokens: int = 5,
    raw_dict: dict | None = None,
) -> MagicMock:
    """Build a MagicMock shaped like an OpenAI Responses-API response.

    The real ``openai.types.responses.Response`` object is a Pydantic
    model with ``output_text`` (joined message text), ``usage``
    (input/output token counts), and ``model_dump()`` (Pydantic v2
    dict serialization). Mock just enough of that surface for the
    happy-path projection.
    """
    resp = MagicMock()
    resp.output_text = output_text
    resp.usage = MagicMock(
        input_tokens=input_tokens, output_tokens=output_tokens
    )
    resp.model_dump = MagicMock(
        return_value=raw_dict if raw_dict is not None else {"id": "resp_x"}
    )
    return resp


def _patch_async_openai(response: MagicMock):
    """Patch ``AsyncOpenAI`` so ``.responses.create(...)`` returns ``response``.

    Returns the patched class so callers can assert on construction
    kwargs / call counts when needed.
    """
    fake_client = MagicMock()
    fake_client.responses = MagicMock()
    fake_client.responses.create = AsyncMock(return_value=response)
    fake_class = MagicMock(return_value=fake_client)
    return patch(
        "clauditor._providers._openai.AsyncOpenAI", new=fake_class
    ), fake_client


class TestModuleConstants:
    def test_default_models_are_strings(self) -> None:
        # DEC-001 of plans/super/145-openai-provider.md pins these
        # to real Responses-API models. Don't assert byte-equality
        # (DEC-001 reserves the right to refresh per the OpenAI
        # docs page) but assert shape: non-empty string.
        assert isinstance(DEFAULT_MODEL_L3, str)
        assert DEFAULT_MODEL_L3 != ""
        assert isinstance(DEFAULT_MODEL_L2, str)
        assert DEFAULT_MODEL_L2 != ""


class TestOpenAIHelperError:
    def test_is_exception_subclass(self) -> None:
        # Distinct from AnthropicHelperError per DEC-006 of #145 (no
        # common ancestor). Plain Exception subclass.
        assert issubclass(OpenAIHelperError, Exception)

    def test_message_is_preserved(self) -> None:
        exc = OpenAIHelperError("boom")
        assert str(exc) == "boom"


class TestCallOpenAISuccess:
    @pytest.mark.asyncio
    async def test_returns_result_with_tokens(self) -> None:
        resp = _mock_response(
            output_text="hello",
            input_tokens=10,
            output_tokens=5,
            raw_dict={"id": "resp_x", "status": "completed"},
        )
        ctx, fake_client = _patch_async_openai(resp)
        with ctx:
            result = await call_openai("prompt", model="gpt-5.4")

        assert result.response_text == "hello"
        assert result.text_blocks == ["hello"]
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        # Sanity-check the SDK was invoked with the documented kwargs
        # (``input=`` and ``max_output_tokens=`` per Responses API,
        # NOT ``messages=`` / ``max_tokens=``).
        fake_client.responses.create.assert_awaited_once()
        kwargs = fake_client.responses.create.call_args.kwargs
        assert kwargs["input"] == "prompt"
        assert kwargs["model"] == "gpt-5.4"
        assert kwargs["max_output_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_provider_and_source_stamped(self) -> None:
        resp = _mock_response()
        ctx, _ = _patch_async_openai(resp)
        with ctx:
            result = await call_openai("p", model="gpt-5.4-mini")
        # DEC-002: source is ALWAYS "api" for openai (no CLI
        # transport axis); provider is "openai" so call_model
        # consumers can branch on it.
        assert result.provider == "openai"
        assert result.source == "api"

    @pytest.mark.asyncio
    async def test_duration_uses_monotonic_indirection(self) -> None:
        # Per .claude/rules/monotonic-time-indirection.md: tests patch
        # the module-level _monotonic alias rather than time.monotonic
        # so the asyncio event loop's own scheduler ticks are not
        # disturbed.
        resp = _mock_response()
        ctx, _ = _patch_async_openai(resp)
        with ctx, patch(
            "clauditor._providers._openai._monotonic",
            side_effect=[0.0, 1.5],
        ):
            result = await call_openai("p", model="gpt-5.4")
        assert result.duration_seconds == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_raw_message_is_dict(self) -> None:
        # DEC-001 raw_message divergence: OpenAI surfaces a
        # Pydantic-v2 dict via .model_dump() rather than Anthropic's
        # raw Message object. Callers that introspect raw_message
        # for refusal semantics must branch on provider.
        raw = {"id": "resp_y", "status": "completed", "output": []}
        resp = _mock_response(raw_dict=raw)
        ctx, _ = _patch_async_openai(resp)
        with ctx:
            result = await call_openai("p", model="gpt-5.4")
        assert isinstance(result.raw_message, dict)
        assert result.raw_message == raw

    @pytest.mark.asyncio
    async def test_empty_output_text_yields_empty_blocks(self) -> None:
        # Defensive: OpenAI may emit a response with no text content
        # (refusal, tool-only, incomplete). The minimal happy-path
        # projection collapses that to text_blocks=[] so callers
        # checking truthiness of the list can short-circuit.
        resp = _mock_response(output_text="")
        ctx, _ = _patch_async_openai(resp)
        with ctx:
            result = await call_openai("p", model="gpt-5.4")
        assert result.response_text == ""
        assert result.text_blocks == []

    @pytest.mark.asyncio
    async def test_transport_kwarg_accepted_and_ignored(self) -> None:
        # DEC-002: transport kwarg is accepted at the signature level
        # (so the dispatcher can pass it uniformly) but ignored —
        # OpenAI has no CLI transport axis. source stays "api".
        resp = _mock_response()
        ctx, _ = _patch_async_openai(resp)
        with ctx:
            result = await call_openai(
                "p", model="gpt-5.4", transport="cli"
            )
        assert result.source == "api"

    @pytest.mark.asyncio
    async def test_subject_kwarg_accepted_and_ignored(self) -> None:
        # subject is Anthropic-CLI-specific (apiKeySource telemetry).
        # OpenAI accepts the kwarg for signature uniformity but does
        # nothing with it.
        resp = _mock_response()
        ctx, _ = _patch_async_openai(resp)
        with ctx:
            result = await call_openai(
                "p", model="gpt-5.4", subject="L3 grading"
            )
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_garbage_token_counts_default_to_zero(self) -> None:
        # Defensive parity with _anthropic._extract_result: a future
        # SDK that emits non-numeric ``input_tokens`` / ``output_tokens``
        # must not crash the projection. Fall back to 0.
        resp = _mock_response()
        resp.usage = MagicMock(
            input_tokens="not-a-number", output_tokens=object()
        )
        ctx, _ = _patch_async_openai(resp)
        with ctx:
            result = await call_openai("p", model="gpt-5.4")
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    @pytest.mark.asyncio
    async def test_max_tokens_threaded_to_max_output_tokens(self) -> None:
        # The Responses API names the cap ``max_output_tokens`` (not
        # ``max_tokens``). The wrapper translates.
        resp = _mock_response()
        ctx, fake_client = _patch_async_openai(resp)
        with ctx:
            await call_openai("p", model="gpt-5.4", max_tokens=2048)
        kwargs = fake_client.responses.create.call_args.kwargs
        assert kwargs["max_output_tokens"] == 2048
