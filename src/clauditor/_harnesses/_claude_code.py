"""Claude-Code-specific pure helpers extracted from :mod:`clauditor.runner`.

This module hosts the pure (no-I/O, no-global-state) helpers that
classify and detect harness-specific signals on a ``claude -p``
``--output-format stream-json`` capture. Each helper traces back to a
DEC in ``plans/super/63-runner-error-surfacing.md`` (US-002 / US-003 /
DEC-010) and ``plans/super/97-background-task-noncompletion.md`` and
is the canonical-implementation anchor in
``.claude/rules/pure-compute-vs-io-split.md`` (sixth anchor).

Per US-002 of ``plans/super/148-extract-harness-protocol.md``, these
helpers live alongside the (yet-to-be-introduced) ``ClaudeCodeHarness``
class so harness-specific logic does not pollute the cross-harness
:class:`clauditor.runner.SkillRunner` surface.

The two warning-prefix constants (``_INTERACTIVE_HANG_WARNING_PREFIX``
and ``_BACKGROUND_TASK_WARNING_PREFIX``) intentionally remain in
:mod:`clauditor.runner` because :attr:`SkillResult.succeeded_cleanly`
inspects them at the data-class level. They are imported back here so
the warning *body* strings (which start with those prefixes) stay in
exact lockstep with the prefix definitions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar

from clauditor.runner import (
    _BACKGROUND_TASK_WARNING_PREFIX,
    _INTERACTIVE_HANG_WARNING_PREFIX,
    _SYNC_TASKS_ENV_VAR,
    InvokeResult,
)

# Module-level alias for ``time.monotonic`` per
# ``.claude/rules/monotonic-time-indirection.md``. Tests patch this alias
# rather than ``time.monotonic`` directly so the asyncio event loop's own
# scheduler ticks are not clobbered.
_monotonic = time.monotonic

# Env vars stripped by :func:`env_without_api_key`. Both are
# documented Anthropic SDK env-auth paths (DEC-007 of
# ``plans/super/64-runner-auth-timeout.md``). Non-auth Anthropic env
# vars such as ``ANTHROPIC_BASE_URL`` are intentionally preserved
# (DEC-016).
_API_KEY_ENV_VARS = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})


def env_without_api_key(
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a new env dict with both auth env vars removed.

    Pure, non-mutating helper per
    ``.claude/rules/non-mutating-scrub.md``. When ``base_env`` is
    ``None``, reads from ``os.environ``. Always returns a new dict
    (never mutates the input). Strips ``ANTHROPIC_API_KEY`` and
    ``ANTHROPIC_AUTH_TOKEN``; preserves every other key (including
    ``ANTHROPIC_BASE_URL``).
    """
    source = base_env if base_env is not None else os.environ
    return {k: v for k, v in source.items() if k not in _API_KEY_ENV_VARS}


# Soft cap applied to stream-json ``result`` text surfaced on
# ``SkillResult.error``. Per DEC-008 of
# ``plans/super/63-runner-error-surfacing.md`` — bound the memory
# cost of a pathological multi-MB error payload without truncating
# realistic 100-300 byte provider-error strings.
_RESULT_TEXT_MAX_CHARS = 4096


# Case-insensitive match on the final assistant text for phrases that
# indicate the skill was still expecting background work to finish when
# the subprocess ended. Word-boundary anchored so "in progress bar" and
# similar incidental substrings do not match.
_BACKGROUND_TASK_WAITING_RE = re.compile(
    r"\b(waiting on|still waiting|continuing|in progress|in the background)\b",
    re.IGNORECASE,
)


# DEC-005 / DEC-010: interactive-hang heuristic warning body. The
# leading ``"interactive-hang:"`` prefix is load-bearing —
# :attr:`SkillResult.succeeded_cleanly` looks for exactly this prefix
# in ``warnings`` to down-classify an apparently-successful run that
# actually waited for input. The prefix constant lives in
# :mod:`clauditor.runner`; we concatenate it here so the two never drift.
_INTERACTIVE_HANG_WARNING = (
    f"{_INTERACTIVE_HANG_WARNING_PREFIX} skill may have asked for input — "
    "ensure all parameters are in test_args (heuristic)"
)


# Background-task non-completion heuristic warning body. The leading
# ``"background-task:"`` prefix is load-bearing —
# :attr:`SkillResult.succeeded_cleanly` looks for exactly this prefix
# in ``warnings`` to down-classify a nominally-successful run that
# launched ``Task(run_in_background=true)`` calls and exited before
# polling them. Traces to GitHub #97. The prefix constant lives in
# :mod:`clauditor.runner`; we concatenate it here so the two never drift.
_BACKGROUND_TASK_WARNING = (
    f"{_BACKGROUND_TASK_WARNING_PREFIX} skill launched "
    "Task(run_in_background=true) and exited without polling — "
    "claude -p does not poll background tasks, so output is likely "
    "truncated (heuristic)"
)


def _count_background_task_launches(stream_events: list[dict]) -> int:
    """Count ``Task`` tool_use blocks with ``run_in_background: true``.

    Pure helper. Walks assistant messages' ``content`` lists looking for
    ``{"type": "tool_use", "name": "Task", "input": {"run_in_background":
    True}}`` blocks. Every malformed/missing field degrades to skipping
    the block rather than raising.
    """
    count = 0
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") != "Task":
                continue
            inp = block.get("input")
            if not isinstance(inp, dict):
                continue
            if inp.get("run_in_background") is True:
                count += 1
    return count


def _detect_background_task_noncompletion(
    stream_events: list[dict], final_text: str
) -> bool:
    """Return True when a run looks like it exited with background tasks pending.

    Pure helper (no I/O, no global state) per
    ``.claude/rules/pure-compute-vs-io-split.md``. Mirrors
    :func:`_detect_interactive_hang`'s shape. Returns True only when:

    - At least one assistant ``tool_use`` block with ``name="Task"`` and
      ``input.run_in_background=True`` appears in the stream, AND
    - Either (a) ``final_text`` matches
      ``_BACKGROUND_TASK_WAITING_RE`` (case-insensitive word-boundary
      match on "waiting on", "still waiting", "continuing", "in
      progress", "in the background"), OR (b) the final
      ``result`` message's ``num_turns`` is less than
      ``launches + 2`` — a skill that properly polls each background
      task takes at least one turn per poll plus one for the final
      synthesis.

    Tolerates missing / malformed fields via ``.get`` + ``isinstance``.
    Malformed events degrade to False rather than raising — the
    detector is advisory and must never abort a run.

    Failure mode this catches: GitHub #97 — ``find-restaurants
    --depth deep`` launched 3 ``Task(run_in_background=true)`` agents,
    then emitted "Waiting on editorial agent." and exited. ``claude
    -p`` does not poll background tasks, so the subprocess terminates
    at that point with a valid ``result`` message and no error signal.
    """
    launches = _count_background_task_launches(stream_events)
    if launches == 0:
        return False

    # Signal (a): waiting-pattern regex on the concatenated text.
    waiting_match = bool(_BACKGROUND_TASK_WAITING_RE.search(final_text))

    # Signal (b): num_turns is suspiciously low relative to launches.
    num_turns: int | None = None
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "result":
            continue
        raw = event.get("num_turns")
        if isinstance(raw, int):
            num_turns = raw
    few_turns = num_turns is not None and num_turns < launches + 2

    return waiting_match or few_turns


def _detect_interactive_hang(stream_events: list[dict], final_text: str) -> bool:
    """Return True when a stream-json capture looks like an interactive hang.

    Pure helper (no I/O, no global state) per
    ``.claude/rules/pure-compute-vs-io-split.md``. Returns True only
    when ALL of:

    - The run made exactly 1 turn. Read ``num_turns`` off the
      ``type="result"`` message; if absent, return False (conservative).
    - The final assistant message's ``stop_reason`` is ``"end_turn"``.
      If missing, return False.
    - Either (a) ``final_text.strip()`` ends with ``"?"``, OR (b) any
      assistant message's ``content`` list contains a ``tool_use``
      block whose ``name`` is ``"AskUserQuestion"``.

    Tolerates missing / malformed fields via ``.get`` + ``isinstance``.
    Malformed events degrade to False rather than raising — the
    detector is advisory and must never abort a run.
    """
    # num_turns check (conservative: missing or not 1 → False).
    num_turns: int | None = None
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "result":
            continue
        raw = event.get("num_turns")
        if isinstance(raw, int):
            num_turns = raw
        # Last result message wins if multiple are present (defensive).
    if num_turns != 1:
        return False

    # Last assistant message's stop_reason (conservative: must be
    # "end_turn" — anything else means the model did not end cleanly
    # on a question).
    last_stop_reason: str | None = None
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        stop_reason = message.get("stop_reason")
        if isinstance(stop_reason, str):
            last_stop_reason = stop_reason
    if last_stop_reason != "end_turn":
        return False

    # Signal (a): trailing question mark on the concatenated text.
    trailing_question = final_text.strip().endswith("?")

    # Signal (b): AskUserQuestion tool_use in assistant content.
    ask_user_question = False
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "AskUserQuestion"
            ):
                ask_user_question = True
                break
        if ask_user_question:
            break

    return trailing_question or ask_user_question


def _classify_result_message(msg: dict) -> tuple[str | None, str | None]:
    """Classify a stream-json ``type="result"`` message's error payload.

    Pure helper. Given the full message dict, returns a
    ``(error_text, error_category)`` pair:

    - ``(None, None)`` when ``msg["is_error"]`` is not strictly
      ``True`` (absent key, ``False``, or any non-True value like the
      string ``"true"``).
    - ``(<text>, <category>)`` otherwise. ``<text>`` is the
      ``msg["result"]`` field, truncated to ``_RESULT_TEXT_MAX_CHARS``
      (with a ``" ... (truncated)"`` suffix when clipped). A missing
      or non-string ``result`` field falls back to
      ``"API error (no detail)"``.

    Category inference (per DEC-010) is a keyword match on the
    error text, ordered to resolve ambiguity deterministically:

    - ``"rate_limit"`` — any of ``"429"``, ``"rate limit"`` (case-
      insensitive), ``"rate-limit"`` (case-insensitive).
    - ``"auth"`` — any of ``"401"``, ``"403"``, ``"unauthorized"``
      (case-insensitive), ``"authentication"`` (case-insensitive),
      ``"auth error"`` (case-insensitive), or the substring
      ``"ANTHROPIC_API_KEY"``.
    - ``"api"`` — the fallback when no keyword matches.

    The rate-limit check runs before the auth check so a message
    that happens to contain both ``"429"`` and ``"auth"`` is
    classified as a rate-limit failure.

    Per ``.claude/rules/pure-compute-vs-io-split.md`` this helper
    performs no I/O: no stderr writes, no global mutations. Callers
    surface the result on ``SkillResult.error`` /
    ``SkillResult.error_category`` at the I/O boundary in
    :meth:`SkillRunner._invoke`.
    """
    if msg.get("is_error") is not True:
        return None, None

    result_text = msg.get("result")
    if not isinstance(result_text, str):
        error_text = "API error (no detail)"
    else:
        error_text = result_text

    if len(error_text) > _RESULT_TEXT_MAX_CHARS:
        error_text = error_text[:_RESULT_TEXT_MAX_CHARS] + " ... (truncated)"

    lower = error_text.lower()
    if "429" in error_text or "rate limit" in lower or "rate-limit" in lower:
        category = "rate_limit"
    elif (
        "401" in error_text
        or "403" in error_text
        or "unauthorized" in lower
        or "authentication" in lower
        or "auth error" in lower
        or "anthropic_api_key" in lower
    ):
        category = "auth"
    else:
        category = "api"

    return error_text, category


class ClaudeCodeHarness:
    """Harness implementation that drives the ``claude -p`` CLI.

    Concrete implementation of the :class:`clauditor._harnesses.Harness`
    structural protocol introduced in US-001 of issue #148. Owns
    subprocess invocation, stream-json parsing, the interactive-hang and
    background-task heuristics, and Claude-Code-specific auth-env
    scrubbing. Replaces the module-level CLI helper that lived on
    :mod:`clauditor.runner` through US-003 of #148 (US-004 deletes
    the helper — DEC-001 / Q1 → A: no compatibility shim).

    Per DEC-008 the cross-harness :class:`Harness.invoke` protocol does
    NOT carry ``allow_hang_heuristic``; it is a Claude-Code-specific
    knob that lives here on construction. Per DEC-006 ``model`` on the
    method overrides the instance default so ``call_anthropic``'s CLI
    transport branch can pass a per-call model without rebuilding the
    harness.
    """

    name: ClassVar[str] = "claude-code"

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        model: str | None = None,
        allow_hang_heuristic: bool = True,
    ) -> None:
        self.claude_bin = claude_bin
        self.model = model
        self.allow_hang_heuristic = allow_hang_heuristic

    def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]:
        """Strip Anthropic auth env vars, returning a new dict.

        Delegates to :func:`env_without_api_key` so the auth-stripping
        contract has a single canonical implementation. Pure +
        non-mutating per ``.claude/rules/non-mutating-scrub.md``.
        """
        return env_without_api_key(env)

    def build_prompt(
        self,
        skill_name: str,
        args: str,
        *,
        system_prompt: str | None,
    ) -> str:
        """Render a Claude-Code slash-style invocation.

        Returns ``"/{skill_name}"`` when ``args`` is empty, otherwise
        ``"/{skill_name} {args}"``. ``system_prompt`` is part of the
        cross-harness :class:`Harness.build_prompt` contract but has no
        analogue on the ``claude -p`` CLI surface, so it is intentionally
        ignored here (US-001 of issue #150). Future raw-API harnesses
        will consume it.

        Pure compute (no I/O) per
        ``.claude/rules/pure-compute-vs-io-split.md``.
        """
        if args == "":
            return f"/{skill_name}"
        return f"/{skill_name} {args}"

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
        """Run ``claude -p <prompt>`` with stream-json output and parse the NDJSON.

        Migrated body of the former CLI helper that lived on
        :mod:`clauditor.runner` through US-003 (US-004 of
        ``plans/super/148-extract-harness-protocol.md``). Behavior is
        identical to the deleted helper except the harness-owned config
        (``claude_bin``, ``allow_hang_heuristic``, default ``model``) is
        sourced from ``self`` rather than passed as kwargs. ``model`` on
        this method, when not ``None``, overrides ``self.model`` for
        a single call.

        ``env`` is forwarded to :class:`subprocess.Popen` verbatim:
        ``None`` inherits ``os.environ`` (Popen's default); a dict
        replaces the child environment entirely. The CLI-transport
        caller passes ``env=env_without_api_key(os.environ)`` so the
        parent's ``ANTHROPIC_API_KEY`` is never inherited (DEC-013 —
        subscription-first routing).

        ``subject`` is an optional free-form label appended (in
        parentheses) to the ``apiKeySource=`` stderr info line per
        Issue #107 so operators running grader subprocesses can
        attribute each line to a specific internal LLM call. Sanitized
        (CR/LF → space, trimmed, capped at 200 chars).

        Uses ``try/finally`` so ``duration_seconds`` is populated on
        every exit path (success, timeout, FileNotFoundError).
        """
        claude_bin = self.claude_bin
        effective_model = model if model is not None else self.model
        allow_hang_heuristic = self.allow_hang_heuristic

        start = _monotonic()
        raw_messages: list[dict] = []
        stream_events: list[dict] = []
        text_chunks: list[str] = []
        input_tokens = 0
        output_tokens = 0
        saw_result = False
        # Stream-json ``is_error: true`` classification (US-002, DEC-001,
        # DEC-010). Populated by :func:`_classify_result_message` when
        # the ``result`` message signals an error. When set, takes
        # precedence over stderr per DEC-001.
        stream_json_error_text: str | None = None
        stream_json_error_category: str | None = None
        # US-004 / DEC-005 / DEC-015 / DEC-017: capture the first
        # ``type=="system" AND subtype=="init"`` message's
        # ``apiKeySource`` value. First init wins; subsequent inits
        # ignored. ``None`` when absent (older CLI builds / malformed
        # stream) — per DEC-012, suppresses the stderr info line.
        api_key_source: str | None = None
        init_captured = False
        result: InvokeResult | None = None
        proc: subprocess.Popen | None = None
        stderr_thread: threading.Thread | None = None
        # Main-thread warnings collected during parse + cleanup.
        warnings: list[str] = []
        # Thread-safe collector for warnings raised inside the stderr
        # drainer (runs in a background thread; appending to a plain
        # list would race with the main thread's drain at join time).
        stderr_warnings_lock = threading.Lock()
        stderr_warnings: list[str] = []
        try:
            try:
                argv = [claude_bin, "-p", prompt]
                if effective_model is not None:
                    argv += ["--model", effective_model]
                argv += ["--output-format", "stream-json", "--verbose"]
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(cwd) if cwd is not None else None,
                    env=env,
                )
            except FileNotFoundError:
                result = InvokeResult(
                    output="",
                    exit_code=-1,
                    error=f"Claude CLI not found: {claude_bin}",
                )
                return result

            # Drain stderr on a background thread so a chatty child does
            # not deadlock by filling its PIPE buffer while we read stdout.
            stderr_chunks: list[str] = []

            def _drain_stderr() -> None:
                if proc is None or proc.stderr is None:  # pragma: no cover
                    return
                try:
                    for chunk in proc.stderr:
                        stderr_chunks.append(chunk)
                except (EOFError, OSError) as exc:
                    # Expected terminal states for a pipe: EOF or
                    # underlying OS error (broken pipe, closed fd).
                    # Record + continue.
                    with stderr_warnings_lock:
                        stderr_warnings.append(
                            f"stderr drainer stopped: {type(exc).__name__}: {exc}"
                        )
                except Exception as exc:  # noqa: BLE001 — defensive observability
                    # Truly unexpected: record with type info so a
                    # regression in the CLI's stderr behavior surfaces
                    # in SkillResult.warnings rather than vanishing.
                    with stderr_warnings_lock:
                        stderr_warnings.append(
                            "stderr drainer raised unexpected "
                            f"{type(exc).__name__}: {exc}"
                        )

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            # Watchdog: kill the child if it runs past the configured
            # timeout. A blocked stdout read would otherwise never time
            # out.
            timed_out = {"hit": False}

            def _on_timeout() -> None:
                if proc is None:  # pragma: no cover
                    return
                # Don't flip the flag if the child already exited
                # cleanly — prevents a race where the read loop
                # finishes right as the watchdog fires, yielding a
                # false "timeout" result.
                if proc.poll() is not None:
                    return
                timed_out["hit"] = True
                try:
                    proc.kill()
                except (OSError, ProcessLookupError) as exc:  # pragma: no cover
                    # Child already reaped or kill syscall failed.
                    # Record into stderr_warnings (same thread-safe
                    # channel) so the main thread surfaces it on
                    # SkillResult.warnings.
                    with stderr_warnings_lock:
                        stderr_warnings.append(
                            f"watchdog kill failed: {type(exc).__name__}: {exc}"
                        )

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
                            # Keep the existing stderr print (the
                            # stream-json-schema.md rule requires a
                            # skip+warn contract); ALSO record to
                            # warnings so callers can detect data loss
                            # programmatically without scraping stderr.
                            print(
                                "clauditor.runner: skipping malformed "
                                f"stream-json line: {exc}",
                                file=sys.stderr,
                            )
                            warnings.append(
                                f"malformed stream-json line skipped: {exc}"
                            )
                            continue
                        if not isinstance(msg, dict):
                            # Defensive: a well-formed JSON scalar /
                            # array is not a valid stream-json message.
                            continue

                        raw_messages.append(msg)
                        if "type" in msg:
                            stream_events.append(msg)
                        mtype = msg.get("type")
                        # US-004 / DEC-017: capture ``apiKeySource``
                        # from the FIRST ``system/init`` message.
                        # DEC-015: later init messages are ignored.
                        # DEC-012: absence stays ``None`` (no stderr
                        # line here — emitted once after the loop).
                        if (
                            not init_captured
                            and mtype == "system"
                            and msg.get("subtype") == "init"
                        ):
                            init_captured = True
                            val = msg.get("apiKeySource")
                            if isinstance(val, str):
                                api_key_source = val
                        if mtype == "assistant":
                            message = msg.get("message") or {}
                            content = message.get("content") or []
                            if not isinstance(content, list):
                                continue
                            for block in content:
                                if (
                                    isinstance(block, dict)
                                    and block.get("type") == "text"
                                ):
                                    text_chunks.append(block.get("text", ""))
                        elif mtype == "result":
                            saw_result = True
                            # Classify is_error: true payload per
                            # DEC-001 / DEC-008 / DEC-010. Only
                            # overwrite the accumulator when the
                            # classifier reports an error, so a benign
                            # later result message does not erase a
                            # prior error classification (defensive —
                            # in practice one result per run).
                            err_text, err_category = _classify_result_message(msg)
                            if err_text is not None:
                                stream_json_error_text = err_text
                                stream_json_error_category = err_category
                            usage = msg.get("usage") or {}
                            if isinstance(usage, dict):
                                # Defensive int() casts — if the CLI
                                # ever emits None/str/float, don't
                                # abort the run.
                                try:
                                    input_tokens = int(
                                        usage.get("input_tokens", 0) or 0
                                    )
                                except (TypeError, ValueError):
                                    input_tokens = 0
                                try:
                                    output_tokens = int(
                                        usage.get("output_tokens", 0) or 0
                                    )
                                except (TypeError, ValueError):
                                    output_tokens = 0

                returncode = proc.wait()
            finally:
                watchdog.cancel()

            # Join the drainer before reading stderr_chunks — otherwise
            # ``"".join(stderr_chunks)`` can race with the drainer's
            # in-progress ``.append()`` and produce a truncated or
            # partially-concatenated stderr on SkillResult.error. The
            # outer-finally join is retained for the exception path
            # (where this happy-path join is skipped).
            stderr_thread.join(timeout=2.0)
            stderr_text = "".join(stderr_chunks)

            if timed_out["hit"] and returncode != 0:
                # Preserve any captured stderr as a warning for
                # operators debugging why the subprocess ran past the
                # deadline. Parallel to the normal-exit path's
                # stderr-to-warnings pattern (see below), but kept here
                # because the timeout branch returns early and would
                # otherwise drop it.
                if stderr_text:
                    warnings.append(stderr_text)
                result = InvokeResult(
                    output="\n".join(text_chunks),
                    exit_code=-1,
                    error="timeout",
                    error_category="timeout",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    raw_messages=raw_messages,
                    stream_events=stream_events,
                    warnings=list(warnings),
                    api_key_source=api_key_source,
                )
                # Early return is load-bearing: a post-timeout
                # stream-json is_error:true must not clobber the
                # "timeout" error. Keep this as an early return; do not
                # fall through to the normal-exit path below.
                return result

            if not saw_result:
                print(
                    "clauditor.runner: stream-json ended without a 'result' "
                    "message; token usage unavailable",
                    file=sys.stderr,
                )
                warnings.append(
                    "stream-json ended without a 'result' message; "
                    "token usage unavailable"
                )

            # US-004 / DEC-005: emit one stderr info line per run when
            # ``apiKeySource`` was captured. DEC-012: suppress entirely
            # when the field is absent (no "apiKeySource=unavailable"
            # line) — absence is the signal. Values are labels
            # (``ANTHROPIC_API_KEY``, ``claude.ai``, ``none``), not
            # secrets, so printing them is safe.
            #
            # Issue #107: when ``subject`` is provided (callers like
            # the L2 extraction grader, the L3 grading judge, the
            # suggest proposer) append ``" (<subject>)"`` so operators
            # running ``grade --transport cli`` can attribute each line
            # to a specific internal LLM call instead of seeing
            # identical lines from multiple grader subprocesses.
            # Sanitize any embedded newlines / carriage returns and cap
            # the length so a hostile or buggy caller cannot break the
            # "one line per run" invariant that log-scraping tools rely
            # on.
            if api_key_source is not None:
                sanitized_subject = None
                if subject:
                    sanitized_subject = (
                        subject.replace("\r", " ").replace("\n", " ").strip()
                    )
                    if sanitized_subject:
                        sanitized_subject = sanitized_subject[:200]
                suffix = f" ({sanitized_subject})" if sanitized_subject else ""
                print(
                    f"clauditor.runner: apiKeySource={api_key_source}{suffix}",
                    file=sys.stderr,
                )

            # DEC-005 / DEC-010: interactive-hang heuristic. Only run
            # the detector when the escape hatch is enabled AND no
            # API-error classification already landed (stream-json
            # error wins). When the detector fires, append the prefixed
            # warning and mark ``error_category = "interactive"``
            # WITHOUT setting an error text — the run's ``output`` and
            # ``exit_code`` still reflect the nominally-successful
            # stream.
            final_text = "\n".join(text_chunks)
            if (
                allow_hang_heuristic
                and stream_json_error_text is None
                and _detect_interactive_hang(stream_events, final_text)
            ):
                warnings.append(_INTERACTIVE_HANG_WARNING)
                stream_json_error_category = "interactive"

            # GitHub #97: background-task non-completion heuristic.
            # Runs only when interactive-hang did NOT fire (categories
            # are mutually exclusive — a run that ends with a trailing
            # ``?`` is classified as interactive-hang even if it also
            # launched background tasks). Same shape as
            # interactive-hang: warning prefix + category, no error
            # text, output/exit_code preserved. Suppressed when
            # ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` is set in the
            # subprocess env: the caller forced sync mode, so detecting
            # the pattern is expected and not a user concern (Tier 1.5
            # of GitHub #103).
            _effective_env = env if env is not None else os.environ
            _sync_tasks_forced = _effective_env.get(_SYNC_TASKS_ENV_VAR) == "1"
            if (
                allow_hang_heuristic
                and not _sync_tasks_forced
                and stream_json_error_text is None
                and stream_json_error_category is None
                and _detect_background_task_noncompletion(stream_events, final_text)
            ):
                warnings.append(_BACKGROUND_TASK_WARNING)
                stream_json_error_category = "background-task"

            # DEC-001: stream-json ``is_error: true`` wins over stderr.
            # When classified, stderr (if any) moves into warnings so
            # it is still observable to callers without shadowing the
            # authoritative provider error on ``error``.
            if stream_json_error_text is not None:
                final_error: str | None = stream_json_error_text
                final_category: str | None = stream_json_error_category
                if stderr_text:
                    warnings.append(stderr_text)
            elif stream_json_error_category in ("interactive", "background-task"):
                # Heuristic set the category without an error text.
                # Stderr may still carry subprocess diagnostics (e.g. a
                # retry notice); preserve it in warnings so it's
                # observable to callers, parallel to the stream-json
                # error branch above.
                final_error = None
                final_category = stream_json_error_category
                if stderr_text:
                    warnings.append(stderr_text)
            else:
                final_error = stderr_text if returncode != 0 and stderr_text else None
                final_category = None

            result = InvokeResult(
                output=final_text,
                exit_code=returncode,
                error=final_error,
                error_category=final_category,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_messages=raw_messages,
                stream_events=stream_events,
                warnings=list(warnings),
                api_key_source=api_key_source,
            )
            return result
        finally:
            # Defensive cleanup: if an unexpected exception escaped the
            # inner try, the subprocess could still be running. Always
            # try to reap it so we never leak a claude process. Each
            # step is guarded independently and records its failure
            # into ``warnings`` so lost cleanup errors surface on the
            # result.
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except (OSError, ProcessLookupError) as exc:
                    warnings.append(
                        f"cleanup terminate failed: {type(exc).__name__}: {exc}"
                    )
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired as exc:
                    # terminate() didn't finish in time; escalate to kill.
                    warnings.append(f"cleanup wait after terminate timed out: {exc}")
                    try:
                        proc.kill()
                    except (OSError, ProcessLookupError) as kill_exc:
                        warnings.append(
                            f"cleanup kill failed: "
                            f"{type(kill_exc).__name__}: {kill_exc}"
                        )
                    try:
                        proc.wait(timeout=1)
                    except (subprocess.TimeoutExpired, OSError) as wait_exc:
                        warnings.append(
                            f"cleanup wait after kill failed: "
                            f"{type(wait_exc).__name__}: {wait_exc}"
                        )
                except OSError as exc:
                    warnings.append(
                        f"cleanup wait after terminate failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
            if proc is not None:
                for stream_name, stream in (
                    ("stdout", proc.stdout),
                    ("stderr", proc.stderr),
                ):
                    if stream is None or not hasattr(stream, "close"):
                        continue
                    try:
                        stream.close()
                    except OSError as exc:
                        warnings.append(
                            f"cleanup close({stream_name}) failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
            # Join the drainer thread + surface its warnings on every
            # exit path (success OR exception). Guarded because the
            # thread may not have been created if Popen itself failed.
            if stderr_thread is not None:
                stderr_thread.join(timeout=2.0)
                with stderr_warnings_lock:
                    warnings.extend(stderr_warnings)
                    stderr_warnings.clear()
            duration = _monotonic() - start
            if result is not None:
                result.duration_seconds = duration
                # Any cleanup warnings added after result construction
                # need to be surfaced on the result too.
                if warnings:
                    existing = set(result.warnings)
                    for w in warnings:
                        if w not in existing:
                            result.warnings.append(w)
                            existing.add(w)
