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
    AnthropicHelperError,
    AnthropicResult,
    ClaudeCLIError,
    _body_excerpt,
    _classify_invoke_result,
    _extract_result,
    call_anthropic,
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform",
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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


# ---------------------------------------------------------------------------
# US-003 (#86): CLI transport in call_anthropic
# ---------------------------------------------------------------------------


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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._monotonic",
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
            "clauditor._providers._anthropic._monotonic",
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
            "clauditor._providers._anthropic._announced_cli_transport", False
        )
        fake = _make_cli_success_popen("cli-picked")
        with patch(
            "clauditor._providers._anthropic.shutil.which",
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
            "clauditor._providers._anthropic.shutil.which", return_value=None
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
            "clauditor._providers._anthropic.shutil.which",
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
            "clauditor._providers._anthropic.shutil.which", return_value=None
        ), patch(
            "clauditor._harnesses._claude_code.subprocess.Popen",
            side_effect=FileNotFoundError,
        ), patch(
            "clauditor._providers._anthropic._sleep", sleep_mock
        ), patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
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
            "clauditor._providers._anthropic._announced_cli_transport", False
        )

    @pytest.mark.asyncio
    async def test_first_auto_to_cli_resolution_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _make_cli_success_popen("ok")
        with patch(
            "clauditor._providers._anthropic.shutil.which",
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
            "clauditor._providers._anthropic.shutil.which",
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
            "clauditor._providers._anthropic.shutil.which", return_value=None
        ), patch(
            "anthropic.AsyncAnthropic", return_value=mock_client
        ):
            await call_anthropic("p", model="m", transport="auto")
        captured = capsys.readouterr()
        assert (
            "clauditor: using Claude CLI transport"
            not in captured.err
        )


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


class TestModelResult:
    """Regression tests for the #144 US-002 ``AnthropicResult`` →
    ``ModelResult`` rename + ``provider`` field addition.

    Traces to DEC-006 of ``plans/super/144-providers-call-model.md``:
    ``ModelResult`` carries a ``provider: Literal["anthropic", "openai"]``
    field (default ``"anthropic"``) so the future ``call_model``
    dispatcher (US-003) can stamp the originating backend on every
    result. The legacy name ``AnthropicResult`` stays as a back-compat
    alias — same class object, same identity invariant.
    """

    def test_model_result_provider_default(self) -> None:
        """A ``ModelResult`` constructed without ``provider`` defaults
        to ``"anthropic"`` so every pre-#144 caller stays unchanged."""
        from clauditor._providers._anthropic import ModelResult

        result = ModelResult(response_text="ok")
        assert result.provider == "anthropic"

    def test_anthropic_result_alias_identity(self) -> None:
        """``AnthropicResult`` is the same class object as
        ``ModelResult`` — ``is`` returns True. Defining it as a
        separate subclass would break ``isinstance`` ladders for
        callers that imported one name vs the other."""
        from clauditor._providers._anthropic import (
            AnthropicResult,
            ModelResult,
        )

        assert AnthropicResult is ModelResult

    def test_anthropic_result_alias_identity_via_shim(self) -> None:
        """The shim re-export of ``AnthropicResult`` is the same class
        object as ``ModelResult`` from the canonical location."""
        from clauditor._anthropic import (
            AnthropicResult as ShimAnthropicResult,
        )
        from clauditor._providers._anthropic import ModelResult

        assert ShimAnthropicResult is ModelResult

    def test_call_anthropic_still_works_via_shim(self) -> None:
        """``from clauditor._anthropic import call_anthropic`` resolves
        to a callable. Per #144 US-007 (DEC-004), the shim's
        ``call_anthropic`` is a thin deprecation-wrapper around
        :func:`clauditor._providers.call_model`, NOT the same callable
        object as :func:`clauditor._providers.call_anthropic` — so the
        old ``is`` identity invariant intentionally no longer holds.
        What we DO require is that both names resolve to callables and
        the shim wrapper still produces a ``ModelResult`` when invoked.
        """
        from clauditor._anthropic import call_anthropic as shim_call
        from clauditor._providers import call_anthropic as canonical_call

        assert callable(shim_call)
        assert callable(canonical_call)
        # The shim wrapper is a fresh function defined in the shim
        # module; the canonical name resolves to the
        # ``_providers/_anthropic.py`` body. Identity intentionally
        # does NOT hold post-US-007.
        assert shim_call is not canonical_call

    def test_provider_field_accepts_openai(self) -> None:
        """Forward-compat: a ``ModelResult`` can be constructed with
        ``provider="openai"`` ahead of the #145 OpenAI backend
        landing. The dataclass does not validate the literal at
        runtime (Python's ``Literal`` is type-hint only); that's
        fine — the dispatcher in #145 will be the gatekeeper."""
        from clauditor._providers._anthropic import ModelResult

        result = ModelResult(response_text="ok", provider="openai")
        assert result.provider == "openai"


class TestCallModel:
    """Regression tests for the #144 US-003 ``call_model`` dispatcher.

    Traces to DEC-001 (signature does not include ``subject``) of
    ``plans/super/144-providers-call-model.md`` and #145 US-005
    (``provider="openai"`` dispatches to
    :func:`clauditor._providers._openai.call_openai`; the prior
    ``NotImplementedError`` placeholder from #144 DEC-002 was
    replaced).
    """

    @pytest.mark.asyncio
    async def test_call_model_routes_anthropic_to_call_anthropic(
        self,
    ) -> None:
        """``call_model(provider="anthropic", ...)`` delegates to
        :func:`call_anthropic` with the same kwargs."""
        from clauditor._providers import ModelResult, call_model

        canned = ModelResult(response_text="ok", provider="anthropic")
        with patch(
            "clauditor._providers._anthropic.call_anthropic",
            new=AsyncMock(return_value=canned),
        ) as mock_call:
            result = await call_model(
                "the prompt",
                provider="anthropic",
                model="claude-3-5-haiku-latest",
                transport="api",
                max_tokens=4096,
            )

        mock_call.assert_awaited_once_with(
            "the prompt",
            model="claude-3-5-haiku-latest",
            transport="api",
            max_tokens=4096,
        )
        assert result is canned

    @pytest.mark.asyncio
    async def test_call_model_anthropic_returns_model_result(self) -> None:
        """The dispatcher returns the ``ModelResult`` produced by
        :func:`call_anthropic` unchanged."""
        from clauditor._providers import ModelResult, call_model

        canned = ModelResult(
            response_text="ok",
            provider="anthropic",
            source="api",
            input_tokens=12,
            output_tokens=34,
        )
        with patch(
            "clauditor._providers._anthropic.call_anthropic",
            new=AsyncMock(return_value=canned),
        ):
            result = await call_model(
                "the prompt",
                provider="anthropic",
                model="claude-3-5-haiku-latest",
            )

        assert isinstance(result, ModelResult)
        assert result.provider == "anthropic"
        assert result.source == "api"
        assert result.input_tokens == 12
        assert result.output_tokens == 34

    @pytest.mark.asyncio
    async def test_call_model_dispatches_to_openai(self) -> None:
        """#145 US-005: ``provider="openai"`` delegates to
        :func:`clauditor._providers._openai.call_openai` with the
        forwarded kwargs. Patches the canonical module path per
        ``.claude/rules/back-compat-shim-discipline.md`` Pattern 3."""
        from clauditor._providers import ModelResult, call_model

        canned = ModelResult(
            response_text="ok",
            provider="openai",
            source="api",
            input_tokens=7,
            output_tokens=11,
        )
        with patch(
            "clauditor._providers._openai.call_openai",
            new=AsyncMock(return_value=canned),
        ) as mock_call:
            result = await call_model(
                "hi",
                provider="openai",
                model="gpt-5.4",
            )

        mock_call.assert_awaited_once_with(
            "hi",
            model="gpt-5.4",
            transport="auto",
            max_tokens=4096,
        )
        assert result is canned
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_call_model_propagates_openai_helper_error(self) -> None:
        """#145 US-005: an :class:`OpenAIHelperError` raised inside
        :func:`call_openai` propagates verbatim through the
        dispatcher — the dispatcher must NOT swallow or wrap it."""
        from clauditor._providers import OpenAIHelperError, call_model

        with patch(
            "clauditor._providers._openai.call_openai",
            new=AsyncMock(side_effect=OpenAIHelperError("simulated")),
        ):
            with pytest.raises(OpenAIHelperError, match="simulated"):
                await call_model(
                    "hi",
                    provider="openai",
                    model="gpt-5.4",
                )

    def test_call_model_signature_does_not_include_subject(self) -> None:
        """DEC-001 guard: ``call_model`` MUST NOT carry a ``subject``
        parameter. ``subject`` is Claude-Code-CLI-specific and does
        not generalize across providers."""
        import inspect

        from clauditor._providers import call_model

        sig = inspect.signature(call_model)
        assert "subject" not in sig.parameters

    def test_call_model_in_providers_all(self) -> None:
        """``call_model`` is exported from
        :mod:`clauditor._providers`'s ``__all__``."""
        import clauditor._providers as providers

        assert "call_model" in providers.__all__

    @pytest.mark.asyncio
    async def test_call_model_unknown_provider_raises_value_error(
        self,
    ) -> None:
        """An unknown ``provider`` value (neither ``"anthropic"`` nor
        ``"openai"``) raises :class:`ValueError` at the dispatcher
        boundary so callers see a crisp pre-call error rather than
        a deeper ``AttributeError`` later."""
        from clauditor._providers import call_model

        with pytest.raises(ValueError, match="unknown provider"):
            await call_model(
                "the prompt",
                provider="vertex",  # type: ignore[arg-type]
                model="claude-3-5-haiku-latest",
            )

    def test_call_model_importable_via_shim(self) -> None:
        """``from clauditor._anthropic import call_model`` resolves
        to the same callable as the canonical
        ``clauditor._providers.call_model`` (transitional re-export
        per US-003)."""
        from clauditor._anthropic import call_model as shim_call_model
        from clauditor._providers import call_model as canonical_call_model

        assert shim_call_model is canonical_call_model


class TestGraderImportsTargetProviders:
    """#144 US-005: the six grader call sites import ``call_model``
    from :mod:`clauditor._providers` (not ``clauditor._anthropic``).

    Production grep-regression: after US-005 no production module
    under ``src/clauditor/`` (excluding ``_providers/`` and the
    deprecated shim ``_anthropic.py`` itself) imports from
    ``clauditor._anthropic``. US-006 will further tighten this to
    the CLI / pytest_plugin import sites; this test pins the US-005
    grader invariant.
    """

    def test_grader_modules_do_not_import_from_anthropic_shim(self) -> None:
        import re
        from pathlib import Path

        # The five grader modules touched in US-005.
        targets = [
            Path("src/clauditor/grader.py"),
            Path("src/clauditor/quality_grader.py"),
            Path("src/clauditor/triggers.py"),
            Path("src/clauditor/propose_eval.py"),
            Path("src/clauditor/suggest.py"),
        ]
        # Resolve relative to the repo root (tests run from repo root
        # via ``pytest``).
        repo_root = Path(__file__).resolve().parent.parent
        pattern = re.compile(r"from\s+clauditor\._anthropic\b")

        offenders: list[str] = []
        for rel in targets:
            full = repo_root / rel
            text = full.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

        assert not offenders, (
            "Grader call sites must import from clauditor._providers "
            "(US-005 acceptance criterion). Offenders:\n"
            + "\n".join(offenders)
        )


class TestNoProductionImportsFromAnthropicShim:
    """#144 US-006 grep-regression: zero production-code files under
    ``src/clauditor/`` import from ``clauditor._anthropic``, except
    the shim itself (``src/clauditor/_anthropic.py``).

    Strengthens the US-005 grader-only invariant
    (:class:`TestGraderImportsTargetProviders`) to cover the entire
    production tree: the six LLM-mediated CLI commands, the pytest
    plugin's three fixture sites, and any other `clauditor._anthropic`
    consumer must now route through ``clauditor._providers``. The only
    exempt file is the shim itself, which exists to re-export for
    one-release back-compat.
    """

    def test_no_production_imports_from_anthropic_shim(self) -> None:
        import re
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        src_root = repo_root / "src" / "clauditor"
        # Exempt: the shim itself (re-exports for back-compat), and
        # the canonical ``_providers/`` package (defines the symbols
        # the shim re-exports; may reference the shim path in
        # docstrings / comments per DEC-005).
        shim_path = (src_root / "_anthropic.py").resolve()
        providers_root = (src_root / "_providers").resolve()
        pattern = re.compile(r"from\s+clauditor\._anthropic\b")

        offenders: list[str] = []
        for py_path in src_root.rglob("*.py"):
            resolved = py_path.resolve()
            if resolved == shim_path:
                continue
            if providers_root in resolved.parents:
                continue
            text = py_path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    rel = py_path.relative_to(repo_root)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

        assert not offenders, (
            "Production code under src/clauditor/ must import from "
            "clauditor._providers (US-006 acceptance criterion); the "
            "back-compat shim ``clauditor._anthropic`` is the only "
            "exempt file. Offenders:\n" + "\n".join(offenders)
        )
