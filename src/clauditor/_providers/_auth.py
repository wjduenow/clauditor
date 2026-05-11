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

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Final

# Imports cleanly because ``AnthropicAuthMissingError`` is defined in
# ``_providers/__init__.py`` BEFORE the line that imports this module.
# The partial parent-package module object already has the class.
from clauditor._providers import AnthropicAuthMissingError

# DEC-003 / DEC-009 / DEC-011 (#95 US-002): one-shot stderr announcement
# when ``--transport cli`` implicitly strips ``ANTHROPIC_API_KEY`` /
# ``ANTHROPIC_AUTH_TOKEN`` from the skill subprocess env. Flipped to
# ``True`` after the first emission per Python process. Sibling to the
# transport-coupled ``_announced_cli_transport`` flag in
# ``clauditor._providers._anthropic`` and the deprecation-coupled
# ``_announced_call_anthropic_deprecation`` flag below; together they
# form the emerging announcement family (DEC-009 of
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


def _codex_cli_is_available() -> bool:
    """Return True when the ``codex`` binary is on PATH.

    DEC-001 / DEC-002 of ``plans/super/175-codex-chatgpt-login-auth.md``.
    Presence check only — we do NOT verify the CLI is authenticated or
    functional. That's deliberate: the goal of the pre-flight guard is
    to bail out cheaply when *neither* env-var auth path nor the CLI
    binary is even theoretically available. If the CLI is on PATH but
    its ``~/.codex/auth.json`` is stale or absent, ``codex exec``
    itself produces a crisp ``"Please log out and sign in again"``
    error downstream — pre-flight does NOT try to parse the JSON to
    second-guess codex (DEC-008: explicit decision to skip
    ``auth.json`` parsing in favor of trusting the CLI binary).

    Parallel shape to :func:`_claude_cli_is_available`: both helpers
    pin ``shutil.which`` against their respective harness binary names
    and return a bool. Tests override the autouse
    ``shutil.which → None`` pin from ``tests/conftest.py`` via
    ``monkeypatch.setattr`` targeting this module's ``shutil``.
    """
    return shutil.which("codex") is not None


# DEC-012 (#177 US-001): hard cap on the size of ``auth.json`` read by
# :func:`_parse_codex_auth_json`. Real codex ``auth.json`` files are
# well under 4 KB; the 1 MB cap defends against symlink-bomb or
# accidental oversize without ever materializing a large blob in
# memory. Exceeding the cap returns ``None`` (failure-open per DEC-005)
# so the broader pre-flight chain falls through to env-var / PATH
# checks rather than aborting.
_CODEX_AUTH_JSON_MAX_BYTES: Final[int] = 1024 * 1024


def _codex_auth_json_path() -> Path:
    """Return the canonical path to ``auth.json`` for the codex CLI.

    DEC-009 of ``plans/super/177-codex-auth-mode-conflict.md``.
    Mirrors the codex CLI's own resolution: consults ``$CODEX_HOME``
    first, falls back to ``~/.codex/auth.json``. Whitespace-only values
    are treated as unset (same shape as :func:`_api_key_is_set` /
    :func:`_codex_api_key_is_set`) so an accidental
    ``export CODEX_HOME=" "`` does not silently redirect the resolver.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads ``os.environ`` only; does NOT touch the filesystem (no
    ``Path.exists()``, no ``Path.stat()``). The caller
    (:func:`_parse_codex_auth_json`) owns the actual file read.

    Returns:
        The :class:`pathlib.Path` pointing at the codex CLI's
        ``auth.json`` location. The file itself may or may not
        exist — that's the parser's concern, not the resolver's.
    """
    raw = os.environ.get("CODEX_HOME")
    if raw is not None and raw.strip() != "":
        return Path(raw) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _parse_codex_auth_json(path: Path) -> dict | None:
    """Defensively read and parse the codex CLI's ``auth.json``.

    DEC-005 / DEC-008 / DEC-012 of
    ``plans/super/177-codex-auth-mode-conflict.md``. Returns the parsed
    dict on success; returns ``None`` on ANY failure (file not found,
    :class:`OSError`, oversize per :data:`_CODEX_AUTH_JSON_MAX_BYTES`,
    :class:`json.JSONDecodeError`, non-``dict`` root,
    :class:`UnicodeDecodeError`). Never raises.

    Failure-open semantics (DEC-005): when the file cannot be parsed,
    the broader pre-flight chain falls through to env-var / PATH
    checks. The whole point of #177 is to refuse ``auth_mode ==
    "chatgpt"`` specifically; an unreadable ``auth.json`` is not a
    "chatgpt" signal and should not block authentication. Mirrors the
    defensive-read shape in ``.claude/rules/stream-json-schema.md``.

    Per DEC-014, the parsed dict is NEVER serialized downstream — no
    sidecar field, no log line, no error-message interpolation
    contains parsed content. The dict is consumed in-process by
    :func:`_auth_mode_is_acceptable` and immediately discarded. Tokens,
    account ids, and refresh tokens stay in-process. Error messages
    name the file PATH only, never its body.

    Pure-ish per ``.claude/rules/pure-compute-vs-io-split.md``: the
    one documented side-effect is the file read (size check + UTF-8
    decode + JSON parse). All four failure paths return ``None``
    cleanly; the caller branches on a single sentinel.

    Args:
        path: Path to ``auth.json`` (typically the result of
            :func:`_codex_auth_json_path`). The parser does not
            assume the file exists.

    Returns:
        The top-level ``dict`` on success, or ``None`` on any
        failure. UTF-8 strict decode (no ``errors="replace"``) — a
        non-UTF-8 file returns ``None`` rather than a corrupted dict.
    """
    try:
        # Size check first: avoid allocating a 100 MB string into
        # memory just to discover the file is oversize. ``stat()``
        # raises ``FileNotFoundError`` (subclass of ``OSError``) for
        # missing files; we catch the broad ``OSError`` to fold
        # permission errors, device errors, etc. into the same
        # failure-open path.
        st = path.stat()
        if st.st_size > _CODEX_AUTH_JSON_MAX_BYTES:
            return None
        with path.open("r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _auth_mode_is_acceptable(parsed: dict | None) -> bool:
    """Pure verdict: is the parsed ``auth.json`` acceptable for codex?

    DEC-004 / DEC-013 of
    ``plans/super/177-codex-auth-mode-conflict.md``. Returns ``True``
    in all of:

    - ``parsed is None`` (failure-open per DEC-005 — the parser
      returned no opinion; defer to other auth signals).
    - ``parsed`` is a ``dict`` but has no ``auth_mode`` key (failure-
      open per DEC-004 — a future schema that drops the field should
      not block clauditor).
    - ``auth_mode`` is present but ``isinstance(..., str)`` is
      ``False`` (e.g. JSON ``true`` / ``false`` / ``null`` / number).
      The defensive guard (DEC-013) returns BEFORE the ``==
      "chatgpt"`` comparison so a non-string value never enters the
      string compare. Mirrors the discipline in
      ``.claude/rules/constant-with-type-info.md``.
    - ``auth_mode`` is a string OTHER than ``"chatgpt"`` exactly
      (e.g. ``"apikey"``, ``"chatgptAuthTokens"``, a future enum
      value). Per DEC-004 the refusal is conservative — exact match
      on ``"chatgpt"`` only.

    Returns ``False`` ONLY when ``parsed`` is a ``dict``,
    ``parsed.get("auth_mode")`` ``isinstance(..., str)``, AND equals
    ``"chatgpt"`` exactly. This is the load-bearing #177 refusal:
    ChatGPT-mode rejects every model the harness currently asks for
    (``gpt-5-codex``, ``gpt-5``), so accepting it via PATH shipped a
    latent failure that this verdict catches up-front.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    no I/O, no state, no side-effects. Trivially unit-testable with
    inline dict literals — no ``tmp_path`` or ``monkeypatch``
    needed.

    Args:
        parsed: The dict returned by :func:`_parse_codex_auth_json`,
            or ``None`` when that helper failed-open.

    Returns:
        ``True`` if pre-flight should accept this auth posture;
        ``False`` only on the exact-string ``"chatgpt"`` case.
    """
    if parsed is None:
        return True
    auth_mode = parsed.get("auth_mode")
    # DEC-013: defensive ``isinstance`` guard BEFORE the ``==``
    # comparison so JSON bool / int / null / dict values do not
    # enter the string compare. The ``str`` type does not share
    # Python's ``bool is int`` foot-gun, but the discipline carries
    # forward — explicit type guard at every value boundary.
    if not isinstance(auth_mode, str):
        return True
    return auth_mode != "chatgpt"


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


# DEC-006 (#145 US-006): message template for :func:`check_openai_auth`,
# the strict pre-flight guard for the OpenAI provider. Mirrors
# :data:`_AUTH_MISSING_TEMPLATE_KEY_ONLY` (the Anthropic strict variant)
# in shape — single ``{cmd_name}`` interpolation, naming the env-var
# users must export, and listing the commands that don't require auth
# so users see the escape hatch.
#
# Two durable substrings tests pin: ``OPENAI_API_KEY`` (the env-var
# name) and ``platform.openai.com`` (the canonical place to obtain a
# key — analogous to ``console.anthropic.com`` on the Anthropic side).
_OPENAI_AUTH_MISSING_TEMPLATE = (
    "ERROR: OPENAI_API_KEY is not set.\n"
    "clauditor {cmd_name} calls the OpenAI API directly and needs an API\n"
    "key. Get a key at https://platform.openai.com/api-keys, then export\n"
    "OPENAI_API_KEY=... and re-run.\n"
    "Commands that don't need a key: validate, capture, run, lint, init,\n"
    "badge, audit, trend."
)


def _openai_api_key_is_set() -> bool:
    """Return True when ``OPENAI_API_KEY`` is present and non-empty.

    Whitespace-only values count as absent — same shape as
    :func:`_api_key_is_set` for ``ANTHROPIC_API_KEY``. The OpenAI SDK's
    own "could not resolve authentication" path triggers on these
    shapes, and the pre-flight guard's whole point is to catch the
    SDK's opaque failure with an actionable message upstream.
    """
    value = os.environ.get("OPENAI_API_KEY")
    return value is not None and value.strip() != ""


def check_openai_auth(cmd_name: str) -> None:
    """Pre-flight guard: raise if ``OPENAI_API_KEY`` is missing.

    DEC-006 of ``plans/super/145-openai-provider.md``. Pure helper
    mirroring :func:`check_api_key_only`'s shape for the OpenAI
    provider — reads ``os.environ["OPENAI_API_KEY"]`` and raises
    :class:`OpenAIAuthMissingError` when the value is absent, an empty
    string, or whitespace-only. There is no CLI-fallback branch (no
    OpenAI equivalent of the ``claude`` CLI subscription path), so the
    guard is unconditionally strict.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads ``os.environ`` only; does NOT print to stderr, does NOT call
    ``sys.exit``, does NOT log. The CLI wrapper catches
    :class:`OpenAIAuthMissingError` (a direct subclass of
    :class:`Exception`, NOT :class:`AnthropicAuthMissingError` or any
    helper-error class) and maps it to ``return 2`` per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``.

    Args:
        cmd_name: Subcommand label (e.g. ``"grade"``, ``"extract"``,
            ``"propose-eval"``, ``"triggers"``) interpolated into the
            error message so users see ``clauditor grade`` for
            immediately actionable UX.

    Raises:
        OpenAIAuthMissingError: when ``OPENAI_API_KEY`` is absent, an
            empty string, or whitespace-only. Message contains the
            two durable substrings (``OPENAI_API_KEY``,
            ``platform.openai.com``) and the interpolated command
            name.
    """
    if _openai_api_key_is_set():
        return None
    # Local import to avoid a module-load circular hazard analogous to
    # ``AnthropicAuthMissingError`` (defined in ``_providers/__init__``
    # so both the auth helpers and the SDK seam reference it). At call
    # time the parent package is fully initialized.
    from clauditor._providers import OpenAIAuthMissingError

    raise OpenAIAuthMissingError(
        _OPENAI_AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
    )


def check_provider_auth(provider: str, cmd_name: str) -> None:
    """Public dispatcher routing pre-flight auth guards by provider.

    DEC-006 of ``plans/super/145-openai-provider.md``. Single seam
    every LLM-mediated CLI command targets after resolving
    ``provider = eval_spec.grading_provider or "anthropic"``. The
    branches:

    - ``provider == "anthropic"`` →
      :func:`check_any_auth_available` (the existing relaxed guard
      preserving #86 DEC-008's key-OR-CLI semantics; raises
      :class:`AnthropicAuthMissingError`).
    - ``provider == "openai"`` → :func:`check_openai_auth` (the strict
      key-only guard for OpenAI; raises
      :class:`OpenAIAuthMissingError`).
    - Unknown value → :class:`ValueError`.

    Distinct exception classes per provider keep the CLI's
    ``except`` ladder structural (one branch per class → one exit
    code) per ``.claude/rules/llm-cli-exit-code-taxonomy.md``.
    Adding a future ``provider="vertex"`` or ``"bedrock"`` is one
    branch in this dispatcher.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    delegates to pure helpers; does NOT print to stderr, does NOT
    call ``sys.exit``, does NOT log.

    Args:
        provider: Either ``"anthropic"`` or ``"openai"``.
        cmd_name: Subcommand label forwarded to the provider-specific
            guard for error-message interpolation.

    Raises:
        AnthropicAuthMissingError: ``provider="anthropic"`` and no
            usable Anthropic auth path is available.
        OpenAIAuthMissingError: ``provider="openai"`` and
            ``OPENAI_API_KEY`` is missing.
        ValueError: ``provider`` is not one of the known values.
    """
    if provider == "anthropic":
        check_any_auth_available(cmd_name)
        return None
    if provider == "openai":
        check_openai_auth(cmd_name)
        return None
    raise ValueError(
        f"check_provider_auth: unknown provider {provider!r} — "
        "expected 'anthropic' or 'openai'"
    )


# DEC-007 / DEC-010 (#151 US-003): one-shot stderr announcement when
# the four-layer harness resolver auto-resolves to ``"codex"`` (i.e. no
# explicit CLI flag, env var, or spec field; ``claude`` not on PATH;
# ``codex`` on PATH). Flipped to ``True`` after the first emission per
# Python process. Co-located with the other one-shot announcement flags
# (:data:`_announced_implicit_no_api_key`,
# :data:`_announced_call_anthropic_deprecation`) per
# ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
# announcements — an emerging family". The notice is auth-coupled (it
# names ``CODEX_API_KEY`` / ``OPENAI_API_KEY``) so this module is the
# right home rather than ``_providers/_anthropic.py`` (transport-coupled
# notices live there). Tests reset via the
# ``monkeypatch.setattr(..., False)`` autouse fixture pattern,
# targeting the canonical flag location at
# ``clauditor._providers._auth._announced_auto_codex_harness``.
_announced_auto_codex_harness: bool = False


# DEC-007 / DEC-011 (#151 US-003): the auto→codex announcement emitted
# on the first auto-resolution per Python process. Names env-var names
# only (NEVER interpolates values) per Auth review #7 of the plan. Two
# durable substrings tests pin: ``CODEX_API_KEY`` and ``OPENAI_API_KEY``
# — the env-var names users must export to authenticate Codex. The
# remainder of the message is stylistic copy and may be edited without
# churning tests.
_AUTO_CODEX_ANNOUNCEMENT: Final[str] = (
    "clauditor: auto-resolved harness to 'codex' (claude CLI not on "
    "PATH; codex CLI present). Codex needs CODEX_API_KEY or "
    "OPENAI_API_KEY exported. If you want Claude Code instead, "
    "install the claude CLI and then pin --harness=claude-code (or "
    "CLAUDITOR_HARNESS=claude-code)."
)


def announce_auto_codex_harness() -> None:
    """Emit the auto→codex harness notice to stderr once per process.

    DEC-007 / DEC-011 (#151 US-003). Called by the CLI wrapper
    ``_resolve_harness`` (US-004) when the four-layer harness resolver
    returns ``auto_resolved_to == "codex"`` — i.e. no explicit CLI
    flag, env var, or spec field forced the choice; ``claude`` is not
    on PATH; ``codex`` is. The one-shot module flag
    :data:`_announced_auto_codex_harness` ensures a single
    announcement per Python process regardless of how many subsequent
    CLI commands resolve under the same conditions.

    Same shape as :func:`announce_implicit_no_api_key` (#95 US-002)
    and :func:`announce_call_anthropic_deprecation` (#144 US-007):
    public helper, print-and-flip, one-shot per process. Tests reset
    via ``monkeypatch.setattr`` on the canonical flag location.

    Per ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
    announcements — an emerging family", the helper lives in
    ``_providers/_auth.py`` because the notice is auth-coupled (it
    names ``CODEX_API_KEY`` / ``OPENAI_API_KEY``). Transport-coupled
    notices live in ``_providers/_anthropic.py``.
    """
    global _announced_auto_codex_harness
    if _announced_auto_codex_harness:
        return
    print(_AUTO_CODEX_ANNOUNCEMENT, file=sys.stderr)
    _announced_auto_codex_harness = True


# DEC-003 / DEC-009 (#175 US-001): one-shot stderr announcement when
# :func:`check_codex_auth` accepts the pre-flight via the codex-CLI-on-
# PATH branch (i.e. neither ``CODEX_API_KEY`` nor ``OPENAI_API_KEY``
# is set; ``codex`` binary is on PATH; user is authenticated via
# ChatGPT login persisted in ``~/.codex/auth.json``). Flipped to
# ``True`` after the first emission per Python process. Fourth member
# of the implicit-coupling announcement family (co-located with
# :data:`_announced_implicit_no_api_key`,
# :data:`_announced_call_anthropic_deprecation`, and
# :data:`_announced_auto_codex_harness`) per
# ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
# announcements — an emerging family". Per DEC-009 the announcement
# fires ONLY when the PATH branch is the load-bearing acceptance
# signal — env-var-driven acceptance stays silent to keep CI noise
# down. Tests reset via the standard
# ``monkeypatch.setattr(..., False)`` autouse fixture pattern,
# targeting the canonical flag location at
# ``clauditor._providers._auth._announced_codex_cli_on_path``.
_announced_codex_cli_on_path: bool = False


# DEC-003 / DEC-004 (#175 US-001): the codex-CLI-on-PATH announcement
# emitted on the first PATH-load-bearing :func:`check_codex_auth`
# acceptance per Python process. Names env-var names and file paths
# only (NEVER interpolates values) per the Auth review #7 precedent
# from #151. Three durable substrings tests pin: ``codex`` (the CLI
# name), ``PATH`` (the discovery mechanism), and
# ``~/.codex/auth.json`` (where codex itself looks for credentials,
# so a user who has codex on PATH but never logged in knows what to
# do next). The remainder of the message is stylistic copy and may be
# edited without churning tests.
_CODEX_CLI_ON_PATH_ANNOUNCEMENT: Final[str] = (
    "clauditor: accepted codex pre-flight via codex CLI on PATH "
    "(neither CODEX_API_KEY nor OPENAI_API_KEY is set; codex itself "
    "will resolve credentials from ~/.codex/auth.json — typically "
    "the ChatGPT-login flow). If you intended to use an API key, "
    "export CODEX_API_KEY or OPENAI_API_KEY."
)


def announce_codex_cli_on_path() -> None:
    """Emit the codex-CLI-on-PATH notice to stderr once per process.

    DEC-003 / DEC-009 (#175 US-001). Called from :func:`check_codex_auth`
    immediately before returning ``None`` via the PATH-on-disk branch
    (i.e. neither ``CODEX_API_KEY`` nor ``OPENAI_API_KEY`` was set, but
    ``shutil.which("codex")`` returned a path). The one-shot module
    flag :data:`_announced_codex_cli_on_path` ensures a single
    announcement per Python process regardless of how many subsequent
    CLI commands resolve under the same conditions.

    Same shape as :func:`announce_implicit_no_api_key` (#95 US-002),
    :func:`announce_call_anthropic_deprecation` (#144 US-007), and
    :func:`announce_auto_codex_harness` (#151 US-003): public helper,
    print-and-flip, one-shot per process. Tests reset via
    ``monkeypatch.setattr`` on the canonical flag location.

    Per ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
    announcements — an emerging family", the helper lives in
    ``_providers/_auth.py`` because the notice is auth-coupled (it
    names ``CODEX_API_KEY`` / ``OPENAI_API_KEY`` and
    ``~/.codex/auth.json``).
    """
    global _announced_codex_cli_on_path
    if _announced_codex_cli_on_path:
        return
    print(_CODEX_CLI_ON_PATH_ANNOUNCEMENT, file=sys.stderr)
    _announced_codex_cli_on_path = True


# DEC-003 / DEC-010 (#151 US-003) + DEC-002 / DEC-006 / DEC-010 (#177
# US-002): message template for :func:`check_codex_auth`, the strict-OR
# pre-flight guard for the Codex harness. Mirrors
# :data:`_OPENAI_AUTH_MISSING_TEMPLATE` in shape — single
# ``{cmd_name}`` interpolation, naming the env-var names users must
# export, and listing the commands that don't require auth so users
# see the escape hatch.
#
# Four durable substrings tests pin: ``CODEX_API_KEY`` and
# ``OPENAI_API_KEY`` (the two env-var names Codex accepts) and
# ``platform.openai.com`` (the canonical place to obtain a key —
# Codex's underlying transport routes to OpenAI). The fourth anchor
# (DEC-004 of ``plans/super/175-codex-chatgpt-login-auth.md``) is
# the literal ``"codex CLI"`` — naming the third acceptance route so
# users who prefer the codex-CLI install path learn it from the error
# message.
#
# Post-#177: bullet 3 no longer mentions the ChatGPT-login flow. Per
# DEC-002 / DEC-006 of ``plans/super/177-codex-auth-mode-conflict.md``,
# clauditor refuses ChatGPT-mode credentials at pre-flight (the codex
# subprocess would route via ChatGPT and reject every model). The
# template's third bullet therefore directs users to the API-key login
# subcommand (``codex login --with-api-key``) so the resulting
# ``~/.codex/auth.json`` is in API-key mode.
_CODEX_AUTH_MISSING_TEMPLATE: Final[str] = (
    "ERROR: No usable Codex authentication found.\n"
    "clauditor {cmd_name} runs the codex harness, which needs one of:\n"
    "  1. CODEX_API_KEY exported (preferred), OR\n"
    "  2. OPENAI_API_KEY exported (get a key at "
    "https://platform.openai.com/api-keys), OR\n"
    "  3. codex CLI installed on PATH and authenticated in API-key mode\n"
    "     (run: codex login --with-api-key)\n"
    "Commands that don't need a key: lint, init, badge, audit, trend."
)


# DEC-002 / DEC-006 / DEC-010 (#177 US-002): refusal message for the
# ChatGPT-mode auth-conflict branch of :func:`check_codex_auth`.
# clauditor runs the codex harness in API-key mode; if the user has
# logged in via the ChatGPT flow (``~/.codex/auth.json`` declares
# ``auth_mode="chatgpt"``), the codex subprocess routes via ChatGPT
# and rejects every model. We refuse at pre-flight rather than
# letting the subprocess fail opaquely.
#
# Four durable substrings tests pin: ``ChatGPT`` (the auth-mode name
# we're refusing), ``~/.codex/auth.json`` (the canonical credentials
# file users edit / re-materialize), ``codex login --with-api-key``
# (the one-line remediation), and ``{cmd_name}`` (the interpolation
# anchor users see in the message).
_CODEX_AUTH_CHATGPT_MODE_TEMPLATE: Final[str] = (
    "ERROR: Codex auth-mode mismatch.\n"
    "clauditor {cmd_name} runs the codex harness in API-key mode,\n"
    "but ~/.codex/auth.json declares auth_mode=\"chatgpt\". The\n"
    "subprocess would route via ChatGPT and reject every model.\n"
    "Fix: run `codex login --with-api-key` to re-materialize\n"
    "~/.codex/auth.json in API-key mode."
)


def _codex_api_key_is_set() -> bool:
    """Return True when ``CODEX_API_KEY`` is present and non-empty.

    Whitespace-only values count as absent — same shape as
    :func:`_api_key_is_set` for ``ANTHROPIC_API_KEY`` and
    :func:`_openai_api_key_is_set` for ``OPENAI_API_KEY``. Codex's
    own "could not resolve authentication" path triggers on these
    shapes, and the pre-flight guard's whole point is to catch the
    failure with an actionable message upstream.
    """
    value = os.environ.get("CODEX_API_KEY")
    return value is not None and value.strip() != ""


def check_codex_auth(cmd_name: str) -> None:
    """Pre-flight guard: raise if no Codex auth path is available.

    DEC-001 / DEC-002 / DEC-009 / DEC-010 of
    ``plans/super/175-codex-chatgpt-login-auth.md`` (extending
    DEC-003 / DEC-010 of ``plans/super/151-harness-precedence.md``).
    Three-branch strict-OR: accepts when ANY of

    1. ``CODEX_API_KEY`` is set (whitespace-trimmed non-empty), OR
    2. ``OPENAI_API_KEY`` is set (whitespace-trimmed non-empty), OR
    3. The ``codex`` binary is on PATH (i.e. the user is logged in
       via ChatGPT and credentials are persisted at
       ``~/.codex/auth.json``; the codex CLI itself resolves them
       downstream).

    Raises :class:`CodexAuthMissingError` only when all three branches
    fail.

    Per DEC-010 the env-var branches are checked FIRST and
    short-circuit before the PATH probe — a CI run with
    ``CODEX_API_KEY`` set never triggers the codex-CLI-on-PATH
    announcement even when the CLI happens to be installed.

    Per DEC-009 :func:`announce_codex_cli_on_path` fires ONLY when
    the PATH branch is the load-bearing acceptance signal (no env
    vars set, but codex on PATH). The announcement is one-shot per
    Python process, same shape as the other implicit-coupling
    announcements (see ``.claude/rules/centralized-sdk-call.md``
    "Implicit-coupling announcements — an emerging family").

    Codex is a HARNESS axis, not a PROVIDER axis (DEC-010 of #151):
    the :func:`check_provider_auth` dispatcher is unchanged; the CLI
    seam directly calls :func:`check_codex_auth` when the resolved
    harness is ``"codex"``.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``
    in the return-value sense: reads ``os.environ`` and probes PATH
    via ``shutil.which`` only; raises on missing auth. The one
    documented side-effect is the announcement family member
    :func:`announce_codex_cli_on_path` (gated by a module-level
    one-shot flag; resets only via test monkeypatch). The CLI
    wrapper catches :class:`CodexAuthMissingError` (a direct
    subclass of :class:`Exception`, NOT
    :class:`AnthropicAuthMissingError`,
    :class:`OpenAIAuthMissingError`, or any helper-error class)
    and maps it to ``return 2`` per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``.

    Args:
        cmd_name: Subcommand label (e.g. ``"grade"``, ``"validate"``,
            ``"capture"``, ``"run"``) interpolated into the error
            message so users see ``clauditor grade`` for immediately
            actionable UX.

    Raises:
        CodexAuthMissingError: when neither ``CODEX_API_KEY`` nor
            ``OPENAI_API_KEY`` is set (both checked via whitespace-
            trimmed non-empty) AND the ``codex`` binary is not on
            PATH. Message contains the four durable substrings
            (``CODEX_API_KEY``, ``OPENAI_API_KEY``,
            ``platform.openai.com``, ``codex CLI``) and the
            interpolated command name.
    """
    # DEC-010: env-var branches short-circuit BEFORE the PATH probe so
    # env-driven acceptance stays silent (no codex-CLI-on-PATH notice).
    if _codex_api_key_is_set() or _openai_api_key_is_set():
        return None
    # DEC-001 / DEC-002: third acceptance branch — codex on PATH.
    # DEC-009: announce only here, where PATH is the load-bearing
    # acceptance signal. The helper is one-shot per process.
    if _codex_cli_is_available():
        announce_codex_cli_on_path()
        return None
    # Local import to avoid a module-load circular hazard analogous to
    # ``AnthropicAuthMissingError`` (defined in ``_providers/__init__``
    # so both the auth helpers and the SDK seam reference it). At call
    # time the parent package is fully initialized.
    from clauditor._providers import CodexAuthMissingError

    raise CodexAuthMissingError(
        _CODEX_AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
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
