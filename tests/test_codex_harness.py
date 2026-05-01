"""Tests for :mod:`clauditor._harnesses._codex` — pure helpers.

US-001 of issue #149 covers the module skeleton and pure helpers:

- :func:`_classify_codex_failure` — substring-classify Codex error
  text into one of the closed ``error_category`` Literal values
  (``rate_limit``, ``auth``, ``api``) per DEC-007.
- :func:`_detect_codex_dropped_events` — count Lagged-synthetic
  events surfaced as ``item.completed`` with item type ``error``
  per DEC-018.
- :func:`_detect_codex_truncated_output` — flag the case where the
  Codex stream produced no ``agent_message`` items but the
  ``--output-last-message`` tempfile contains text per DEC-018.

The :class:`CodexHarness` class itself lands in US-002; this file
covers only the pure-helper surface.
"""

from __future__ import annotations

import importlib

# Reload the module under test so coverage instrumentation (which
# starts after collection) sees every line. Mirrors the pattern in
# ``tests/test_runner.py`` for ``_claude_code``.
import clauditor._harnesses._codex as _codex_mod

importlib.reload(_codex_mod)

from clauditor._harnesses._codex import (  # noqa: E402
    _AUTH_LEAK_PATTERNS,
    _AUTH_PATTERNS,
    _CODEX_COMMAND_OUTPUT_MAX_CHARS,
    _CODEX_STDERR_MAX_CHARS,
    _CODEX_STREAM_EVENTS_MAX_SIZE,
    _RATE_LIMIT_PATTERNS,
    _RESULT_TEXT_MAX_CHARS,
    _STRIP_ENV_VARS,
    _classify_codex_failure,
    _detect_codex_dropped_events,
    _detect_codex_truncated_output,
    _filter_stderr,
)
from clauditor.runner import (  # noqa: E402
    _CODEX_DEPRECATION_WARNING_PREFIX,
    _DROPPED_EVENTS_WARNING_PREFIX,
    _LAST_MESSAGE_EMPTY_WARNING_PREFIX,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Constants land at the values DEC-007/013/015/018 specify."""

    def test_strip_env_vars_dec_012(self) -> None:
        """DEC-012: strip three OpenAI/Codex credential env vars."""
        assert _STRIP_ENV_VARS == frozenset(
            {"CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"}
        )

    def test_result_text_max_chars(self) -> None:
        """4 KB cap mirrors Claude's ``_RESULT_TEXT_MAX_CHARS``."""
        assert _RESULT_TEXT_MAX_CHARS == 4096

    def test_codex_command_output_max_chars(self) -> None:
        """DEC-015: 64 KB cap on ``command_execution.aggregated_output``."""
        assert _CODEX_COMMAND_OUTPUT_MAX_CHARS == 65536

    def test_codex_stream_events_max_size(self) -> None:
        """DEC-015: 50 MB envelope cap on ``stream_events`` accumulator."""
        assert _CODEX_STREAM_EVENTS_MAX_SIZE == 52_428_800

    def test_codex_stderr_max_chars(self) -> None:
        """DEC-013: 8 KB cap on captured stderr text."""
        assert _CODEX_STDERR_MAX_CHARS == 8192

    def test_auth_leak_patterns_present(self) -> None:
        """DEC-013: stderr-redaction patterns include the documented set."""
        # Case-insensitive match on substrings — store the test bar at
        # the lowercased form so a future capitalization tweak does not
        # silently invalidate this drift-guard.
        lowered = {p.lower() for p in _AUTH_LEAK_PATTERNS}
        assert "api_key" in lowered
        assert "authorization" in lowered
        assert "openai_api_key=" in lowered
        assert "codex_api_key=" in lowered
        assert "codex_home=" in lowered

    def test_rate_limit_patterns_present(self) -> None:
        """DEC-007: rate-limit substrings."""
        lowered = {p.lower() for p in _RATE_LIMIT_PATTERNS}
        assert "rate limit" in lowered
        assert "quota" in lowered

    def test_auth_patterns_present(self) -> None:
        """DEC-007: auth substrings — 401/403/etc."""
        lowered = {p.lower() for p in _AUTH_PATTERNS}
        assert "unauthorized" in lowered
        assert "openai_api_key" in lowered
        # 401 / 403 substrings live verbatim (already lowercase).
        assert "401" in lowered
        assert "403" in lowered
        assert "invalid api key" in lowered


class TestRunnerWarningPrefixes:
    """DEC-018 prefixes live on :mod:`clauditor.runner` (per US-001 plan).

    They share a module with ``_INTERACTIVE_HANG_WARNING_PREFIX`` /
    ``_BACKGROUND_TASK_WARNING_PREFIX`` so :attr:`SkillResult.succeeded_cleanly`
    can pattern-match advisory vs. failure-type warnings consistently.
    """

    def test_dropped_events_prefix(self) -> None:
        assert _DROPPED_EVENTS_WARNING_PREFIX == "dropped-events:"

    def test_codex_deprecation_prefix(self) -> None:
        assert _CODEX_DEPRECATION_WARNING_PREFIX == "codex-deprecation:"

    def test_last_message_empty_prefix(self) -> None:
        assert _LAST_MESSAGE_EMPTY_WARNING_PREFIX == "last-message-empty:"


# ---------------------------------------------------------------------------
# _classify_codex_failure (DEC-007)
# ---------------------------------------------------------------------------


class TestClassifyCodexFailure:
    """Pure-unit tests for the substring-driven failure classifier.

    Mirrors ``TestClassifyResultMessage`` in ``test_runner.py`` for the
    Claude side. The Codex flavor takes a raw ``message: str | None``
    rather than a stream-event dict because Codex surfaces failure
    text on ``turn.failed.error.message`` and top-level ``error.message``
    fields rather than a single ``result`` message.
    """

    def test_rate_limit_classifies_as_rate_limit(self) -> None:
        """DEC-007: ``"rate limit"`` substring → ``rate_limit``."""
        text, category = _classify_codex_failure("Rate Limit exceeded")
        assert category == "rate_limit"
        assert text == "Rate Limit exceeded"

    def test_quota_classifies_as_rate_limit(self) -> None:
        """DEC-007: ``"quota"`` substring → ``rate_limit``."""
        text, category = _classify_codex_failure("project quota exhausted")
        assert category == "rate_limit"
        assert text == "project quota exhausted"

    def test_401_classifies_as_auth(self) -> None:
        """DEC-007: ``"401"`` substring → ``auth``."""
        text, category = _classify_codex_failure("HTTP 401 unauthorized")
        assert category == "auth"
        assert text == "HTTP 401 unauthorized"

    def test_403_classifies_as_auth(self) -> None:
        """DEC-007: ``"403"`` substring → ``auth``."""
        text, category = _classify_codex_failure("403 forbidden")
        assert category == "auth"
        assert text == "403 forbidden"

    def test_openai_api_key_classifies_as_auth(self) -> None:
        """DEC-007: ``"OPENAI_API_KEY"`` substring (case-insensitive) → ``auth``."""
        text, category = _classify_codex_failure(
            "Please set OPENAI_API_KEY in env"
        )
        assert category == "auth"
        assert "OPENAI_API_KEY" in text

    def test_invalid_api_key_classifies_as_auth(self) -> None:
        """DEC-007: ``"invalid api key"`` substring → ``auth``."""
        text, category = _classify_codex_failure("invalid API key provided")
        assert category == "auth"
        assert text == "invalid API key provided"

    def test_unauthorized_classifies_as_auth(self) -> None:
        """DEC-007: ``"unauthorized"`` substring → ``auth``."""
        _, category = _classify_codex_failure("Unauthorized: bad token")
        assert category == "auth"

    def test_generic_classifies_as_api(self) -> None:
        """DEC-007: no rate-limit/auth keyword → ``api``."""
        text, category = _classify_codex_failure("Internal server error")
        assert category == "api"
        assert text == "Internal server error"

    def test_rate_limit_wins_over_auth_when_both_present(self) -> None:
        """DEC-007 ordering: rate-limit check runs first."""
        _, category = _classify_codex_failure(
            "rate limit hit; 401 unauthorized fallback"
        )
        assert category == "rate_limit"

    def test_none_message_returns_sentinel(self) -> None:
        """``None`` is the "no message available" sentinel."""
        text, category = _classify_codex_failure(None)
        # Mirror Claude's ``"API error (no detail)"`` sentinel + ``api``.
        assert text == "API error (no detail)"
        assert category == "api"

    def test_empty_message_returns_sentinel(self) -> None:
        """Empty string falls back to the same sentinel."""
        text, category = _classify_codex_failure("")
        assert text == "API error (no detail)"
        assert category == "api"

    def test_non_string_message_returns_sentinel(self) -> None:
        """Defensive: an int / dict / list slipped in returns the sentinel."""
        text, category = _classify_codex_failure(123)  # type: ignore[arg-type]
        assert text == "API error (no detail)"
        assert category == "api"

    def test_truncation_at_4kb(self) -> None:
        """Long text clipped at the soft cap with the suffix."""
        big = "X" * 5000
        text, category = _classify_codex_failure(big)
        assert text.endswith(" ... (truncated)")
        assert len(text) == _RESULT_TEXT_MAX_CHARS + len(" ... (truncated)")
        assert category == "api"

    def test_truncation_preserves_classification(self) -> None:
        """Keyword in the surviving prefix is still detected after truncation."""
        msg = "rate limit exceeded — " + "X" * 5000
        text, category = _classify_codex_failure(msg)
        assert category == "rate_limit"
        assert text.endswith(" ... (truncated)")


# ---------------------------------------------------------------------------
# _detect_codex_dropped_events (DEC-018)
# ---------------------------------------------------------------------------


class TestDetectCodexDroppedEvents:
    """The Lagged-synthetic counter is a defensive sum across events."""

    def test_empty_stream_returns_zero(self) -> None:
        """No events → no drops."""
        assert _detect_codex_dropped_events([]) == 0

    def test_no_lagged_events_returns_zero(self) -> None:
        """Ordinary item.completed / turn.completed events contribute nothing."""
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "a", "type": "agent_message", "text": "hi"},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        ]
        assert _detect_codex_dropped_events(events) == 0

    def test_single_lagged_synthetic_returns_count(self) -> None:
        """One synthetic ``item.completed`` with item.type=error and
        ``5 events were dropped`` text → 5."""
        msg = "5 events were dropped"
        events = [
            {
                "type": "item.completed",
                "item": {"id": "lag", "type": "error", "message": msg},
            },
        ]
        assert _detect_codex_dropped_events(events) == 5

    def test_multiple_lagged_events_sum(self) -> None:
        """Two synthetic Lagged events with N=2 and N=7 → 9."""
        events = [
            {
                "type": "item.completed",
                "item": {
                    "id": "a",
                    "type": "error",
                    "message": "2 events were dropped",
                },
            },
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "b",
                    "type": "error",
                    "message": "7 events were dropped",
                },
            },
        ]
        assert _detect_codex_dropped_events(events) == 9

    def test_malformed_events_tolerated(self) -> None:
        """A non-dict item, missing ``message`` field, or non-numeric
        leading token does not raise — defensive read posture."""
        events = [
            {"type": "item.completed"},  # missing item
            {"type": "item.completed", "item": None},  # null item
            {"type": "item.completed", "item": "not a dict"},  # wrong type
            {
                "type": "item.completed",
                "item": {"type": "error"},  # missing message
            },
            {
                "type": "item.completed",
                # ``int("events")`` → ValueError; absorbed silently.
                "item": {
                    "id": "x",
                    "type": "error",
                    "message": "events were dropped: unknown count",
                },
            },
            "bare string",  # not even a dict
            None,
            {
                "type": "item.completed",
                "item": {
                    "id": "c",
                    "type": "error",
                    "message": "3 events were dropped",
                },
            },
        ]
        assert _detect_codex_dropped_events(events) == 3


# ---------------------------------------------------------------------------
# _detect_codex_truncated_output (DEC-018)
# ---------------------------------------------------------------------------


class TestDetectCodexTruncatedOutput:
    """Truncation detector: True iff stream had no ``agent_message`` items
    and the ``--output-last-message`` tempfile is non-empty."""

    def test_no_agent_message_with_nonempty_tempfile_returns_true(self) -> None:
        """Stream parsing missed every agent_message item but the file
        carries the final message → truncation suspected (DEC-018)."""
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {
                "type": "item.completed",
                "item": {"id": "r", "type": "reasoning", "text": "thought"},
            },
        ]
        assert _detect_codex_truncated_output(events, "the final answer") is True

    def test_one_agent_message_returns_false(self) -> None:
        """At least one agent_message item present → not truncated."""
        events = [
            {
                "type": "item.completed",
                "item": {"id": "a", "type": "agent_message", "text": "answer"},
            },
        ]
        assert _detect_codex_truncated_output(events, "answer") is False

    def test_no_agent_message_with_empty_tempfile_returns_false(self) -> None:
        """Both signals empty → can't tell anything is truncated."""
        events = [
            {"type": "thread.started", "thread_id": "t1"},
        ]
        assert _detect_codex_truncated_output(events, "") is False

    def test_malformed_events_tolerated(self) -> None:
        """Defensive read: bad shapes do not raise."""
        events = [
            None,
            "string",
            {"type": "item.completed"},
            {"type": "item.completed", "item": None},
            {"type": "item.completed", "item": "wrong"},
        ]
        # No agent_message anywhere; non-empty file → True.
        assert _detect_codex_truncated_output(events, "x") is True

    def test_empty_stream_with_empty_text_returns_false(self) -> None:
        """The all-empty case is not truncation."""
        assert _detect_codex_truncated_output([], "") is False


# ---------------------------------------------------------------------------
# _filter_stderr (DEC-013)
# ---------------------------------------------------------------------------


class TestFilterStderr:
    """Hybrid stderr surfacing: redact auth-leaks, cap at 8 KB."""

    def test_passthrough_on_clean_text(self) -> None:
        """No redaction patterns → input returned untouched (after cap)."""
        text = "starting codex exec\nturn started\n"
        assert _filter_stderr(text) == text

    def test_redacts_line_with_authorization_header(self) -> None:
        """A line mentioning ``Authorization`` is replaced with the
        sentinel."""
        text = "ok\nAuthorization: Bearer xyz\nfine\n"
        out = _filter_stderr(text)
        assert "xyz" not in out
        assert "<line redacted: matched auth-leak pattern>" in out
        assert "ok" in out
        assert "fine" in out

    def test_redacts_line_with_openai_api_key_assignment(self) -> None:
        text = "OPENAI_API_KEY=sk-secret\n"
        out = _filter_stderr(text)
        assert "sk-secret" not in out
        assert "<line redacted: matched auth-leak pattern>" in out

    def test_caps_at_8kb_with_truncated_suffix(self) -> None:
        """Anything over ``_CODEX_STDERR_MAX_CHARS`` is clipped + suffix."""
        big = "X" * (_CODEX_STDERR_MAX_CHARS + 100)
        out = _filter_stderr(big)
        assert out.endswith("... (truncated)")
        # Total length: cap + suffix.
        assert len(out) == _CODEX_STDERR_MAX_CHARS + len("... (truncated)")
