"""Tests for the centralized Anthropic SDK helper (bead clauditor-24h.3).

Covers each retry branch (success path, rate-limit backoff, server 5xx
retry, 4xx fail-fast, auth fail-fast, connection retry), token
extraction, defensive content/usage parsing, and the jitter formula.
Per ``.claude/rules/mock-side-effect-for-distinct-calls.md`` the retry
tests hand distinct return values per call so the retry arithmetic
touches real state rather than sliding through green.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

from clauditor._anthropic import (
    AnthropicAuthMissingError,
    AnthropicHelperError,
    AnthropicResult,
    _body_excerpt,
    _compute_backoff,
    _extract_result,
    call_anthropic,
    check_anthropic_auth,
)


def _mock_response(
    *,
    text: str = "ok",
    input_tokens: int = 42,
    output_tokens: int = 7,
    blocks: list | None = None,
    usage: object | None = ...,  # type: ignore[assignment]
) -> MagicMock:
    """Build a MagicMock shaped like an Anthropic SDK response."""
    resp = MagicMock()
    if blocks is None:
        block = MagicMock()
        block.type = "text"
        block.text = text
        blocks = [block]
    resp.content = blocks
    if usage is Ellipsis:
        resp.usage = MagicMock(
            input_tokens=input_tokens, output_tokens=output_tokens
        )
    else:
        resp.usage = usage
    return resp


def _make_rate_limit_error(
    *, message: str = "rate limit", body: object | None = None
) -> RateLimitError:
    req = httpx.Request("POST", "https://example.com/messages")
    httpx_resp = httpx.Response(429, request=req)
    return RateLimitError(message, response=httpx_resp, body=body)


def _make_status_error(
    status: int,
    *,
    message: str = "boom",
    body: object | None = None,
) -> APIStatusError:
    req = httpx.Request("POST", "https://example.com/messages")
    httpx_resp = httpx.Response(status, request=req)
    return APIStatusError(message, response=httpx_resp, body=body)


def _make_auth_error(
    *, message: str = "invalid key", body: object | None = None
) -> AuthenticationError:
    req = httpx.Request("POST", "https://example.com/messages")
    httpx_resp = httpx.Response(401, request=req)
    return AuthenticationError(message, response=httpx_resp, body=body)


def _make_permission_error() -> PermissionDeniedError:
    req = httpx.Request("POST", "https://example.com/messages")
    httpx_resp = httpx.Response(403, request=req)
    return PermissionDeniedError(
        "forbidden", response=httpx_resp, body={"error": {"message": "nope"}}
    )


def _make_connection_error() -> APIConnectionError:
    req = httpx.Request("POST", "https://example.com/messages")
    return APIConnectionError(message="connection reset", request=req)


class TestExtractResult:
    def test_joins_multiple_text_blocks(self) -> None:
        b1 = MagicMock(type="text", text="foo")
        b2 = MagicMock(type="text", text="bar")
        resp = _mock_response(blocks=[b1, b2])
        result = _extract_result(resp)
        assert result.response_text == "foobar"
        assert result.text_blocks == ["foo", "bar"]

    def test_filters_non_text_blocks(self) -> None:
        text_block = MagicMock(type="text", text="keep me")
        tool_block = MagicMock(spec=["type"])
        tool_block.type = "tool_use"
        resp = _mock_response(blocks=[tool_block, text_block])
        result = _extract_result(resp)
        assert result.response_text == "keep me"
        assert result.text_blocks == ["keep me"]

    def test_empty_content_returns_empty(self) -> None:
        resp = _mock_response(blocks=[])
        result = _extract_result(resp)
        assert result.response_text == ""
        assert result.text_blocks == []

    def test_missing_content_tolerated(self) -> None:
        resp = MagicMock()
        resp.content = None
        resp.usage = MagicMock(input_tokens=1, output_tokens=2)
        result = _extract_result(resp)
        assert result.response_text == ""
        assert result.input_tokens == 1
        assert result.output_tokens == 2

    def test_non_list_content_tolerated(self) -> None:
        resp = MagicMock()
        resp.content = "not a list"
        resp.usage = MagicMock(input_tokens=0, output_tokens=0)
        result = _extract_result(resp)
        assert result.text_blocks == []

    def test_missing_usage_defaults_to_zero(self) -> None:
        resp = _mock_response(usage=None)
        result = _extract_result(resp)
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_non_int_usage_coerced_to_zero(self) -> None:
        usage = MagicMock()
        usage.input_tokens = "not-a-number"
        usage.output_tokens = None
        resp = _mock_response(usage=usage)
        result = _extract_result(resp)
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_non_int_output_tokens_coerced_to_zero(self) -> None:
        # Covers the output_tokens-specific ValueError branch. The
        # input-tokens test above routes through the None branch for
        # output_tokens, so this test exercises the sister branch
        # with a non-coercible string.
        usage = MagicMock()
        usage.input_tokens = 0
        usage.output_tokens = "also-not-a-number"
        resp = _mock_response(usage=usage)
        result = _extract_result(resp)
        assert result.output_tokens == 0

    def test_raw_message_preserved(self) -> None:
        resp = _mock_response(text="x")
        result = _extract_result(resp)
        assert result.raw_message is resp


class TestBodyExcerpt:
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
        # the "<unrenderable body>" sentinel rather than propagate —
        # body-excerpt is a diagnostic path and should never be the
        # cause of an outage.
        class Bad:
            def __repr__(self) -> str:
                raise RuntimeError("nope")

        exc = MagicMock()
        exc.body = Bad()
        assert _body_excerpt(exc) == "<unrenderable body>"


class TestRandUniformDefault:
    def test_default_path_returns_value_in_range(self) -> None:
        # Most tests patch _rand_uniform; this one exercises the real
        # implementation so the stdlib-random wrapper itself is
        # covered. A few samples pin the contract: values stay inside
        # the requested closed interval.
        from clauditor._anthropic import _rand_uniform

        for _ in range(10):
            val = _rand_uniform(-0.25, 0.25)
            assert -0.25 <= val <= 0.25


class TestComputeBackoff:
    def test_delay_grows_exponentially(self) -> None:
        # With zero jitter, delays are 1, 2, 4 seconds.
        with patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            assert _compute_backoff(0) == pytest.approx(1.0)
            assert _compute_backoff(1) == pytest.approx(2.0)
            assert _compute_backoff(2) == pytest.approx(4.0)

    def test_positive_jitter_extends_delay(self) -> None:
        # Max positive jitter (0.25) → base * 1.25
        with patch(
            "clauditor._anthropic._rand_uniform", return_value=0.25
        ):
            assert _compute_backoff(0) == pytest.approx(1.25)
            assert _compute_backoff(2) == pytest.approx(5.0)

    def test_negative_jitter_shortens_delay(self) -> None:
        # Max negative jitter (-0.25) → base * 0.75
        with patch(
            "clauditor._anthropic._rand_uniform", return_value=-0.25
        ):
            assert _compute_backoff(0) == pytest.approx(0.75)
            assert _compute_backoff(1) == pytest.approx(1.5)

    def test_delay_never_negative(self) -> None:
        # Pathological jitter that tries to push delay negative is
        # floored at 0.
        with patch(
            "clauditor._anthropic._rand_uniform", return_value=-100.0
        ):
            assert _compute_backoff(0) == 0.0


class TestCallAnthropicSuccess:
    @pytest.mark.asyncio
    async def test_success_returns_result_with_tokens(self) -> None:
        resp = _mock_response(text="hello", input_tokens=200, output_tokens=80)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await call_anthropic(
                "hi", model="claude-sonnet-4-6", max_tokens=2048
            )
        assert isinstance(result, AnthropicResult)
        assert result.response_text == "hello"
        assert result.text_blocks == ["hello"]
        assert result.input_tokens == 200
        assert result.output_tokens == 80
        assert result.raw_message is resp

    @pytest.mark.asyncio
    async def test_success_passes_prompt_and_model(self) -> None:
        resp = _mock_response()
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            await call_anthropic(
                "PROMPT-BODY", model="test-model", max_tokens=123
            )
        call = mock_client.messages.create.await_args
        assert call.kwargs["model"] == "test-model"
        assert call.kwargs["max_tokens"] == 123
        assert call.kwargs["messages"] == [
            {"role": "user", "content": "PROMPT-BODY"}
        ]

    @pytest.mark.asyncio
    async def test_default_max_tokens_is_4096(self) -> None:
        resp = _mock_response()
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            await call_anthropic("p", model="m")
        assert (
            mock_client.messages.create.await_args.kwargs["max_tokens"]
            == 4096
        )


class TestCallAnthropicRateLimit:
    @pytest.mark.asyncio
    async def test_retries_three_times_then_raises(self) -> None:
        # Four failures: retries 0, 1, 2 then raise on attempt 4.
        # Distinct bodies per call prove the loop keeps iterating.
        errors = [
            _make_rate_limit_error(body={"attempt": i}) for i in range(4)
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=errors)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        assert "rate limit" in str(exc_info.value).lower()
        assert "429" in str(exc_info.value)
        # 4 create calls, 3 sleeps between them.
        assert mock_client.messages.create.await_count == 4
        assert sleep_mock.await_count == 3
        # Sleeps should be 1, 2, 4 with zero jitter.
        delays = [c.args[0] for c in sleep_mock.await_args_list]
        assert delays == [1.0, 2.0, 4.0]
        # Original SDK exception preserved via __cause__.
        assert isinstance(exc_info.value.__cause__, RateLimitError)

    @pytest.mark.asyncio
    async def test_recovery_after_two_retries(self) -> None:
        # Distinct retry errors followed by a success — per rule
        # mock-side-effect-for-distinct-calls.md, each call value is
        # unique so the retry loop actually iterates.
        resp = _mock_response(text="recovered", input_tokens=10, output_tokens=3)
        sequence = [
            _make_rate_limit_error(body={"n": 1}),
            _make_rate_limit_error(body={"n": 2}),
            resp,
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=sequence)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m")
        assert result.response_text == "recovered"
        assert mock_client.messages.create.await_count == 3
        assert sleep_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_backoff_respects_jitter_band(self) -> None:
        # Stub _rand_uniform to ±0.25 extremes; verify delays sit
        # inside the documented band.
        errors = [
            _make_rate_limit_error(body={"n": i}) for i in range(4)
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=errors)
        sleep_mock = AsyncMock()
        # Alternate max-positive and max-negative jitter to hit
        # both edges of the band per retry.
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform",
            side_effect=[0.25, -0.25, 0.25],
        ):
            with pytest.raises(AnthropicHelperError):
                await call_anthropic("p", model="m")
        delays = [c.args[0] for c in sleep_mock.await_args_list]
        # retry 0: base 1, +25% → 1.25
        # retry 1: base 2, -25% → 1.5
        # retry 2: base 4, +25% → 5.0
        assert delays[0] == pytest.approx(1.25)
        assert delays[1] == pytest.approx(1.5)
        assert delays[2] == pytest.approx(5.0)


class TestCallAnthropicServerError:
    @pytest.mark.asyncio
    async def test_503_retries_once_then_raises(self) -> None:
        # Two distinct 503s so the side_effect list documents both
        # the first and the retry arm; the retry is exhausted on the
        # second and the helper raises.
        errors = [
            _make_status_error(503, message="svc1", body={"n": 1}),
            _make_status_error(503, message="svc2", body={"n": 2}),
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=errors)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        assert "503" in str(exc_info.value)
        assert "server error" in str(exc_info.value).lower()
        assert mock_client.messages.create.await_count == 2
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_500_recovers_after_one_retry(self) -> None:
        resp = _mock_response(text="recovered", input_tokens=1, output_tokens=1)
        sequence = [
            _make_status_error(500, message="internal", body={"n": 1}),
            resp,
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=sequence)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m")
        assert result.response_text == "recovered"
        assert sleep_mock.await_count == 1


class TestCallAnthropicClientError:
    @pytest.mark.asyncio
    async def test_400_fails_fast_no_retry(self) -> None:
        err = _make_status_error(
            400, message="bad request", body={"error": {"message": "nope"}}
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=err)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        msg = str(exc_info.value)
        assert "400" in msg
        assert "bad request" in msg
        assert mock_client.messages.create.await_count == 1
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_404_fails_fast_no_retry(self) -> None:
        err = _make_status_error(404, message="not found", body=None)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=err)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        assert "404" in str(exc_info.value)
        assert mock_client.messages.create.await_count == 1
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_401_mentions_api_key_env_var(self) -> None:
        err = _make_auth_error(
            message="invalid x-api-key",
            body={"error": {"message": "invalid key"}},
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=err)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        msg = str(exc_info.value)
        assert "ANTHROPIC_API_KEY" in msg
        assert "401" in msg
        # Auth errors must not retry.
        assert mock_client.messages.create.await_count == 1
        assert sleep_mock.await_count == 0
        assert isinstance(exc_info.value.__cause__, AuthenticationError)

    @pytest.mark.asyncio
    async def test_403_also_mentions_api_key_env_var(self) -> None:
        err = _make_permission_error()
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=err)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        msg = str(exc_info.value)
        assert "ANTHROPIC_API_KEY" in msg
        assert "403" in msg
        assert sleep_mock.await_count == 0


class TestCallAnthropicConnectionError:
    @pytest.mark.asyncio
    async def test_retries_once_then_raises(self) -> None:
        # Distinct connection errors so the retry loop state is
        # observable (each error carries its own request object).
        errors = [_make_connection_error(), _make_connection_error()]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=errors)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        assert "connection" in str(exc_info.value).lower()
        assert mock_client.messages.create.await_count == 2
        assert sleep_mock.await_count == 1
        assert isinstance(exc_info.value.__cause__, APIConnectionError)

    @pytest.mark.asyncio
    async def test_recovers_after_one_retry(self) -> None:
        resp = _mock_response(text="got it")
        sequence = [_make_connection_error(), resp]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=sequence)
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m")
        assert result.response_text == "got it"
        assert sleep_mock.await_count == 1


class TestCallAnthropicImportError:
    @pytest.mark.asyncio
    async def test_missing_sdk_raises_importerror(self) -> None:
        # Simulate the SDK being uninstalled by making the in-function
        # import raise. The helper must re-raise ImportError with a
        # message that mentions the grader extra so CLI callers can
        # surface the existing install hint.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(ImportError) as exc_info:
                await call_anthropic("p", model="m")
        assert "clauditor[grader]" in str(exc_info.value)


class TestCallAnthropicTypeError:
    """Defense-in-depth wrap for SDK ``TypeError`` (US-002 / clauditor-2df.2).

    Pins DEC-008 (wrap as ``AnthropicHelperError`` not
    ``AnthropicAuthMissingError`` — the pre-flight guard in US-003
    catches the missing-key case first at exit 2; the wrap exists
    for the bypass case which is exit 3 territory), and DEC-015 (no
    SDK exception text in the user-facing message; original
    exception preserved via ``__cause__``).
    """

    @pytest.mark.asyncio
    async def test_sdk_typeerror_wrapped_as_helper_error(self) -> None:
        # Simulate the current SDK behavior when no key is set and
        # ``messages.create`` is reached: the SDK raises
        # ``TypeError: Could not resolve authentication method``.
        sdk_text = "Could not resolve authentication method"
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=TypeError(sdk_text)
        )
        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        msg = str(exc_info.value)
        # Fixed sanitized message contains the DEC-015 anchors.
        assert "Anthropic SDK client initialization failed" in msg
        assert "ANTHROPIC_API_KEY" in msg
        # SDK-sourced text must NOT leak into the user-facing message.
        assert sdk_text not in msg
        # TypeError is a config error, not transient — no retry.
        assert mock_client.messages.create.await_count == 1
        assert sleep_mock.await_count == 0
        # Original exception preserved via __cause__ for debugging.
        assert isinstance(exc_info.value.__cause__, TypeError)
        assert sdk_text in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    async def test_sdk_typeerror_at_construction_wrapped(self) -> None:
        # Future-proofing: if a future Anthropic SDK moves the
        # ``TypeError: Could not resolve authentication method`` site
        # from ``messages.create`` to ``AsyncAnthropic.__init__``, the
        # wrap should still fire. Patch ``AsyncAnthropic`` to raise
        # ``TypeError`` at construction, assert the same sanitized
        # ``AnthropicHelperError`` surface.
        sdk_text = "Could not resolve authentication method"

        def _raise_at_construct(*args: object, **kwargs: object) -> None:
            raise TypeError(sdk_text)

        sleep_mock = AsyncMock()
        with patch(
            "anthropic.AsyncAnthropic", side_effect=_raise_at_construct
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m")
        msg = str(exc_info.value)
        assert "Anthropic SDK client initialization failed" in msg
        assert "ANTHROPIC_API_KEY" in msg
        assert sdk_text not in msg
        # No retry; construction failure is not transient.
        assert sleep_mock.await_count == 0
        assert isinstance(exc_info.value.__cause__, TypeError)
        assert sdk_text in str(exc_info.value.__cause__)


class TestCheckAnthropicAuth:
    """Unit tests for the pre-flight auth guard (clauditor-2df.1 / US-001).

    Pins DEC-001 (only ``ANTHROPIC_API_KEY`` counts),
    DEC-010 (new exception class distinct from ``AnthropicHelperError``),
    DEC-011 (message template with ``{cmd_name}`` substitution), and
    DEC-012 (three test-asserted substrings: ``ANTHROPIC_API_KEY``,
    ``Claude Pro``, ``console.anthropic.com``).
    """

    def test_key_present_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert check_anthropic_auth("grade") is None

    def test_key_absent_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_anthropic_auth("grade")
        message = str(exc_info.value)
        # DEC-012 durable anchors.
        assert "ANTHROPIC_API_KEY" in message
        assert "Claude Pro" in message
        assert "console.anthropic.com" in message
        # DEC-011: command-name interpolation.
        assert "clauditor grade" in message

    def test_key_empty_string_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(AnthropicAuthMissingError):
            check_anthropic_auth("grade")

    def test_key_whitespace_only_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   \t\n")
        with pytest.raises(AnthropicAuthMissingError):
            check_anthropic_auth("grade")

    def test_auth_token_only_still_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-001: only ANTHROPIC_API_KEY counts, not ANTHROPIC_AUTH_TOKEN."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "some-token")
        with pytest.raises(AnthropicAuthMissingError):
            check_anthropic_auth("grade")
