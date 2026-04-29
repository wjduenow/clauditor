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
module load. Tests patch ``clauditor._anthropic._sleep``,
``clauditor._anthropic._rand_uniform``, and
``clauditor._anthropic._monotonic`` rather than the stdlib originals
so the asyncio event loop's own scheduler calls are not disturbed and
tests do not burn wallclock.

CLI transport (US-003 of ``plans/super/86-claude-cli-transport.md``):
``call_anthropic(prompt, model=..., transport="auto")`` accepts a
``transport`` kwarg resolving to ``"api"`` (SDK path) or ``"cli"``
(subprocess path via :func:`clauditor.runner._invoke_claude_cli`).
The ``"auto"`` default picks CLI when ``shutil.which("claude")`` is
set (DEC-001 subscription-first). CLI failures surface as
:class:`ClaudeCLIError`, a subclass of :class:`AnthropicHelperError`
so every existing ``except AnthropicHelperError:`` caller stays
transport-blind.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Literal

# DEC-005 of ``plans/super/144-providers-call-model.md``: the auth
# sub-seam moved into ``clauditor._providers._auth`` (US-001). The
# imports below re-export every moved symbol so existing call sites
# (``from clauditor._anthropic import check_any_auth_available``,
# ``from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT``,
# etc.) keep working unmodified for one release. The class-identity
# invariant — ``clauditor._anthropic.AnthropicAuthMissingError is
# clauditor._providers.AnthropicAuthMissingError`` — holds because the
# class is defined exactly once in ``_providers/__init__.py`` and
# re-exported here, not redefined.
#
# Each name is suppressed with a noqa marker (F401) because ruff sees
# these as unused inside this module — they ARE unused here, since they
# are re-exports for back-compat callers.
#
# The mutable one-shot announcement flag ``_announced_implicit_no_api_key``
# is intentionally NOT re-exported here. A ``from clauditor._providers
# import _announced_implicit_no_api_key`` would frozen-copy the initial
# ``False`` value into this module — ``announce_implicit_no_api_key()``
# rebinds the flag on its source module via ``global``, so the alias
# here would silently diverge after the first call. Code that needs to
# read or reset the flag must target its canonical location:
# ``clauditor._providers._auth._announced_implicit_no_api_key``.
from clauditor._providers import (
    _AUTH_MISSING_TEMPLATE,  # noqa: F401
    _AUTH_MISSING_TEMPLATE_KEY_ONLY,  # noqa: F401
    _IMPLICIT_NO_API_KEY_ANNOUNCEMENT,  # noqa: F401
    AnthropicAuthMissingError,  # noqa: F401
    _api_key_is_set,  # noqa: F401
    _claude_cli_is_available,  # noqa: F401
    announce_implicit_no_api_key,  # noqa: F401
    check_any_auth_available,  # noqa: F401
    check_api_key_only,  # noqa: F401
)

# Module-level alias per .claude/rules/monotonic-time-indirection.md.
# ``_sleep`` is patched in retry-branch tests to avoid real wallclock.
# ``_rand_uniform`` lets tests pin jitter to deterministic values.
# ``_monotonic`` lets tests pin duration measurements deterministically
# without clobbering the asyncio event loop's own scheduler ticks.
_sleep = asyncio.sleep
_monotonic = time.monotonic

# DEC-019: one-shot stderr announcement when ``transport="auto"``
# resolves to CLI. Flipped to ``True`` after the first emission per
# Python process; explicit ``transport="cli"`` never flips it.
_announced_cli_transport = False


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


class ClaudeCLIError(AnthropicHelperError):
    """Raised by the CLI-transport branch of :func:`call_anthropic`.

    Subclass of :class:`AnthropicHelperError` per DEC-006 of
    ``plans/super/86-claude-cli-transport.md``: every existing
    ``except AnthropicHelperError:`` call site stays transport-blind,
    while future callers that want to branch on transport can
    ``except ClaudeCLIError:``. Exit 3 mapping is inherited (no new
    exit code per ``.claude/rules/llm-cli-exit-code-taxonomy.md``).

    Attributes:
        category: One of ``"rate_limit"``, ``"auth"``, ``"api"``, or
            ``"transport"``. The first three mirror
            :func:`clauditor.runner._classify_result_message`'s
            output; ``"transport"`` covers subprocess-level failures
            (binary missing, timeout, malformed output) that surface
            before any stream-json ``result`` message classification.
    """

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


# DEC-014: fixed per-category templates committed verbatim. Tests
# assert substrings; any phrasing drift surfaces as a red test. The
# machine-readable suffix ``(transport=cli, category=<cat>)`` is
# parseable by log scrapers and future ``clauditor audit
# --by-category`` segmentation without substring-matching exception
# text. No stream-json ``result`` text is ever echoed into the
# message (sanitization per #83 DEC-015).
_CLI_ERROR_TEMPLATES: dict[str, str] = {
    "rate_limit": (
        "Anthropic rate limit exceeded (after retries). Try again "
        "later. (transport=cli, category=rate_limit)"
    ),
    "auth": (
        "Claude CLI authentication failed. Run `claude` interactively "
        "to refresh credentials, or export ANTHROPIC_API_KEY and pass "
        "--transport api. (transport=cli, category=auth)"
    ),
    "api": (
        "Claude CLI returned an error (category=api). See `clauditor "
        "doctor` for diagnostics. (transport=cli, category=api)"
    ),
    "transport": (
        "Claude CLI subprocess failed (binary missing, timeout, or "
        "malformed output). (transport=cli, category=transport)"
    ),
}


# DEC-019: one-shot stderr line emitted when ``transport="auto"``
# resolves to CLI. Committed verbatim so tests assert equality.
_CLI_AUTO_ANNOUNCEMENT = (
    "clauditor: using Claude CLI transport (subscription auth); "
    "pass --transport api to opt out"
)


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
            (refusal handling, tool-use blocks, etc). ``None`` under
            CLI transport (DEC-007 of
            ``plans/super/86-claude-cli-transport.md``) — the
            subprocess output carries no SDK ``Message`` object.
            Callers must tolerate ``None`` (US-002 regression guard).
        source: Which transport produced this result. ``"api"`` for
            the SDK path; ``"cli"`` for the subprocess path. DEC-007.
        duration_seconds: Wall-clock time the successful attempt
            took, measured via the :data:`_monotonic` alias so tests
            can pin it deterministically. EXCLUDES retry sleeps — a
            single-attempt 5 s call and a successful-after-retry
            12 s call both report the successful attempt's own
            duration (DEC-020).
    """

    response_text: str
    text_blocks: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_message: Any = None
    source: Literal["api", "cli"] = "api"
    duration_seconds: float = 0.0


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


def _compute_retry_decision(
    category: str, retry_index: int
) -> Literal["retry", "raise"]:
    """Return whether to retry a failure given its category + retry index.

    Pure helper per ``.claude/rules/pure-compute-vs-io-split.md``.
    Shared by the SDK and CLI transport branches so a failure with
    the same category retries the same number of times regardless
    of which transport produced it (DEC-005 retry parity).

    Ladder (retry indices are 0-based — index ``i`` is "the decision
    made before the ``i+1``-th attempt's delay"):

    - ``"rate_limit"``: retry at indices 0, 1, 2; raise at 3 (matches
      :data:`_RATE_LIMIT_MAX_RETRIES` = 3 — up to 3 retries ≡ 4
      total attempts).
    - ``"auth"``: always raise (no retry at any index).
    - ``"api"``: retry at index 0; raise at 1 (one retry, matches
      :data:`_SERVER_MAX_RETRIES` = 1 — used for 5xx SDK errors and
      the analogous CLI ``api`` category).
    - ``"connection"``: retry at index 0; raise at 1 (matches
      :data:`_CONN_MAX_RETRIES` = 1 — SDK ``APIConnectionError``).
    - ``"transport"``: retry at index 0; raise at 1 (CLI-only;
      covers subprocess binary-missing, timeout, malformed output).
    - Any other category: always raise (defensive default — an
      unknown category is not something we should retry blindly).
    """
    if category == "rate_limit":
        return "retry" if retry_index < _RATE_LIMIT_MAX_RETRIES else "raise"
    if category == "auth":
        return "raise"
    if category == "api":
        return "retry" if retry_index < _SERVER_MAX_RETRIES else "raise"
    if category == "connection":
        return "retry" if retry_index < _CONN_MAX_RETRIES else "raise"
    if category == "transport":
        return "retry" if retry_index < _CONN_MAX_RETRIES else "raise"
    return "raise"


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
        source="api",
    )


_VALID_TRANSPORT_VALUES: tuple[str, ...] = ("api", "cli", "auto")


def resolve_transport(
    cli_override: str | None,
    env_override: str | None,
    spec_value: str | None,
) -> str:
    """Pick the winning transport from the four-layer precedence.

    DEC-012 / DEC-017 of ``plans/super/86-claude-cli-transport.md``.
    Pure helper per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads no env / filesystem / SDK state — all three inputs are
    passed in. The caller (``SkillSpec.run``) is responsible for
    reading ``os.environ["CLAUDITOR_TRANSPORT"]`` and passing the
    result as ``env_override``.

    Precedence (highest → lowest): CLI override > env override >
    spec value > default ``"auto"``. A layer is "set" when its
    value is non-``None``; any set value short-circuits the chain
    (the *first* non-``None`` wins). If all three are ``None``,
    returns the default ``"auto"``.

    Every non-``None`` input is validated against
    ``{"api", "cli", "auto"}``; an invalid value raises
    ``ValueError`` with a message that names the layer (``CLI
    --transport``, ``CLAUDITOR_TRANSPORT``, or ``EvalSpec.transport``)
    so the CLI can route the failure to exit 2 per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``.

    Args:
        cli_override: Value from the ``--transport`` argparse flag;
            ``None`` when the flag was not passed.
        env_override: Value of ``os.environ["CLAUDITOR_TRANSPORT"]``
            as a string (or ``None`` when unset / empty).
        spec_value: Value of ``EvalSpec.transport`` (or ``None`` when
            no eval spec is attached to the ``SkillSpec``).

    Returns:
        One of ``"api"``, ``"cli"``, ``"auto"``.

    Raises:
        ValueError: when a non-``None`` layer holds an invalid value.
    """
    if cli_override is not None:
        if cli_override not in _VALID_TRANSPORT_VALUES:
            raise ValueError(
                f"CLI --transport must be one of "
                f"'api', 'cli', 'auto', got {cli_override!r}"
            )
        return cli_override
    if env_override is not None:
        if env_override not in _VALID_TRANSPORT_VALUES:
            raise ValueError(
                f"CLAUDITOR_TRANSPORT must be one of "
                f"'api', 'cli', 'auto', got {env_override!r}"
            )
        return env_override
    if spec_value is not None:
        if spec_value not in _VALID_TRANSPORT_VALUES:
            raise ValueError(
                f"EvalSpec.transport must be one of "
                f"'api', 'cli', 'auto', got {spec_value!r}"
            )
        return spec_value
    return "auto"


def _resolve_transport(
    transport: Literal["api", "cli", "auto"],
) -> tuple[Literal["api", "cli"], bool]:
    """Resolve an explicit or auto transport choice to ``"api"`` / ``"cli"``.

    Returns ``(resolved, from_auto)`` where ``from_auto`` signals the
    caller should consider emitting the DEC-019 announcement (the
    announcement itself is additionally gated by the one-shot module
    flag, handled in :func:`call_anthropic`).

    - ``transport="api"`` → ``("api", False)``.
    - ``transport="cli"`` → ``("cli", False)``.
    - ``transport="auto"`` → picks CLI when ``shutil.which("claude")``
      returns a path (DEC-001 subscription-first), else API.
      ``from_auto`` is ``True`` so the caller can announce.
    """
    if transport == "api":
        return "api", False
    if transport == "cli":
        return "cli", False
    if transport == "auto":
        # "auto" per DEC-001.
        if shutil.which("claude") is not None:
            return "cli", True
        return "api", True
    raise ValueError(
        f"Unknown transport {transport!r}; expected 'api', 'cli', or 'auto'"
    )


async def call_anthropic(
    prompt: str,
    *,
    model: str,
    max_tokens: int = 4096,
    transport: Literal["api", "cli", "auto"] = "auto",
    subject: str | None = None,
) -> AnthropicResult:
    """Issue a single-turn user prompt against ``model`` with retries.

    See module docstring for the retry policy. On success returns an
    :class:`AnthropicResult`; on any non-retriable or retry-exhausted
    failure raises :class:`AnthropicHelperError` (or its
    :class:`ClaudeCLIError` subclass) with a user-facing message.
    ``ImportError`` is raised (not wrapped) when the ``anthropic``
    SDK is not installed so callers can surface the existing "install
    with: pip install clauditor[grader]" hint.

    Args:
        prompt: Single-turn user prompt body.
        model: Anthropic model name (e.g. ``"claude-sonnet-4-6"``).
        max_tokens: Upper bound on response tokens. Defaults to 4096.
        transport: Which transport to route through.

            - ``"api"``: force the SDK (HTTP) path.
            - ``"cli"``: force the subprocess path via
              :func:`clauditor.runner._invoke_claude_cli`.
            - ``"auto"`` (default): pick CLI when the ``claude``
              binary is on PATH, else API (DEC-001 subscription-first).
              The first ``auto → cli`` resolution per Python process
              emits a one-shot stderr announcement (DEC-019).
        subject: Optional call-site label threaded to the CLI transport
            for :func:`clauditor.runner._invoke_claude_cli`'s
            ``apiKeySource`` telemetry line. When set, the CLI branch
            emits ``clauditor.runner: apiKeySource=<val> (<subject>)``
            so operators can attribute each line to a specific internal
            LLM call (e.g. ``"L2 extraction"``, ``"L3 grading"``). See
            issue #107. Ignored by the SDK transport (no telemetry
            line is emitted there).
    """
    resolved, from_auto = _resolve_transport(transport)

    # DEC-019: one-shot stderr announcement on ``auto → cli`` only.
    # Explicit ``transport="cli"`` never announces (no surprise;
    # caller chose it). Explicit ``transport="api"`` never announces.
    if resolved == "cli" and from_auto:
        global _announced_cli_transport
        if not _announced_cli_transport:
            print(_CLI_AUTO_ANNOUNCEMENT, file=sys.stderr)
            _announced_cli_transport = True

    if resolved == "cli":
        return await _call_via_claude_cli(
            prompt, model=model, max_tokens=max_tokens, subject=subject
        )
    return await _call_via_sdk(prompt, model=model, max_tokens=max_tokens)


async def _call_via_sdk(
    prompt: str,
    *,
    model: str,
    max_tokens: int,
) -> AnthropicResult:
    """SDK (HTTP) transport branch. See :func:`call_anthropic` for policy."""
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

    # Defense-in-depth (DEC-008 of #83): wrap the
    # ``AsyncAnthropic()`` construction site the same way we wrap
    # ``messages.create`` below, so a future SDK that moves the
    # ``TypeError: Could not resolve authentication method`` site to
    # ``__init__`` still surfaces as a clean ``AnthropicHelperError``
    # rather than a raw traceback. Fixed sanitized message; original
    # ``TypeError`` preserved on ``__cause__`` via ``raise ... from``.
    try:
        client = AsyncAnthropic()
    except TypeError as exc:
        raise AnthropicHelperError(
            "Anthropic SDK client initialization failed — "
            "verify ANTHROPIC_API_KEY is set."
        ) from exc

    rate_limit_retries = 0
    server_retries = 0
    conn_retries = 0

    while True:
        # DEC-020: duration measures the successful attempt's wall
        # clock only, excluding retry sleeps. Reset ``start`` on
        # every ``continue`` so a successful-after-retry call reports
        # the final attempt's own duration, not the end-to-end wall
        # clock.
        start = _monotonic()
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
        except TypeError as exc:
            # Defense-in-depth (DEC-008, DEC-015 of
            # plans/super/83-subscription-auth-gap.md). Current
            # Anthropic SDK raises ``TypeError: Could not resolve
            # authentication method`` from ``messages.create`` when
            # no API key is configured. The pre-flight guard
            # :func:`check_any_auth_available` (#86 DEC-008, which
            # relaxed the original guard introduced in #83) catches
            # the no-auth-available case at exit 2 before we reach
            # here, but any future caller that bypasses the guard
            # will hit this branch and see a crisp
            # ``AnthropicHelperError`` (exit 3) instead of a raw
            # ``TypeError`` traceback.
            #
            # DEC-015: fixed sanitized message — no ``str(exc)``,
            # no ``exc.args``, no SDK-sourced text. The original
            # ``TypeError`` is preserved on ``__cause__`` for
            # debugging via ``raise ... from exc``. Not retried:
            # a ``TypeError`` is a config error, not transient.
            raise AnthropicHelperError(
                "Anthropic SDK client initialization failed — "
                "verify ANTHROPIC_API_KEY is set."
            ) from exc

        duration = _monotonic() - start
        result = _extract_result(response)
        result.duration_seconds = duration
        return result


# CLI-transport default timeout. A single grading call should not
# legitimately exceed this; skills that need longer run budgets use
# :class:`SkillRunner`'s separate (and larger, 300 s) default. The
# grader budget is intentionally tighter — if a grading call is
# taking minutes, something is wrong with the prompt or the model.
_CLI_TRANSPORT_TIMEOUT = 180


async def _call_via_claude_cli(
    prompt: str,
    *,
    model: str,
    max_tokens: int,  # noqa: ARG001 — CLI does not take max_tokens.
    subject: str | None = None,
) -> AnthropicResult:
    """CLI (subprocess) transport branch.

    Routes the prompt through :func:`clauditor.runner._invoke_claude_cli`
    in a thread (the helper is synchronous) and projects its
    :class:`clauditor.runner.InvokeResult` onto :class:`AnthropicResult`.

    Retry parity with the SDK branch per DEC-005: rate-limit up to 3
    retries; auth no retry; api / 5xx one retry; transport-level
    failures (binary missing, timeout, malformed output) one retry
    then raise. All retry decisions go through
    :func:`_compute_retry_decision` so SDK and CLI ladders stay
    lockstep.

    DEC-013: ``env=env_without_api_key(os.environ)`` — the parent's
    ``ANTHROPIC_API_KEY`` is never inherited by the child
    ``claude -p`` subprocess, preserving DEC-001's subscription-first
    guarantee.

    DEC-020: ``duration_seconds`` measures the successful attempt's
    wall clock only, via the :data:`_monotonic` alias.

    DEC-007: ``raw_message = None`` — the subprocess output carries
    no SDK ``Message`` object. Callers audited in US-002 tolerate
    ``None``.
    """
    # Imports deferred to call time so a module whose ``call_anthropic``
    # users only ever hit the SDK branch does not pay the
    # ``clauditor.runner`` import cost up-front. Mirrors the SDK
    # branch's deferred ``anthropic`` import.
    from clauditor.runner import _invoke_claude_cli, env_without_api_key

    retry_counts: dict[str, int] = {
        "rate_limit": 0,
        "api": 0,
        "transport": 0,
    }

    while True:
        start = _monotonic()
        # ``_invoke_claude_cli`` is synchronous (subprocess + blocking
        # stdout read). Run it in a thread so the asyncio event loop
        # stays responsive (important for ``asyncio.gather`` fan-outs
        # like ``blind_compare``'s two parallel judges — DEC-010).
        invoke = await asyncio.to_thread(
            _invoke_claude_cli,
            prompt,
            cwd=None,
            env=env_without_api_key(os.environ),
            timeout=_CLI_TRANSPORT_TIMEOUT,
            claude_bin="claude",
            model=model,
            allow_hang_heuristic=False,
            subject=subject,
        )
        duration = _monotonic() - start

        category = _classify_invoke_result(invoke)
        if category is None:
            # Success path.
            return AnthropicResult(
                response_text=invoke.output,
                text_blocks=[invoke.output] if invoke.output else [],
                input_tokens=invoke.input_tokens,
                output_tokens=invoke.output_tokens,
                raw_message=None,
                source="cli",
                duration_seconds=duration,
            )

        # Decide retry vs raise using the shared ladder.
        retry_index = retry_counts.get(category, 0)
        decision = _compute_retry_decision(category, retry_index)
        if decision == "raise":
            template = _CLI_ERROR_TEMPLATES.get(
                category, _CLI_ERROR_TEMPLATES["transport"]
            )
            # DEC-014: preserve ``__cause__`` via an inner
            # ``RuntimeError`` so debugging tools can still find
            # the original invoke result (as a plain RuntimeError
            # wrapping the sanitized invoke.error). The
            # user-facing message is the fixed template; no
            # stream-json ``result`` text leaks.
            cause = RuntimeError(
                f"CLI transport failure: category={category}, "
                f"exit_code={invoke.exit_code}, "
                f"error={invoke.error!r}"
            )
            raise ClaudeCLIError(template, category=category) from cause

        if category in retry_counts:
            retry_counts[category] += 1
        delay = _compute_backoff(retry_index)
        await _sleep(delay)
        continue


def _classify_invoke_result(invoke: Any) -> str | None:
    """Classify a :class:`clauditor.runner.InvokeResult` into a retry category.

    Returns ``None`` when the invocation succeeded; otherwise returns
    one of ``"rate_limit"``, ``"auth"``, ``"api"``, or ``"transport"``.

    Success vs failure: follows :attr:`SkillResult.succeeded`'s spirit
    — a run with zero exit code, no error text, no error category, and
    non-empty stripped output is a success. Any other shape is a
    failure, and the category is derived from:

    - ``invoke.error_category == "rate_limit"`` → ``"rate_limit"``.
    - ``invoke.error_category == "auth"`` → ``"auth"``.
    - ``invoke.error_category == "api"`` → ``"api"``.
    - Any transport-level failure (timeout, subprocess, empty output
      with no classification) → ``"transport"``.
    """
    # Transport-level "no output, no classification" (FileNotFoundError
    # on the binary, empty stream, or a timeout kill that hit before any
    # result message) → transport.
    if invoke.exit_code == -1:
        return "transport"
    if invoke.error_category == "timeout":
        return "transport"
    if invoke.error_category == "rate_limit":
        return "rate_limit"
    if invoke.error_category == "auth":
        return "auth"
    if invoke.error_category == "api":
        return "api"
    if invoke.error_category == "subprocess":
        return "transport"
    # A non-zero exit with no classification is a transport-level
    # failure (the subprocess died but the stream-json parser never
    # classified an error).
    if invoke.exit_code != 0:
        return "transport"
    # Zero exit, no classification, but empty output → transport
    # (malformed stream, or the CLI emitted no assistant text).
    if not (invoke.output or "").strip():
        return "transport"
    return None
