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
import os

# Reload the module under test so coverage instrumentation (which
# starts after collection) sees every line. Mirrors the pattern in
# ``tests/test_runner.py`` for ``_claude_code``.
import clauditor._harnesses._codex as _codex_mod
from tests.conftest import (
    _FakeCodexPopen,
    make_fake_codex_agent_message_item,
    make_fake_codex_command_execution_item,
    make_fake_codex_file_change_item,
    make_fake_codex_malformed_line_in_stream,
    make_fake_codex_mcp_tool_call_item,
    make_fake_codex_no_agent_message,
    make_fake_codex_reasoning_item,
    make_fake_codex_stream,
    make_fake_codex_todo_list_item,
    make_fake_codex_top_level_error,
    make_fake_codex_turn_failed,
    make_fake_codex_web_search_item,
    make_fake_codex_with_lagged_event,
)

importlib.reload(_codex_mod)

from clauditor._harnesses._codex import (  # noqa: E402
    _AUTH_LEAK_PATTERNS,
    _AUTH_PATTERNS,
    _CODEX_COMMAND_OUTPUT_MAX_CHARS,
    _CODEX_STDERR_MAX_CHARS,
    _CODEX_STREAM_EVENTS_MAX_SIZE,
    _PRESERVED_ENV_VARS_DOC,
    _RATE_LIMIT_PATTERNS,
    _RESULT_TEXT_MAX_CHARS,
    _STRIP_ENV_VARS,
    CodexHarness,
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

    def test_strip_set_disjoint_from_preserved_doc(self) -> None:
        """DEC-012 drift-guard: every key in ``_STRIP_ENV_VARS`` must NOT
        appear in ``_PRESERVED_ENV_VARS_DOC`` (and vice-versa). A future
        contributor who adds a key to one set without realizing the other
        already lists it would silently produce contradictory behavior."""
        assert _STRIP_ENV_VARS.isdisjoint(_PRESERVED_ENV_VARS_DOC)


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

    def test_redacts_bare_sk_proj_key_without_anchor(self) -> None:
        """Pass B regex layer: a stderr line carrying a bare
        ``sk-proj-...`` key (no surrounding ``api_key`` /
        ``Authorization`` anchor) is still redacted in full."""
        text = "client init done\nsk-proj-abcDEF123456789012345xyz\nbye\n"
        out = _filter_stderr(text)
        assert "sk-proj-abcDEF123456789012345xyz" not in out
        assert "<line redacted: matched auth-leak pattern>" in out
        # Surrounding lines pass through untouched.
        assert "client init done" in out
        assert "bye" in out

    def test_redacts_bare_bearer_token_without_anchor(self) -> None:
        """Pass B regex layer: a bare ``Bearer <opaque>`` token (no
        ``Authorization:`` label) is still redacted."""
        text = "stack frame: Bearer abcdefghijklmnopqrstuvwx\n"
        out = _filter_stderr(text)
        assert "abcdefghijklmnopqrstuvwx" not in out
        assert "<line redacted: matched auth-leak pattern>" in out

    def test_multi_line_with_one_bad_line_only_that_line_redacted(self) -> None:
        """Defense: only the line containing the key shape is redacted;
        the surrounding clean lines pass through unmodified."""
        text = (
            "INFO 2026-04-30T01:02:03Z starting\n"
            "DEBUG  http header sk-proj-LEAKEDsomething123456789x\n"
            "INFO 2026-04-30T01:02:04Z done\n"
        )
        out = _filter_stderr(text)
        assert "sk-proj-LEAKEDsomething123456789x" not in out
        assert "<line redacted: matched auth-leak pattern>" in out
        # Other lines preserved verbatim.
        assert "INFO 2026-04-30T01:02:03Z starting" in out
        assert "INFO 2026-04-30T01:02:04Z done" in out


# ---------------------------------------------------------------------------
# CodexHarness.strip_auth_keys (DEC-012, US-002)
# ---------------------------------------------------------------------------


class TestCodexHarnessStripAuthKeys:
    """``CodexHarness.strip_auth_keys`` removes the three Codex/OpenAI
    credential env vars per DEC-012, returns a NEW dict (non-mutating per
    ``.claude/rules/non-mutating-scrub.md``), preserves every other key
    (including the six explicitly documented preserved vars), and reads
    from ``os.environ`` when called with ``None``.
    """

    def test_strips_three_named_credentials(self) -> None:
        """DEC-012: ``CODEX_API_KEY``, ``OPENAI_API_KEY``, ``OPENAI_BASE_URL``
        are removed. ``OPENAI_BASE_URL`` is included to prevent attacker-
        routed Codex traffic to a malicious endpoint."""
        env = {
            "CODEX_API_KEY": "sk-codex",
            "OPENAI_API_KEY": "sk-openai",
            "OPENAI_BASE_URL": "https://evil.example/v1",
            "PATH": "/usr/bin",
        }
        scrubbed = CodexHarness().strip_auth_keys(env)
        assert "CODEX_API_KEY" not in scrubbed
        assert "OPENAI_API_KEY" not in scrubbed
        assert "OPENAI_BASE_URL" not in scrubbed
        assert scrubbed["PATH"] == "/usr/bin"

    def test_preserves_six_named_non_credentials_and_arbitrary_others(self) -> None:
        """DEC-012: the six documented preserved vars and arbitrary
        unrelated env vars survive the scrub."""
        env = {
            # The three stripped:
            "CODEX_API_KEY": "sk-codex",
            "OPENAI_API_KEY": "sk-openai",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            # The six preserved per DEC-012:
            "CODEX_HOME": "/home/u/.codex",
            "SSL_CERT_FILE": "/etc/ssl/ca.pem",
            "HTTPS_PROXY": "http://proxy:8080",
            "HTTP_PROXY": "http://proxy:8080",
            "NO_PROXY": "localhost",
            "CODEX_CA_CERTIFICATE": "/etc/ssl/codex.pem",
            # Non-credential metadata also preserved:
            "OPENAI_ORG_ID": "org-abc",
            "OPENAI_API_VERSION": "2024-10-01",
            # Arbitrary unrelated env:
            "PATH": "/usr/bin",
            "HOME": "/home/u",
            "FOO": "bar",
        }
        scrubbed = CodexHarness().strip_auth_keys(env)
        for stripped_key in ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            assert stripped_key not in scrubbed
        for preserved_key in (
            "CODEX_HOME",
            "SSL_CERT_FILE",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "CODEX_CA_CERTIFICATE",
            "OPENAI_ORG_ID",
            "OPENAI_API_VERSION",
            "PATH",
            "HOME",
            "FOO",
        ):
            assert preserved_key in scrubbed
            assert scrubbed[preserved_key] == env[preserved_key]

    def test_non_mutating_input_unchanged(self) -> None:
        """The input dict is not mutated; the original credentials
        survive on the caller's reference per
        ``.claude/rules/non-mutating-scrub.md``."""
        env = {
            "CODEX_API_KEY": "sk-codex",
            "OPENAI_API_KEY": "sk-openai",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "PATH": "/usr/bin",
        }
        snapshot = dict(env)
        scrubbed = CodexHarness().strip_auth_keys(env)
        # Caller's dict survives intact.
        assert env == snapshot
        # And the returned dict is genuinely a different object.
        assert scrubbed is not env

    def test_no_args_raises_typeerror(self) -> None:
        """``strip_auth_keys`` mirrors the ``Harness`` protocol exactly:
        ``env`` is a required positional parameter. Calling without args
        raises ``TypeError``, matching ``ClaudeCodeHarness`` and
        ``MockHarness``. Callers who want process-env behavior pass
        :data:`os.environ` explicitly."""
        import pytest

        with pytest.raises(TypeError):
            CodexHarness().strip_auth_keys()  # type: ignore[call-arg]

    def test_explicit_os_environ_passthrough(self, monkeypatch) -> None:
        """Operators who want to scrub the live process env pass
        :data:`os.environ` explicitly rather than relying on a default."""
        import os

        monkeypatch.setenv("CODEX_API_KEY", "sk-from-env")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-also-from-env")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setenv("FOO_FROM_ENV", "preserved")
        scrubbed = CodexHarness().strip_auth_keys(dict(os.environ))
        assert "CODEX_API_KEY" not in scrubbed
        assert "OPENAI_API_KEY" not in scrubbed
        assert "OPENAI_BASE_URL" not in scrubbed
        assert scrubbed.get("FOO_FROM_ENV") == "preserved"

    def test_case_insensitive_strip(self) -> None:
        """DEC-012 / Pass B: lowercase variants of the strip-set keys
        are also removed — defense-in-depth against environments that
        surface inconsistent casing."""
        env = {
            "openai_api_key": "sk-lower",
            "OPENAI_API_KEY": "sk-upper",
            "Codex_Api_Key": "sk-mixed",
            "PATH": "/usr/bin",
        }
        scrubbed = CodexHarness().strip_auth_keys(env)
        assert "openai_api_key" not in scrubbed
        assert "OPENAI_API_KEY" not in scrubbed
        assert "Codex_Api_Key" not in scrubbed
        assert scrubbed["PATH"] == "/usr/bin"

    def test_empty_input_returns_empty_dict(self) -> None:
        """Edge case: empty input dict yields empty output dict
        (not the same object — non-mutating contract)."""
        env: dict[str, str] = {}
        scrubbed = CodexHarness().strip_auth_keys(env)
        assert scrubbed == {}
        assert scrubbed is not env


# ---------------------------------------------------------------------------
# CodexHarness._sanitize_subject (DEC-009 + Pass B hardening)
# ---------------------------------------------------------------------------


class TestSanitizeSubject:
    """``_sanitize_subject`` strips ANSI escape sequences, C0 control
    characters, and DEL — defense-in-depth around the
    ``clauditor.runner: codex ... (subject)`` stderr line so a hostile
    label cannot inject terminal-control codes (cursor moves, color
    resets, OSC hyperlinks) into operator-facing output.
    """

    def test_none_input_returns_none(self) -> None:
        assert CodexHarness._sanitize_subject(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert CodexHarness._sanitize_subject("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert CodexHarness._sanitize_subject("   \t\n  ") is None

    def test_basic_label_passthrough(self) -> None:
        assert (
            CodexHarness._sanitize_subject("L2 extraction") == "L2 extraction"
        )

    def test_crlf_replaced_with_spaces(self) -> None:
        out = CodexHarness._sanitize_subject("a\r\nb")
        assert out == "a  b"

    def test_strips_ansi_color_codes(self) -> None:
        """An ANSI CSI sequence (``\\x1b[31m...``) is stripped — the
        visible characters survive, the escape itself is gone."""
        out = CodexHarness._sanitize_subject("\x1b[31mFAIL\x1b[0m grading")
        assert out == "FAIL grading"
        assert "\x1b" not in out

    def test_strips_tab_and_null_byte(self) -> None:
        """C0 controls (NUL through US) are dropped, leaving the visible
        text behind."""
        out = CodexHarness._sanitize_subject("a\tb\x00c")
        assert out == "abc"

    def test_strips_del_character(self) -> None:
        """DEL (``0x7F``) is also dropped per the C0 + DEL scrub."""
        out = CodexHarness._sanitize_subject("foo\x7fbar")
        assert out == "foobar"

    def test_strips_osc_hyperlink(self) -> None:
        """An ANSI OSC 8 hyperlink sequence (``\\x1b]8;;url\\x1b\\\\text...``)
        is stripped before sanitization continues."""
        out = CodexHarness._sanitize_subject(
            "\x1b]8;;https://evil.example\x1b\\click here\x1b]8;;\x1b\\"
        )
        # The visible "click here" text survives; both OSC framings
        # (open + close) are removed.
        assert "click here" in out
        assert "\x1b" not in out
        assert "evil.example" not in out

    def test_caps_at_200_chars(self) -> None:
        out = CodexHarness._sanitize_subject("x" * 500)
        assert out is not None
        assert len(out) == 200


# ---------------------------------------------------------------------------
# CodexHarness.build_prompt (DEC-011, US-002)
# ---------------------------------------------------------------------------


class TestCodexHarnessBuildPrompt:
    """``CodexHarness.build_prompt`` joins ``system_prompt`` and ``args``
    with ``\\n\\n`` per DEC-011. ``skill_name`` is intentionally ignored
    (Codex has no slash-command analog). ``system_prompt=""`` is treated
    as falsy.
    """

    def test_system_prompt_none_returns_args_unchanged(self) -> None:
        """``system_prompt=None`` → return ``args`` alone (no separator,
        no leading newlines)."""
        result = CodexHarness().build_prompt("foo", "do the thing", system_prompt=None)
        assert result == "do the thing"

    def test_empty_system_prompt_treated_as_none(self) -> None:
        """DEC-011: ``system_prompt=""`` is treated as falsy, same as
        ``None`` — no leading newlines or separator are emitted."""
        result = CodexHarness().build_prompt("foo", "do the thing", system_prompt="")
        assert result == "do the thing"

    def test_truthy_system_prompt_joined_with_double_newline(self) -> None:
        """DEC-011: non-empty ``system_prompt`` is joined to ``args``
        with ``\\n\\n`` separator."""
        result = CodexHarness().build_prompt(
            "foo", "do the thing", system_prompt="You are an expert."
        )
        assert result == "You are an expert.\n\ndo the thing"

    def test_preserves_embedded_newlines_in_both_inputs(self) -> None:
        """Multi-line system_prompt and args are emitted verbatim with the
        ``\\n\\n`` separator between them."""
        sysp = "Line 1\nLine 2\nLine 3"
        args = "Step A\nStep B"
        result = CodexHarness().build_prompt("foo", args, system_prompt=sysp)
        assert result == "Line 1\nLine 2\nLine 3\n\nStep A\nStep B"

    def test_skill_name_is_ignored(self) -> None:
        """DEC-011: Codex has no slash-command analog — ``skill_name``
        does not appear anywhere in the rendered prompt regardless of
        value."""
        sysp = "system"
        args = "args"
        result_a = CodexHarness().build_prompt("alpha", args, system_prompt=sysp)
        result_b = CodexHarness().build_prompt(
            "beta-different", args, system_prompt=sysp
        )
        # Same output regardless of skill_name.
        assert result_a == result_b
        # And no skill_name substring appears in either.
        assert "alpha" not in result_a
        assert "beta-different" not in result_b

    def test_empty_args_with_system_prompt(self) -> None:
        """Empty ``args`` with truthy ``system_prompt`` → keeps the
        ``\\n\\n`` separator (i.e. ``f"{system_prompt}\\n\\n"``).
        This is the documented behavior per DEC-011's ``f"{sysp}\\n\\n{args}"``
        format string applied with ``args == ""``."""
        result = CodexHarness().build_prompt("foo", "", system_prompt="hello")
        assert result == "hello\n\n"


# ---------------------------------------------------------------------------
# CodexHarness.invoke happy path (DEC-001/003/005/008/009/010/014/015/016/017,
# US-003)
# ---------------------------------------------------------------------------


class TestInvokeCodexExec:
    """Happy-path tests for :meth:`CodexHarness.invoke`.

    Mirrors :class:`tests.test_runner.TestInvokeViaClaudeCode` shape but
    exercises Codex-specific behavior: argv assembly per DEC-001,
    process-group flag per DEC-014, ``--output-last-message`` tempfile
    per DEC-005, ``harness_metadata`` keys per DEC-008, the auth-source
    + sandbox stderr lines per DEC-009/017, and the harness=codex tag
    on every appended event per DEC-010.

    Error paths (timeout, codex-bin missing, ``turn.failed``, malformed
    lines, envelope cap) land in US-004 and are filtered out here via
    ``-k 'TestInvokeCodexExec and not error and not timeout'``.
    """

    def _patch_popen(self, monkeypatch, fake):
        """Helper: patch ``subprocess.Popen`` in the codex module."""
        import clauditor._harnesses._codex as _codex_mod

        calls = []

        def _fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            return fake

        monkeypatch.setattr(_codex_mod.subprocess, "Popen", _fake_popen)
        return calls

    def test_argv_assembled_per_dec_001(self, monkeypatch, tmp_path):
        """DEC-001: argv is ``[codex_bin, "exec", "--json",
        "--output-last-message", <path>, "--skip-git-repo-check",
        "-s", "workspace-write", "-m", model, "-"]``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        harness = CodexHarness(model="gpt-5-codex")
        harness.invoke("prompt body", cwd=tmp_path, env=None, timeout=30)
        assert len(calls) == 1
        argv = calls[0][0][0]
        assert argv[0] == "codex"
        assert argv[1] == "exec"
        assert argv[2] == "--json"
        assert argv[3] == "--output-last-message"
        # argv[4] is the tempfile path — checked separately below.
        assert isinstance(argv[4], str)
        assert argv[5] == "--skip-git-repo-check"
        assert argv[6] == "-s"
        assert argv[7] == "workspace-write"
        assert argv[8] == "-m"
        assert argv[9] == "gpt-5-codex"
        assert argv[10] == "-"

    def test_codex_bin_override(self, monkeypatch, tmp_path):
        """``codex_bin`` constructor kwarg is honored in argv[0]."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        harness = CodexHarness(codex_bin="/opt/codex/bin/codex", model="gpt-5")
        harness.invoke("p", cwd=tmp_path, env=None, timeout=30)
        argv = calls[0][0][0]
        assert argv[0] == "/opt/codex/bin/codex"

    def test_per_call_model_override(self, monkeypatch, tmp_path):
        """``invoke(model=...)`` overrides ``self.model``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        harness = CodexHarness(model="gpt-5-codex")
        harness.invoke("p", cwd=tmp_path, env=None, timeout=30, model="o4-mini")
        argv = calls[0][0][0]
        assert "o4-mini" in argv
        assert "gpt-5-codex" not in argv

    def test_default_model_when_none(self, monkeypatch, tmp_path):
        """When neither ``self.model`` nor the per-call override are set,
        the harness falls back to a documented default model id."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        harness = CodexHarness()  # model=None
        harness.invoke("p", cwd=tmp_path, env=None, timeout=30)
        argv = calls[0][0][0]
        # ``-m`` flag is present and the value is a non-empty str.
        i = argv.index("-m")
        assert isinstance(argv[i + 1], str)
        assert argv[i + 1] != ""

    def test_prompt_written_to_stdin_then_closed(self, monkeypatch, tmp_path):
        """The prompt is written verbatim to ``proc.stdin`` and stdin
        is closed before the read loop starts (codex reads-from-stdin
        when invoked with the trailing ``-`` argv)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        CodexHarness().invoke("hello world", cwd=tmp_path, env=None, timeout=30)
        # StringIO.getvalue() returns the entire written buffer regardless
        # of close state — verify the prompt landed there.
        assert fake.stdin.getvalue() == "hello world"
        # And that stdin was closed.
        assert fake.stdin.closed

    def test_cwd_forwarded_to_popen(self, monkeypatch, tmp_path):
        """``cwd`` is forwarded as a string to ``Popen``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert calls[0][1]["cwd"] == str(tmp_path)

    def test_env_forwarded_verbatim_to_popen(self, monkeypatch, tmp_path):
        """``env`` dict is forwarded verbatim (no auth-stripping at this
        layer; the caller does that)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        env = {"CODEX_API_KEY": "kept-by-caller", "PATH": "/usr/bin"}
        CodexHarness().invoke("p", cwd=tmp_path, env=env, timeout=30)
        assert calls[0][1]["env"] == env

    def test_posix_uses_start_new_session(self, monkeypatch, tmp_path):
        """DEC-014: on POSIX, ``start_new_session=True`` is passed so
        the Codex subprocess gets its own process group for clean
        teardown if subprocesses (e.g. ``command_execution``) orphan."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        # Force POSIX semantics regardless of the host.
        import clauditor._harnesses._codex as _codex_mod

        monkeypatch.setattr(_codex_mod.os, "name", "posix")
        CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert calls[0][1].get("start_new_session") is True

    def test_windows_skips_start_new_session(self, monkeypatch, tmp_path):
        """DEC-014: on Windows, ``start_new_session`` is NOT passed
        (no equivalent — Windows uses CREATE_NEW_PROCESS_GROUP, but
        Codex CLI on Windows is a tier-2 path; we fall back to single-
        pid kill per DEC-014's Windows clause)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        calls = self._patch_popen(monkeypatch, fake)
        import clauditor._harnesses._codex as _codex_mod

        monkeypatch.setattr(_codex_mod.os, "name", "nt")
        CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        # ``start_new_session`` is either absent or False on Windows.
        assert not calls[0][1].get("start_new_session")

    def test_thread_started_populates_thread_id(self, monkeypatch, tmp_path):
        """DEC-008: ``thread.started.thread_id`` lands in
        ``harness_metadata["thread_id"]``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi", thread_id="t-abc-123")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.harness_metadata["thread_id"] == "t-abc-123"

    def test_turn_completed_populates_token_metadata(self, monkeypatch, tmp_path):
        """DEC-008: ``turn.completed.usage`` populates
        :attr:`InvokeResult.input_tokens`,
        :attr:`InvokeResult.output_tokens`,
        ``harness_metadata["cached_input_tokens"]``, and
        ``harness_metadata["reasoning_output_tokens"]``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream(
            "answer",
            input_tokens=123,
            output_tokens=45,
            cached_input_tokens=12,
            reasoning_output_tokens=7,
        )
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.input_tokens == 123
        assert result.output_tokens == 45
        assert result.harness_metadata["cached_input_tokens"] == 12
        assert result.harness_metadata["reasoning_output_tokens"] == 7

    def test_agent_message_text_concatenated_into_output(
        self, monkeypatch, tmp_path
    ):
        """``item.completed[agent_message]`` text events are joined
        (newline) and surfaced on :attr:`InvokeResult.output`."""
        from clauditor._harnesses._codex import CodexHarness

        extra = [make_fake_codex_agent_message_item("second", item_id="agent_2")]
        fake = make_fake_codex_stream("first", extra_items=extra)
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.output == "first\nsecond"

    def test_reasoning_in_stream_events_not_in_output(
        self, monkeypatch, tmp_path
    ):
        """``item.completed[reasoning]`` lands in ``stream_events`` but
        is NOT concatenated into ``output`` (so reasoning text never
        shows up as if it were the answer)."""
        from clauditor._harnesses._codex import CodexHarness

        extra = [make_fake_codex_reasoning_item("internal scratchpad")]
        fake = make_fake_codex_stream("answer", extra_items=extra)
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.output == "answer"
        # The reasoning item is recorded on stream_events.
        reasoning_events = [
            e
            for e in result.stream_events
            if isinstance(e, dict)
            and e.get("type") == "item.completed"
            and isinstance(e.get("item"), dict)
            and e["item"].get("type") == "reasoning"
        ]
        assert len(reasoning_events) == 1
        assert reasoning_events[0]["item"]["text"] == "internal scratchpad"

    def test_every_appended_event_tagged_harness_codex(
        self, monkeypatch, tmp_path
    ):
        """DEC-010: every event in ``stream_events`` carries the
        top-level ``"harness": "codex"`` discriminator."""
        from clauditor._harnesses._codex import CodexHarness

        extra = [
            make_fake_codex_reasoning_item("r"),
            make_fake_codex_command_execution_item(
                command="echo hi", aggregated_output="hi"
            ),
            make_fake_codex_file_change_item(),
            make_fake_codex_mcp_tool_call_item(),
            make_fake_codex_web_search_item(),
            make_fake_codex_todo_list_item(),
        ]
        fake = make_fake_codex_stream("done", extra_items=extra)
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert len(result.stream_events) > 0
        for event in result.stream_events:
            assert event.get("harness") == "codex", event

    def test_command_execution_aggregated_output_softcap(
        self, monkeypatch, tmp_path
    ):
        """DEC-015: ``command_execution.aggregated_output`` over 64 KB
        is truncated with a ``... (truncated)`` suffix and emits one
        warning per invoke."""
        from clauditor._harnesses._codex import (
            _CODEX_COMMAND_OUTPUT_MAX_CHARS,
            CodexHarness,
        )

        big = "X" * (_CODEX_COMMAND_OUTPUT_MAX_CHARS + 5000)
        extra = [
            make_fake_codex_command_execution_item(aggregated_output=big),
        ]
        fake = make_fake_codex_stream("done", extra_items=extra)
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        # The truncated event surfaces in stream_events with the cap.
        cmd_events = [
            e
            for e in result.stream_events
            if isinstance(e, dict)
            and e.get("type") == "item.completed"
            and isinstance(e.get("item"), dict)
            and e["item"].get("type") == "command_execution"
        ]
        assert len(cmd_events) == 1
        out = cmd_events[0]["item"]["aggregated_output"]
        assert out.endswith("... (truncated)")
        assert len(out) == _CODEX_COMMAND_OUTPUT_MAX_CHARS + len("... (truncated)")
        # Exactly one warning emitted (avoid log flooding).
        truncation_warnings = [
            w for w in result.warnings if "command_execution aggregated_output" in w
        ]
        assert len(truncation_warnings) == 1

    def test_last_message_path_recorded_in_metadata(
        self, monkeypatch, tmp_path
    ):
        """DEC-005/008/016: the ``--output-last-message`` tempfile path
        is captured into ``harness_metadata["last_message_path"]``
        BEFORE deletion (so #154's consumer has a string label)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert "last_message_path" in result.harness_metadata
        assert isinstance(result.harness_metadata["last_message_path"], str)
        assert result.harness_metadata["last_message_path"] != ""
        # The path's basename is "last_message.txt" per DEC-016.
        assert result.harness_metadata["last_message_path"].endswith(
            "last_message.txt"
        )

    def test_temporary_directory_deleted_on_success(
        self, monkeypatch, tmp_path
    ):
        """DEC-016: the per-invocation TemporaryDirectory is cleaned up
        on success (the recorded ``last_message_path`` no longer exists
        after invoke returns)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        last_path = result.harness_metadata["last_message_path"]
        assert not os.path.exists(last_path)

    def test_sandbox_mode_in_metadata(self, monkeypatch, tmp_path):
        """DEC-008: ``harness_metadata["sandbox_mode"]`` is the literal
        string ``"workspace-write"`` (DEC-001)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.harness_metadata["sandbox_mode"] == "workspace-write"

    def test_auth_source_recorded_when_codex_api_key_set(
        self, monkeypatch, tmp_path
    ):
        """DEC-017: ``CODEX_API_KEY`` set → ``auth_source="CODEX_API_KEY"``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        env = {"CODEX_API_KEY": "sk-codex", "PATH": "/usr/bin"}
        result = CodexHarness().invoke("p", cwd=tmp_path, env=env, timeout=30)
        assert result.harness_metadata["auth_source"] == "CODEX_API_KEY"

    def test_auth_source_recorded_when_openai_api_key_set(
        self, monkeypatch, tmp_path
    ):
        """DEC-017: ``OPENAI_API_KEY`` set (no ``CODEX_API_KEY``) →
        ``auth_source="OPENAI_API_KEY"``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        env = {"OPENAI_API_KEY": "sk-openai", "PATH": "/usr/bin"}
        result = CodexHarness().invoke("p", cwd=tmp_path, env=env, timeout=30)
        assert result.harness_metadata["auth_source"] == "OPENAI_API_KEY"

    def test_auth_source_unknown_when_no_keys(self, monkeypatch, tmp_path):
        """DEC-017: no ``CODEX_API_KEY``, no ``OPENAI_API_KEY``, and no
        ``$CODEX_HOME/auth.json`` → ``auth_source="unknown"``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        # Empty env + point CODEX_HOME at a dir with no auth.json.
        env = {"CODEX_HOME": str(tmp_path / "codex_home_empty"), "PATH": "/usr/bin"}
        result = CodexHarness().invoke("p", cwd=tmp_path, env=env, timeout=30)
        assert result.harness_metadata["auth_source"] == "unknown"

    def test_auth_source_cached_when_auth_json_exists(
        self, monkeypatch, tmp_path
    ):
        """DEC-017: ``$CODEX_HOME/auth.json`` exists (and no env keys)
        → ``auth_source="cached"``."""
        from clauditor._harnesses._codex import CodexHarness

        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text("{}")
        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        env = {"CODEX_HOME": str(codex_home), "PATH": "/usr/bin"}
        result = CodexHarness().invoke("p", cwd=tmp_path, env=env, timeout=30)
        assert result.harness_metadata["auth_source"] == "cached"

    def test_subject_sanitization(self, monkeypatch, tmp_path, capsys):
        """DEC-009: subject is sanitized — CR/LF → space, strip, 200-char
        cap. The sanitized form lands in the ``codex sandbox=`` /
        ``codex auth=`` stderr lines."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        env = {"CODEX_API_KEY": "sk-codex", "PATH": "/usr/bin"}
        # Embedded \r\n in subject — must be replaced with space.
        evil_subject = "  L2 extraction\r\nhostile-line\n  "
        CodexHarness().invoke(
            "p", cwd=tmp_path, env=env, timeout=30, subject=evil_subject
        )
        err = capsys.readouterr().err
        # Sanitized subject appears (no embedded CR/LF, trimmed).
        # Mirrors Claude's pattern of replacing ``\r`` and ``\n``
        # individually — a ``\r\n`` becomes two spaces, not one.
        assert "L2 extraction  hostile-line" in err
        # No raw newline-in-subject leakage.
        assert "L2 extraction\n" not in err
        assert "L2 extraction\r" not in err

    def test_subject_capped_at_200_chars(self, monkeypatch, tmp_path, capsys):
        """DEC-009: subject longer than 200 chars is truncated to 200."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        long_subject = "a" * 500
        env = {"CODEX_API_KEY": "sk-codex"}
        CodexHarness().invoke(
            "p", cwd=tmp_path, env=env, timeout=30, subject=long_subject
        )
        err = capsys.readouterr().err
        # The "(aaaa...)"-shaped suffix is at most 200 chars of 'a' + parens.
        # Find the parenthesized suffix on the auth line.
        for line in err.splitlines():
            if "auth=" in line and "(" in line:
                # Extract substring inside parens.
                lparen = line.index("(")
                rparen = line.rindex(")")
                inside = line[lparen + 1 : rparen]
                # Sanitized + capped at 200 chars.
                assert len(inside) <= 200
                assert inside == "a" * 200
                break
        else:  # pragma: no cover
            raise AssertionError(f"no auth= line found: {err!r}")

    def test_stderr_lines_emitted_with_subject(self, monkeypatch, tmp_path, capsys):
        """DEC-009/017: ``codex sandbox=workspace-write`` and
        ``codex auth=<source>`` lines emitted to stderr with subject
        suffix when subject is non-empty."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)
        env = {"CODEX_API_KEY": "sk-codex"}
        CodexHarness().invoke(
            "p", cwd=tmp_path, env=env, timeout=30, subject="L3 grading"
        )
        err = capsys.readouterr().err
        assert "codex sandbox=workspace-write (L3 grading)" in err
        assert "codex auth=CODEX_API_KEY (L3 grading)" in err

    def test_duration_seconds_populated(self, monkeypatch, tmp_path):
        """``duration_seconds`` is populated on every exit path
        (mirrors Claude's pattern)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)

        # Pin _monotonic so duration arithmetic is deterministic.
        import clauditor._harnesses._codex as _codex_mod

        seq = iter([10.0, 12.5])
        monkeypatch.setattr(_codex_mod, "_monotonic", lambda: next(seq))
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.duration_seconds == 2.5

    def test_exit_code_zero_on_clean_run(self, monkeypatch, tmp_path):
        """A clean run yields ``exit_code=0`` and ``error=None``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream("hi", returncode=0)
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.exit_code == 0
        assert result.error is None
        assert result.error_category is None


class TestInvokeCodexExecErrorPaths:
    """Error-path tests for :meth:`CodexHarness.invoke` (US-004).

    Covers ``turn.failed`` / top-level ``error`` classification (DEC-007),
    ``FileNotFoundError`` on Popen, watchdog timeout + POSIX killpg
    escalation (DEC-014), malformed JSON line skip+warn, stderr
    redact + cap (DEC-013), envelope cap enforcement (DEC-015), and
    the two advisory detectors with the corresponding warning prefixes
    (DEC-018). Tempfile cleanup is also asserted on every exit path.
    """

    def _patch_popen(self, monkeypatch, fake):
        import clauditor._harnesses._codex as _codex_mod

        calls = []

        def _fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            return fake

        monkeypatch.setattr(_codex_mod.subprocess, "Popen", _fake_popen)
        return calls

    # ---- DEC-007 classification of turn.failed and top-level error ----

    def test_turn_failed_rate_limit_classifies_as_rate_limit(
        self, monkeypatch, tmp_path
    ):
        """DEC-007: ``turn.failed.error.message`` containing ``"rate
        limit"`` classifies as ``error_category="rate_limit"``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_turn_failed(
            error_message="rate limit exceeded; retry after 60s"
        )
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.error_category == "rate_limit"
        assert result.error is not None
        assert "rate limit" in result.error

    def test_turn_failed_401_classifies_as_auth(self, monkeypatch, tmp_path):
        """DEC-007: ``"401 unauthorized"`` classifies as ``"auth"``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_turn_failed(error_message="401 unauthorized")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.error_category == "auth"
        assert "401" in result.error

    def test_turn_failed_generic_classifies_as_api(self, monkeypatch, tmp_path):
        """DEC-007: generic message → ``"api"`` (catchall)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_turn_failed(
            error_message="internal server error"
        )
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.error_category == "api"
        assert "internal server error" in result.error

    def test_top_level_error_classifies_via_message(self, monkeypatch, tmp_path):
        """DEC-007: top-level ``error`` event also routes through
        :func:`_classify_codex_failure`. ``error.message`` populates
        :attr:`InvokeResult.error`."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_top_level_error(error_message="quota exceeded")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.error_category == "rate_limit"  # quota → rate_limit
        assert "quota" in result.error

    def test_first_error_wins_over_subsequent(self, monkeypatch, tmp_path):
        """When both ``turn.failed`` and a later ``error`` event land
        in the stream, the FIRST classification wins (defensive — in
        practice Codex emits at most one)."""
        # Build a stream with turn.failed (rate_limit) followed by a
        # top-level error (would otherwise classify as auth).
        import json

        from clauditor._harnesses._codex import CodexHarness

        lines = [
            json.dumps({"type": "thread.started", "thread_id": "t-1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": "rate limit exceeded"},
                }
            ),
            json.dumps({"type": "error", "message": "401 unauthorized"}),
        ]
        fake = _FakeCodexPopen(lines, returncode=1)
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.error_category == "rate_limit"

    # ---- Malformed JSON line skip+warn ----

    def test_malformed_json_line_skipped_with_warning(
        self, monkeypatch, tmp_path
    ):
        """Per ``stream-json-schema.md``, a malformed JSON line is
        skipped, a warning is appended, and parsing continues so the
        run still picks up subsequent valid events (token usage,
        agent_message)."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_malformed_line_in_stream(text="answer")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        # Subsequent valid events still landed.
        assert result.output == "answer"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        # Warning recorded for the malformed line.
        assert any(
            "malformed codex stream-json line" in w for w in result.warnings
        )

    # ---- FileNotFoundError ----

    def test_codex_binary_missing_returns_minus_one_error(
        self, monkeypatch, tmp_path
    ):
        """``FileNotFoundError`` on Popen surfaces as
        ``InvokeResult(exit_code=-1, error="Codex CLI not found: ...")``."""
        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        def _raises(*args, **kwargs):
            raise FileNotFoundError("[Errno 2] No such file: 'codex'")

        monkeypatch.setattr(_codex_mod.subprocess, "Popen", _raises)
        result = CodexHarness(codex_bin="/nonexistent/codex").invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        assert result.exit_code == -1
        assert result.error is not None
        assert "Codex CLI not found" in result.error
        assert "/nonexistent/codex" in result.error
        # Local failure — no stream-level error category.
        assert result.error_category is None

    # ---- Watchdog timeout + POSIX killpg path ----

    def test_timeout_invokes_killpg_on_posix(self, monkeypatch, tmp_path):
        """DEC-014: on POSIX, the watchdog escalates to
        ``os.killpg(os.getpgid(pid), SIGTERM)``. We force timeout via a
        zero-second timer and verify the killpg path was taken."""
        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        # Force POSIX semantics regardless of host.
        monkeypatch.setattr(_codex_mod.os, "name", "posix")

        # Build a fake whose stdout never delivers a final "done" event;
        # the watchdog timer drives the kill path. We use an empty
        # stdout (read loop exits immediately) — the watchdog runs
        # synchronously when invoked from the main thread via the
        # lock-side path, but here we trigger it manually by calling
        # ``_kill_proc`` directly through the timeout simulation: just
        # set a 0.0-second timeout so the timer fires before
        # ``proc.wait()`` returns.
        fake = make_fake_codex_stream("hi")
        # Override poll() to mark "alive" until kill_called fires, so
        # the watchdog path triggers killpg.
        original_poll = fake.poll
        fake._fake_alive = True

        def _poll() -> int | None:
            if fake._fake_alive:
                return None
            return original_poll()

        fake.poll = _poll  # type: ignore[method-assign]

        killpg_calls: list[tuple[int, int]] = []
        getpgid_calls: list[int] = []

        def _fake_getpgid(pid: int) -> int:
            getpgid_calls.append(pid)
            return pid + 1000  # arbitrary "process group id"

        def _fake_killpg(pgid: int, sig: int) -> None:
            killpg_calls.append((pgid, sig))
            fake._fake_alive = False  # simulate the kill landing
            # Mark the fake popen as killed so subsequent poll/wait
            # returns the right code.
            fake._killed = True
            fake.returncode = -9

        monkeypatch.setattr(_codex_mod.os, "getpgid", _fake_getpgid)
        monkeypatch.setattr(_codex_mod.os, "killpg", _fake_killpg)
        self._patch_popen(monkeypatch, fake)

        # Patch ``threading.Timer`` to fire synchronously so the test is
        # deterministic (no flake from a real timer race).
        class _FakeTimer:
            def __init__(self, interval, function):
                self._function = function

            def start(self) -> None:
                # Fire immediately — emulates the timeout having
                # already elapsed by the time we start reading stdout.
                self._function()

            def cancel(self) -> None:
                pass

            @property
            def daemon(self) -> bool:
                return True

            @daemon.setter
            def daemon(self, value: bool) -> None:
                pass

        monkeypatch.setattr(_codex_mod.threading, "Timer", _FakeTimer)

        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        # killpg fired with SIGTERM; getpgid was consulted.
        assert len(getpgid_calls) >= 1
        assert any(sig == _codex_mod.signal.SIGTERM for _, sig in killpg_calls)
        # The result reflects the timeout.
        assert result.error == "timeout"
        assert result.error_category == "timeout"
        assert result.exit_code == -1

    def test_timeout_on_windows_uses_terminate(self, monkeypatch, tmp_path):
        """DEC-014: on Windows (``os.name == "nt"``), kill path uses
        ``proc.terminate()`` / ``proc.kill()``, NOT ``killpg``."""
        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        monkeypatch.setattr(_codex_mod.os, "name", "nt")

        fake = make_fake_codex_stream("hi")
        fake._fake_alive = True
        original_poll = fake.poll

        def _poll() -> int | None:
            if fake._fake_alive:
                return None
            return original_poll()

        fake.poll = _poll  # type: ignore[method-assign]

        # Wrap terminate() so we can confirm it was called via the
        # kill helper path (NOT via the cleanup-finally branch).
        original_terminate = fake.terminate
        terminate_calls: list[int] = []

        def _terminate() -> None:
            terminate_calls.append(1)
            fake._fake_alive = False
            original_terminate()

        fake.terminate = _terminate  # type: ignore[method-assign]

        # Forbid killpg — should never be called on Windows.
        def _killpg_forbidden(*args, **kwargs):
            raise AssertionError(
                "killpg must not be called on Windows path"
            )

        monkeypatch.setattr(_codex_mod.os, "killpg", _killpg_forbidden)
        self._patch_popen(monkeypatch, fake)

        # Synchronous timer for determinism.
        class _FakeTimer:
            def __init__(self, interval, function):
                self._function = function

            def start(self) -> None:
                self._function()

            def cancel(self) -> None:
                pass

            @property
            def daemon(self) -> bool:
                return True

            @daemon.setter
            def daemon(self, value: bool) -> None:
                pass

        monkeypatch.setattr(_codex_mod.threading, "Timer", _FakeTimer)

        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        assert len(terminate_calls) >= 1
        assert result.error == "timeout"
        assert result.error_category == "timeout"

    # ---- DEC-013 stderr filter + cap ----

    def test_stderr_redacted_for_auth_leak(self, monkeypatch, tmp_path):
        """DEC-013: stderr lines containing auth-leak patterns are
        replaced by the redaction sentinel before landing on warnings.
        Content (the actual key value) must NOT appear in warnings."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream(
            "hi",
            stderr_lines=[
                "starting up",
                "OPENAI_API_KEY=sk-leaky-secret-12345",
                "shutting down",
            ],
        )
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        all_warnings = "\n".join(result.warnings)
        # Redaction sentinel landed.
        assert "<line redacted: matched auth-leak pattern>" in all_warnings
        # Secret content did NOT.
        assert "sk-leaky-secret-12345" not in all_warnings
        # Non-leaking lines pass through.
        assert "starting up" in all_warnings
        assert "shutting down" in all_warnings

    def test_stderr_capped_at_8kb(self, monkeypatch, tmp_path):
        """DEC-013: captured stderr text > 8 KB is truncated with
        ``"... (truncated)"`` suffix."""
        from clauditor._harnesses._codex import (
            _CODEX_STDERR_MAX_CHARS,
            CodexHarness,
        )

        # Build a single line longer than the cap.
        big_line = "x" * (_CODEX_STDERR_MAX_CHARS + 4096)
        fake = make_fake_codex_stream("hi", stderr_lines=[big_line])
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        # The stderr warning is one of the entries.
        stderr_warnings = [
            w for w in result.warnings if w.endswith("... (truncated)")
        ]
        assert len(stderr_warnings) >= 1
        # The captured text fits the cap.
        assert (
            len(stderr_warnings[0])
            == _CODEX_STDERR_MAX_CHARS + len("... (truncated)")
        )

    # ---- DEC-015 envelope cap enforcement ----

    def test_envelope_cap_truncates_stream_events_keeps_token_usage(
        self, monkeypatch, tmp_path
    ):
        """DEC-015: once ``stream_events_size`` crosses
        :data:`_CODEX_STREAM_EVENTS_MAX_SIZE`, subsequent events are
        NOT appended but parsing continues so the final
        ``turn.completed`` token usage still lands. The
        ``stream_events_truncated`` metadata flag is set; a warning
        with the ``stream-events-truncated:`` prefix lands."""
        import json

        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        # Patch the cap to a tiny value so we can blow through it
        # cheaply with normal-sized fixtures.
        monkeypatch.setattr(_codex_mod, "_CODEX_STREAM_EVENTS_MAX_SIZE", 200)

        # Build many small events that overflow the patched cap.
        # The final turn.completed must land for token assertions.
        msgs: list[dict] = [
            {"type": "thread.started", "thread_id": "t-1"},
            {"type": "turn.started"},
        ]
        for i in range(20):
            msgs.append(
                make_fake_codex_agent_message_item(
                    f"chunk-{i:03d}", item_id=f"agent_{i}"
                )
            )
        msgs.append(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 999, "output_tokens": 111},
            }
        )
        fake = _FakeCodexPopen([json.dumps(m) for m in msgs])
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)

        # The truncated flag landed.
        assert result.harness_metadata.get("stream_events_truncated") is True
        # Token usage from the final turn.completed still landed.
        assert result.input_tokens == 999
        assert result.output_tokens == 111
        # Warning with the documented prefix is present.
        assert any(
            "stream-events-truncated:" in w for w in result.warnings
        )

    # ---- DEC-018 advisory detectors ----

    def test_dropped_events_advisory_warning_with_count(
        self, monkeypatch, tmp_path
    ):
        """DEC-018: a ``Lagged`` synthetic event in the stream produces
        a ``dropped-events:``-prefixed warning AND populates
        ``harness_metadata["dropped_events_count"]``. This is advisory
        — ``error_category`` stays ``None``."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_with_lagged_event(
            text="answer", dropped_count=42
        )
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)

        assert result.harness_metadata.get("dropped_events_count") == 42
        assert any(
            w.startswith("dropped-events:") for w in result.warnings
        )
        # Advisory: NOT a failure category.
        assert result.error_category is None
        assert result.error is None

    def test_truncated_output_falls_back_to_tempfile(
        self, monkeypatch, tmp_path
    ):
        """DEC-018 + DEC-005: when the stream produced no
        ``agent_message`` items and the
        ``--output-last-message`` tempfile contains text, the harness
        falls back to the tempfile content for ``output`` and emits a
        ``last-message-empty:``-prefixed advisory warning."""
        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_no_agent_message()
        self._patch_popen(monkeypatch, fake)

        # Patch ``open`` inside the codex module so the fallback read
        # of ``last_message_path`` returns a known string regardless
        # of whether the real path was created.
        canned_text = "answer-from-tempfile-fallback"

        # Patch os.path.isfile + open to simulate the tempfile being
        # populated by Codex (which our fake popen does not do).
        original_isfile = _codex_mod.os.path.isfile

        def _isfile(p):
            if "last_message.txt" in str(p):
                return True
            return original_isfile(p)

        monkeypatch.setattr(_codex_mod.os.path, "isfile", _isfile)

        import builtins

        original_open = builtins.open

        def _fake_open(path, *args, **kwargs):
            if "last_message.txt" in str(path):
                from io import StringIO

                return StringIO(canned_text)
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _fake_open)

        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        assert result.output == canned_text
        assert any(
            w.startswith("last-message-empty:") for w in result.warnings
        )
        # Advisory: NOT a failure category.
        assert result.error_category is None

    # ---- Cleanup invariants ----

    def test_temp_directory_deleted_on_turn_failed(
        self, monkeypatch, tmp_path
    ):
        """The per-invocation TemporaryDirectory is cleaned up even on
        the ``turn.failed`` path."""
        import os as _os

        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_turn_failed(error_message="api error")
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)
        last_path = result.harness_metadata.get("last_message_path")
        # Path was recorded BEFORE deletion per DEC-016.
        assert isinstance(last_path, str)
        # The tmpdir is gone — neither the file nor its parent dir
        # should still exist.
        assert not _os.path.exists(last_path)
        assert not _os.path.exists(_os.path.dirname(last_path))

    def test_temp_directory_deleted_on_file_not_found(
        self, monkeypatch, tmp_path
    ):
        """Tempfile cleanup runs even when ``Popen`` raises
        ``FileNotFoundError`` (the early-return path through the
        ``TemporaryDirectory`` context)."""
        import os as _os
        import tempfile as _tempfile

        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        # Snapshot the tempdir before the call so we can detect leaks
        # of ``clauditor_codex_*`` dirs (the prefix the harness uses).
        tempdir_root = _tempfile.gettempdir()
        before = {
            d
            for d in _os.listdir(tempdir_root)
            if d.startswith("clauditor_codex_")
        }

        def _raises(*args, **kwargs):
            raise FileNotFoundError("not on PATH")

        monkeypatch.setattr(_codex_mod.subprocess, "Popen", _raises)
        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        assert result.exit_code == -1
        # Verify no clauditor_codex_* dir leaked.
        after = {
            d
            for d in _os.listdir(tempdir_root)
            if d.startswith("clauditor_codex_")
        }
        leaked = sorted(after - before)
        assert leaked == [], (
            f"clauditor_codex_* dirs leaked into {tempdir_root}: {leaked}"
        )

    def test_temp_directory_deleted_on_timeout(
        self, monkeypatch, tmp_path
    ):
        """The per-invocation TemporaryDirectory is cleaned up on the
        watchdog-timeout path. Mirrors the turn-failed cleanup test, but
        triggers via the synchronous ``_FakeTimer`` pattern from
        ``test_timeout_invokes_killpg_on_posix``."""
        import os as _os

        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        monkeypatch.setattr(_codex_mod.os, "name", "posix")

        fake = make_fake_codex_stream("hi")
        fake._fake_alive = True
        original_poll = fake.poll

        def _poll() -> int | None:
            if fake._fake_alive:
                return None
            return original_poll()

        fake.poll = _poll  # type: ignore[method-assign]

        def _fake_getpgid(pid: int) -> int:
            return pid + 1000

        def _fake_killpg(pgid: int, sig: int) -> None:
            fake._fake_alive = False
            fake._killed = True
            fake.returncode = -9

        monkeypatch.setattr(_codex_mod.os, "getpgid", _fake_getpgid)
        monkeypatch.setattr(_codex_mod.os, "killpg", _fake_killpg)
        self._patch_popen(monkeypatch, fake)

        class _FakeTimer:
            def __init__(self, interval, function):
                self._function = function

            def start(self) -> None:
                self._function()

            def cancel(self) -> None:
                pass

            @property
            def daemon(self) -> bool:
                return True

            @daemon.setter
            def daemon(self, value: bool) -> None:
                pass

        monkeypatch.setattr(_codex_mod.threading, "Timer", _FakeTimer)

        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        # Timeout fired and was recorded.
        assert result.error == "timeout"
        last_path = result.harness_metadata.get("last_message_path")
        assert isinstance(last_path, str)
        # Tempdir cleaned up by ``with TemporaryDirectory():`` context exit.
        assert not _os.path.exists(last_path)
        assert not _os.path.exists(_os.path.dirname(last_path))

    def test_temp_directory_deleted_on_parse_exception(
        self, monkeypatch, tmp_path
    ):
        """When an unexpected exception (NOT ``json.JSONDecodeError``)
        escapes the parse loop, the per-invocation TemporaryDirectory
        must still be cleaned up by the ``with TemporaryDirectory():``
        context manager. Mirrors ClaudeCodeHarness's exception-during-
        parse contract: only ``JSONDecodeError`` is swallowed; any
        other exception propagates while cleanup runs."""
        import os as _os
        import tempfile as _tempfile

        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        tempdir_root = _tempfile.gettempdir()
        before = {
            d
            for d in _os.listdir(tempdir_root)
            if d.startswith("clauditor_codex_")
        }

        original_loads = _codex_mod.json.loads

        def _exploding_loads(*args, **kwargs):
            # First and only call raises a non-JSONDecodeError exception
            # so the parse loop's narrow ``except json.JSONDecodeError``
            # does NOT catch it. The exception propagates out of the
            # parse loop, but the surrounding ``with TemporaryDirectory()``
            # context cleans up the staging dir on the way through.
            raise RuntimeError("simulated parser internal error")

        monkeypatch.setattr(_codex_mod.json, "loads", _exploding_loads)

        fake = make_fake_codex_stream("hi")
        self._patch_popen(monkeypatch, fake)

        # The exception MUST propagate (matches ClaudeCodeHarness.invoke
        # which only catches JSONDecodeError on the inner json.loads).
        import pytest

        with pytest.raises(RuntimeError, match="simulated parser"):
            CodexHarness().invoke("p", cwd=tmp_path, env=None, timeout=30)

        # Restore json.loads so any subsequent test fixtures work.
        monkeypatch.setattr(_codex_mod.json, "loads", original_loads)

        # No clauditor_codex_* dir leaked despite the exception.
        after = {
            d
            for d in _os.listdir(tempdir_root)
            if d.startswith("clauditor_codex_")
        }
        leaked = sorted(after - before)
        assert leaked == [], (
            f"clauditor_codex_* dirs leaked into {tempdir_root}: {leaked}"
        )

    def test_sigkill_after_sigterm_grace_when_codex_survives(
        self, monkeypatch, tmp_path
    ):
        """DEC-014 (POSIX): if the child is still alive after SIGTERM
        + 250 ms grace, the kill helper escalates to SIGKILL. Drive the
        escalation by making ``proc.wait(timeout=0.25)`` raise
        ``TimeoutExpired`` once via the ``_FakeCodexPopen.wait()``
        knob, then verify two killpg calls (SIGTERM, SIGKILL)."""
        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        monkeypatch.setattr(_codex_mod.os, "name", "posix")

        # ``wait_raises_timeout_count=1`` makes the post-SIGTERM
        # ``proc.wait(timeout=0.25)`` raise once, so the kill helper
        # escalates to SIGKILL on the same path.
        fake = make_fake_codex_stream("hi")
        fake._wait_raises_timeout_count = 1
        fake._fake_alive = True
        original_poll = fake.poll

        def _poll() -> int | None:
            if fake._fake_alive:
                return None
            return original_poll()

        fake.poll = _poll  # type: ignore[method-assign]

        killpg_calls: list[tuple[int, int]] = []

        def _fake_getpgid(pid: int) -> int:
            return pid + 1000

        def _fake_killpg(pgid: int, sig: int) -> None:
            killpg_calls.append((pgid, sig))
            # Only mark dead AFTER SIGKILL escalates so the SIGTERM
            # arm sees the wait timeout and falls through to SIGKILL.
            if sig == _codex_mod.signal.SIGKILL:
                fake._fake_alive = False
                fake._killed = True
                fake.returncode = -9

        monkeypatch.setattr(_codex_mod.os, "getpgid", _fake_getpgid)
        monkeypatch.setattr(_codex_mod.os, "killpg", _fake_killpg)
        self._patch_popen(monkeypatch, fake)

        class _FakeTimer:
            def __init__(self, interval, function):
                self._function = function

            def start(self) -> None:
                self._function()

            def cancel(self) -> None:
                pass

            @property
            def daemon(self) -> bool:
                return True

            @daemon.setter
            def daemon(self, value: bool) -> None:
                pass

        monkeypatch.setattr(_codex_mod.threading, "Timer", _FakeTimer)

        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        # Both SIGTERM and SIGKILL fired.
        sigs = [sig for _, sig in killpg_calls]
        assert _codex_mod.signal.SIGTERM in sigs
        assert _codex_mod.signal.SIGKILL in sigs
        # SIGTERM came first.
        assert sigs.index(_codex_mod.signal.SIGTERM) < sigs.index(
            _codex_mod.signal.SIGKILL
        )
        assert result.error == "timeout"

    def test_windows_kill_after_terminate_grace(
        self, monkeypatch, tmp_path
    ):
        """DEC-014 (Windows): if the child survives ``terminate()`` +
        250 ms grace, the kill helper escalates to ``proc.kill()``.
        Same shape as the POSIX SIGKILL escalation test but routed
        through the Windows fallback path."""
        import clauditor._harnesses._codex as _codex_mod
        from clauditor._harnesses._codex import CodexHarness

        monkeypatch.setattr(_codex_mod.os, "name", "nt")

        fake = make_fake_codex_stream("hi")
        fake._wait_raises_timeout_count = 1
        fake._fake_alive = True
        original_poll = fake.poll

        def _poll() -> int | None:
            if fake._fake_alive:
                return None
            return original_poll()

        fake.poll = _poll  # type: ignore[method-assign]

        # Wrap terminate() and kill() to record call order.
        call_order: list[str] = []
        original_terminate = fake.terminate
        original_kill = fake.kill

        def _terminate() -> None:
            call_order.append("terminate")
            original_terminate()
            # Don't actually mark dead — let kill() do it.
            fake._killed = False
            fake.returncode = 0
            fake._fake_alive = True

        def _kill() -> None:
            call_order.append("kill")
            original_kill()
            fake._fake_alive = False

        fake.terminate = _terminate  # type: ignore[method-assign]
        fake.kill = _kill  # type: ignore[method-assign]

        # Forbid killpg on Windows.
        def _killpg_forbidden(*args, **kwargs):
            raise AssertionError(
                "killpg must not be called on Windows path"
            )

        monkeypatch.setattr(_codex_mod.os, "killpg", _killpg_forbidden)
        self._patch_popen(monkeypatch, fake)

        class _FakeTimer:
            def __init__(self, interval, function):
                self._function = function

            def start(self) -> None:
                self._function()

            def cancel(self) -> None:
                pass

            @property
            def daemon(self) -> bool:
                return True

            @daemon.setter
            def daemon(self, value: bool) -> None:
                pass

        monkeypatch.setattr(_codex_mod.threading, "Timer", _FakeTimer)

        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        # Both terminate and kill fired, in order.
        assert "terminate" in call_order
        assert "kill" in call_order
        assert call_order.index("terminate") < call_order.index("kill")
        assert result.error == "timeout"

    def test_codex_deprecation_warning_detected_on_stderr(
        self, monkeypatch, tmp_path
    ):
        """DEC-018: a stderr line containing both ``warning:`` and
        ``deprecated`` produces a single ``codex-deprecation:``-prefixed
        advisory warning. Wires the previously-defined-but-unused
        ``_CODEX_DEPRECATION_WARNING_PREFIX`` constant."""
        from clauditor._harnesses._codex import CodexHarness

        fake = make_fake_codex_stream(
            "hi",
            stderr_lines=[
                "warning: --json flag is deprecated; use --jsonl instead",
                "starting codex",
            ],
        )
        self._patch_popen(monkeypatch, fake)
        result = CodexHarness().invoke(
            "p", cwd=tmp_path, env=None, timeout=30
        )
        # One advisory warning with the documented prefix.
        deprecation_warnings = [
            w for w in result.warnings if w.startswith("codex-deprecation:")
        ]
        assert len(deprecation_warnings) == 1
        assert "deprecated" in deprecation_warnings[0]
        # Advisory: NOT a failure.
        assert result.error_category is None
