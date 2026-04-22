"""Centralized Anthropic SDK helper with retry + error categorization.

All Anthropic ``messages.create`` call sites across clauditor funnel
through :func:`call_anthropic`. The helper:

- builds an :class:`anthropic.AsyncAnthropic` client per invocation,
- issues a single-turn user prompt against the given ``model``,
- categorizes transport failures and retries where appropriate,
- surfaces non-retriable failures as :class:`AnthropicHelperError`
  with a user-facing message (auth errors include a pointer to
  ``ANTHROPIC_API_KEY``, status errors include the response status
  code and a body excerpt),
- returns an :class:`AnthropicResult` bundling the joined text,
  the per-block text list, token usage, and the raw SDK response.

Retry policy (bead ``clauditor-24h.3``):

- ``RateLimitError`` (HTTP 429): up to 3 retries (4 attempts total).
- ``APIStatusError`` with ``status_code >= 500``: 1 retry then raise.
- ``APIStatusError`` 4xx (other than 401/403): no retry; raise
  immediately.
- ``AuthenticationError`` (401) / ``PermissionDeniedError`` (403): no
  retry; raise immediately with a message pointing the operator at
  ``ANTHROPIC_API_KEY``.
- ``APIConnectionError``: 1 retry then raise.

Backoff: ``2 ** retry_index`` seconds (i.e. ``1``, ``2``, ``4``) with a
uniform ``±25%`` jitter band.

Per ``.claude/rules/monotonic-time-indirection.md`` the helper is
async, so ``time.monotonic`` and ``asyncio.sleep`` are aliased at
module load. Tests patch ``clauditor._anthropic._sleep`` and
``clauditor._anthropic._rand_uniform`` rather than the stdlib
originals so the asyncio event loop's own scheduler calls are not
disturbed and tests do not burn wallclock.
"""

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass, field
from typing import Any

# Module-level alias per .claude/rules/monotonic-time-indirection.md.
# ``_sleep`` is patched in retry-branch tests to avoid real wallclock.
# ``_rand_uniform`` lets tests pin jitter to deterministic values.
_sleep = asyncio.sleep


def _rand_uniform(lo: float, hi: float) -> float:
    """Return a uniform random float in ``[lo, hi]``.

    Indirected so tests can patch jitter deterministically without
    clobbering ``random.random`` globally. The default implementation
    uses a module-local ``random.Random`` instance so patching the
    stdlib ``random`` module does not affect this helper, and vice
    versa.
    """
    return _rng.uniform(lo, hi)


_rng = random.Random()


# Per-exception retry caps (bead clauditor-24h.3 acceptance criteria).
_RATE_LIMIT_MAX_RETRIES = 3
_SERVER_MAX_RETRIES = 1
_CONN_MAX_RETRIES = 1

# Body excerpt length when surfacing APIStatusError messages. 512 is
# enough to include the canonical ``{"error": {"type": "...", "message":
# "..."}}`` envelope without flooding stderr.
_BODY_EXCERPT_CHARS = 512


class AnthropicHelperError(RuntimeError):
    """Raised by :func:`call_anthropic` for non-retriable or exhausted failures.

    Carries a user-facing message suitable for stderr surfacing. The
    original SDK exception is preserved on :attr:`__cause__` via
    ``raise ... from exc`` so callers that want to introspect (e.g. for
    status code) still can.
    """


class AnthropicAuthMissingError(Exception):
    """Raised by :func:`check_anthropic_auth` when ``ANTHROPIC_API_KEY`` is missing.

    Distinct from :class:`AnthropicHelperError` by design (DEC-010 of
    ``plans/super/83-subscription-auth-gap.md``): the CLI layer routes
    ``AnthropicAuthMissingError`` to exit 2 (pre-call input-validation
    error per ``.claude/rules/llm-cli-exit-code-taxonomy.md``), while
    ``AnthropicHelperError`` is routed to exit 3 (actual API failure).
    Reusing the helper-error class would conflate those exit codes and
    make the routing a string-match hack instead of a structural
    ``except`` ladder.
    """


# Message template used by :func:`check_anthropic_auth`. DEC-011:
# interpolates the command name into the second line so users see
# ``clauditor grade`` (or ``propose-eval``, ``suggest``, ``triggers``,
# ``extract``) and know exactly which invocation triggered the guard.
# DEC-012: three durable substrings must appear in every raised message
# — ``ANTHROPIC_API_KEY``, ``Claude Pro``, ``console.anthropic.com``.
# The ``#86`` reference is deliberately NOT test-asserted so a
# renumber/close does not churn tests.
_AUTH_MISSING_TEMPLATE = (
    "ERROR: ANTHROPIC_API_KEY is not set.\n"
    "clauditor {cmd_name} calls the Anthropic API directly and needs an API\n"
    "key — a Claude Pro/Max subscription alone does not grant API access.\n"
    "Get a key at https://console.anthropic.com/, then export\n"
    "ANTHROPIC_API_KEY=... and re-run. Subscription support via claude -p\n"
    "is tracked in #86.\n"
    "Commands that don't need a key: validate, capture, run, lint, init,\n"
    "badge, audit, trend."
)


def check_anthropic_auth(cmd_name: str) -> None:
    """Pre-flight guard: raise if ``ANTHROPIC_API_KEY`` is missing.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads ``os.environ`` only; does NOT print to stderr, does NOT call
    ``sys.exit``, does NOT log. The CLI wrapper catches
    :class:`AnthropicAuthMissingError` and maps it to ``return 2`` +
    stderr surfacing.

    Per DEC-001, only ``ANTHROPIC_API_KEY`` counts — ``ANTHROPIC_AUTH_TOKEN``
    is ignored even though the underlying Anthropic SDK honors it. The
    guard is deliberately stricter than the SDK's own fallback chain;
    widening later is cheap if it bites.

    Args:
        cmd_name: Subcommand label (e.g. ``"grade"``, ``"propose-eval"``)
            interpolated into the error message so users see
            ``clauditor grade`` for immediately actionable UX.

    Raises:
        AnthropicAuthMissingError: when ``ANTHROPIC_API_KEY`` is absent,
            an empty string, or whitespace-only. Message contains the
            three DEC-012 durable substrings and the interpolated
            command name.
    """
    value = os.environ.get("ANTHROPIC_API_KEY")
    if value is None or value.strip() == "":
        raise AnthropicAuthMissingError(
            _AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
        )
    return None


@dataclass
class AnthropicResult:
    """Bundle returned by :func:`call_anthropic`.

    Attributes:
        response_text: Joined concatenation of every text content block
            in the response. Empty string when the response contained
            no text blocks (e.g. refusal-only, tool-use-only).
        text_blocks: Per-block list of text strings in response order.
            Empty list mirrors ``response_text == ""``. Callers that
            need to distinguish "no text" from "empty text" should
            check ``text_blocks`` rather than ``response_text``.
        input_tokens: Token count reported by ``response.usage``. 0 if
            the SDK did not populate ``usage`` (defensive).
        output_tokens: See ``input_tokens``.
        raw_message: The underlying SDK response object, for callers
            that need content-list inspection beyond text blocks
            (refusal handling, tool-use blocks, etc).
    """

    response_text: str
    text_blocks: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_message: Any = None


def _compute_backoff(retry_index: int) -> float:
    """Return the sleep duration for the ``retry_index``-th retry.

    Formula: ``2 ** retry_index`` seconds with ``±25%`` uniform
    jitter. Retry indices start at 0, so the first retry waits
    ``1 s`` (plus jitter), the second ``2 s``, the third ``4 s``.
    """
    base = float(2**retry_index)
    jitter = _rand_uniform(-0.25, 0.25) * base
    delay = base + jitter
    # Floor at 0 defensively; negative jitter at retry_index=0 with
    # deterministic seeds that push to the lower bound could otherwise
    # bottom out near 0.75 — still positive, but we keep the guard in
    # case future formula changes flip the sign.
    return max(delay, 0.0)


def _body_excerpt(exc: Any) -> str:
    """Return a short string representation of an SDK exception body.

    ``APIStatusError.body`` may be a decoded dict (success path), raw
    bytes/string (malformed response), or ``None`` (no response). All
    three are coerced to a best-effort string truncated to
    :data:`_BODY_EXCERPT_CHARS`.
    """
    body = getattr(exc, "body", None)
    if body is None:
        return "<no body>"
    try:
        text = body if isinstance(body, str) else repr(body)
    except Exception:  # noqa: BLE001 - defensive repr
        text = "<unrenderable body>"
    if len(text) > _BODY_EXCERPT_CHARS:
        return text[:_BODY_EXCERPT_CHARS] + "..."
    return text


def _extract_result(response: Any) -> AnthropicResult:
    """Project an SDK response into an :class:`AnthropicResult`.

    Defensive around every field: content may be missing/non-list,
    blocks may lack ``.text`` or ``.type``, ``usage`` may be absent.
    Mirrors the tolerated-if-missing posture the call sites currently
    implement inline.
    """
    content = getattr(response, "content", None) or []
    if not isinstance(content, list):
        content = []
    text_blocks: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text" and hasattr(block, "text"):
            text_blocks.append(block.text)
    response_text = "".join(text_blocks)

    usage = getattr(response, "usage", None)
    try:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        output_tokens = 0

    return AnthropicResult(
        response_text=response_text,
        text_blocks=text_blocks,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_message=response,
    )


async def call_anthropic(
    prompt: str,
    *,
    model: str,
    max_tokens: int = 4096,
) -> AnthropicResult:
    """Issue a single-turn user prompt against ``model`` with retries.

    See module docstring for the retry policy. On success returns an
    :class:`AnthropicResult`; on any non-retriable or retry-exhausted
    failure raises :class:`AnthropicHelperError` with a user-facing
    message. ``ImportError`` is raised (not wrapped) when the
    ``anthropic`` SDK is not installed so callers can surface the
    existing "install with: pip install clauditor[grader]" hint.
    """
    try:
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            AsyncAnthropic,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError as exc:
        raise ImportError(
            "clauditor._anthropic.call_anthropic requires the anthropic "
            "SDK. Install with: pip install clauditor[grader]"
        ) from exc

    client = AsyncAnthropic()

    rate_limit_retries = 0
    server_retries = 0
    conn_retries = 0

    while True:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except RateLimitError as exc:
            if rate_limit_retries >= _RATE_LIMIT_MAX_RETRIES:
                raise AnthropicHelperError(
                    f"Anthropic rate limit ({exc.status_code}) after "
                    f"{_RATE_LIMIT_MAX_RETRIES} retries. Body: "
                    f"{_body_excerpt(exc)}"
                ) from exc
            delay = _compute_backoff(rate_limit_retries)
            rate_limit_retries += 1
            await _sleep(delay)
            continue
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise AnthropicHelperError(
                f"Anthropic authentication failed "
                f"({exc.status_code}): check the ANTHROPIC_API_KEY "
                f"environment variable. Body: {_body_excerpt(exc)}"
            ) from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", 0)
            if status < 500:
                # Any other 4xx: bad request, not found, conflict,
                # unprocessable entity, etc. No retry.
                raise AnthropicHelperError(
                    f"Anthropic API request failed "
                    f"({status}): {exc.message}. Body: "
                    f"{_body_excerpt(exc)}"
                ) from exc
            if server_retries >= _SERVER_MAX_RETRIES:
                raise AnthropicHelperError(
                    f"Anthropic server error ({status}) after "
                    f"{_SERVER_MAX_RETRIES} retry. Body: "
                    f"{_body_excerpt(exc)}"
                ) from exc
            delay = _compute_backoff(server_retries)
            server_retries += 1
            await _sleep(delay)
            continue
        except APIConnectionError as exc:
            if conn_retries >= _CONN_MAX_RETRIES:
                raise AnthropicHelperError(
                    f"Anthropic connection error after "
                    f"{_CONN_MAX_RETRIES} retry: "
                    f"{getattr(exc, 'message', repr(exc))}"
                ) from exc
            delay = _compute_backoff(conn_retries)
            conn_retries += 1
            await _sleep(delay)
            continue

        return _extract_result(response)
