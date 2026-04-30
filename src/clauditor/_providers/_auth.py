"""Auth sub-seam for the Anthropic provider.

DEC-005 of ``plans/super/144-providers-call-model.md``: the auth
helpers move out of :mod:`clauditor._anthropic` into this module so
the abstraction shape is provider-agnostic ahead of #146's per-
provider auth (OpenAI's ``OPENAI_API_KEY``). Today the checks read
``ANTHROPIC_API_KEY`` and probe the ``claude`` binary on PATH; the
helper names are the stable public seam.

``AnthropicAuthMissingError`` is defined in
:mod:`clauditor._providers` (the package ``__init__``) — NOT here —
to preserve the class-identity invariant: every
``except AnthropicAuthMissingError`` ladder must catch the same
class object regardless of which module raised it. We import it
from the parent package below; that import succeeds during
package-init because the class is defined before
``_providers/__init__.py`` runs the ``from ._auth import *`` line.

The one-shot stderr announcement family lives here too
(``announce_implicit_no_api_key`` + the ``_announced_*`` flag +
the announcement constant). Per ``.claude/rules/centralized-sdk-call.md``
"Implicit-coupling announcements — an emerging family", the gating
flag is mutated via ``global`` inside the helper — so callers that
need to **reset** the flag for tests must patch
``clauditor._providers._auth._announced_implicit_no_api_key``
(NOT ``clauditor._anthropic._announced_implicit_no_api_key``: a
star-import re-export creates a separate name binding, but
``global`` only mutates the defining module's namespace).
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Final

# Imports cleanly because ``AnthropicAuthMissingError`` is defined in
# ``_providers/__init__.py`` BEFORE the line that imports this module.
# The partial parent-package module object already has the class.
from clauditor._providers import AnthropicAuthMissingError

# DEC-003 / DEC-009 / DEC-011 (#95 US-002): one-shot stderr announcement
# when ``--transport cli`` implicitly strips ``ANTHROPIC_API_KEY`` /
# ``ANTHROPIC_AUTH_TOKEN`` from the skill subprocess env. Flipped to
# ``True`` after the first emission per Python process. Co-located with
# the (still-in-``_anthropic.py``) ``_announced_cli_transport`` flag
# because the announcement flags form an emerging family (DEC-009 of
# ``plans/super/95-subscription-auth-flag.md``).
_announced_implicit_no_api_key: bool = False


# DEC-011 (#95 US-002): one-shot stderr line emitted when
# ``--transport cli`` implicitly strips ``ANTHROPIC_API_KEY`` /
# ``ANTHROPIC_AUTH_TOKEN`` from the skill subprocess env so the
# subscription-auth guarantee extends end-to-end (SDK grader call AND
# skill subprocess). Committed verbatim; tests assert substring presence
# for ``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``, and
# ``--transport api`` (the escape hatch).
_IMPLICIT_NO_API_KEY_ANNOUNCEMENT: Final[str] = (
    "clauditor: --transport cli stripped ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN "
    "from the skill subprocess env (subscription auth end-to-end); "
    "pass --transport api to keep the keys."
)


def announce_implicit_no_api_key() -> None:
    """Emit the implicit-no-api-key notice to stderr once per process.

    DEC-003 / DEC-009 / DEC-011 (#95 US-002). Called by CLI commands
    when ``--transport cli`` resolves and the skill subprocess env has
    ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` stripped. The
    one-shot module flag :data:`_announced_implicit_no_api_key` ensures
    a single announcement per Python process regardless of how many
    subsequent CLI commands resolve under the same conditions.

    Parallel to the ``auto → CLI`` announcement gating inside
    :func:`clauditor._anthropic.call_anthropic` — kept as a standalone
    helper (not inlined) so non-SDK call sites (``cli/grade.py`` etc.)
    can invoke it directly without routing through ``call_anthropic``.
    """
    global _announced_implicit_no_api_key
    if _announced_implicit_no_api_key:
        return
    print(_IMPLICIT_NO_API_KEY_ANNOUNCEMENT, file=sys.stderr)
    _announced_implicit_no_api_key = True


# DEC-004 (#144 US-007): one-shot stderr announcement when the
# back-compat shim ``clauditor._anthropic.call_anthropic`` is invoked.
# Flipped to ``True`` after the first emission per Python process. Co-
# located with the other one-shot announcement flags
# (:data:`_announced_implicit_no_api_key` here, ``_announced_cli_transport``
# in ``_providers/_anthropic.py``) per
# ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
# announcements — an emerging family". Tests reset via the
# ``monkeypatch.setattr(..., False)`` autouse fixture pattern,
# targeting the canonical flag location at
# ``clauditor._providers._auth._announced_call_anthropic_deprecation``.
_announced_call_anthropic_deprecation: bool = False


# DEC-004 (#144 US-007): the deprecation notice emitted on the first
# back-compat-shim ``call_anthropic`` invocation per Python process.
# Three durable substrings are test-asserted:
#   1. ``clauditor._anthropic`` — the deprecated import path so users
#      know exactly which module triggered the notice.
#   2. ``clauditor._providers`` — the canonical replacement path so
#      users have an immediate next step.
#   3. ``will be removed`` — the future-removal hint so users know the
#      deprecation is on a clock (one-release horizon).
# Stylistic copy edits are tolerated; the three anchors are the
# load-bearing contract per
# ``.claude/rules/precall-env-validation.md``'s durable-substring
# discipline.
_CALL_ANTHROPIC_DEPRECATION_NOTICE: Final[str] = (
    "DeprecationWarning: clauditor._anthropic is deprecated and will be "
    "removed in a future release; import from clauditor._providers "
    "instead (e.g. `from clauditor._providers import call_model, "
    "AnthropicHelperError`). See plans/super/144-providers-call-model.md "
    "for the migration."
)


def announce_call_anthropic_deprecation() -> None:
    """Emit the ``clauditor._anthropic`` deprecation notice once per process.

    DEC-004 of ``plans/super/144-providers-call-model.md``. Called from
    the back-compat shim's :func:`clauditor._anthropic.call_anthropic`
    wrapper before each delegation to :func:`call_model`. The one-shot
    module flag :data:`_announced_call_anthropic_deprecation` ensures a
    single announcement per Python process regardless of how many
    subsequent shim calls land — same shape as
    :func:`announce_implicit_no_api_key` (#95 US-002). Tests reset by
    ``monkeypatch.setattr`` on the canonical flag location.
    """
    global _announced_call_anthropic_deprecation
    if _announced_call_anthropic_deprecation:
        return
    print(_CALL_ANTHROPIC_DEPRECATION_NOTICE, file=sys.stderr)
    _announced_call_anthropic_deprecation = True


# DEC-015 / #86 US-005: message template for :func:`check_any_auth_available`
# — the relaxed pre-flight guard that passes when either
# ``ANTHROPIC_API_KEY`` is set OR the ``claude`` CLI binary is on PATH.
# Four durable substrings are test-asserted: ``ANTHROPIC_API_KEY``,
# ``Claude Pro``, ``console.anthropic.com``, ``claude CLI``. The first
# three preserve #83 DEC-012's anchors; the fourth adds the CLI-path
# escape hatch introduced in #86.
#
# ``{cmd_name}`` interpolation keeps #83 DEC-011's "say which command
# fired" UX — users see ``clauditor grade`` (or ``propose-eval``,
# ``suggest``, ``triggers``, ``extract``, ``compare --blind``) in the
# message and know which invocation triggered the guard.
_AUTH_MISSING_TEMPLATE = (
    "ERROR: No usable authentication found.\n"
    "clauditor {cmd_name} needs either:\n"
    "  1. ANTHROPIC_API_KEY exported (API key from "
    "https://console.anthropic.com/), OR\n"
    "  2. claude CLI installed and authenticated (Claude Pro/Max "
    "subscription)\n"
    "Commands that don't need authentication: validate, capture, run, "
    "lint, init,\n"
    "badge, audit, trend."
)


# DEC-009 / #86 US-005: message template for :func:`check_api_key_only`,
# the strict variant used by the three pytest fixtures. Preserves the
# three #83 DEC-012 durable substrings (``ANTHROPIC_API_KEY``,
# ``Claude Pro``, ``console.anthropic.com``). Fixtures opt into the
# relaxed guard explicitly via ``CLAUDITOR_FIXTURE_ALLOW_CLI=1``.
_AUTH_MISSING_TEMPLATE_KEY_ONLY = (
    "ERROR: ANTHROPIC_API_KEY is not set.\n"
    "clauditor {cmd_name} calls the Anthropic API directly and needs an API\n"
    "key — a Claude Pro/Max subscription alone does not grant API access.\n"
    "Get a key at https://console.anthropic.com/, then export\n"
    "ANTHROPIC_API_KEY=... and re-run. Set CLAUDITOR_FIXTURE_ALLOW_CLI=1\n"
    "to allow the claude CLI transport in pytest fixtures.\n"
    "Commands that don't need a key: validate, capture, run, lint, init,\n"
    "badge, audit, trend."
)


def _api_key_is_set() -> bool:
    """Return True when ``ANTHROPIC_API_KEY`` is present and non-empty.

    Whitespace-only values count as absent: the SDK's own "could not
    resolve authentication method" path triggers on these shapes, and
    the pre-flight guard's whole point is to catch the SDK's opaque
    failure with an actionable message upstream.
    """
    value = os.environ.get("ANTHROPIC_API_KEY")
    return value is not None and value.strip() != ""


def _claude_cli_is_available() -> bool:
    """Return True when the ``claude`` binary is on PATH.

    Presence check only — we do NOT verify the CLI is authenticated or
    functional. That's deliberate: the goal of the pre-flight guard is
    to bail out cheaply when *neither* auth path is even theoretically
    available. If the CLI is on PATH but mis-authenticated, the
    subsequent CLI subprocess invocation will surface the failure via
    :class:`clauditor._anthropic.ClaudeCLIError` (exit 3) with a
    category-keyed message.
    """
    return shutil.which("claude") is not None


def check_any_auth_available(cmd_name: str) -> None:
    """Pre-flight guard: raise only when no auth path is available at all.

    DEC-008 of ``plans/super/86-claude-cli-transport.md``. Passes when
    either ``ANTHROPIC_API_KEY`` is set OR ``shutil.which("claude")``
    returns a path. Raises :class:`AnthropicAuthMissingError` with the
    DEC-015 message only when both avenues are closed.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads ``os.environ`` and probes PATH via ``shutil.which`` only; does
    NOT print to stderr, does NOT call ``sys.exit``, does NOT log. The
    CLI wrapper catches :class:`AnthropicAuthMissingError` and maps it
    to ``return 2`` + stderr surfacing.

    Per DEC-001 (#83), only ``ANTHROPIC_API_KEY`` counts for the key
    branch — ``ANTHROPIC_AUTH_TOKEN`` is ignored even though the
    underlying Anthropic SDK honors it. Per DEC-008 (#86), the CLI
    branch succeeds on PATH-presence alone; authentication of the CLI
    itself is not verified here (that failure surfaces downstream as
    :class:`clauditor._anthropic.ClaudeCLIError` exit 3).

    Args:
        cmd_name: Subcommand label (e.g. ``"grade"``, ``"propose-eval"``,
            ``"compare --blind"``) interpolated into the error message
            so users see ``clauditor grade`` for immediately actionable
            UX.

    Raises:
        AnthropicAuthMissingError: when neither auth path is available.
            Message contains the four DEC-015 durable substrings
            (``ANTHROPIC_API_KEY``, ``Claude Pro``,
            ``console.anthropic.com``, ``claude CLI``) and the
            interpolated command name.
    """
    if _api_key_is_set() or _claude_cli_is_available():
        return None
    raise AnthropicAuthMissingError(
        _AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
    )


def check_api_key_only(cmd_name: str) -> None:
    """Strict pre-flight guard: raise if ``ANTHROPIC_API_KEY`` is missing.

    DEC-009 of ``plans/super/86-claude-cli-transport.md`` — pytest
    fixtures stay strict. The three grading fixtures
    (``clauditor_grader``, ``clauditor_triggers``,
    ``clauditor_blind_compare``) call this helper rather than
    :func:`check_any_auth_available` so a CI run under subscription-only
    auth surfaces a config regression instead of silently falling back
    to the CLI transport. Users who deliberately want fixtures to exercise
    the CLI transport opt in with ``CLAUDITOR_FIXTURE_ALLOW_CLI=1``
    (the pytest plugin routes through :func:`check_any_auth_available`
    in that case).

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads ``os.environ`` only; does NOT print to stderr, does NOT call
    ``sys.exit``, does NOT log. The caller (pytest fixture factory)
    catches :class:`AnthropicAuthMissingError` implicitly by letting it
    propagate as a test setup failure (NOT ``pytest.skip`` per
    ``.claude/rules/precall-env-validation.md`` — a silent skip under
    subscription-only auth would mask a regression).

    Args:
        cmd_name: Fixture label (e.g. ``"grader"``, ``"triggers"``,
            ``"blind_compare"``) interpolated into the error message.

    Raises:
        AnthropicAuthMissingError: when ``ANTHROPIC_API_KEY`` is absent,
            an empty string, or whitespace-only. Message preserves the
            three #83 DEC-012 durable substrings.
    """
    if _api_key_is_set():
        return None
    raise AnthropicAuthMissingError(
        _AUTH_MISSING_TEMPLATE_KEY_ONLY.format(cmd_name=cmd_name)
    )
