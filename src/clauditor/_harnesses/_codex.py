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

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

from clauditor.runner import (
    _DROPPED_EVENTS_WARNING_PREFIX,
    _LAST_MESSAGE_EMPTY_WARNING_PREFIX,
    InvokeResult,
)

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


# DEC-001: hardcoded sandbox mode for v1. Configurability deferred to
# #151 (the ``EvalSpec.harness`` flag ticket).
_SANDBOX_MODE = "workspace-write"


# Default Codex model used when neither the constructor nor the
# per-call ``model=`` override pins a value. Documented as a single
# source of truth so future bumps land in one place. Matches the
# Codex CLI's own documented default at the time of #149.
_DEFAULT_MODEL = "gpt-5-codex"


# Truncation suffix appended on
# ``command_execution.aggregated_output`` overflow per DEC-015.
_TRUNCATED_SUFFIX = "... (truncated)"


# Warning prefix for the DEC-015 envelope-cap overflow path. Lives in
# this module (not :mod:`clauditor.runner`) because it is purely
# observability-internal — :attr:`SkillResult.succeeded_cleanly` does
# NOT inspect it (the envelope cap is advisory and does not down-
# classify success). Mirrors the locality of Claude's own
# observability-internal substrings.
_STREAM_EVENTS_TRUNCATED_WARNING_PREFIX = "stream-events-truncated:"


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

        Full implementation across US-003 (happy path) and US-004
        (error paths). Mirrors
        :meth:`clauditor._harnesses._claude_code.ClaudeCodeHarness.invoke`
        structural shape (subprocess + drainer + watchdog + NDJSON
        loop + ``try/finally`` cleanup) but substitutes Codex's argv,
        event-type dispatch, ``--output-last-message`` tempfile, and
        process-group cleanup per DEC-014.

        Error paths covered (US-004):

        - ``FileNotFoundError`` on Popen — :attr:`InvokeResult.exit_code`
          set to ``-1`` with a ``"Codex CLI not found: ..."`` message.
        - Watchdog timeout — POSIX ``os.killpg(getpgid(pid), SIGTERM)``
          then SIGKILL escalation per DEC-014; Windows uses single-pid
          ``terminate()`` / ``kill()``. Returns
          ``error="timeout"``, ``error_category="timeout"``.
        - ``turn.failed`` and top-level ``error`` events classify via
          :func:`_classify_codex_failure` per DEC-007 (closed Literal,
          ``"rate_limit" | "auth" | "api"``).
        - Malformed JSON line — skip + append warning per
          ``.claude/rules/stream-json-schema.md``.
        - Stream-events envelope cap (DEC-015) — once the running byte
          count crosses :data:`_CODEX_STREAM_EVENTS_MAX_SIZE`, stop
          appending events but keep parsing stdout for the final
          ``turn.completed`` so token usage still lands.
        - Advisory detectors (DEC-018) — both
          :func:`_detect_codex_dropped_events` and
          :func:`_detect_codex_truncated_output` run after the parse
          loop and append warnings with the
          :data:`_DROPPED_EVENTS_WARNING_PREFIX` /
          :data:`_LAST_MESSAGE_EMPTY_WARNING_PREFIX` prefixes. They are
          advisory: ``error_category`` stays ``None`` and
          ``succeeded_cleanly`` is NOT down-classified.
        - Stderr capture wraps every line through
          :func:`_filter_stderr` (DEC-013 redact + 8 KB cap).
        """
        effective_model = (
            model if model is not None else (self.model or _DEFAULT_MODEL)
        )

        start = _monotonic()
        raw_messages: list[dict] = []
        stream_events: list[dict] = []
        text_chunks: list[str] = []
        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        reasoning_output_tokens = 0
        thread_id: str | None = None
        turn_count = 0
        # Running byte count for the DEC-015 envelope cap. Once the
        # accumulator exceeds :data:`_CODEX_STREAM_EVENTS_MAX_SIZE`, we
        # stop appending events but keep reading stdout so the final
        # ``turn.completed`` (with token usage) still lands in
        # observability.
        stream_events_size = 0
        stream_events_truncated = False
        # One-shot warning flag for the command-output cap to avoid
        # log flooding under tool-heavy runs.
        cmd_output_truncated = False
        warnings: list[str] = []
        proc: subprocess.Popen | None = None
        stderr_thread: threading.Thread | None = None
        stderr_warnings_lock = threading.Lock()
        stderr_warnings: list[str] = []
        # Stream-level error classification populated by
        # :func:`_classify_codex_failure` when ``turn.failed`` /
        # top-level ``error`` events land. Once set, stays set for the
        # remainder of the run (defensive — in practice one error per
        # stream).
        stream_error_text: str | None = None
        stream_error_category: str | None = None

        # DEC-017: pre-Popen auth-source detection. Read from ``env``
        # when provided, else ``os.environ``. Order is deterministic
        # per DEC-017: CODEX_API_KEY → OPENAI_API_KEY → cached-auth
        # file → unknown.
        auth_source = self._detect_auth_source(env)

        # DEC-009 + DEC-017: sanitize subject once for both stderr
        # lines so they share the same suffix shape.
        sanitized_subject = self._sanitize_subject(subject)
        subject_suffix = (
            f" ({sanitized_subject})" if sanitized_subject else ""
        )

        # Sandbox-line is unconditional per DEC-009. Auth-line is
        # unconditional per DEC-017 (auth_source is always populated,
        # even if it is the ``"unknown"`` sentinel).
        print(
            f"clauditor.runner: codex sandbox={_SANDBOX_MODE}{subject_suffix}",
            file=sys.stderr,
        )
        print(
            f"clauditor.runner: codex auth={auth_source}{subject_suffix}",
            file=sys.stderr,
        )

        # DEC-016: per-invocation TemporaryDirectory wrapping the
        # entire body. Cleanup is automatic on context exit (success,
        # exception, or timeout).
        with tempfile.TemporaryDirectory(prefix="clauditor_codex_") as tmpdir:
            last_message_path = os.path.join(tmpdir, "last_message.txt")
            timed_out = {"hit": False}
            try:
                argv = [
                    self.codex_bin,
                    "exec",
                    "--json",
                    "--output-last-message",
                    last_message_path,
                    "--skip-git-repo-check",
                    "-s",
                    _SANDBOX_MODE,
                    "-m",
                    effective_model,
                    "-",
                ]

                # DEC-014: process-group cleanup on POSIX, single-pid
                # fallback on Windows. ``start_new_session=True`` is
                # honored by Popen on POSIX only; passing it on
                # Windows would be a TypeError, hence the branch.
                popen_kwargs: dict[str, Any] = {
                    "stdin": subprocess.PIPE,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                    "cwd": str(cwd) if cwd is not None else None,
                    "env": env,
                }
                if os.name != "nt":
                    popen_kwargs["start_new_session"] = True

                try:
                    proc = subprocess.Popen(argv, **popen_kwargs)
                except FileNotFoundError:
                    # Codex CLI not on PATH (or ``codex_bin`` typo).
                    # Surface a crisp, operator-actionable error rather
                    # than a raw OSError traceback. ``error_category``
                    # stays ``None`` because the failure is local
                    # (subprocess never spawned), not provider-side.
                    duration = _monotonic() - start
                    return InvokeResult(
                        output="",
                        exit_code=-1,
                        duration_seconds=duration,
                        error=f"Codex CLI not found: {self.codex_bin}",
                        error_category=None,
                        harness_metadata={
                            "sandbox_mode": _SANDBOX_MODE,
                            "auth_source": auth_source,
                            "model": effective_model,
                        },
                    )

                # Write the prompt to stdin (Codex reads from stdin
                # when invoked with the trailing ``-`` argv) and close
                # so the child sees EOF.
                if proc.stdin is not None:
                    try:
                        proc.stdin.write(prompt)
                    finally:
                        proc.stdin.close()

                # Drain stderr on a background thread so a chatty
                # child does not deadlock by filling its PIPE buffer
                # while we read stdout.
                stderr_chunks: list[str] = []

                def _drain_stderr() -> None:
                    if proc is None or proc.stderr is None:  # pragma: no cover
                        return
                    try:
                        for chunk in proc.stderr:
                            stderr_chunks.append(chunk)
                    except (EOFError, OSError) as exc:
                        with stderr_warnings_lock:
                            stderr_warnings.append(
                                "stderr drainer stopped: "
                                f"{type(exc).__name__}: {exc}"
                            )
                    except Exception as exc:  # noqa: BLE001 — defensive
                        with stderr_warnings_lock:
                            stderr_warnings.append(
                                "stderr drainer raised unexpected "
                                f"{type(exc).__name__}: {exc}"
                            )

                stderr_thread = threading.Thread(
                    target=_drain_stderr, daemon=True
                )
                stderr_thread.start()

                # Watchdog: kill the child if it runs past the
                # configured timeout. DEC-014: on POSIX, escalate via
                # ``os.killpg(getpgid(pid), SIGTERM)`` first then
                # SIGKILL after a brief grace period; on Windows fall
                # back to ``proc.terminate()`` / ``proc.kill()``. Each
                # syscall is wrapped in ``try / except OSError`` so a
                # race-to-exit (ESRCH "no such process") never crashes
                # the harness.
                def _on_timeout() -> None:
                    if proc is None:  # pragma: no cover
                        return
                    if proc.poll() is not None:
                        return
                    timed_out["hit"] = True
                    self._kill_proc(proc, stderr_warnings, stderr_warnings_lock)

                watchdog = threading.Timer(timeout, _on_timeout)
                watchdog.daemon = True
                watchdog.start()

                try:
                    if proc.stdout is not None:
                        for raw_line in proc.stdout:
                            line = raw_line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                            except json.JSONDecodeError as exc:
                                # Per ``.claude/rules/stream-json-schema.md``
                                # the parser MUST skip + warn on
                                # malformed lines and keep reading.
                                print(
                                    "clauditor.runner: skipping malformed "
                                    f"codex stream-json line: {exc}",
                                    file=sys.stderr,
                                )
                                warnings.append(
                                    "malformed codex stream-json line "
                                    f"skipped: {exc}"
                                )
                                continue
                            if not isinstance(msg, dict):
                                continue

                            raw_messages.append(msg)
                            mtype = msg.get("type")

                            # DEC-010: tag every appended event with
                            # ``harness="codex"``. Build a NEW event
                            # dict (non-mutating per
                            # ``.claude/rules/non-mutating-scrub.md``)
                            # so we don't mutate the caller's parsed
                            # JSON.
                            tagged_event: dict = {**msg, "harness": "codex"}

                            # Per-type processing. Soft-cap on
                            # ``command_execution.aggregated_output``
                            # rebuilds the item dict so the cap is
                            # visible in stream_events as well as in
                            # any downstream consumer.
                            if mtype == "item.completed":
                                item = msg.get("item")
                                if isinstance(item, dict):
                                    item_type = item.get("type")
                                    if item_type == "agent_message":
                                        text = item.get("text", "")
                                        if isinstance(text, str):
                                            text_chunks.append(text)
                                    elif item_type == "command_execution":
                                        capped_item, fired = (
                                            self._maybe_truncate_command_output(
                                                item
                                            )
                                        )
                                        if fired and not cmd_output_truncated:
                                            cmd_output_truncated = True
                                            cap = _CODEX_COMMAND_OUTPUT_MAX_CHARS
                                            warnings.append(
                                                "command_execution "
                                                "aggregated_output truncated "
                                                f"at {cap} chars"
                                            )
                                        tagged_event["item"] = capped_item

                            elif mtype == "thread.started":
                                # First thread.started wins (Codex
                                # emits one per process).
                                if thread_id is None:
                                    val = msg.get("thread_id")
                                    if isinstance(val, str):
                                        thread_id = val

                            elif mtype == "turn.started":
                                turn_count += 1

                            elif mtype == "turn.completed":
                                usage = msg.get("usage") or {}
                                if isinstance(usage, dict):
                                    input_tokens = self._safe_int(
                                        usage.get("input_tokens")
                                    )
                                    output_tokens = self._safe_int(
                                        usage.get("output_tokens")
                                    )
                                    cached_input_tokens = self._safe_int(
                                        usage.get("cached_input_tokens")
                                    )
                                    reasoning_output_tokens = self._safe_int(
                                        usage.get("reasoning_output_tokens")
                                    )

                            elif mtype == "turn.failed":
                                # DEC-007: per-turn failure carries
                                # ``error.message``. Classify only the
                                # FIRST such message so a benign later
                                # event does not erase a prior
                                # classification.
                                if stream_error_text is None:
                                    err_obj = msg.get("error")
                                    err_msg = (
                                        err_obj.get("message")
                                        if isinstance(err_obj, dict)
                                        else None
                                    )
                                    text, category = _classify_codex_failure(
                                        err_msg
                                    )
                                    stream_error_text = text
                                    stream_error_category = category

                            elif mtype == "error":
                                # DEC-007: top-level fatal error event
                                # — Codex emits this when something
                                # goes wrong before any turn can run
                                # (e.g. auth failure on first request).
                                if stream_error_text is None:
                                    err_msg = msg.get("message")
                                    text, category = _classify_codex_failure(
                                        err_msg
                                    )
                                    stream_error_text = text
                                    stream_error_category = category

                            # DEC-015 envelope cap: track the running
                            # byte count and stop appending once the
                            # accumulator crosses the threshold. Keep
                            # parsing subsequent lines so the final
                            # ``turn.completed`` token usage still
                            # lands. Emit one warning the first time
                            # the cap fires.
                            if not stream_events_truncated:
                                stream_events.append(tagged_event)
                                stream_events_size += len(line)
                                if (
                                    stream_events_size
                                    > _CODEX_STREAM_EVENTS_MAX_SIZE
                                ):
                                    stream_events_truncated = True
                                    cap = _CODEX_STREAM_EVENTS_MAX_SIZE
                                    warnings.append(
                                        f"{_STREAM_EVENTS_TRUNCATED_WARNING_PREFIX} "
                                        f"stream_events accumulator exceeded "
                                        f"{cap} bytes; subsequent events "
                                        "dropped (parsing continues for "
                                        "turn.completed)"
                                    )

                    returncode = proc.wait()
                finally:
                    watchdog.cancel()

                # Join the drainer before reading stderr_chunks to
                # avoid a race with the in-progress ``.append()``.
                if stderr_thread is not None:
                    stderr_thread.join(timeout=2.0)
                stderr_text = "".join(stderr_chunks)
                # DEC-013 hybrid stderr surfacing: filter (auth-leak
                # redact) + cap at 8 KB before emitting as a warning.
                if stderr_text:
                    warnings.append(_filter_stderr(stderr_text))

                # DEC-013-ish: surface drainer + watchdog channel
                # errors on the result.
                with stderr_warnings_lock:
                    if stderr_warnings:
                        warnings.extend(stderr_warnings)

                # DEC-018 advisory detectors. Both run regardless of
                # error category (they are advisory observability,
                # NOT failure classifiers). When the truncated-output
                # detector fires AND there is no agent_message text,
                # fall back to reading the ``--output-last-message``
                # tempfile content per DEC-005.
                last_message_text = ""
                try:
                    if os.path.isfile(last_message_path):
                        with open(
                            last_message_path, encoding="utf-8", errors="replace"
                        ) as f:
                            last_message_text = f.read()
                except OSError as exc:
                    warnings.append(
                        "failed to read --output-last-message tempfile: "
                        f"{type(exc).__name__}: {exc}"
                    )

                dropped_count = _detect_codex_dropped_events(stream_events)
                if dropped_count > 0:
                    warnings.append(
                        f"{_DROPPED_EVENTS_WARNING_PREFIX} {dropped_count} "
                        "events dropped from Codex in-process channel "
                        "(see Lagged synthetic events in stream_events)"
                    )

                truncated_output = _detect_codex_truncated_output(
                    stream_events, last_message_text
                )
                if truncated_output:
                    warnings.append(
                        f"{_LAST_MESSAGE_EMPTY_WARNING_PREFIX} stream "
                        "produced no agent_message items; falling back "
                        "to --output-last-message tempfile content"
                    )

                # DEC-008: build the harness_metadata dict. Absent
                # keys are OMITTED, never None. The four always-
                # populated keys (``sandbox_mode``, ``auth_source``,
                # ``last_message_path``, ``turn_count``) land
                # unconditionally.
                metadata: dict[str, Any] = {
                    "sandbox_mode": _SANDBOX_MODE,
                    "auth_source": auth_source,
                    "last_message_path": last_message_path,
                    "turn_count": turn_count,
                    "model": effective_model,
                }
                if thread_id is not None:
                    metadata["thread_id"] = thread_id
                # cached + reasoning token counts always populated
                # when turn.completed landed (token totals default
                # to 0). Test bar in DEC-008 is "available", which
                # we interpret as "we saw at least one turn.completed"
                # — but a 0 count is meaningful, so we emit them
                # whenever they are computed (not just non-zero).
                metadata["cached_input_tokens"] = cached_input_tokens
                metadata["reasoning_output_tokens"] = reasoning_output_tokens
                if dropped_count > 0:
                    metadata["dropped_events_count"] = dropped_count
                if stream_events_truncated:
                    metadata["stream_events_truncated"] = True

                # Determine the final output. DEC-005 fallback: when
                # the stream produced no agent_message items but the
                # tempfile is non-empty, use the tempfile content as
                # ``output``. Otherwise concatenate the streamed
                # ``agent_message`` text chunks.
                if text_chunks:
                    final_output = "\n".join(text_chunks)
                elif truncated_output:
                    final_output = last_message_text
                else:
                    final_output = ""

                # Timeout takes precedence over stream-level error:
                # the user-facing message is "timeout", not the
                # provider's last error before the watchdog fired.
                if timed_out["hit"]:
                    final_error: str | None = "timeout"
                    final_category: str | None = "timeout"
                    final_exit_code = -1
                else:
                    final_error = stream_error_text
                    final_category = stream_error_category
                    final_exit_code = returncode

                duration = _monotonic() - start

                return InvokeResult(
                    output=final_output,
                    exit_code=final_exit_code,
                    duration_seconds=duration,
                    error=final_error,
                    error_category=final_category,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    raw_messages=raw_messages,
                    stream_events=stream_events,
                    warnings=list(warnings),
                    api_key_source=None,
                    harness_metadata=metadata,
                )

            finally:
                # Defensive cleanup: if an exception escaped the
                # inner try, the subprocess could still be running.
                # Mirrors Claude's terminate→wait→kill escalation per
                # DEC-014, but routes through ``_kill_proc`` so the
                # POSIX-vs-Windows branching lives in one place.
                if proc is not None and proc.poll() is None:
                    self._kill_proc(proc, warnings, None)
                    try:
                        proc.wait(timeout=1)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                if proc is not None:
                    for stream in (proc.stdout, proc.stderr, proc.stdin):
                        if stream is None or not hasattr(stream, "close"):
                            continue
                        try:
                            stream.close()
                        except OSError:
                            pass

    @staticmethod
    def _kill_proc(
        proc: subprocess.Popen,
        warnings_sink: list[str],
        warnings_lock: threading.Lock | None,
    ) -> None:
        """Kill ``proc`` via DEC-014's POSIX-killpg / Windows-fallback path.

        On POSIX (``os.name != "nt"``): send SIGTERM to the process
        group via ``os.killpg(os.getpgid(pid), SIGTERM)``, sleep ~250
        ms, escalate to SIGKILL if the child is still alive. Each
        syscall is wrapped in ``try / except OSError`` so a
        race-to-exit (ESRCH "no such process") does not crash the
        harness.

        On Windows: fall back to ``proc.terminate()`` / ``proc.kill()``.
        Windows has no process-group equivalent — Codex on Windows is
        a tier-2 path per DEC-014.

        ``warnings_lock`` is the optional thread-safe lock the
        watchdog timer uses (which runs on a separate thread).
        ``None`` means the caller is on the main thread and the
        list-mutation does not need synchronization. The kill helper
        must absorb every OS-level failure into the warnings sink so
        cleanup never raises.
        """

        def _record(msg: str) -> None:
            if warnings_lock is not None:
                with warnings_lock:
                    warnings_sink.append(msg)
            else:
                warnings_sink.append(msg)

        if os.name != "nt":
            # POSIX: process-group kill so any orphaned subprocesses
            # spawned by Codex (e.g. ``command_execution`` shell
            # invocations) get cleaned up too.
            try:
                pgid = os.getpgid(proc.pid)
            except (OSError, ProcessLookupError) as exc:
                # Child already reaped — nothing to kill.
                _record(
                    "kill: getpgid failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                return
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (OSError, ProcessLookupError) as exc:
                _record(
                    "kill: killpg(SIGTERM) failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            try:
                proc.wait(timeout=0.25)
                return
            except subprocess.TimeoutExpired:
                pass
            except OSError as exc:
                _record(
                    "kill: wait after SIGTERM failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                return
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError) as exc:
                _record(
                    "kill: killpg(SIGKILL) failed: "
                    f"{type(exc).__name__}: {exc}"
                )
        else:
            # Windows: single-pid terminate/kill fallback.
            try:
                proc.terminate()
            except (OSError, ProcessLookupError) as exc:
                _record(
                    "kill: terminate failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            try:
                proc.wait(timeout=0.25)
                return
            except subprocess.TimeoutExpired:
                pass
            except OSError as exc:
                _record(
                    "kill: wait after terminate failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                return
            try:
                proc.kill()
            except (OSError, ProcessLookupError) as exc:
                _record(
                    "kill: kill failed: "
                    f"{type(exc).__name__}: {exc}"
                )

    @staticmethod
    def _safe_int(value: Any) -> int:
        """Defensive ``int()`` cast for token-usage fields per
        ``.claude/rules/stream-json-schema.md`` — falls back to 0 on
        ``None`` / non-numeric / non-string values."""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _maybe_truncate_command_output(item: dict) -> tuple[dict, bool]:
        """Apply DEC-015 soft-cap to ``aggregated_output``.

        Returns a tuple of ``(maybe_capped_item, fired)`` where
        ``fired`` is ``True`` iff the cap was applied. The returned
        item dict is a NEW object (non-mutating per
        ``.claude/rules/non-mutating-scrub.md``) so the caller can
        store both the capped form on ``stream_events`` and the
        original raw form on ``raw_messages`` if needed.
        """
        agg = item.get("aggregated_output")
        if not isinstance(agg, str):
            return item, False
        if len(agg) <= _CODEX_COMMAND_OUTPUT_MAX_CHARS:
            return item, False
        capped = agg[:_CODEX_COMMAND_OUTPUT_MAX_CHARS] + _TRUNCATED_SUFFIX
        return {**item, "aggregated_output": capped}, True

    @staticmethod
    def _detect_auth_source(env: dict[str, str] | None) -> str:
        """Resolve the active Codex auth source per DEC-017.

        Pure compute over the supplied env dict (or ``os.environ``
        when ``None``). Order: ``CODEX_API_KEY`` → ``OPENAI_API_KEY``
        → cached-auth file at ``$CODEX_HOME/auth.json`` (default
        ``~/.codex/auth.json``) → ``"unknown"`` sentinel.

        Uses ``os.path`` (not :class:`pathlib.Path`) for the cached-
        file check because :class:`Path` consults ``os.name`` to pick
        ``PosixPath`` vs ``WindowsPath`` — tests that monkeypatch
        ``os.name`` to ``"nt"`` (to exercise the DEC-014 Windows
        fallback) would otherwise hit a ``WindowsPath`` instantiation
        error on a POSIX host.
        """
        source = env if env is not None else os.environ
        if source.get("CODEX_API_KEY"):
            return "CODEX_API_KEY"
        if source.get("OPENAI_API_KEY"):
            return "OPENAI_API_KEY"
        codex_home = source.get("CODEX_HOME")
        if codex_home:
            auth_path = os.path.join(codex_home, "auth.json")
        else:
            try:
                home = os.path.expanduser("~")
            except OSError:
                home = ""
            if not home or home == "~":
                return "unknown"
            auth_path = os.path.join(home, ".codex", "auth.json")
        try:
            if os.path.isfile(auth_path):
                return "cached"
        except OSError:
            # Permission denied / path traversal guard — fall through
            # to unknown rather than raise.
            pass
        return "unknown"

    @staticmethod
    def _sanitize_subject(subject: str | None) -> str | None:
        """Apply DEC-009 sanitization: CRLF→space, strip, 200-char cap.

        Returns the sanitized string, or ``None`` if the input was
        ``None`` / empty after sanitization.
        """
        if not subject:
            return None
        cleaned = subject.replace("\r", " ").replace("\n", " ").strip()
        if not cleaned:
            return None
        return cleaned[:200]
