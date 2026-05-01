"""Codex-specific pure helpers (US-001 of issue #149).

This module hosts the pure (no-I/O, no-global-state) helpers that
classify and detect harness-specific signals on a ``codex exec
--json`` capture. Each helper traces back to a DEC in
``plans/super/149-codex-harness.md`` and is the canonical-
implementation anchor in
``.claude/rules/pure-compute-vs-io-split.md`` (sixth-anchor pattern).

US-001 ships only the module skeleton, constants, and four pure
helpers — :func:`_classify_codex_failure`,
:func:`_detect_codex_dropped_events`,
:func:`_detect_codex_truncated_output`, and :func:`_filter_stderr`.
The :class:`CodexHarness` class itself lands in US-002.

The three advisory warning-prefix constants
(``_DROPPED_EVENTS_WARNING_PREFIX``,
``_CODEX_DEPRECATION_WARNING_PREFIX``,
``_LAST_MESSAGE_EMPTY_WARNING_PREFIX``) intentionally remain in
:mod:`clauditor.runner` because they sit alongside Claude's
prefixes for symmetry — every cross-harness consumer of
:attr:`SkillResult.warnings` (e.g. #154's context sidecar) has a
single import seam. They are imported back here on demand by
US-004's ``invoke`` body so the warning *bodies* stay in lockstep
with the prefix definitions.

Per ``.claude/rules/monotonic-time-indirection.md`` the module-level
``_monotonic`` alias lets US-003/US-004 tests pin duration tracking
without clobbering the asyncio scheduler's own ``time.monotonic``
calls.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import ClassVar

from clauditor.runner import InvokeResult

# ---------------------------------------------------------------------------
# Module-level test indirections
# ---------------------------------------------------------------------------

# Module-level alias for ``time.monotonic`` per
# ``.claude/rules/monotonic-time-indirection.md``. US-003/US-004 patch
# this alias rather than ``time.monotonic`` directly so the asyncio
# event loop's own scheduler ticks are not clobbered when a test pins
# duration arithmetic.
_monotonic = time.monotonic


# ---------------------------------------------------------------------------
# Env-scrub constants (DEC-012)
# ---------------------------------------------------------------------------

# DEC-012: strip three OpenAI / Codex credential env vars on subprocess
# spawn. ``OPENAI_BASE_URL`` is included to prevent an attacker-
# controlled value from routing Codex traffic to a malicious endpoint.
# ``CODEX_API_KEY`` and ``OPENAI_API_KEY`` are the documented Codex
# credential paths.
_STRIP_ENV_VARS = frozenset(
    {
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)


# Documented preserved env vars per DEC-012. Listed here as a
# reference-only constant (not consumed by code) so the next
# maintainer reading :class:`CodexHarness.strip_auth_keys` sees why
# proxy / TLS / cached-auth env vars are intentionally NOT in
# ``_STRIP_ENV_VARS``. ``CODEX_HOME`` is the cached-auth source for
# the operator-cached-auth happy path; the TLS/proxy vars matter
# under corporate networks.
_PRESERVED_ENV_VARS_DOC = frozenset(
    {
        "CODEX_HOME",
        "SSL_CERT_FILE",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "CODEX_CA_CERTIFICATE",
        # Non-credential metadata (preserved for billing audit per
        # DEC-012):
        "OPENAI_ORG_ID",
        "OPENAI_API_VERSION",
    }
)


# ---------------------------------------------------------------------------
# Soft caps (DEC-013, DEC-015)
# ---------------------------------------------------------------------------

# Soft cap on classified Codex error text surfaced via
# :attr:`InvokeResult.error`. Bound the memory cost of a pathological
# multi-MB error payload while preserving forensic value of realistic
# 100-300 byte provider-error strings. Mirrors Claude's
# ``_RESULT_TEXT_MAX_CHARS``.
_RESULT_TEXT_MAX_CHARS = 4096


# DEC-015: 64 KB cap on ``command_execution.aggregated_output`` at
# append time. Beyond the cap, append a ``... (truncated)`` suffix
# and emit one warning per invoke (NOT per event — avoid log
# flooding under tool-heavy runs). The 16x-Claude factor reflects
# that Codex command_execution items legitimately carry shell
# transcripts, not just provider error strings.
_CODEX_COMMAND_OUTPUT_MAX_CHARS = 65536


# DEC-015: 50 MB envelope cap on the ``stream_events`` accumulator.
# Measured via running byte-count rather than periodic
# ``json.dumps`` (which would itself dominate cost on a 50 MB list).
# Per US-004: on overflow, stop appending events but keep parsing
# stdout so the final ``turn.completed`` token usage still lands.
_CODEX_STREAM_EVENTS_MAX_SIZE = 52_428_800


# DEC-013: 8 KB cap on captured stderr text before surfacing as a
# single warning entry. Codex's tracing-subscriber output can be
# verbose; trim aggressively while preserving the shape that
# operators need for triage.
_CODEX_STDERR_MAX_CHARS = 8192


# ---------------------------------------------------------------------------
# Substring patterns (DEC-007 classification + DEC-013 redaction)
# ---------------------------------------------------------------------------

# DEC-007 — error-category substring patterns. Match is
# case-insensitive on the message text. Rate-limit category is
# checked BEFORE auth (mirrors Claude's deterministic precedence in
# ``_claude_code._classify_result_message``) so a message containing
# both ``"rate limit"`` and ``"401"`` lands in ``rate_limit``.
_RATE_LIMIT_PATTERNS = (
    "rate limit",
    "rate-limit",
    "quota",
    "429",
)


_AUTH_PATTERNS = (
    "401",
    "403",
    "unauthorized",
    "OPENAI_API_KEY",
    "invalid api key",
)


# DEC-013 — substrings whose presence on a stderr line triggers
# whole-line redaction. Match is case-insensitive (the helper
# lowercases each line before probing). Each pattern targets a
# concrete leak shape:
#
# - ``api_key`` / ``Authorization`` — generic credential markers.
# - ``OPENAI_API_KEY=`` / ``CODEX_API_KEY=`` — env-dump shapes.
# - ``CODEX_HOME=`` — points at the cached-auth file.
_AUTH_LEAK_PATTERNS = (
    "api_key",
    "Authorization",
    "OPENAI_API_KEY=",
    "CODEX_API_KEY=",
    "CODEX_HOME=",
)


# Sentinel inserted in place of each redacted stderr line. The
# parenthesized rationale tells the operator that *something* was
# replaced (so silent-empty output is not the only signal).
_REDACTED_LINE_SENTINEL = "<line redacted: matched auth-leak pattern>"


# Sentinel returned by :func:`_classify_codex_failure` when the
# message is missing / empty / non-string. Mirrors Claude's
# ``"API error (no detail)"`` shape so cross-harness CLI-side
# rendering stays uniform.
_NO_DETAIL_SENTINEL = "API error (no detail)"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _classify_codex_failure(
    message: str | None,
) -> tuple[str, str]:
    """Classify a Codex error message string into ``(text, category)``.

    Pure helper. Codex surfaces failure text on
    ``turn.failed.error.message`` (per-turn) and top-level
    ``error.message`` (fatal stream-level). This helper takes the
    raw string and returns:

    - ``(<truncated_text>, <category>)`` where ``<category>`` is one
      of the closed Literal values ``"rate_limit" | "auth" | "api"``
      per DEC-007. Codex has no separate transport / interactive
      categories — the closed Literal stays closed.

    Categorization rules (DEC-007):

    - Match against :data:`_RATE_LIMIT_PATTERNS` (case-insensitive).
      Wins over ``auth`` if both keyword classes match — mirrors
      Claude's deterministic precedence.
    - Otherwise match against :data:`_AUTH_PATTERNS` (case-
      insensitive).
    - Otherwise ``"api"`` (the catchall).

    Truncation: if ``message`` exceeds
    :data:`_RESULT_TEXT_MAX_CHARS` (4096), the returned text is
    clipped and suffixed with ``" ... (truncated)"``. Classification
    runs against the truncated text, so a keyword in the surviving
    prefix still routes correctly.

    Sentinel handling: ``message`` of ``None``, empty string, or
    non-string types returns
    ``(_NO_DETAIL_SENTINEL, "api")`` — same shape Claude's
    ``_classify_result_message`` uses for missing-detail cases so
    cross-harness CLI rendering stays uniform.
    """
    if not isinstance(message, str) or message == "":
        return _NO_DETAIL_SENTINEL, "api"

    text = message
    if len(text) > _RESULT_TEXT_MAX_CHARS:
        text = text[:_RESULT_TEXT_MAX_CHARS] + " ... (truncated)"

    lowered = text.lower()
    if any(p.lower() in lowered for p in _RATE_LIMIT_PATTERNS):
        return text, "rate_limit"
    if any(p.lower() in lowered for p in _AUTH_PATTERNS):
        return text, "auth"
    return text, "api"


def _detect_codex_dropped_events(stream_events: list) -> int:
    """Sum of Lagged-synthetic dropped-event counts in the stream.

    Pure helper per DEC-018. Codex surfaces in-process channel
    overflow as a synthetic ``item.completed`` event whose
    ``item.type == "error"`` and whose ``item.message`` carries a
    leading integer ("``<N> events were dropped...``"). This helper
    walks the event list, locates every such synthetic, parses the
    leading integer, and returns the sum. ``0`` means no Lagged
    events fired.

    Defensive read posture per ``.claude/rules/stream-json-schema.md``:
    a non-dict event, a missing ``item`` key, a non-dict ``item``
    value, or a missing/non-parseable ``message`` field all
    contribute 0 (rather than raising).

    Used by US-004's ``invoke`` body to populate
    ``InvokeResult.harness_metadata["dropped_events_count"]`` (only
    when non-zero) and emit a ``dropped-events:``-prefixed warning.
    """
    total = 0
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "error":
            continue
        message = item.get("message")
        if not isinstance(message, str):
            continue
        # Parse leading integer. Anything else (including the literal
        # string ``"events were dropped..."`` with no count) → skip.
        head = message.split(" ", 1)[0]
        try:
            count = int(head)
        except ValueError:
            continue
        if count > 0:
            total += count
    return total


def _detect_codex_truncated_output(
    stream_events: list,
    last_message_text: str,
) -> bool:
    """True iff the stream produced no ``agent_message`` items but the
    ``--output-last-message`` tempfile is non-empty.

    Pure helper per DEC-018. Used as an advisory signal that the
    stream parse missed every agent-message event — typically
    because Codex's wire shape changed under us, or because the
    in-process channel dropped the agent-message events
    specifically. The fallback path in DEC-005 then reads the
    tempfile content into ``InvokeResult.output``; this detector
    fires the ``last-message-empty:`` warning so an operator knows
    why the data shape diverged from the Claude reference.

    Defensive read posture: bad event shapes do not raise; they
    simply do not count toward the agent-message tally.

    Returns ``False`` when:
    - any ``item.completed`` event has ``item.type ==
      "agent_message"`` (parse worked), OR
    - ``last_message_text`` is empty (no fallback content; nothing
      to be truncated against).
    """
    if last_message_text == "":
        return False
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message":
            return False
    return True


def _filter_stderr(stderr_text: str) -> str:
    """Apply DEC-013 hybrid stderr surfacing: redact + cap.

    Pure helper. Walks ``stderr_text`` line-by-line, replaces any
    line whose lowercased form contains a substring from
    :data:`_AUTH_LEAK_PATTERNS` with
    :data:`_REDACTED_LINE_SENTINEL`, then caps the resulting text
    at :data:`_CODEX_STDERR_MAX_CHARS` (8 KB). On overflow the
    output is suffixed with ``"... (truncated)"`` so an operator
    can tell the cap fired.

    Pattern matching is case-insensitive (lines lowercased before
    substring probe) so ``Authorization: Bearer ...`` and
    ``authorization=`` both trigger redaction.

    The redaction sentinel is distinct from the truncation suffix:
    redaction signals "a line was here and we removed its content";
    truncation signals "we have more bytes than will fit." Both can
    fire in the same call.
    """
    out_lines: list[str] = []
    # Preserve the original separator shape — Codex's stderr tends to
    # be ``\n``-terminated. ``splitlines(keepends=False)`` strips the
    # separators; we re-join with ``\n`` so the cap math is
    # deterministic. If the input had a trailing newline, preserve it.
    trailing_newline = stderr_text.endswith("\n")
    for raw_line in stderr_text.splitlines():
        lowered = raw_line.lower()
        if any(p.lower() in lowered for p in _AUTH_LEAK_PATTERNS):
            out_lines.append(_REDACTED_LINE_SENTINEL)
        else:
            out_lines.append(raw_line)
    rebuilt = "\n".join(out_lines)
    if trailing_newline and rebuilt:
        rebuilt = rebuilt + "\n"

    if len(rebuilt) > _CODEX_STDERR_MAX_CHARS:
        rebuilt = rebuilt[:_CODEX_STDERR_MAX_CHARS] + "... (truncated)"
    return rebuilt


# ---------------------------------------------------------------------------
# CodexHarness — class surface (US-002)
# ---------------------------------------------------------------------------


class CodexHarness:
    """Harness implementation that drives the ``codex exec --json`` CLI.

    Concrete implementation of the :class:`clauditor._harnesses.Harness`
    structural protocol introduced in US-001 of issue #148, parallel to
    :class:`clauditor._harnesses._claude_code.ClaudeCodeHarness` but
    targeting OpenAI's Codex CLI (per #149).

    Per DEC-011 of ``plans/super/149-codex-harness.md`` the class lives
    in this private module and is intentionally NOT exported from
    :mod:`clauditor._harnesses` — instantiation goes through
    ``from clauditor._harnesses._codex import CodexHarness`` only,
    mirroring Claude's privacy.

    Per DEC-006 the construction surface is two kwargs only:
    ``codex_bin`` (CLI binary path; default ``"codex"``) and ``model``
    (default ``None`` — the per-call ``invoke(model=...)`` override
    lands in US-003). There is intentionally NO ``allow_hang_heuristic``
    knob: Codex has no hang-detection heuristics and
    :mod:`clauditor.runner` short-circuits on the missing attribute via
    ``getattr(self.harness, "allow_hang_heuristic", None)``.

    The :meth:`invoke` body lands in US-003/US-004; this class provides
    the three protocol methods that don't require a subprocess
    (``__init__``, :meth:`strip_auth_keys`, :meth:`build_prompt`).
    """

    name: ClassVar[str] = "codex"

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        model: str | None = None,
    ) -> None:
        """Construct a Codex harness with optional binary path / model override.

        Both kwargs are keyword-only per DEC-006 — no positional surface
        to grow into. ``codex_bin`` is the path to the ``codex`` CLI
        executable (the default ``"codex"`` resolves via ``$PATH``).
        ``model`` is the default model id used when :meth:`invoke` is
        called without a per-call ``model=`` override (US-003).
        """
        self.codex_bin = codex_bin
        self.model = model

    def strip_auth_keys(
        self, env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Return a new env dict with Codex/OpenAI auth env vars removed.

        Pure, non-mutating per ``.claude/rules/non-mutating-scrub.md``.
        Removes the three env vars listed in :data:`_STRIP_ENV_VARS`
        (``CODEX_API_KEY``, ``OPENAI_API_KEY``, ``OPENAI_BASE_URL``)
        per DEC-012. ``OPENAI_BASE_URL`` is included in the strip set
        to prevent an attacker-controlled value from routing Codex
        traffic to a malicious endpoint.

        When ``env`` is ``None``, reads from :data:`os.environ` so the
        helper composes with future ``call_codex(env=None)`` callers
        without forcing each caller to materialize an env snapshot.
        """
        source = env if env is not None else os.environ
        return {k: v for k, v in source.items() if k not in _STRIP_ENV_VARS}

    def build_prompt(
        self,
        skill_name: str,
        args: str,
        *,
        system_prompt: str | None,
    ) -> str:
        """Compose a flat prompt string from ``system_prompt`` and ``args``.

        Per DEC-011 of ``plans/super/149-codex-harness.md`` Codex has no
        slash-command analog, so ``skill_name`` is intentionally ignored
        — every harness still receives it for cross-harness signature
        parity. When ``system_prompt`` is truthy, returns
        ``f"{system_prompt}\\n\\n{args}"``; when ``system_prompt`` is
        ``None`` or the empty string, returns ``args`` alone (no leading
        separator).

        ``system_prompt`` is keyword-only per
        ``.claude/rules/harness-protocol-shape.md`` (it is a positional-
        swap risk against ``args``: both are strings).

        Pure compute (no I/O, no global state) per
        ``.claude/rules/pure-compute-vs-io-split.md``.
        """
        if system_prompt:
            return f"{system_prompt}\n\n{args}"
        return args

    def invoke(
        self,
        prompt: str,
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
        timeout: int,
        model: str | None = None,
        subject: str | None = None,
    ) -> InvokeResult:
        """Run ``codex exec --json`` and return an :class:`InvokeResult`.

        US-002 stub: present so :class:`CodexHarness` satisfies the
        ``Harness`` runtime-checkable protocol, but the body lands in
        US-003 (happy path) and US-004 (error paths) of issue #149.
        Raises :class:`NotImplementedError` if invoked before those
        stories ship.
        """
        raise NotImplementedError(
            "CodexHarness.invoke lands in US-003/US-004 of issue #149"
        )
