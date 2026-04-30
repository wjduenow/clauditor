"""OpenAI provider backend — Responses API seam (US-002 of #145).

Happy-path-only implementation of :func:`call_openai`, the OpenAI
counterpart to :func:`clauditor._providers._anthropic.call_anthropic`.
Mirrors the Anthropic seam's structural shape — module-level
test-indirection aliases for :func:`time.monotonic` /
:func:`asyncio.sleep`, a sibling exception class
(:class:`OpenAIHelperError`) for non-retriable failures, and a
:class:`clauditor._providers.ModelResult` projection — but defers
retry logic, error categorization, and rich
``response.output[]`` walking to subsequent beads (US-003 / US-004).

Responses API divergences from Anthropic's ``messages.create``:

- The SDK call is ``client.responses.create(input=..., model=...,
  max_output_tokens=...)`` rather than ``messages.create(messages=[
  {"role": "user", ...}], max_tokens=...)``. The single-turn prompt
  goes through the ``input=`` kwarg directly.
- The response surfaces joined assistant text via the
  ``response.output_text`` SDK convenience accessor (Pydantic
  computed field). Per-block walking of ``response.output[]``
  filtering ``type == "message"`` is US-003's job and is NOT in
  scope for this happy path.
- Refusal / incomplete-output state lives at ``response.status`` +
  ``response.incomplete_details.reason`` rather than Anthropic's
  ``stop_reason`` on the raw ``Message``. Callers introspecting
  :attr:`ModelResult.raw_message` for refusal semantics must
  branch on :attr:`ModelResult.provider` — see :func:`call_openai`'s
  docstring.
- :attr:`ModelResult.raw_message` is the Pydantic-v2 dict from
  ``response.model_dump()`` rather than Anthropic's raw ``Message``
  object (DEC-001 of ``plans/super/145-openai-provider.md``). This
  divergence is documented; downstream consumers across the codebase
  pre-#145 only ever introspect ``raw_message`` on the Anthropic
  path.

DEC-002: :attr:`ModelResult.source` is always ``"api"`` — OpenAI has
no CLI transport axis. The ``transport`` and ``subject`` kwargs are
accepted at the signature level so the future dispatcher can pass
them uniformly across providers, but both are ignored here.

Module-level constants (DEC-001 of #145): :data:`DEFAULT_MODEL_L3`
and :data:`DEFAULT_MODEL_L2` pin the L3-grader and L2-extraction
defaults to confirmed Responses-API model names. Callers that need
a per-call override pass ``model=`` explicitly; default-model
resolution at grader call sites lands in US-010.

Per ``.claude/rules/monotonic-time-indirection.md`` :data:`_sleep`
and :data:`_monotonic` are aliased at module load. Tests patch
``clauditor._providers._openai._sleep`` and
``clauditor._providers._openai._monotonic`` directly so the asyncio
event loop's own scheduler ticks are not disturbed and an
Anthropic-side patch on ``_anthropic._sleep`` does not leak into
OpenAI's call loop. The shared :func:`compute_backoff` jitter
indirection lives in :mod:`clauditor._providers._retry` and is
imported when retry logic lands in US-004.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final

# Late import inside the call site (mirroring _anthropic.py's
# pattern) is unnecessary here — ``openai`` is a hard dependency
# per US-002 (DEC-004 of #145, ``openai>=1.66.0``). Import at
# module load so tests can patch ``AsyncOpenAI`` at the seam.
from openai import AsyncOpenAI

from clauditor._providers import ModelResult
from clauditor._providers._retry import (
    CONN_MAX_RETRIES,
    RATE_LIMIT_MAX_RETRIES,
    SERVER_MAX_RETRIES,
    compute_backoff,
)

# Module-level alias per .claude/rules/monotonic-time-indirection.md.
# ``_sleep`` is patched in retry-branch tests to avoid real
# wallclock; ``_monotonic`` lets tests pin duration measurements
# deterministically without clobbering the asyncio event loop's own
# scheduler ticks.
_sleep = asyncio.sleep
_monotonic = time.monotonic


# Body excerpt length when surfacing APIStatusError messages. 512 is
# enough to include the canonical
# ``{"error": {"type": "...", "message": "..."}}`` envelope without
# flooding stderr. Mirrors :data:`clauditor._providers._anthropic._BODY_EXCERPT_CHARS`.
# Duplicated rather than imported because each provider may evolve
# its own body-shape conventions; today they happen to match.
_BODY_EXCERPT_CHARS = 512


def _body_excerpt(exc: Any) -> str:
    """Return a short string representation of an SDK exception body.

    Mirrors :func:`clauditor._providers._anthropic._body_excerpt`.
    Duplicated rather than shared because each provider's SDK may
    evolve different body shapes; today both happen to expose ``.body``
    as either a decoded dict, raw bytes/string, or ``None``. All three
    are coerced to a best-effort string truncated to
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


# DEC-001 of plans/super/145-openai-provider.md: pin the default
# models to confirmed Responses-API names per
# https://developers.openai.com/api/docs/models . L3 grading uses
# the larger model; L2 schema-extraction uses the smaller one.
# Callers override per-call via the ``model=`` kwarg; default-model
# resolution at grader call sites is US-010's job.
DEFAULT_MODEL_L3: Final[str] = "gpt-5.4"
DEFAULT_MODEL_L2: Final[str] = "gpt-5.4-mini"


class OpenAIHelperError(Exception):
    """Raised by :func:`call_openai` for non-retriable / exhausted failures.

    Sibling of :class:`clauditor._providers._anthropic.AnthropicHelperError`
    (DEC-006 of ``plans/super/145-openai-provider.md``): subclass of
    :class:`Exception` directly, NOT of ``AnthropicHelperError`` — a
    common ancestor would defeat the structural ``except`` ladder
    every CLI dispatcher depends on. Retry / categorization branches
    that raise this class land in US-004.
    """


def _extract_openai_result(
    response: Any,
) -> tuple[str, list[str], int, int, dict]:
    """Project a Responses-API response into the ``ModelResult`` payload.

    Pure helper per ``.claude/rules/pure-compute-vs-io-split.md``:
    no I/O, never raises, defensive against missing or malformed
    fields. Mirrors the structural shape of
    :func:`clauditor._providers._anthropic._extract_result` so the
    two providers' projections stay reviewable side-by-side.

    Walks ``response.output[]`` skipping non-``message`` items
    (e.g. ``type == "reasoning"`` items emitted by extended-thinking
    modes — forward-compat with #154's harness-context sidecar
    work). Each ``message`` item's ``content[]`` is walked for
    ``type == "output_text"`` blocks; the per-message text is
    joined and appended to ``text_blocks``.

    Args:
        response: Responses-API response object (or any duck-typed
            stand-in). All attribute reads are guarded with
            :func:`getattr` defaults so a future SDK shape change
            cannot crash the projection.

    Returns:
        Tuple of ``(response_text, text_blocks, input_tokens,
        output_tokens, raw_message)``:

        - ``response_text`` prefers ``response.output_text`` (the
          SDK's joined-message-text convenience accessor). Falls
          back to ``"".join(text_blocks)`` when the accessor is
          absent or empty.
        - ``text_blocks`` is the per-message joined text list; an
          empty list means the response had no message-typed
          output items (refusal, tool-only, incomplete).
        - ``input_tokens`` / ``output_tokens`` come from
          ``response.usage`` with defensive ``int()`` coercion;
          fall back to 0 on missing/null/non-numeric values.
        - ``raw_message`` is ``response.model_dump()`` (Pydantic-v2
          dict) when available, else ``{}``.
    """
    # Walk response.output[], filtering to message items and
    # collecting per-message joined text. Non-message items
    # (reasoning, tool-use, etc.) are skipped; per-block walking is
    # defensive against a future SDK that adds new content-block
    # types — we only collect text from output_text blocks.
    text_blocks: list[str] = []
    output = getattr(response, "output", None) or []
    if isinstance(output, list):
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            content = getattr(item, "content", None) or []
            if not isinstance(content, list):
                continue
            parts: list[str] = []
            for block in content:
                if getattr(block, "type", None) != "output_text":
                    continue
                text = getattr(block, "text", "") or ""
                if isinstance(text, str):
                    parts.append(text)
            # QG pass 2 (#145): only append when this message yielded
            # at least one text part. A message item with no
            # ``output_text`` content blocks (refusal-only, tool-use-
            # only, or ``content=None``) collapses to no entry rather
            # than an empty-string entry, so the downstream contract
            # ``not result.text_blocks`` reliably distinguishes
            # "no message-typed text" from "message had empty text".
            if parts:
                text_blocks.append("".join(parts))

    # Prefer the SDK's joined output_text accessor; fall back to
    # joining text_blocks when it is absent or empty.
    output_text = getattr(response, "output_text", "") or ""
    if not output_text and text_blocks:
        output_text = "".join(text_blocks)

    usage = getattr(response, "usage", None)
    try:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        input_tokens = 0
    try:
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        output_tokens = 0

    # raw_message: prefer the Pydantic-v2 dict (DEC-001 of #145).
    # Fall back to {} so downstream callers that ``isinstance(...,
    # dict)`` keep a stable shape. Defensive against a future SDK
    # that drops .model_dump() entirely.
    raw_message: dict = {}
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except (TypeError, AttributeError):
            dumped = None
        if isinstance(dumped, dict):
            raw_message = dumped

    return output_text, text_blocks, input_tokens, output_tokens, raw_message


async def call_openai(
    prompt: str,
    *,
    model: str,
    max_tokens: int = 4096,
    transport: str = "auto",
    subject: str | None = None,
) -> ModelResult:
    """Issue a single-turn user prompt against an OpenAI Responses-API model.

    Happy-path-only (US-002 of #145). On success returns a
    :class:`ModelResult` with :attr:`ModelResult.provider` stamped to
    ``"openai"`` and :attr:`ModelResult.source` stamped to ``"api"``.
    Retry logic, error categorization, and rich ``output[]`` walking
    land in US-003 / US-004.

    Args:
        prompt: Single-turn user prompt body. Forwarded to the
            Responses-API ``input=`` kwarg.
        model: OpenAI model name (e.g. :data:`DEFAULT_MODEL_L3`).
        max_tokens: Upper bound on response tokens. Forwarded to the
            Responses-API ``max_output_tokens=`` kwarg (renamed from
            Anthropic's ``max_tokens``). Defaults to 4096.
        transport: Accepted for signature parity with
            :func:`call_anthropic` but **ignored** — OpenAI has no
            CLI transport axis (DEC-002 of
            ``plans/super/145-openai-provider.md``).
            :attr:`ModelResult.source` is always ``"api"``.
        subject: Accepted for signature parity but **ignored** —
            ``subject`` is the Claude-Code-CLI ``apiKeySource``
            telemetry label and has no OpenAI counterpart.

    Returns:
        :class:`ModelResult` with:

        - ``response_text`` = ``response.output_text`` (the SDK's
          joined-assistant-text convenience accessor).
        - ``text_blocks`` = ``[response.output_text]`` when
          non-empty, else ``[]``. Richer per-block walking of
          ``response.output[]`` filtering ``type == "message"`` is
          US-003's job; the happy path collapses to a single block.
        - ``input_tokens`` / ``output_tokens`` from
          ``response.usage``.
        - ``raw_message`` = ``response.model_dump()`` — Pydantic-v2
          dict serialization. **Divergence from Anthropic**: OpenAI
          surfaces refusal / incomplete state at
          ``response.status`` + ``response.incomplete_details.reason``
          rather than ``stop_reason`` on a raw ``Message`` object.
          Callers introspecting ``raw_message`` for refusal
          semantics must branch on ``provider``.
        - ``provider`` = ``"openai"``; ``source`` = ``"api"``;
          ``duration_seconds`` measured via the :data:`_monotonic`
          alias.
    """
    # DEC-002: ``transport`` / ``subject`` are accepted for
    # signature parity but ignored. The names are intentionally
    # bound (no leading underscore renaming) so the kwargs stay
    # call-site-discoverable.
    del transport, subject

    # Local imports mirror :func:`call_anthropic`'s pattern. The
    # ``openai`` SDK is a hard dependency per DEC-004 of #145 so the
    # ``AsyncOpenAI`` symbol is already imported at module load (the
    # tests patch the module-level ``AsyncOpenAI`` symbol). Import the
    # exception classes here so the retry ladder names them locally.
    # Per ``.claude/rules/centralized-sdk-call.md``: ``ImportError``
    # is RE-RAISED un-wrapped so callers surface a clean
    # ``pip install openai>=1.66.0`` hint.
    from openai import (
        APIConnectionError,
        APIStatusError,
        AuthenticationError,
        OpenAIError,
        PermissionDeniedError,
        RateLimitError,
    )

    # Defense-in-depth (per ``.claude/rules/precall-env-validation.md``):
    # wrap the ``AsyncOpenAI()`` construction site so the SDK's auth
    # missing-key error (``OpenAIError`` raised when ``OPENAI_API_KEY``
    # is unset) and any future ``TypeError`` from ``__init__`` (e.g.
    # on an unresolved auth method) surface as a clean
    # ``OpenAIHelperError`` rather than a raw traceback. Fixed
    # sanitized message; original exception preserved on
    # ``__cause__`` via ``raise ... from``. ``ImportError`` is NOT
    # caught — per the centralized-sdk-call rule, missing-SDK errors
    # must propagate un-wrapped so the install hint surfaces.
    #
    # ``OpenAIError`` is the SDK's base exception. ``AuthenticationError``,
    # ``RateLimitError``, ``APIStatusError``, ``APIConnectionError`` are
    # all subclasses, but those are raised from ``responses.create()`` —
    # not from ``AsyncOpenAI()`` construction — so this site only sees
    # the bare ``OpenAIError`` (or ``TypeError`` for legacy SDK config
    # errors).
    try:
        client = AsyncOpenAI()
    except ImportError:
        raise
    except (TypeError, OpenAIError) as exc:
        raise OpenAIHelperError(
            "OpenAI SDK client initialization failed — "
            "verify OPENAI_API_KEY is set."
        ) from exc

    rate_limit_retries = 0
    server_retries = 0
    conn_retries = 0

    while True:
        # Duration measures the successful attempt's wall clock only,
        # excluding retry sleeps (mirrors DEC-020 of the Anthropic
        # branch). Reset ``start`` on every ``continue`` so a
        # successful-after-retry call reports the final attempt's
        # own duration.
        start = _monotonic()
        try:
            response: Any = await client.responses.create(
                input=prompt,
                model=model,
                max_output_tokens=max_tokens,
            )
        except RateLimitError as exc:
            if rate_limit_retries >= RATE_LIMIT_MAX_RETRIES:
                raise OpenAIHelperError(
                    f"OpenAI rate limit (429) after "
                    f"{RATE_LIMIT_MAX_RETRIES} retries. Body: "
                    f"{_body_excerpt(exc)}"
                ) from exc
            delay = compute_backoff(rate_limit_retries)
            rate_limit_retries += 1
            await _sleep(delay)
            continue
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise OpenAIHelperError(
                f"OpenAI authentication failed "
                f"({exc.status_code}): check the OPENAI_API_KEY "
                f"environment variable. Body: {_body_excerpt(exc)}"
            ) from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", 0)
            if status < 500:
                # 4xx (other than 401/403): bad request, not found,
                # unprocessable entity, etc. No retry.
                raise OpenAIHelperError(
                    f"OpenAI API request failed "
                    f"({status}): {exc.message}. Body: "
                    f"{_body_excerpt(exc)}"
                ) from exc
            if server_retries >= SERVER_MAX_RETRIES:
                raise OpenAIHelperError(
                    f"OpenAI server error ({status}) after "
                    f"{SERVER_MAX_RETRIES} retry. Body: "
                    f"{_body_excerpt(exc)}"
                ) from exc
            delay = compute_backoff(server_retries)
            server_retries += 1
            await _sleep(delay)
            continue
        except APIConnectionError as exc:
            if conn_retries >= CONN_MAX_RETRIES:
                raise OpenAIHelperError(
                    f"OpenAI connection error after "
                    f"{CONN_MAX_RETRIES} retry: "
                    f"{getattr(exc, 'message', repr(exc))}"
                ) from exc
            delay = compute_backoff(conn_retries)
            conn_retries += 1
            await _sleep(delay)
            continue
        except TypeError as exc:
            # Defense-in-depth wrap (per
            # ``.claude/rules/precall-env-validation.md``). If a
            # future caller bypasses the pre-flight auth guard and
            # the SDK raises ``TypeError`` from
            # ``responses.create`` (e.g. unresolved auth, malformed
            # client config), surface a clean
            # ``OpenAIHelperError`` rather than the raw traceback.
            # Fixed sanitized message — no ``str(exc)``,
            # no ``exc.args``. Original exception preserved on
            # ``__cause__`` via ``raise ... from exc``. Not retried:
            # a ``TypeError`` is a config error, not transient.
            raise OpenAIHelperError(
                "OpenAI SDK client initialization failed — "
                "verify OPENAI_API_KEY is set."
            ) from exc

        duration = _monotonic() - start

        # Delegate the projection to the pure helper per
        # .claude/rules/pure-compute-vs-io-split.md.
        (
            response_text,
            text_blocks,
            input_tokens,
            output_tokens,
            raw_message,
        ) = _extract_openai_result(response)

        return ModelResult(
            response_text=response_text,
            text_blocks=text_blocks,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_message=raw_message,
            source="api",
            duration_seconds=duration,
            provider="openai",
        )
