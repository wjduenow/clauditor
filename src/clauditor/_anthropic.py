"""Back-compat shim for the Anthropic SDK seam (#144 US-002, US-007).

The SDK seam moved to :mod:`clauditor._providers._anthropic` in #144
US-002. The auth seam lives in :mod:`clauditor._providers._auth`.
This module exists for one release as a deprecated shim that
re-exports every public symbol from the new home so existing call
sites (``from clauditor._anthropic import call_anthropic``,
``from clauditor._anthropic import AnthropicHelperError``, etc.) keep
working unmodified.

New code should import directly from :mod:`clauditor._providers` (or
its sub-modules). After the deprecation window closes, this module
will be removed in favor of the canonical ``_providers`` package.

Class-identity invariant: every class re-exported here is the *same*
object as the one defined in ``_providers/_anthropic.py`` /
``_providers/__init__.py``. ``except AnthropicHelperError`` ladders
catch the same class regardless of whether the caller imported it
from the shim or from the canonical location.

DEC-004 (#144 US-007): the back-compat ``call_anthropic`` is a thin
wrapper here (NOT a re-export) that calls
:func:`announce_call_anthropic_deprecation` once per process before
delegating to :func:`call_model`. The deprecation notice is the third
member of the "Implicit-coupling announcements — an emerging family"
documented in ``.claude/rules/centralized-sdk-call.md``.
"""

from __future__ import annotations

# ``shutil`` re-exported as a module attribute so test patches like
# ``patch("clauditor._anthropic.shutil.which", ...)`` continue to
# resolve. Patching ``X.shutil.which`` mutates ``shutil.which``
# globally (``shutil`` is the same module object everywhere), so the
# patch takes effect on the canonical ``_providers/_anthropic.py``
# call site as well.
import shutil  # noqa: E402, F401
from typing import Literal

# DEC-005 of ``plans/super/144-providers-call-model.md``: the auth
# sub-seam moved into ``clauditor._providers._auth`` (US-001) and the
# SDK sub-seam moved into ``clauditor._providers._anthropic``
# (US-002). The imports below re-export every moved symbol so existing
# call sites keep working unmodified for one release.
#
# The mutable one-shot announcement flags
# (``_announced_implicit_no_api_key``,
# ``_announced_call_anthropic_deprecation``,
# ``_announced_cli_transport``) are intentionally NOT re-exported here.
# ``from X import Y`` would frozen-copy the initial ``False`` value
# into this module, but the helpers rebind the flag on their source
# module via ``global`` — the alias here would silently diverge after
# the first call. Code that needs to read or reset a flag must target
# its canonical location: auth-coupled and deprecation-coupled flags
# live in ``clauditor._providers._auth``; transport-coupled flags live
# in ``clauditor._providers._anthropic``.
from clauditor._providers import (
    _AUTH_MISSING_TEMPLATE,  # noqa: F401
    _AUTH_MISSING_TEMPLATE_KEY_ONLY,  # noqa: F401
    _CALL_ANTHROPIC_DEPRECATION_NOTICE,  # noqa: F401
    _IMPLICIT_NO_API_KEY_ANNOUNCEMENT,  # noqa: F401
    AnthropicAuthMissingError,  # noqa: F401
    AnthropicHelperError,  # noqa: F401
    AnthropicResult,  # noqa: F401  (alias for ModelResult)
    ClaudeCLIError,  # noqa: F401
    ModelResult,  # noqa: F401
    _api_key_is_set,  # noqa: F401
    _claude_cli_is_available,  # noqa: F401
    announce_call_anthropic_deprecation,
    announce_implicit_no_api_key,  # noqa: F401
    call_model,  # noqa: F401  (re-exported for back-compat)
    check_any_auth_available,  # noqa: F401
    check_api_key_only,  # noqa: F401
    resolve_transport,  # noqa: F401
)

# DEC-007 of ``plans/super/145-openai-provider.md``: the retry helpers
# moved to ``clauditor._providers._retry``. Re-export under the
# legacy underscored names so existing call sites that imported
# ``_compute_backoff`` / ``_compute_retry_decision`` / the retry
# constants from ``clauditor._anthropic`` keep resolving for the
# back-compat window. The jitter indirection (``_rand_uniform`` /
# ``_rng``) also moved with the helpers — tests that patched the
# legacy path follow the symbols to ``clauditor._providers._retry``
# per ``.claude/rules/back-compat-shim-discipline.md`` Pattern 3.
from clauditor._providers import _retry as _retry_mod  # noqa: E402

# Re-export the SDK-seam private surface from the canonical module so
# tests / call sites that referenced these names via
# ``clauditor._anthropic`` keep resolving for the back-compat window.
from clauditor._providers._anthropic import (  # noqa: F401, E402
    _BODY_EXCERPT_CHARS,
    _CLI_AUTO_ANNOUNCEMENT,
    _CLI_ERROR_TEMPLATES,
    _CLI_TRANSPORT_TIMEOUT,
    _VALID_TRANSPORT_VALUES,
    _body_excerpt,
    _build_default_harness,
    _call_via_claude_cli,
    _call_via_sdk,
    _classify_invoke_result,
    _default_harness,
    _extract_result,
    _monotonic,
    _resolve_transport,
    _sleep,
)

# Module-attribute aliases (NOT ``from X import Y``) so the legacy
# underscored names are bound on this shim's module object. Tests
# that monkeypatched ``clauditor._anthropic._compute_backoff`` etc.
# kept resolving for one release of back-compat. New tests target
# the canonical home ``clauditor._providers._retry`` per
# ``.claude/rules/back-compat-shim-discipline.md`` Pattern 3.
_RATE_LIMIT_MAX_RETRIES = _retry_mod.RATE_LIMIT_MAX_RETRIES
_SERVER_MAX_RETRIES = _retry_mod.SERVER_MAX_RETRIES
_CONN_MAX_RETRIES = _retry_mod.CONN_MAX_RETRIES
_compute_backoff = _retry_mod.compute_backoff
_compute_retry_decision = _retry_mod.compute_retry_decision
_rand_uniform = _retry_mod._rand_uniform
_rng = _retry_mod._rng


async def call_anthropic(
    prompt: str,
    *,
    model: str,
    max_tokens: int = 4096,
    transport: Literal["api", "cli", "auto"] = "auto",
    subject: str | None = None,
) -> ModelResult:
    """Deprecated back-compat wrapper for the Anthropic provider seam.

    DEC-004 of ``plans/super/144-providers-call-model.md``. Existing
    callers that imported ``call_anthropic`` from
    :mod:`clauditor._anthropic` keep working for one release, but the
    first call per Python process emits a one-shot deprecation notice
    pointing at the canonical replacement
    (:func:`clauditor._providers.call_model` with
    ``provider="anthropic"``). The notice is suppressed on subsequent
    calls within the same process.

    Delegates directly to
    :func:`clauditor._providers._anthropic.call_anthropic` (NOT
    :func:`call_model`) so the ``subject`` keyword threads through to
    the CLI transport's ``apiKeySource`` telemetry line. The
    :func:`call_model` dispatcher's signature deliberately omits
    ``subject`` per DEC-001 (it does not generalize across providers);
    new code that needs ``subject`` should target
    :func:`clauditor._providers._anthropic.call_anthropic` directly.
    """
    announce_call_anthropic_deprecation()
    # Resolve via module attribute so test patches that target
    # ``clauditor._providers._anthropic.call_anthropic`` (the canonical
    # patch path per ``.claude/rules/centralized-sdk-call.md``) take
    # effect here. A direct import-bound call would resolve via the
    # ``from clauditor._providers import call_anthropic`` binding
    # above, which a patch on ``_providers._anthropic`` would NOT
    # affect — same reason :func:`call_model` uses a deferred import.
    from clauditor._providers import _anthropic as _anthropic_mod

    return await _anthropic_mod.call_anthropic(
        prompt,
        model=model,
        max_tokens=max_tokens,
        transport=transport,
        subject=subject,
    )


__all__ = [
    "AnthropicAuthMissingError",
    "AnthropicHelperError",
    "AnthropicResult",
    "ClaudeCLIError",
    "ModelResult",
    "announce_call_anthropic_deprecation",
    "announce_implicit_no_api_key",
    "call_anthropic",
    "call_model",
    "check_any_auth_available",
    "check_api_key_only",
    "resolve_transport",
]
