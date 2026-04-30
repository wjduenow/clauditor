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

# Module-level alias per .claude/rules/monotonic-time-indirection.md.
# ``_sleep`` is patched in (future) retry-branch tests to avoid real
# wallclock; ``_monotonic`` lets tests pin duration measurements
# deterministically without clobbering the asyncio event loop's own
# scheduler ticks.
_sleep = asyncio.sleep
_monotonic = time.monotonic


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

    # The OpenAI SDK reads ``OPENAI_API_KEY`` automatically. The
    # ``check_openai_auth`` pre-flight (US-006 / DEC-006) runs
    # upstream of this call site at the CLI / fixture boundary.
    client = AsyncOpenAI()

    start = _monotonic()
    response: Any = await client.responses.create(
        input=prompt,
        model=model,
        max_output_tokens=max_tokens,
    )
    duration = _monotonic() - start

    output_text = getattr(response, "output_text", "") or ""
    text_blocks = [output_text] if output_text else []

    usage = getattr(response, "usage", None)
    try:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        output_tokens = 0

    # DEC-001 raw_message shape: Pydantic v2 dict via .model_dump().
    # Defensive against a future SDK that drops the method —
    # fall back to ``None`` so :attr:`ModelResult.raw_message` stays
    # tolerable for ``isinstance(..., dict)`` checks downstream.
    model_dump = getattr(response, "model_dump", None)
    raw_message = model_dump() if callable(model_dump) else None

    return ModelResult(
        response_text=output_text,
        text_blocks=text_blocks,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_message=raw_message,
        source="api",
        duration_seconds=duration,
        provider="openai",
    )
