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
    _extract_openai_result,
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
    model with ``output[]`` (list of output items: messages,
    reasoning, tool-use), ``output_text`` (SDK convenience accessor
    joining message text), ``usage`` (input/output token counts),
    and ``model_dump()`` (Pydantic v2 dict serialization). Build a
    single-message ``output[]`` mirroring ``output_text`` so the
    extractor's per-block walker yields matching ``text_blocks``.
    """
    resp = MagicMock()
    if output_text:
        # Build a single message item with one output_text block so
        # the extractor's walker collects matching text_blocks.
        msg = MagicMock()
        msg.type = "message"
        block = MagicMock()
        block.type = "output_text"
        block.text = output_text
        msg.content = [block]
        resp.output = [msg]
    else:
        resp.output = []
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


def _build_message_item(*texts: str) -> MagicMock:
    """Build a Responses-API ``message``-typed output item.

    Each text becomes one ``output_text``-typed content block. The
    real SDK shape is ``ResponseOutputMessage`` with ``type ==
    "message"`` and a ``content`` list of ``ResponseOutputText`` /
    ``ResponseOutputRefusal`` objects.
    """
    item = MagicMock()
    item.type = "message"
    blocks = []
    for text in texts:
        block = MagicMock()
        block.type = "output_text"
        block.text = text
        blocks.append(block)
    item.content = blocks
    return item


def _build_reasoning_item() -> MagicMock:
    """Build a Responses-API ``reasoning``-typed output item.

    Reasoning items appear in the response.output[] list under
    extended-thinking modes; the helper must skip them when
    collecting text blocks. Forward-compat with #154's
    harness-context sidecar work.
    """
    item = MagicMock()
    item.type = "reasoning"
    # Reasoning items have a ``summary`` field, not ``content``.
    item.summary = [MagicMock(text="thinking...")]
    return item


def _make_response(
    *,
    output: list | None = None,
    output_text: str | None = "",
    usage: MagicMock | None = None,
    raw_dict: dict | None = None,
    has_model_dump: bool = True,
) -> MagicMock:
    """Build a Responses-API response stub for the extractor tests.

    Distinct from the call-site ``_mock_response`` helper: this one
    exposes ``output`` (the list of output items the extractor
    walks), where ``_mock_response`` exposes only ``output_text``.
    Pass ``output_text=None`` to omit the attribute entirely.
    """
    resp = MagicMock()
    resp.output = output if output is not None else []
    if output_text is None:
        del resp.output_text
    else:
        resp.output_text = output_text
    if usage is not None:
        resp.usage = usage
    else:
        # Default: simple usage with zero tokens so tests that don't
        # care about token counts get a clean baseline.
        u = MagicMock()
        u.input_tokens = 0
        u.output_tokens = 0
        resp.usage = u
    if has_model_dump:
        resp.model_dump = MagicMock(
            return_value=raw_dict
            if raw_dict is not None
            else {"id": "resp_x"}
        )
    else:
        del resp.model_dump
    return resp


class TestExtractOpenAIResult:
    """Defensive parser tests for ``_extract_openai_result``.

    The helper walks ``response.output[]`` skipping non-message
    items (reasoning, tool-use), collects text from ``output_text``
    blocks, and falls back to ``response.output_text`` when the
    walker yields nothing. Token coercion mirrors
    ``_anthropic._extract_result``'s defensive shape.
    """

    def test_happy_path_message_only(self) -> None:
        # Single message with one output_text block. text_blocks
        # captures the per-message joined text; response_text
        # prefers the SDK's output_text accessor.
        resp = _make_response(
            output=[_build_message_item("hello world")],
            output_text="hello world",
        )
        text, blocks, in_t, out_t, raw = _extract_openai_result(resp)
        assert blocks == ["hello world"]
        assert text == "hello world"

    def test_filters_reasoning_items(self) -> None:
        # Reasoning items in the response.output[] list must be
        # skipped — only message-typed items contribute to
        # text_blocks. Forward-compat with #154's harness-context
        # sidecar work where reasoning summaries land elsewhere.
        resp = _make_response(
            output=[
                _build_reasoning_item(),
                _build_message_item("msg"),
            ],
            output_text="msg",
        )
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == ["msg"]
        assert text == "msg"

    def test_multiple_message_blocks_joined(self) -> None:
        # Two message items with one output_text block each. The
        # walker collects per-message joined text into text_blocks;
        # response_text mirrors the SDK's joined accessor.
        resp = _make_response(
            output=[
                _build_message_item("first"),
                _build_message_item("second"),
            ],
            output_text="firstsecond",
        )
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == ["first", "second"]
        assert text == "firstsecond"

    def test_empty_output_yields_empty(self) -> None:
        # Empty response.output[] list AND empty output_text — the
        # extractor returns empty containers without raising.
        resp = _make_response(output=[], output_text="")
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert text == ""
        assert blocks == []

    def test_missing_usage_yields_zero_tokens(self) -> None:
        # Defensive: a response with no ``usage`` attribute (or
        # usage=None) must not crash the projection. Token counts
        # default to 0.
        resp = _make_response(output=[], output_text="")
        del resp.usage
        _, _, in_t, out_t, _ = _extract_openai_result(resp)
        assert in_t == 0
        assert out_t == 0

    def test_null_usage_input_tokens_yields_zero(self) -> None:
        # Defensive: usage.input_tokens = None (a future SDK quirk
        # or an incomplete response) must coerce to 0 rather than
        # raising TypeError on int(None).
        usage = MagicMock()
        usage.input_tokens = None
        usage.output_tokens = None
        resp = _make_response(output=[], output_text="", usage=usage)
        _, _, in_t, out_t, _ = _extract_openai_result(resp)
        assert in_t == 0
        assert out_t == 0

    def test_string_usage_field_falls_back_to_zero(self) -> None:
        # Defensive parity with _anthropic._extract_result: a
        # non-numeric string in input_tokens / output_tokens falls
        # back to 0 rather than crashing the projection.
        usage = MagicMock()
        usage.input_tokens = "not a number"
        usage.output_tokens = "garbage"
        resp = _make_response(output=[], output_text="", usage=usage)
        _, _, in_t, out_t, _ = _extract_openai_result(resp)
        assert in_t == 0
        assert out_t == 0

    def test_status_incomplete_does_not_raise(self) -> None:
        # An ``incomplete`` status (max_output_tokens exhausted,
        # filtered, etc.) must not abort the extractor. The caller
        # decides what to do — the helper just projects what's
        # there.
        resp = _make_response(
            output=[_build_message_item("partial")],
            output_text="partial",
            raw_dict={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
        )
        resp.status = "incomplete"
        resp.incomplete_details = MagicMock(reason="max_output_tokens")
        text, blocks, _, _, raw = _extract_openai_result(resp)
        assert text == "partial"
        assert blocks == ["partial"]
        assert raw["status"] == "incomplete"

    def test_raw_message_dict_round_trip(self) -> None:
        # raw_message is the Pydantic-v2 dict from
        # ``response.model_dump()``. Round-trip the canned dict to
        # confirm the helper does not mutate or reshape it.
        raw = {"id": "x", "status": "completed"}
        resp = _make_response(
            output=[], output_text="", raw_dict=raw
        )
        _, _, _, _, got = _extract_openai_result(resp)
        assert got == raw

    def test_raw_message_fallback_on_missing_model_dump(self) -> None:
        # Defensive against a future SDK that drops .model_dump():
        # raw_message falls back to {} (NOT None — a dict matches
        # the documented shape downstream callers may isinstance).
        resp = _make_response(
            output=[], output_text="", has_model_dump=False
        )
        _, _, _, _, raw = _extract_openai_result(resp)
        assert raw == {}

    def test_output_text_attribute_missing_falls_back_to_blocks(
        self,
    ) -> None:
        # If the SDK ever drops the convenience output_text
        # accessor, the helper falls back to joining text_blocks so
        # response_text stays populated.
        resp = _make_response(
            output=[_build_message_item("alpha", "beta")],
            output_text=None,
        )
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == ["alphabeta"]
        assert text == "alphabeta"

    def test_skips_non_output_text_content_blocks(self) -> None:
        # Defensive: a message item whose content[] mixes
        # output_text blocks with other types (refusal, future
        # block types) must skip the non-text blocks without
        # raising.
        msg = MagicMock()
        msg.type = "message"
        text_block = MagicMock()
        text_block.type = "output_text"
        text_block.text = "real"
        refusal = MagicMock()
        refusal.type = "refusal"
        refusal.refusal = "I cannot help"
        msg.content = [refusal, text_block]
        resp = _make_response(output=[msg], output_text="real")
        text, blocks, _, _, _ = _extract_openai_result(resp)
        # Only the output_text block contributes; refusal is skipped.
        assert blocks == ["real"]
        assert text == "real"

    def test_message_with_non_list_content_is_skipped(self) -> None:
        # Defensive: a future SDK shape change where ``content`` is
        # a string or scalar must not crash the walker.
        msg = MagicMock()
        msg.type = "message"
        msg.content = "not-a-list"
        resp = _make_response(output=[msg], output_text="")
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == []
        assert text == ""

    def test_model_dump_raising_falls_back_to_empty_dict(self) -> None:
        # Defensive: if .model_dump() exists but raises (a future
        # SDK quirk, a partial response object), the helper falls
        # back to {} rather than propagating.
        resp = _make_response(output=[], output_text="")
        resp.model_dump = MagicMock(side_effect=TypeError("boom"))
        _, _, _, _, raw = _extract_openai_result(resp)
        assert raw == {}
