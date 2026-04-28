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

import os
import re

from clauditor.runner import (
    _BACKGROUND_TASK_WARNING_PREFIX,
    _INTERACTIVE_HANG_WARNING_PREFIX,
)

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


def _detect_interactive_hang(
    stream_events: list[dict], final_text: str
) -> bool:
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
