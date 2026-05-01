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
    _body_excerpt,
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
    async def test_async_openai_constructed_with_max_retries_zero(
        self,
    ) -> None:
        # PR #160 review (CodeRabbit): the AsyncOpenAI client must be
        # constructed with ``max_retries=0`` so the SDK's built-in
        # retry loop (default 2 retries on 429/5xx/connection) does
        # NOT compound with the wrapper's per-category retries
        # (RATE_LIMIT_MAX_RETRIES=3, SERVER_MAX_RETRIES=1,
        # CONN_MAX_RETRIES=1). Without this, a single 429 from the
        # SDK's perspective is actually 3 SDK-level attempts hidden
        # inside one wrapper-level retry index, double-retrying with
        # compounded backoff and obscuring the actual retry budget.
        resp = _mock_response()
        ctx, _ = _patch_async_openai(resp)
        with ctx as fake_class:
            await call_openai("p", model="gpt-5.4")
        fake_class.assert_called_once_with(max_retries=0)

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

    def test_message_with_content_none_yields_no_block(self) -> None:
        # QG pass 2 (#145): a message item with ``content=None``
        # (refusal-only / placeholder) must NOT produce an empty-string
        # entry in text_blocks. The contract is symmetric: zero
        # output_text blocks → zero text_blocks entries, regardless of
        # whether ``content`` was None, missing, or an empty list.
        item = MagicMock()
        item.type = "message"
        item.content = None
        resp = _make_response(output=[item], output_text="")
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == []
        assert text == ""

    def test_message_with_content_empty_list_yields_no_block(self) -> None:
        # Symmetric with ``content=None``: an empty content list
        # produces no text_blocks entry (not [""]). Pins the QG pass 2
        # asymmetry fix in ``_providers/_openai.py``.
        item = MagicMock()
        item.type = "message"
        item.content = []
        resp = _make_response(output=[item], output_text="")
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == []
        assert text == ""

    def test_message_with_only_non_output_text_blocks_yields_no_block(self) -> None:
        # A message item with only ``refusal``-typed content blocks
        # (or other non-``output_text`` types) yields no text_blocks
        # entry. Symmetric with content=None / content=[].
        item = MagicMock()
        item.type = "message"
        refusal = MagicMock()
        refusal.type = "refusal"
        refusal.refusal = "I cannot help with that."
        item.content = [refusal]
        resp = _make_response(output=[item], output_text="")
        text, blocks, _, _, _ = _extract_openai_result(resp)
        assert blocks == []
        assert text == ""

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


# ---------------------------------------------------------------------------
# US-004: Retry / error branches in call_openai
# ---------------------------------------------------------------------------


import httpx  # noqa: E402
from openai import (  # noqa: E402
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)


def _make_rate_limit_error(
    *, message: str = "rate limit", body: object | None = None
) -> RateLimitError:
    req = httpx.Request("POST", "https://example.com/v1/responses")
    httpx_resp = httpx.Response(429, request=req)
    return RateLimitError(message, response=httpx_resp, body=body)


def _make_status_error(
    status: int,
    *,
    message: str = "boom",
    body: object | None = None,
) -> APIStatusError:
    req = httpx.Request("POST", "https://example.com/v1/responses")
    httpx_resp = httpx.Response(status, request=req)
    return APIStatusError(message, response=httpx_resp, body=body)


def _make_auth_error(
    *, message: str = "invalid key", body: object | None = None
) -> AuthenticationError:
    req = httpx.Request("POST", "https://example.com/v1/responses")
    httpx_resp = httpx.Response(401, request=req)
    return AuthenticationError(message, response=httpx_resp, body=body)


def _make_permission_error() -> PermissionDeniedError:
    req = httpx.Request("POST", "https://example.com/v1/responses")
    httpx_resp = httpx.Response(403, request=req)
    return PermissionDeniedError(
        "forbidden",
        response=httpx_resp,
        body={"error": {"message": "nope"}},
    )


def _make_connection_error() -> APIConnectionError:
    req = httpx.Request("POST", "https://example.com/v1/responses")
    return APIConnectionError(message="connection reset", request=req)


def _patch_async_openai_with_side_effect(side_effect):
    """Patch ``AsyncOpenAI`` so ``.responses.create`` uses ``side_effect``.

    Distinct from ``_patch_async_openai`` (above) which uses
    ``return_value`` for the happy path. Per
    ``.claude/rules/mock-side-effect-for-distinct-calls.md``, the
    retry tests pass a list / iterable so each retry iteration
    sees a distinct value.
    """
    fake_client = MagicMock()
    fake_client.responses = MagicMock()
    fake_client.responses.create = AsyncMock(side_effect=side_effect)
    fake_class = MagicMock(return_value=fake_client)
    return (
        patch("clauditor._providers._openai.AsyncOpenAI", new=fake_class),
        fake_client,
    )


class TestBodyExcerpt:
    """Coverage for the OpenAI body-excerpt helper.

    Mirrors :class:`tests.test_providers_anthropic.TestBodyExcerpt`
    — the helpers are duplicated per-provider, so the tests are too.
    """

    def test_none_body(self) -> None:
        exc = MagicMock()
        exc.body = None
        assert _body_excerpt(exc) == "<no body>"

    def test_string_body(self) -> None:
        exc = MagicMock()
        exc.body = "some error text"
        assert _body_excerpt(exc) == "some error text"

    def test_dict_body_rendered(self) -> None:
        exc = MagicMock()
        exc.body = {"error": {"message": "bad"}}
        out = _body_excerpt(exc)
        assert "error" in out
        assert "bad" in out

    def test_body_truncated_at_limit(self) -> None:
        exc = MagicMock()
        exc.body = "x" * 1000
        out = _body_excerpt(exc)
        # 512 chars + 3-char ellipsis
        assert out.endswith("...")
        assert len(out) == 512 + 3

    def test_unrenderable_body_tolerated(self) -> None:
        # A body whose ``repr()`` itself raises must fall through to
        # the "<unrenderable body>" sentinel rather than propagate.
        class Bad:
            def __repr__(self) -> str:
                raise RuntimeError("nope")

        exc = MagicMock()
        exc.body = Bad()
        assert _body_excerpt(exc) == "<unrenderable body>"


class TestCallOpenAIRateLimit:
    @pytest.mark.asyncio
    async def test_retries_three_times_then_raises(self) -> None:
        # Four failures: retries 0, 1, 2 then raise on attempt 4.
        # Distinct bodies per call prove the loop keeps iterating.
        errors = [
            _make_rate_limit_error(body={"attempt": i}) for i in range(4)
        ]
        ctx, fake_client = _patch_async_openai_with_side_effect(errors)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        assert "rate limit" in str(exc_info.value).lower()
        # 4 create calls, 3 sleeps between them.
        assert fake_client.responses.create.await_count == 4
        assert sleep_mock.await_count == 3
        # Sleeps should be 1, 2, 4 with zero jitter.
        delays = [c.args[0] for c in sleep_mock.await_args_list]
        assert delays == [1.0, 2.0, 4.0]
        # Original SDK exception preserved via __cause__.
        assert isinstance(exc_info.value.__cause__, RateLimitError)

    @pytest.mark.asyncio
    async def test_recovery_after_two_retries(self) -> None:
        # Distinct retry errors followed by a success — per rule
        # mock-side-effect-for-distinct-calls.md, each call value
        # is unique so the retry loop actually iterates.
        resp = _mock_response(
            output_text="recovered", input_tokens=10, output_tokens=3
        )
        sequence = [
            _make_rate_limit_error(body={"n": 1}),
            _make_rate_limit_error(body={"n": 2}),
            resp,
        ]
        ctx, fake_client = _patch_async_openai_with_side_effect(sequence)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            result = await call_openai("p", model="gpt-5.4")
        assert result.response_text == "recovered"
        assert fake_client.responses.create.await_count == 3
        assert sleep_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_backoff_respects_jitter_band(self) -> None:
        # Stub _rand_uniform to ±0.25 extremes; verify delays sit
        # inside the documented band.
        errors = [
            _make_rate_limit_error(body={"n": i}) for i in range(4)
        ]
        ctx, _ = _patch_async_openai_with_side_effect(errors)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform",
            side_effect=[0.25, -0.25, 0.25],
        ):
            with pytest.raises(OpenAIHelperError):
                await call_openai("p", model="gpt-5.4")
        delays = [c.args[0] for c in sleep_mock.await_args_list]
        # retry 0: base 1, +25% → 1.25
        # retry 1: base 2, -25% → 1.5
        # retry 2: base 4, +25% → 5.0
        assert delays[0] == pytest.approx(1.25)
        assert delays[1] == pytest.approx(1.5)
        assert delays[2] == pytest.approx(5.0)


class TestCallOpenAIServerError:
    @pytest.mark.asyncio
    async def test_503_retries_once_then_raises(self) -> None:
        # Two distinct 503s so the side_effect list documents both
        # the first and the retry arm; the retry is exhausted on
        # the second and the helper raises.
        errors = [
            _make_status_error(503, message="svc1", body={"n": 1}),
            _make_status_error(503, message="svc2", body={"n": 2}),
        ]
        ctx, fake_client = _patch_async_openai_with_side_effect(errors)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        assert "503" in str(exc_info.value)
        assert "server error" in str(exc_info.value).lower()
        assert fake_client.responses.create.await_count == 2
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_500_recovers_after_one_retry(self) -> None:
        resp = _mock_response(
            output_text="recovered", input_tokens=1, output_tokens=1
        )
        sequence = [
            _make_status_error(500, message="internal", body={"n": 1}),
            resp,
        ]
        ctx, _ = _patch_async_openai_with_side_effect(sequence)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            result = await call_openai("p", model="gpt-5.4")
        assert result.response_text == "recovered"
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_502_recovers_after_one_retry(self) -> None:
        # Coverage parity for the 502 status code branch.
        resp = _mock_response(output_text="ok")
        sequence = [
            _make_status_error(502, message="bad gateway", body={"n": 1}),
            resp,
        ]
        ctx, _ = _patch_async_openai_with_side_effect(sequence)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            result = await call_openai("p", model="gpt-5.4")
        assert result.response_text == "ok"


class TestCallOpenAIClientError:
    @pytest.mark.asyncio
    async def test_400_fails_fast_no_retry(self) -> None:
        err = _make_status_error(
            400, message="bad request", body={"error": {"message": "nope"}}
        )
        ctx, fake_client = _patch_async_openai_with_side_effect(err)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        assert "400" in msg
        assert "bad request" in msg
        assert fake_client.responses.create.await_count == 1
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_422_fails_fast_no_retry(self) -> None:
        err = _make_status_error(
            422, message="unprocessable", body=None
        )
        ctx, fake_client = _patch_async_openai_with_side_effect(err)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        assert "422" in str(exc_info.value)
        assert fake_client.responses.create.await_count == 1
        assert sleep_mock.await_count == 0


class TestCallOpenAIAuthErrors:
    @pytest.mark.asyncio
    async def test_401_mentions_api_key_env_var(self) -> None:
        err = _make_auth_error(
            message="invalid api key",
            body={"error": {"message": "invalid key"}},
        )
        ctx, fake_client = _patch_async_openai_with_side_effect(err)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        assert "OPENAI_API_KEY" in msg
        assert "401" in msg
        # Auth errors must not retry.
        assert fake_client.responses.create.await_count == 1
        assert sleep_mock.await_count == 0
        assert isinstance(exc_info.value.__cause__, AuthenticationError)

    @pytest.mark.asyncio
    async def test_403_also_mentions_api_key_env_var(self) -> None:
        err = _make_permission_error()
        ctx, fake_client = _patch_async_openai_with_side_effect(err)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        assert "OPENAI_API_KEY" in msg
        assert "403" in msg
        assert fake_client.responses.create.await_count == 1
        assert sleep_mock.await_count == 0
        assert isinstance(
            exc_info.value.__cause__, PermissionDeniedError
        )


class TestCallOpenAIConnectionError:
    @pytest.mark.asyncio
    async def test_retries_once_then_raises(self) -> None:
        errors = [_make_connection_error(), _make_connection_error()]
        ctx, fake_client = _patch_async_openai_with_side_effect(errors)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        assert "connection" in str(exc_info.value).lower()
        assert fake_client.responses.create.await_count == 2
        assert sleep_mock.await_count == 1
        assert isinstance(exc_info.value.__cause__, APIConnectionError)

    @pytest.mark.asyncio
    async def test_recovers_after_one_retry(self) -> None:
        resp = _mock_response(output_text="got it")
        sequence = [_make_connection_error(), resp]
        ctx, _ = _patch_async_openai_with_side_effect(sequence)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            result = await call_openai("p", model="gpt-5.4")
        assert result.response_text == "got it"
        assert sleep_mock.await_count == 1


class TestCallOpenAIImportError:
    @pytest.mark.asyncio
    async def test_async_openai_importerror_re_raises(self) -> None:
        # Per .claude/rules/centralized-sdk-call.md: ImportError must
        # be raised UN-WRAPPED (not wrapped in OpenAIHelperError) so
        # the user gets a clean `pip install openai>=1.66.0` hint
        # path. Simulate the missing-SDK case by patching the
        # module-level AsyncOpenAI symbol to raise ImportError on
        # construction.
        with patch(
            "clauditor._providers._openai.AsyncOpenAI",
            side_effect=ImportError("No module named 'openai'"),
        ):
            with pytest.raises(ImportError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        # Not wrapped in OpenAIHelperError — bare ImportError.
        assert not isinstance(exc_info.value, OpenAIHelperError)


class TestCallOpenAITypeError:
    """Defense-in-depth wrap for SDK ``TypeError``.

    Per ``.claude/rules/precall-env-validation.md``: wrap
    ``TypeError`` (which the SDK raises when no auth is configured)
    as ``OpenAIHelperError`` with a fixed sanitized message — no
    ``str(exc)``, no ``exc.args``. Original exception preserved on
    ``__cause__`` via ``raise ... from exc``.
    """

    @pytest.mark.asyncio
    async def test_typeerror_at_construction_wrapped(self) -> None:
        # If AsyncOpenAI() construction raises TypeError (e.g. SDK
        # internal config error), the wrap should fire with the
        # fixed sanitized message.
        sdk_text = "Could not resolve authentication method"

        def _raise_at_construct(*args: object, **kwargs: object) -> None:
            raise TypeError(sdk_text)

        sleep_mock = AsyncMock()
        with patch(
            "clauditor._providers._openai.AsyncOpenAI",
            side_effect=_raise_at_construct,
        ), patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        assert "OpenAI SDK client initialization failed" in msg
        assert "OPENAI_API_KEY" in msg
        # SDK-sourced text must NOT leak into the user-facing message.
        assert sdk_text not in msg
        # No retry; construction failure is not transient.
        assert sleep_mock.await_count == 0
        assert isinstance(exc_info.value.__cause__, TypeError)
        assert sdk_text in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    async def test_typeerror_at_responses_create_wrapped(self) -> None:
        # If .responses.create raises TypeError, the wrap should
        # fire with the fixed sanitized message. No retry.
        sdk_text = "Could not resolve authentication method"
        ctx, fake_client = _patch_async_openai_with_side_effect(
            TypeError(sdk_text)
        )
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        assert "OpenAI SDK client initialization failed" in msg
        assert "OPENAI_API_KEY" in msg
        assert sdk_text not in msg
        assert fake_client.responses.create.await_count == 1
        assert sleep_mock.await_count == 0
        assert isinstance(exc_info.value.__cause__, TypeError)
        assert sdk_text in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    async def test_openai_error_at_construction_wrapped(self) -> None:
        """#145 QG pass 1 (Bug 2): the SDK raises ``openai.OpenAIError``
        (NOT ``TypeError``) at ``AsyncOpenAI()`` construction when
        ``OPENAI_API_KEY`` is unset. Without ``OpenAIError`` in the
        construction-site ``except`` tuple, the missing-key path bypasses
        the defense-in-depth and surfaces a raw SDK traceback.
        """
        from openai import OpenAIError

        sdk_text = "The api_key client option must be set"

        def _raise_at_construct(*args: object, **kwargs: object) -> None:
            raise OpenAIError(sdk_text)

        sleep_mock = AsyncMock()
        with patch(
            "clauditor._providers._openai.AsyncOpenAI",
            side_effect=_raise_at_construct,
        ), patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        assert "OpenAI SDK client initialization failed" in msg
        assert "OPENAI_API_KEY" in msg
        # SDK-sourced text must NOT leak into the user-facing message.
        assert sdk_text not in msg
        # No retry; construction failure is not transient.
        assert sleep_mock.await_count == 0
        # Original exception preserved on __cause__ via raise ... from exc.
        assert isinstance(exc_info.value.__cause__, OpenAIError)
        assert sdk_text in str(exc_info.value.__cause__)


class TestCallOpenAIBaseErrorCatchAll:
    """Bare base ``openai.OpenAIError`` catch-all (US-004 of #162).

    Pins DEC-003 (message format ``f"API request failed:
    {type(exc).__name__}"`` — class name only, no ``str(exc)`` per the
    post-merge security tightening: this branch handles unknown SDK
    error shapes by definition, so we cannot assume the SDK's
    ``__str__`` is well-behaved; diagnostic content is preserved on
    ``__cause__``), DEC-005 (two tests: catch-all wraps + ordering
    regression), and DEC-008 (the catch-all test MUST construct an
    ``OpenAIError`` instance NOT in any typed branch's hierarchy —
    otherwise the test passes via the typed branch and the catch-all
    is dead code).
    """

    @pytest.mark.asyncio
    async def test_bare_openai_error_wraps_to_helper_error(self) -> None:
        # DEC-008: define a one-line subclass guaranteed NOT to match
        # any typed branch (RateLimitError, APIStatusError,
        # AuthenticationError, PermissionDeniedError,
        # APIConnectionError, TypeError). This proves the catch-all
        # branch is exercised regardless of any future SDK changes
        # that might re-classify a bare OpenAIError() instance.
        from openai import OpenAIError

        class _UnknownOpenAIError(OpenAIError):
            pass

        # Use a payload that would be considered sensitive (simulates
        # a hypothetical future SDK error type echoing prompt text).
        # The catch-all message MUST NOT surface this content — it
        # only names the exception class.
        sdk_text = "user prompt fragment: SECRET_TOKEN abc123 " + "x" * 600
        ctx, fake_client = _patch_async_openai_with_side_effect(
            _UnknownOpenAIError(sdk_text)
        )
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        # DEC-003 (post-merge security tightening): message format is
        # exactly ``API request failed: <ClassName>`` — class name
        # only, no SDK message content.
        assert msg.startswith("API request failed:")
        assert "_UnknownOpenAIError" in msg
        # Security: NONE of the SDK's ``str(exc)`` content reaches the
        # user-facing message. Even a single-char prefix from the
        # untrusted payload would be a regression.
        assert "user prompt fragment" not in msg
        assert "SECRET_TOKEN" not in msg
        assert sdk_text not in msg
        assert sdk_text[:50] not in msg
        # Not retried: without category info, the catch-all cannot
        # make a sound retry decision.
        assert fake_client.responses.create.await_count == 1
        assert sleep_mock.await_count == 0
        # Diagnostic content preserved on ``__cause__`` — debuggers
        # can introspect the original exception (full ``str(exc)``
        # available via ``str(exc_info.value.__cause__)``).
        assert isinstance(exc_info.value.__cause__, _UnknownOpenAIError)
        assert isinstance(exc_info.value.__cause__, OpenAIError)
        assert "SECRET_TOKEN" in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    async def test_rate_limit_subclass_routes_to_specific_branch_not_catch_all(
        self,
    ) -> None:
        # DEC-005 ordering regression: ``RateLimitError`` is a
        # subclass of ``OpenAIError``, so without correct branch
        # ordering the bare catch-all would swallow it. Exhaust the
        # rate-limit retries and assert the message indicates the
        # rate-limit-specific branch (NOT the generic catch-all
        # phrasing) and the retry count proves the rate-limit
        # ladder applied.
        errors = [
            _make_rate_limit_error(body={"attempt": i}) for i in range(4)
        ]
        ctx, fake_client = _patch_async_openai_with_side_effect(errors)
        sleep_mock = AsyncMock()
        with ctx, patch(
            "clauditor._providers._openai._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            with pytest.raises(OpenAIHelperError) as exc_info:
                await call_openai("p", model="gpt-5.4")
        msg = str(exc_info.value)
        # Rate-limit-specific branch fired: message says "rate
        # limit", NOT the generic catch-all "API request failed:".
        assert "rate limit" in msg.lower()
        assert not msg.startswith("API request failed:")
        # The rate-limit ladder applied: 4 attempts (initial + 3
        # retries) per RATE_LIMIT_MAX_RETRIES=3.
        assert fake_client.responses.create.await_count == 4
