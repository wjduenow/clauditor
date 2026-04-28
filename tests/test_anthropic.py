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
    ClaudeCLIError,
    _body_excerpt,
    _classify_invoke_result,
    _compute_backoff,
    _compute_retry_decision,
    _extract_result,
    announce_implicit_no_api_key,
    call_anthropic,
    check_any_auth_available,
    check_api_key_only,
    resolve_transport,
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
                "hi",
                model="claude-sonnet-4-6",
                max_tokens=2048,
                transport="api",
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
                "PROMPT-BODY",
                model="test-model",
                max_tokens=123,
                transport="api",
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
            await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
            result = await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
            result = await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
            result = await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
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
                await call_anthropic("p", model="m", transport="api")
        msg = str(exc_info.value)
        assert "Anthropic SDK client initialization failed" in msg
        assert "ANTHROPIC_API_KEY" in msg
        assert sdk_text not in msg
        # No retry; construction failure is not transient.
        assert sleep_mock.await_count == 0
        assert isinstance(exc_info.value.__cause__, TypeError)
        assert sdk_text in str(exc_info.value.__cause__)


def _patch_which(monkeypatch: pytest.MonkeyPatch, path: str | None) -> None:
    """Pin ``shutil.which("claude")`` to a deterministic value.

    The autouse ``_force_api_transport_in_tests`` fixture in
    ``tests/conftest.py`` patches ``shutil.which`` on
    ``clauditor._anthropic`` to return ``None``. Tests that exercise
    the CLI-available branch of :func:`check_any_auth_available`
    override that with an explicit path.
    """
    import clauditor._anthropic as _anthropic

    monkeypatch.setattr(
        _anthropic.shutil, "which", lambda name: path
    )


class TestCheckAnyAuthAvailable:
    """Unit tests for the relaxed pre-flight auth guard (US-005 / DEC-008).

    Pins DEC-008 of ``plans/super/86-claude-cli-transport.md`` (relax
    the pre-flight guard to accept either ``ANTHROPIC_API_KEY`` or a
    ``claude`` CLI binary on PATH) and DEC-015 (error-message copy
    committed with four test-asserted durable substrings:
    ``ANTHROPIC_API_KEY``, ``Claude Pro``, ``console.anthropic.com``,
    ``claude CLI``).
    """

    def test_key_present_cli_absent_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, None)
        assert check_any_auth_available("grade") is None

    def test_key_absent_cli_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_both_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_both_absent_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_any_auth_available("grade")
        message = str(exc_info.value)
        # DEC-015 four durable anchors.
        assert "ANTHROPIC_API_KEY" in message
        assert "Claude Pro" in message
        assert "console.anthropic.com" in message
        assert "claude CLI" in message
        # DEC-011 command-name interpolation preserved.
        assert "clauditor grade" in message

    def test_empty_string_key_no_cli_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_any_auth_available("grade")

    def test_whitespace_only_key_no_cli_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   \t\n")
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_any_auth_available("grade")

    def test_empty_string_key_cli_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI presence overrides an empty-string API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_whitespace_only_key_cli_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI presence overrides a whitespace-only API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_auth_token_only_no_cli_still_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-001 (#83) preserved: only ANTHROPIC_API_KEY counts for the key branch."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "some-token")
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_any_auth_available("grade")

    def test_key_whitespace_surrounded_no_cli_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-empty value with surrounding whitespace counts as present."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-test  ")
        _patch_which(monkeypatch, None)
        assert check_any_auth_available("grade") is None

    def test_cmd_name_interpolation_compare_blind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``compare --blind`` is the only multi-word cmd_name; interpolation works."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_any_auth_available("compare --blind")
        assert "clauditor compare --blind" in str(exc_info.value)

    def test_cmd_name_interpolation_propose_eval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_any_auth_available("propose-eval")
        assert "clauditor propose-eval" in str(exc_info.value)


class TestCheckApiKeyOnly:
    """Unit tests for the strict API-key-only pre-flight guard (US-005 / DEC-009).

    Pins DEC-009 of ``plans/super/86-claude-cli-transport.md``: pytest
    fixtures stay stricter than the CLI by default. The strict variant
    only accepts ``ANTHROPIC_API_KEY``; CLI presence does not help.
    """

    def test_key_present_passes_regardless_of_cli(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, None)
        assert check_api_key_only("grader") is None

    def test_key_present_cli_also_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_api_key_only("grader") is None

    def test_key_absent_cli_present_still_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-009: strict variant does NOT accept CLI as a fallback."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_api_key_only("grader")
        message = str(exc_info.value)
        # Preserves #83 DEC-012's three durable anchors.
        assert "ANTHROPIC_API_KEY" in message
        assert "Claude Pro" in message
        assert "console.anthropic.com" in message
        # Mentions the opt-in escape hatch so users know how to enable
        # CLI transport in fixture mode.
        assert "CLAUDITOR_FIXTURE_ALLOW_CLI" in message
        # Command-name interpolation.
        assert "clauditor grader" in message

    def test_key_empty_string_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(AnthropicAuthMissingError):
            check_api_key_only("grader")


# ---------------------------------------------------------------------------
# US-003 (#86): CLI transport in call_anthropic
# ---------------------------------------------------------------------------


class TestComputeRetryDecision:
    """Pure helper extracted per DEC-005 retry parity.

    Shared by SDK and CLI transport branches so a failure with the
    same category retries the same number of times regardless of
    which transport produced it.
    """

    def test_rate_limit_retries_up_to_three_times(self) -> None:
        assert _compute_retry_decision("rate_limit", 0) == "retry"
        assert _compute_retry_decision("rate_limit", 1) == "retry"
        assert _compute_retry_decision("rate_limit", 2) == "retry"

    def test_rate_limit_raises_after_third_retry(self) -> None:
        assert _compute_retry_decision("rate_limit", 3) == "raise"

    def test_auth_never_retries(self) -> None:
        assert _compute_retry_decision("auth", 0) == "raise"
        assert _compute_retry_decision("auth", 5) == "raise"

    def test_api_retries_once_then_raises(self) -> None:
        assert _compute_retry_decision("api", 0) == "retry"
        assert _compute_retry_decision("api", 1) == "raise"

    def test_connection_retries_once_then_raises(self) -> None:
        assert _compute_retry_decision("connection", 0) == "retry"
        assert _compute_retry_decision("connection", 1) == "raise"

    def test_transport_retries_once_then_raises(self) -> None:
        assert _compute_retry_decision("transport", 0) == "retry"
        assert _compute_retry_decision("transport", 1) == "raise"

    def test_unknown_category_raises(self) -> None:
        """Defensive default: an unknown category is not retried."""
        assert _compute_retry_decision("mystery", 0) == "raise"

    def test_empty_string_category_raises(self) -> None:
        """Defensive default: empty-string category is not retried."""
        assert _compute_retry_decision("", 0) == "raise"


# ---------------------------------------------------------------------------
# _FakePopen-driven fixtures for CLI-transport tests. The real Popen is
# mocked out at the ``clauditor.runner`` seam per the centralized
# subprocess-mocking style used in tests/test_runner.py.
# ---------------------------------------------------------------------------


def _make_cli_success_popen(
    text: str = "cli-output-text",
    input_tokens: int = 15,
    output_tokens: int = 9,
):
    """Build a _FakePopen emitting a successful stream-json sequence."""
    from tests.conftest import make_fake_skill_stream

    return make_fake_skill_stream(
        text, input_tokens=input_tokens, output_tokens=output_tokens
    )


def _make_cli_error_popen(error_text: str):
    """Build a _FakePopen emitting an ``is_error: true`` result message."""
    from tests.conftest import make_fake_skill_stream

    return make_fake_skill_stream("partial", error_text=error_text)


class TestCallViaClaudeCli:
    """End-to-end tests for the CLI-transport branch of ``call_anthropic``.

    Every test explicitly passes ``transport="cli"`` so it routes through
    the subprocess branch regardless of whether the test host has
    ``claude`` on PATH. Subprocess mocked at the
    ``clauditor._harnesses._claude_code.subprocess.Popen`` seam (same pattern as
    tests/test_runner.py). _sleep patched so retry waits cost zero
    wallclock.
    """

    @pytest.mark.asyncio
    async def test_success_returns_result_with_source_cli(self) -> None:
        fake = _make_cli_success_popen("hello from cli")
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            result = await call_anthropic(
                "some prompt", model="claude-sonnet-4-6", transport="cli"
            )
        assert isinstance(result, AnthropicResult)
        assert result.source == "cli"
        assert result.response_text == "hello from cli"
        assert result.text_blocks == ["hello from cli"]
        assert result.raw_message is None
        assert result.duration_seconds >= 0.0
        # No retries on success.
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_success_populates_token_counts(self) -> None:
        fake = _make_cli_success_popen(
            "ok", input_tokens=123, output_tokens=45
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            result = await call_anthropic("p", model="m", transport="cli")
        assert result.input_tokens == 123
        assert result.output_tokens == 45

    @pytest.mark.asyncio
    async def test_rate_limit_retries_up_to_three_times(self) -> None:
        """DEC-005 parity: ``rate_limit`` category → up to 3 retries."""
        # Four failure Popens, one per attempt (0..3). The fifth
        # attempt is never reached — retry budget exhausted at 3.
        fakes = [
            _make_cli_error_popen("429 Too Many Requests"),
            _make_cli_error_popen("429 Too Many Requests"),
            _make_cli_error_popen("429 Too Many Requests"),
            _make_cli_error_popen("429 Too Many Requests"),
        ]
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", side_effect=fakes
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        assert exc_info.value.category == "rate_limit"
        msg = str(exc_info.value)
        assert "rate limit exceeded" in msg.lower()
        assert "(transport=cli, category=rate_limit)" in msg
        # 4 subprocesses, 3 retries in between.
        assert sleep_mock.await_count == 3
        # __cause__ preserved per DEC-014.
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_rate_limit_recovers_after_two_retries(self) -> None:
        fakes = [
            _make_cli_error_popen("429 rate limit"),
            _make_cli_error_popen("429 rate limit"),
            _make_cli_success_popen("recovered"),
        ]
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", side_effect=fakes
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m", transport="cli")
        assert result.response_text == "recovered"
        assert result.source == "cli"
        assert sleep_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_auth_no_retry(self) -> None:
        """DEC-005 parity: ``auth`` category → no retry, raise immediately."""
        fake = _make_cli_error_popen("401 Unauthorized: invalid api key")
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        assert exc_info.value.category == "auth"
        msg = str(exc_info.value)
        assert "Claude CLI authentication failed" in msg
        assert "(transport=cli, category=auth)" in msg
        # ``claude`` interactive refresh hint present per DEC-014.
        assert "`claude`" in msg
        assert "--transport api" in msg
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_api_5xx_retries_once_then_raises(self) -> None:
        """DEC-005 parity: ``api`` category → 1 retry then raise."""
        fakes = [
            _make_cli_error_popen("500 Internal Server Error: upstream"),
            _make_cli_error_popen("503 Service Unavailable"),
        ]
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", side_effect=fakes
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        assert exc_info.value.category == "api"
        msg = str(exc_info.value)
        assert "(transport=cli, category=api)" in msg
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_api_5xx_recovers_after_one_retry(self) -> None:
        fakes = [
            _make_cli_error_popen("500 Internal Server Error"),
            _make_cli_success_popen("recovered"),
        ]
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", side_effect=fakes
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m", transport="cli")
        assert result.response_text == "recovered"
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_filenotfounderror_retries_once_then_raises(self) -> None:
        """Binary missing → ``transport`` category → 1 retry then raise."""
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=FileNotFoundError,
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        assert exc_info.value.category == "transport"
        msg = str(exc_info.value)
        assert "Claude CLI subprocess failed" in msg
        assert "(transport=cli, category=transport)" in msg
        # 2 attempts (initial + 1 retry), 1 sleep between them.
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_timeout_kills_process_and_raises_transport_error(
        self,
    ) -> None:
        """Watchdog firing → ``transport`` category → 1 retry then raise."""
        import threading

        class _ImmediateTimer:
            def __init__(
                self, interval, function, args=None, kwargs=None
            ) -> None:
                self.function = function
                self.daemon = True

            def start(self) -> None:
                self.function()

            def cancel(self) -> None:
                pass

        fakes = [_make_cli_success_popen("p1"), _make_cli_success_popen("p2")]
        # Override so poll() returns None (still running) and kill() fires.
        for f in fakes:
            f.poll = lambda: None
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", side_effect=fakes
        ), patch(
            "clauditor._harnesses._claude_code.threading.Timer", _ImmediateTimer
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        _ = threading  # silence unused import warning
        assert exc_info.value.category == "transport"
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_transport_error_recovers_after_one_retry(self) -> None:
        """Transient transport failure + success on retry → success result."""

        def _popen_factory(*args, **kwargs):
            # First call raises FileNotFoundError; second returns success.
            if _popen_factory.count == 0:
                _popen_factory.count += 1
                raise FileNotFoundError
            return _make_cli_success_popen("recovered after transport")

        _popen_factory.count = 0

        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=_popen_factory
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m", transport="cli")
        assert result.response_text == "recovered after transport"
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_malformed_ndjson_surfaces_as_transport_error(self) -> None:
        """A stream with only malformed lines → no output → transport category."""
        import io

        class _BrokenPopen:
            def __init__(self) -> None:
                # Only malformed lines. No ``result`` message, no
                # assistant text. ``ClaudeCodeHarness.invoke`` will emit
                # warnings + return exit_code=0 with empty output.
                self.stdout = io.StringIO(
                    "this is not json\nneither is this\n"
                )
                self.stderr = iter(())
                self.returncode = 0
                self._killed = False

            def wait(self, timeout=None):
                return self.returncode

            def kill(self) -> None:
                self._killed = True

            def terminate(self) -> None:
                self._killed = True

            def poll(self) -> int | None:
                return self.returncode if self._killed else None

        # Retry to a success on attempt 2.
        fakes = [_BrokenPopen(), _make_cli_success_popen("healed")]
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", side_effect=fakes
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            result = await call_anthropic("p", model="m", transport="cli")
        assert result.response_text == "healed"
        assert sleep_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_env_strips_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-013: ``ANTHROPIC_API_KEY`` is stripped from child env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "also-secret")
        monkeypatch.setenv("PATH", "/usr/bin")
        mock_popen = MagicMock()
        mock_popen.return_value = _make_cli_success_popen("ok")
        with patch("clauditor._harnesses._claude_code.subprocess.Popen", mock_popen):
            await call_anthropic("p", model="m", transport="cli")
        call_kwargs = mock_popen.call_args.kwargs
        env = call_kwargs["env"]
        assert env is not None, "env must be an explicit dict under CLI transport"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env.get("PATH") == "/usr/bin"

    @pytest.mark.asyncio
    async def test_claude_cli_error_is_anthropic_helper_error(self) -> None:
        """DEC-006: ``ClaudeCLIError`` is a subclass of ``AnthropicHelperError``.

        Ensures every existing ``except AnthropicHelperError:`` caller
        catches CLI-transport failures transparently.
        """
        fake = _make_cli_error_popen("401 Unauthorized")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            with pytest.raises(AnthropicHelperError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        assert isinstance(exc_info.value, ClaudeCLIError)

    @pytest.mark.asyncio
    async def test_error_message_does_not_echo_stream_json_result_text(
        self,
    ) -> None:
        """DEC-014 sanitization: user-facing message must not leak the
        provider's stream-json ``result`` text."""
        secret_leak = "secret-provider-text-should-not-appear-in-message"
        fake = _make_cli_error_popen(f"401 Unauthorized: {secret_leak}")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        msg = str(exc_info.value)
        assert secret_leak not in msg

    @pytest.mark.asyncio
    async def test_subject_labels_stderr_apikeysource_line(
        self, capsys
    ) -> None:
        """Issue #107: ``subject=`` threads through to the CLI's
        ``apiKeySource`` stderr line so operators running
        ``grade --transport cli`` can attribute each telemetry line to a
        specific internal LLM call (e.g. L2 extraction vs L3 grading).
        """
        from tests.conftest import make_fake_skill_stream

        fake = make_fake_skill_stream(
            "ok",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "none",
            },
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            await call_anthropic(
                "p",
                model="m",
                transport="cli",
                subject="L2 extraction",
            )
        captured = capsys.readouterr()
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        assert len(matching) == 1, captured.err
        assert (
            matching[0]
            == "clauditor.runner: apiKeySource=none (L2 extraction)"
        )

    @pytest.mark.asyncio
    async def test_subject_none_preserves_unlabeled_stderr_line(
        self, capsys
    ) -> None:
        """Issue #107 acceptance criterion 4: existing format unchanged
        when ``subject`` is not threaded through."""
        from tests.conftest import make_fake_skill_stream

        fake = make_fake_skill_stream(
            "ok",
            init_message={
                "type": "system",
                "subtype": "init",
                "apiKeySource": "none",
            },
        )
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            await call_anthropic("p", model="m", transport="cli")
        captured = capsys.readouterr()
        matching = [
            line
            for line in captured.err.splitlines()
            if "apiKeySource=" in line
        ]
        assert matching == ["clauditor.runner: apiKeySource=none"]


class TestAnthropicResultFields:
    """Cover :attr:`AnthropicResult.source` and :attr:`duration_seconds`.

    DEC-007 (source) + DEC-020 (duration).
    """

    @pytest.mark.asyncio
    async def test_source_is_api_under_sdk_transport(self) -> None:
        resp = _mock_response(text="sdk-path")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await call_anthropic(
                "p", model="m", transport="api"
            )
        assert result.source == "api"

    @pytest.mark.asyncio
    async def test_source_is_cli_under_cli_transport(self) -> None:
        fake = _make_cli_success_popen("cli-path")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            result = await call_anthropic(
                "p", model="m", transport="cli"
            )
        assert result.source == "cli"

    @pytest.mark.asyncio
    async def test_duration_seconds_populated_under_sdk(self) -> None:
        """``_monotonic`` returns two distinct values → duration > 0."""
        resp = _mock_response(text="ok")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ), patch(
            "clauditor._anthropic._monotonic",
            side_effect=[100.0, 101.5],
        ):
            result = await call_anthropic(
                "p", model="m", transport="api"
            )
        assert result.duration_seconds == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_duration_seconds_populated_under_cli(self) -> None:
        fake = _make_cli_success_popen("cli")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
        ), patch(
            "clauditor._anthropic._monotonic",
            side_effect=[10.0, 12.25],
        ):
            result = await call_anthropic(
                "p", model="m", transport="cli"
            )
        assert result.duration_seconds == pytest.approx(2.25)


class TestAutoTransportResolution:
    """DEC-001 auto-resolution via ``shutil.which("claude")``."""

    @pytest.mark.asyncio
    async def test_auto_picks_cli_when_claude_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reset the one-shot announcement flag so this test sees a
        # fresh process from the stderr-announcement perspective.
        monkeypatch.setattr(
            "clauditor._anthropic._announced_cli_transport", False
        )
        fake = _make_cli_success_popen("cli-picked")
        with patch(
            "clauditor._anthropic.shutil.which",
            return_value="/usr/local/bin/claude",
        ), patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
        ):
            result = await call_anthropic(
                "p", model="m", transport="auto"
            )
        assert result.source == "cli"

    @pytest.mark.asyncio
    async def test_auto_picks_api_when_claude_not_on_path(
        self,
    ) -> None:
        resp = _mock_response(text="api-picked")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch(
            "clauditor._anthropic.shutil.which", return_value=None
        ), patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ):
            result = await call_anthropic(
                "p", model="m", transport="auto"
            )
        assert result.source == "api"

    @pytest.mark.asyncio
    async def test_explicit_api_forces_sdk_even_when_cli_available(
        self,
    ) -> None:
        """``transport="api"`` must NOT call ``shutil.which``."""
        resp = _mock_response(text="forced-api")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch(
            "clauditor._anthropic.shutil.which",
            return_value="/usr/local/bin/claude",
        ) as which_mock, patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ):
            result = await call_anthropic(
                "p", model="m", transport="api"
            )
        assert result.source == "api"
        # Explicit transport does not probe PATH.
        assert which_mock.call_count == 0

    @pytest.mark.asyncio
    async def test_explicit_cli_forces_subprocess_regardless_of_path(
        self,
    ) -> None:
        """``transport="cli"`` must still attempt the subprocess even
        when the binary is missing — the error surfaces as a
        ``ClaudeCLIError(category="transport")`` rather than a
        silent fallback to the SDK path."""
        sleep_mock = AsyncMock()
        with patch(
            "clauditor._anthropic.shutil.which", return_value=None
        ), patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=FileNotFoundError,
        ), patch(
            "clauditor._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._anthropic._rand_uniform", return_value=0.0
        ):
            with pytest.raises(ClaudeCLIError) as exc_info:
                await call_anthropic("p", model="m", transport="cli")
        assert exc_info.value.category == "transport"


class TestStderrAnnouncement:
    """DEC-019 one-shot ``auto → CLI`` announcement."""

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False."""
        monkeypatch.setattr(
            "clauditor._anthropic._announced_cli_transport", False
        )

    @pytest.mark.asyncio
    async def test_first_auto_to_cli_resolution_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _make_cli_success_popen("ok")
        with patch(
            "clauditor._anthropic.shutil.which",
            return_value="/usr/local/bin/claude",
        ), patch(
            "clauditor._harnesses._claude_code.subprocess.Popen", return_value=fake
        ):
            await call_anthropic("p", model="m", transport="auto")
        captured = capsys.readouterr()
        assert (
            "clauditor: using Claude CLI transport (subscription auth)"
            in captured.err
        )
        assert "pass --transport api to opt out" in captured.err

    @pytest.mark.asyncio
    async def test_second_auto_to_cli_does_not_reemit(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake1 = _make_cli_success_popen("ok1")
        fake2 = _make_cli_success_popen("ok2")
        with patch(
            "clauditor._anthropic.shutil.which",
            return_value="/usr/local/bin/claude",
        ), patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=[fake1, fake2]
        ):
            await call_anthropic("p1", model="m", transport="auto")
            # Drain the first emission before the second call.
            capsys.readouterr()
            await call_anthropic("p2", model="m", transport="auto")
        # Second call: no announcement.
        captured = capsys.readouterr()
        assert (
            "clauditor: using Claude CLI transport"
            not in captured.err
        )

    @pytest.mark.asyncio
    async def test_explicit_cli_does_not_emit_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DEC-019: explicit ``transport="cli"`` never announces
        (no surprise; caller chose it)."""
        fake = _make_cli_success_popen("ok")
        with patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            return_value=fake,
        ):
            await call_anthropic("p", model="m", transport="cli")
        captured = capsys.readouterr()
        assert (
            "clauditor: using Claude CLI transport"
            not in captured.err
        )

    @pytest.mark.asyncio
    async def test_explicit_api_does_not_emit_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resp = _mock_response(text="ok")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            await call_anthropic("p", model="m", transport="api")
        captured = capsys.readouterr()
        assert (
            "clauditor: using Claude CLI transport"
            not in captured.err
        )

    @pytest.mark.asyncio
    async def test_auto_to_api_does_not_emit_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When auto resolves to API (no ``claude`` on PATH), no
        announcement — it's the prior default, no surprise."""
        resp = _mock_response(text="ok")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch(
            "clauditor._anthropic.shutil.which", return_value=None
        ), patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ):
            await call_anthropic("p", model="m", transport="auto")
        captured = capsys.readouterr()
        assert (
            "clauditor: using Claude CLI transport"
            not in captured.err
        )


class TestAnnounceImplicitNoApiKey:
    """DEC-003 / DEC-009 / DEC-011 (#95 US-002): one-shot stderr notice
    emitted when ``--transport cli`` strips ``ANTHROPIC_API_KEY`` /
    ``ANTHROPIC_AUTH_TOKEN`` from the skill subprocess env.

    Parallel to :class:`TestStderrAnnouncement` — same autouse-reset
    pattern; same one-shot-per-process contract.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False."""
        monkeypatch.setattr(
            "clauditor._anthropic._announced_implicit_no_api_key", False
        )

    def test_first_call_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT in captured.err

    def test_second_call_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        announce_implicit_no_api_key()
        # Drain the first emission.
        capsys.readouterr()
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_constant_names_both_env_vars(self) -> None:
        """Prose-presence check — both env-var names must appear so
        users know which variables got stripped."""
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert "ANTHROPIC_API_KEY" in _IMPLICIT_NO_API_KEY_ANNOUNCEMENT
        assert "ANTHROPIC_AUTH_TOKEN" in _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

    def test_constant_names_escape_hatch(self) -> None:
        """DEC-011: users must see the explicit-opt-out path."""
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert "--transport api" in _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

    def test_autouse_fixture_resets_flag_between_tests_first_half(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """First test of a pair — proves first-call emission works."""
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        assert captured.err != ""

    def test_autouse_fixture_resets_flag_between_tests_second_half(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second test of the pair — if the autouse fixture did not
        reset the flag, this test would see silence (the flag would
        still be ``True`` from the first test). Seeing an emission
        here proves the fixture reset works."""
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        assert captured.err != ""


class TestResolveTransport:
    """DEC-012 / DEC-017 of #86: four-layer precedence resolution.

    Pure helper: CLI override > env override > spec value > default
    ``"auto"``. Invalid values at any layer raise ``ValueError`` with
    a message that names the layer so the CLI can route to exit 2.
    """

    def test_cli_wins_over_env(self) -> None:
        """CLI override beats env var, regardless of env content."""
        assert resolve_transport("api", "cli", "auto") == "api"

    def test_env_wins_over_spec(self) -> None:
        """Env var beats spec field when CLI override is None."""
        assert resolve_transport(None, "cli", "auto") == "cli"

    def test_spec_wins_over_default(self) -> None:
        """Spec field beats default when CLI + env are both None."""
        assert resolve_transport(None, None, "cli") == "cli"

    def test_all_none_returns_default_auto(self) -> None:
        """All three ``None`` → default ``"auto"``."""
        assert resolve_transport(None, None, None) == "auto"

    def test_cli_wins_over_all_layers(self) -> None:
        """CLI is the supreme winner, even when env AND spec are set."""
        assert resolve_transport("api", "cli", "cli") == "api"

    def test_cli_auto_does_not_short_circuit_to_env(self) -> None:
        """A CLI override of ``"auto"`` is still a set value — env and
        spec layers must not override it."""
        assert resolve_transport("auto", "cli", "api") == "auto"

    def test_invalid_cli_raises(self) -> None:
        """Invalid CLI value raises with the layer name in the message."""
        with pytest.raises(
            ValueError,
            match=r"CLI --transport must be one of 'api', 'cli', 'auto', got 'sdk'",
        ):
            resolve_transport("sdk", None, None)

    def test_invalid_env_raises(self) -> None:
        """Invalid env var value raises with the layer name in the message."""
        with pytest.raises(
            ValueError,
            match=r"CLAUDITOR_TRANSPORT must be one of 'api', 'cli', 'auto', got 'sdk'",
        ):
            resolve_transport(None, "sdk", None)

    def test_invalid_spec_raises(self) -> None:
        """Invalid spec value raises with the layer name in the message.

        Normally :meth:`EvalSpec.from_dict` rejects these at load time,
        but the helper is defensive — a caller that builds an
        ``EvalSpec`` via the direct constructor without validation
        could still reach this branch.
        """
        with pytest.raises(
            ValueError,
            match=r"EvalSpec.transport must be one of 'api', 'cli', 'auto', got 'sdk'",
        ):
            resolve_transport(None, None, "sdk")

    def test_all_api_returns_api(self) -> None:
        """Every layer set to ``"api"`` → returns ``"api"``."""
        assert resolve_transport("api", "api", "api") == "api"

    def test_env_auto_overrides_spec_cli(self) -> None:
        """Env override of ``"auto"`` wins over spec even though spec is set."""
        assert resolve_transport(None, "auto", "cli") == "auto"


class TestClassifyInvokeResult:
    """Pure-function unit tests for ``_classify_invoke_result``.

    Covers the three branches that live-runner tests don't reach:
    - ``error_category == "timeout"``  → ``"transport"``
    - ``error_category == "subprocess"`` → ``"transport"``
    - non-zero ``exit_code`` with no classification → ``"transport"``
    """

    def _make_invoke(self, **kwargs):
        from types import SimpleNamespace

        defaults = dict(exit_code=0, error_category=None, output="some output")
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_timeout_category_returns_transport(self) -> None:
        invoke = self._make_invoke(exit_code=1, error_category="timeout")
        assert _classify_invoke_result(invoke) == "transport"

    def test_subprocess_category_returns_transport(self) -> None:
        invoke = self._make_invoke(exit_code=1, error_category="subprocess")
        assert _classify_invoke_result(invoke) == "transport"

    def test_nonzero_exit_no_category_returns_transport(self) -> None:
        invoke = self._make_invoke(exit_code=1, error_category=None)
        assert _classify_invoke_result(invoke) == "transport"

    def test_rate_limit_category_returns_rate_limit(self) -> None:
        invoke = self._make_invoke(exit_code=1, error_category="rate_limit")
        assert _classify_invoke_result(invoke) == "rate_limit"

    def test_auth_category_returns_auth(self) -> None:
        invoke = self._make_invoke(exit_code=1, error_category="auth")
        assert _classify_invoke_result(invoke) == "auth"

    def test_api_category_returns_api(self) -> None:
        invoke = self._make_invoke(exit_code=1, error_category="api")
        assert _classify_invoke_result(invoke) == "api"

    def test_exit_minus_one_returns_transport(self) -> None:
        invoke = self._make_invoke(exit_code=-1, error_category=None, output="")
        assert _classify_invoke_result(invoke) == "transport"

    def test_success_returns_none(self) -> None:
        invoke = self._make_invoke(
            exit_code=0, error_category=None, output="result text"
        )
        assert _classify_invoke_result(invoke) is None

    def test_empty_output_zero_exit_returns_transport(self) -> None:
        invoke = self._make_invoke(exit_code=0, error_category=None, output="   ")
        assert _classify_invoke_result(invoke) == "transport"


class TestResolveTransportInternal:
    """Defensive-branch coverage for ``_resolve_transport`` (the pure
    internal helper called by ``call_anthropic``). The public
    ``resolve_transport`` validates its inputs against the
    ``{"api", "cli", "auto"}`` set before reaching this helper, so
    the terminal ``raise ValueError`` is unreachable via type-safe
    callers — but we pin it to guard against a future caller that
    bypasses the outer validator.
    """

    def test_unknown_transport_raises_value_error(self) -> None:
        from typing import cast

        from clauditor._anthropic import _resolve_transport

        with pytest.raises(
            ValueError, match=r"Unknown transport 'sdk'"
        ):
            _resolve_transport(cast("str", "sdk"))  # type: ignore[arg-type]
