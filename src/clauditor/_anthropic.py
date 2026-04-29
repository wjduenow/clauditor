"""Back-compat shim for the Anthropic SDK seam (#144 US-002).

The SDK seam moved to :mod:`clauditor._providers._anthropic` in #144
US-002. This module exists for one release as a deprecated shim that
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
"""

from __future__ import annotations

# ``shutil`` re-exported as a module attribute so test patches like
# ``patch("clauditor._anthropic.shutil.which", ...)`` continue to
# resolve. Patching ``X.shutil.which`` mutates ``shutil.which``
# globally (``shutil`` is the same module object everywhere), so the
# patch takes effect on the canonical ``_providers/_anthropic.py``
# call site as well.
import shutil  # noqa: E402, F401

# DEC-005 of ``plans/super/144-providers-call-model.md``: the auth
# sub-seam moved into ``clauditor._providers._auth`` (US-001) and the
# SDK sub-seam moved into ``clauditor._providers._anthropic``
# (US-002). The imports below re-export every moved symbol so existing
# call sites keep working unmodified for one release. The
# class-identity invariant (every re-exported class ``is`` the same
# object as the one defined in ``_providers``) holds because each
# class is defined exactly once in ``_providers`` and re-exported
# here, not redefined.
#
# Each name is suppressed with a noqa marker (F401) because ruff sees
# these as unused inside this module — they ARE unused here, since
# they are re-exports for back-compat callers.
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
    AnthropicHelperError,  # noqa: F401
    AnthropicResult,  # noqa: F401  (alias for ModelResult)
    ClaudeCLIError,  # noqa: F401
    ModelResult,  # noqa: F401
    _api_key_is_set,  # noqa: F401
    _claude_cli_is_available,  # noqa: F401
    announce_implicit_no_api_key,  # noqa: F401
    call_anthropic,  # noqa: F401
    check_any_auth_available,  # noqa: F401
    check_api_key_only,  # noqa: F401
    resolve_transport,  # noqa: F401
)

# Re-export the SDK-seam private surface from the canonical module so
# tests / call sites that referenced these names via
# ``clauditor._anthropic`` keep resolving for the back-compat window.
# These are NOT in ``_providers/__init__.py``'s ``__all__`` (they are
# implementation details of the Anthropic backend, not public surface
# of the package) — re-exporting only here is intentional.
#
# Mutable patchable hooks (``_sleep``, ``_rand_uniform``,
# ``_monotonic``, ``_announced_cli_transport``) and the ``shutil``
# module reference: these are the canonical patch targets used by
# the retry / backoff / announcement tests. Per #144 US-002 the
# canonical location is ``clauditor._providers._anthropic``; tests
# updated for the move target the new path. The names below stay
# importable here so name-only consumers (``from clauditor._anthropic
# import _CLI_AUTO_ANNOUNCEMENT``) keep working.
from clauditor._providers._anthropic import (  # noqa: F401, E402
    _BODY_EXCERPT_CHARS,
    _CLI_AUTO_ANNOUNCEMENT,
    _CLI_ERROR_TEMPLATES,
    _CLI_TRANSPORT_TIMEOUT,
    _CONN_MAX_RETRIES,
    _RATE_LIMIT_MAX_RETRIES,
    _SERVER_MAX_RETRIES,
    _VALID_TRANSPORT_VALUES,
    _announced_cli_transport,
    _body_excerpt,
    _build_default_harness,
    _call_via_claude_cli,
    _call_via_sdk,
    _classify_invoke_result,
    _compute_backoff,
    _compute_retry_decision,
    _default_harness,
    _extract_result,
    _monotonic,
    _rand_uniform,
    _resolve_transport,
    _rng,
    _sleep,
)
