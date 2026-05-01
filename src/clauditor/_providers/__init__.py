"""Provider seam for clauditor's LLM calls.

This package is the canonical home of every model-provider backend
(Anthropic SDK, Anthropic CLI, OpenAI, …) and the future ``call_model``
dispatcher that routes between them. As of #144 US-002 the SDK seam
lives in :mod:`clauditor._providers._anthropic`; the deprecated shim
:mod:`clauditor._anthropic` re-exports every public name here so
existing call sites keep working unmodified for one release.

``AnthropicAuthMissingError`` is defined here (NOT in ``_auth.py`` and
NOT in ``_anthropic.py``) because both the auth helpers and the SDK
seam reference it. Defining it once at the package level keeps the
class-identity invariant per the architecture review of
``plans/super/144-providers-call-model.md`` (Security concern item #1):
every ``except AnthropicAuthMissingError`` ladder catches the same
class object regardless of which module raised it.
"""

from __future__ import annotations

from typing import Literal


class AnthropicAuthMissingError(Exception):
    """Raised when no usable Anthropic authentication path is available.

    Thrown by :func:`check_any_auth_available` when neither
    ``ANTHROPIC_API_KEY`` is set nor the ``claude`` CLI is on PATH
    (DEC-008 of ``plans/super/86-claude-cli-transport.md``), and by the
    strict variant :func:`check_api_key_only` when ``ANTHROPIC_API_KEY``
    alone is missing (DEC-009 — pytest fixtures stay strict).

    Distinct from :class:`clauditor._providers._anthropic.AnthropicHelperError`
    by design (DEC-010 of ``plans/super/83-subscription-auth-gap.md``):
    the CLI layer routes ``AnthropicAuthMissingError`` to exit 2 (pre-
    call input-validation error per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``), while
    ``AnthropicHelperError`` is routed to exit 3 (actual API failure).
    Reusing the helper-error class would conflate those exit codes and
    make the routing a string-match hack instead of a structural
    ``except`` ladder.

    Class-identity invariant (#144 US-001 acceptance criterion + the
    plan's architecture review Security item #1): this class is
    defined exactly once in this module.
    ``clauditor._anthropic.AnthropicAuthMissingError`` is a re-export —
    ``is`` returns ``True`` against this object.
    """


class OpenAIAuthMissingError(Exception):
    """Raised when ``OPENAI_API_KEY`` is missing for the OpenAI provider.

    Thrown by :func:`check_openai_auth` (and its dispatcher
    :func:`check_provider_auth` when ``provider="openai"``) when
    ``OPENAI_API_KEY`` is absent, empty, or whitespace-only.

    Distinct from :class:`AnthropicAuthMissingError` AND from any
    future ``OpenAIHelperError`` by design (DEC-006 of
    ``plans/super/145-openai-provider.md``): the CLI layer routes
    ``OpenAIAuthMissingError`` to exit 2 (pre-call input-validation
    error per ``.claude/rules/llm-cli-exit-code-taxonomy.md``) — the
    same exit code the Anthropic auth-missing class routes to, but
    via a structurally distinct ``except`` branch so future
    helper-error classes (exit 3) cannot collapse into the same
    branch by accident.

    Subclass of :class:`Exception` directly, NOT of
    :class:`AnthropicAuthMissingError` or any helper-error class — a
    common ancestor would defeat the structural-routing invariant
    every CLI dispatcher depends on.
    """


# Re-export the auth-helper surface from ``_auth.py``. Imported AFTER
# ``AnthropicAuthMissingError`` is defined so ``_auth.py``'s deferred
# / direct ``from clauditor._providers import AnthropicAuthMissingError``
# resolves cleanly without a circular-import hazard.
#
# Import ORDER matters: ``_auth`` MUST be imported before
# ``_anthropic`` because ``_anthropic.py`` imports the auth surface
# from ``clauditor._providers`` at module-load time
# (``from clauditor._providers import _AUTH_MISSING_TEMPLATE, ...``).
# Reversing this order produces a ``cannot import name`` error from
# the partially-initialized ``_providers`` package. The ruff isort
# rules suggest alphabetical ordering, but the import-time ordering
# trumps style here — DO NOT rearrange these two ``from clauditor.
# _providers.*`` blocks.
#
# The mutable one-shot announcement flag ``_announced_implicit_no_api_key``
# is intentionally NOT re-exported. ``from X import Y`` creates a fresh
# binding in this module, and ``announce_implicit_no_api_key()`` rebinds
# the flag on its source module via ``global`` — a re-exported alias here
# would frozen-copy the initial ``False`` and silently diverge after the
# first call. Tests and any future consumer that needs to read or reset
# the flag must target its canonical location:
# ``clauditor._providers._auth._announced_implicit_no_api_key``.
from clauditor._providers._auth import (  # noqa: E402, I001
    _AUTH_MISSING_TEMPLATE,
    _AUTH_MISSING_TEMPLATE_KEY_ONLY,
    _CALL_ANTHROPIC_DEPRECATION_NOTICE,
    _IMPLICIT_NO_API_KEY_ANNOUNCEMENT,
    _OPENAI_AUTH_MISSING_TEMPLATE,
    _api_key_is_set,
    _claude_cli_is_available,
    _openai_api_key_is_set,
    announce_call_anthropic_deprecation,
    announce_implicit_no_api_key,
    check_any_auth_available,
    check_api_key_only,
    check_openai_auth,
    check_provider_auth,
)

# Re-export the SDK-seam public surface from ``_anthropic.py`` (#144
# US-002). These names form the canonical post-#144 public API for
# Anthropic-provider backends; the deprecated shim
# ``clauditor._anthropic`` re-exports them for back-compat. MUST land
# AFTER the ``_auth`` import above — see the import-order comment.
from clauditor._providers._anthropic import (  # noqa: E402, I001
    AnthropicHelperError,
    AnthropicResult,
    ClaudeCLIError,
    ModelResult,
    call_anthropic,
    resolve_transport,
)

# Re-export the OpenAI-backend public error class (#145 US-005). The
# ``call_openai`` callable itself is intentionally NOT re-exported at
# the package level — callers should go through :func:`call_model`
# (the dispatcher) so transport routing and provider stamping stay
# centralized.
from clauditor._providers._openai import OpenAIHelperError  # noqa: E402, I001

async def call_model(
    prompt: str,
    *,
    provider: Literal["anthropic", "openai"],
    model: str,
    transport: Literal["api", "cli", "auto"] = "auto",
    max_tokens: int = 4096,
) -> ModelResult:
    """Provider-agnostic dispatcher routing to the right backend.

    Thin shim that owns provider selection. ``provider="anthropic"``
    delegates to :func:`clauditor._providers._anthropic.call_anthropic`
    (which itself owns transport selection between the SDK and the
    ``claude`` CLI). ``provider="openai"`` delegates to
    :func:`clauditor._providers._openai.call_openai` (#145 US-005;
    no transport axis — DEC-002 of ``plans/super/145-openai-provider.md``).

    Per DEC-001 of ``plans/super/144-providers-call-model.md``, the
    signature deliberately does NOT include a ``subject`` parameter:
    ``subject`` is a Claude-Code-CLI-specific telemetry label
    (apiKeySource attribution per #107) and does not generalize across
    providers. Anthropic-only callers that need ``subject`` continue to
    invoke :func:`call_anthropic` directly.

    Per #145 US-005, ``provider="openai"`` delegates to
    :func:`clauditor._providers._openai.call_openai`. The OpenAI
    backend has no transport axis (DEC-002 of
    ``plans/super/145-openai-provider.md``); the ``transport`` kwarg
    is forwarded for signature parity but ignored by the OpenAI
    backend, which always stamps ``ModelResult.source = "api"``.

    Args:
        prompt: Single-turn user prompt body, forwarded verbatim.
        provider: ``"anthropic"`` or ``"openai"``.
        model: Provider-specific model name (e.g.
            ``"claude-sonnet-4-6"`` for anthropic,
            ``"gpt-5-mini"`` for openai).
        transport: Transport selector forwarded to the anthropic
            backend (``"api"``, ``"cli"``, or ``"auto"``). Ignored
            by the openai backend (no transport axis there).
        max_tokens: Upper bound on response tokens. Defaults to 4096.

    Returns:
        :class:`ModelResult` with ``provider`` stamped to the routed
        backend.

    Raises:
        ValueError: ``provider`` is not ``"anthropic"`` or
            ``"openai"``.
        AnthropicHelperError: Anthropic backend failure (auth, rate
            limit, server error, connection error). See
            :func:`call_anthropic`.
        OpenAIHelperError: OpenAI backend failure (auth, rate limit,
            server error, connection error). See
            :func:`clauditor._providers._openai.call_openai`.
    """
    if provider == "anthropic":
        # Call via the module attribute so test patches that target
        # ``clauditor._providers._anthropic.call_anthropic`` (the
        # canonical patch path per
        # ``.claude/rules/centralized-sdk-call.md``) take effect here.
        # A direct ``call_anthropic(...)`` would resolve via this
        # module's ``from ... import call_anthropic`` binding, which a
        # patch on ``_providers._anthropic`` would NOT affect.
        from clauditor._providers import _anthropic as _anthropic_mod

        return await _anthropic_mod.call_anthropic(
            prompt,
            model=model,
            transport=transport,
            max_tokens=max_tokens,
        )
    if provider == "openai":
        # Deferred per-call import so test patches that target
        # ``clauditor._providers._openai.call_openai`` (the canonical
        # patch path per
        # ``.claude/rules/back-compat-shim-discipline.md`` Pattern 3)
        # take effect here. A direct import-bound call would resolve
        # via this module's ``from ... import call_openai`` binding
        # (if we had one), which a patch on ``_providers._openai``
        # would NOT affect.
        from clauditor._providers import _openai as _openai_mod

        return await _openai_mod.call_openai(
            prompt,
            model=model,
            transport=transport,
            max_tokens=max_tokens,
        )
    raise ValueError(
        f"call_model: unknown provider {provider!r} — "
        "expected 'anthropic' or 'openai'"
    )


__all__ = [
    "AnthropicAuthMissingError",
    "AnthropicHelperError",
    "AnthropicResult",
    "ClaudeCLIError",
    "ModelResult",
    "OpenAIAuthMissingError",
    "OpenAIHelperError",
    "announce_call_anthropic_deprecation",
    "announce_implicit_no_api_key",
    "call_anthropic",
    "call_model",
    "check_any_auth_available",
    "check_api_key_only",
    "check_openai_auth",
    "check_provider_auth",
    "resolve_transport",
    # Private surface re-exported for back-compat with the
    # ``clauditor._anthropic`` shim and for tests that introspect
    # constants by name. The mutable ``_announced_implicit_no_api_key``
    # flag is deliberately absent — see the import comment above.
    "_AUTH_MISSING_TEMPLATE",
    "_AUTH_MISSING_TEMPLATE_KEY_ONLY",
    "_CALL_ANTHROPIC_DEPRECATION_NOTICE",
    "_IMPLICIT_NO_API_KEY_ANNOUNCEMENT",
    "_OPENAI_AUTH_MISSING_TEMPLATE",
    "_api_key_is_set",
    "_claude_cli_is_available",
    "_openai_api_key_is_set",
]
