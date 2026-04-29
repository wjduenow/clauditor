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
    _IMPLICIT_NO_API_KEY_ANNOUNCEMENT,
    _api_key_is_set,
    _claude_cli_is_available,
    announce_implicit_no_api_key,
    check_any_auth_available,
    check_api_key_only,
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

__all__ = [
    "AnthropicAuthMissingError",
    "AnthropicHelperError",
    "AnthropicResult",
    "ClaudeCLIError",
    "ModelResult",
    "announce_implicit_no_api_key",
    "call_anthropic",
    "check_any_auth_available",
    "check_api_key_only",
    "resolve_transport",
    # Private surface re-exported for back-compat with the
    # ``clauditor._anthropic`` shim and for tests that introspect
    # constants by name. The mutable ``_announced_implicit_no_api_key``
    # flag is deliberately absent — see the import comment above.
    "_AUTH_MISSING_TEMPLATE",
    "_AUTH_MISSING_TEMPLATE_KEY_ONLY",
    "_IMPLICIT_NO_API_KEY_ANNOUNCEMENT",
    "_api_key_is_set",
    "_claude_cli_is_available",
]
