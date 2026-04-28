"""Skill runner — executes Claude Code skills and captures output.

Invokes the Claude CLI with ``--output-format stream-json --verbose`` and
parses the NDJSON stream in :meth:`SkillRunner._invoke`. The parser is
intentionally permissive: malformed lines are skipped with a stderr
warning and every field is tolerated-if-missing.

See ``docs/stream-json-schema.md`` (human-readable reference with
concrete examples) and ``.claude/rules/stream-json-schema.md`` (agent
rule: pattern, rationale, canonical implementation pointer).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Env vars stripped by :func:`env_without_api_key`. Both are
# documented Anthropic SDK env-auth paths (DEC-007 of
# ``plans/super/64-runner-auth-timeout.md``). Non-auth Anthropic env
# vars such as ``ANTHROPIC_BASE_URL`` are intentionally preserved
# (DEC-016).
_API_KEY_ENV_VARS = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})

# Documented Anthropic env var that forces ``Task(run_in_background=true)``
# spawns to run synchronously (see
# https://docs.claude.com/en/docs/claude-code/sub-agents — "Run
# subagents in foreground or background"). Setting this to ``"1"`` in
# the subprocess env makes the parent agent wait for each sub-agent
# before emitting its ``result`` message. Tier 1.5 workaround for
# GitHub #103 (see ``docs/adr/transport-research-103.md``).
_SYNC_TASKS_ENV_VAR = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"


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


def env_with_sync_tasks(
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a new env dict with ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1``.

    Pure, non-mutating helper per
    ``.claude/rules/non-mutating-scrub.md``. When ``base_env`` is
    ``None``, reads from ``os.environ``. Always returns a new dict
    (never mutates the input). Forces ``Task(run_in_background=true)``
    calls synchronous in the ``claude -p`` subprocess. Composes with
    :func:`env_without_api_key`: callers chain ``env_with_sync_tasks(
    env_without_api_key())`` (or the reverse) to get both effects.
    """
    source = base_env if base_env is not None else os.environ
    new_env = {k: v for k, v in source.items()}
    new_env[_SYNC_TASKS_ENV_VAR] = "1"
    return new_env


@dataclass
class SkillResult:
    """Captured output from a skill run.

    Pure data container: the Layer 1 ``assert_*`` test helpers live on
    :class:`clauditor.asserters.SkillAsserter`, which composes a
    ``SkillResult``. Non-test callers get a methodless dataclass; tests
    opt into the helpers by constructing ``SkillAsserter(result)``.
    """

    output: str
    exit_code: int
    skill_name: str
    args: str
    duration_seconds: float = 0.0
    error: str | None = None
    # runtime-only — do not serialize to sidecars without bumping schema_version
    error_category: (
        Literal[
            "rate_limit",
            "auth",
            "api",
            "interactive",
            "background-task",
            "subprocess",
            "timeout",
        ]
        | None
    ) = None
    outputs: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_messages: list[dict] = field(default_factory=list)
    stream_events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # US-004 / DEC-005: populated from the first stream-json
    # ``type=="system" AND subtype=="init"`` message when the CLI
    # emits an ``apiKeySource`` field. ``None`` when absent (older CLI
    # builds or a malformed stream — per DEC-012 / DEC-015). The value
    # is a label (``"ANTHROPIC_API_KEY"``, ``"claude.ai"``, ``"none"``),
    # not a secret. See ``docs/stream-json-schema.md``.
    api_key_source: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.output.strip() != ""

    @property
    def succeeded_cleanly(self) -> bool:
        """True only when the run had zero error signals.

        Stricter than :attr:`succeeded`: requires no ``error`` text,
        no ``error_category``, and no interactive-hang warning tag in
        ``warnings``. US-003 wires the real interactive-hang detector
        to this ``"interactive-hang:"`` prefix.
        """
        if not self.succeeded:
            return False
        if self.error is not None:
            return False
        if self.error_category is not None:
            return False
        for w in self.warnings:
            if w.startswith(_INTERACTIVE_HANG_WARNING_PREFIX):
                return False
            if w.startswith(_BACKGROUND_TASK_WARNING_PREFIX):
                return False
        return True


# DEC-005 / DEC-010: interactive-hang heuristic warning prefix. The
# prefix ``"interactive-hang:"`` is load-bearing —
# :attr:`SkillResult.succeeded_cleanly` looks for exactly this prefix
# in ``warnings`` to down-classify an apparently-successful run that
# actually waited for input. The full warning *body* string lives in
# :mod:`clauditor._harnesses._claude_code` (US-002 of issue #148); the
# prefix stays here because the dataclass invariant inspects it.
_INTERACTIVE_HANG_WARNING_PREFIX = "interactive-hang:"


# Background-task non-completion heuristic warning prefix. The prefix
# ``"background-task:"`` is load-bearing —
# :attr:`SkillResult.succeeded_cleanly` looks for exactly this prefix
# in ``warnings`` to down-classify a nominally-successful run that
# launched ``Task(run_in_background=true)`` calls and exited before
# polling them. Traces to GitHub #97. The full warning *body* string
# lives in :mod:`clauditor._harnesses._claude_code` (US-002 of issue
# #148); the prefix stays here because the dataclass invariant
# inspects it.
_BACKGROUND_TASK_WARNING_PREFIX = "background-task:"


@dataclass
class InvokeResult:
    """Transport-level result of a single ``claude -p`` subprocess invocation.

    Pure data container emitted by :func:`_invoke_claude_cli` — the
    subprocess + stream-json parse primitive that both
    :class:`SkillRunner` (for skill runs) and
    :func:`clauditor._anthropic.call_anthropic`'s CLI transport branch
    (US-003, per DEC-003 of ``plans/super/86-claude-cli-transport.md``)
    project onto their own higher-level dataclasses.

    Crucially, ``InvokeResult`` carries NO ``skill_name`` or ``args``
    context: those are slash-command-shaped and meaningful only to the
    skill-runner surface. An async caller sending a raw prompt
    (e.g. the LLM-judge CLI transport) treats the helper as a plain
    "run the CLI with this prompt, give me back the bytes plus
    observability metadata" primitive.

    Every field mirrors a :class:`SkillResult` field and is populated
    with identical semantics so the projection in
    :meth:`SkillRunner._invoke` is a straight field-copy.
    """

    output: str
    exit_code: int
    duration_seconds: float = 0.0
    error: str | None = None
    error_category: (
        Literal[
            "rate_limit",
            "auth",
            "api",
            "interactive",
            "background-task",
            "subprocess",
            "timeout",
        ]
        | None
    ) = None
    input_tokens: int = 0
    output_tokens: int = 0
    raw_messages: list[dict] = field(default_factory=list)
    stream_events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    api_key_source: str | None = None
    harness_metadata: dict[str, Any] = field(default_factory=dict)


# Bridge imports — transition state between US-002 and US-004 of issue
# #148. ``_invoke_claude_cli`` (still in this module) calls these
# Claude-Code-specific pure helpers; once US-004 moves the
# subprocess+parse body into ``ClaudeCodeHarness.invoke``, this bridge
# disappears and the helpers become module-private to
# ``_harnesses/_claude_code.py``. Placed AFTER ``InvokeResult`` so
# ``clauditor._harnesses.__init__``'s ``from clauditor.runner import
# InvokeResult`` resolves cleanly when ``_claude_code`` triggers the
# package init.
from clauditor._harnesses._claude_code import (  # noqa: E402
    _BACKGROUND_TASK_WARNING,
    _INTERACTIVE_HANG_WARNING,
    _classify_result_message,
    _detect_background_task_noncompletion,
    _detect_interactive_hang,
)


def _invoke_claude_cli(
    prompt: str,
    *,
    cwd: Path | None,
    env: dict[str, str] | None,
    timeout: int,
    claude_bin: str,
    model: str | None = None,
    allow_hang_heuristic: bool = True,
    subject: str | None = None,
) -> InvokeResult:
    """Run ``claude -p <prompt>`` with stream-json output and parse the NDJSON.

    Transport-level primitive per DEC-003 of
    ``plans/super/86-claude-cli-transport.md``. This is the lone
    subprocess + stream-json parse seam for the whole codebase;
    :meth:`SkillRunner._invoke` wraps it (synthesizing the
    ``/<skill>`` slash-command prompt), and US-003 adds
    :func:`clauditor._anthropic.call_anthropic`'s CLI transport as the
    second caller (raw-prompt path with no skill context).

    ``env`` is forwarded to :class:`subprocess.Popen` verbatim:
    ``None`` inherits ``os.environ`` (Popen's default); a dict
    replaces the child environment entirely. The CLI-transport caller
    always passes ``env=env_without_api_key(os.environ)`` so the
    parent's ``ANTHROPIC_API_KEY`` is never inherited (DEC-013 —
    subscription-first routing).

    ``timeout`` is not ``None``-able here: the per-invocation
    fallback to ``SkillRunner.timeout`` / the async retry budget is
    resolved by the caller. Keeping a required ``int`` avoids a
    second sentinel-handling branch inside the watchdog.

    ``claude_bin`` is similarly required so the helper has no
    hidden dependence on any ``SkillRunner`` state — a pure
    subprocess-with-explicit-config primitive.

    Uses ``try/finally`` so ``duration_seconds`` is populated on
    every exit path (success, timeout, FileNotFoundError).
    """
    start = time.monotonic()
    raw_messages: list[dict] = []
    stream_events: list[dict] = []
    text_chunks: list[str] = []
    input_tokens = 0
    output_tokens = 0
    saw_result = False
    # Stream-json ``is_error: true`` classification (US-002, DEC-001,
    # DEC-010). Populated by :func:`_classify_result_message` when the
    # ``result`` message signals an error. When set, takes precedence
    # over stderr per DEC-001.
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
    # drainer (runs in a background thread; appending to a plain list
    # would race with the main thread's drain at join time).
    stderr_warnings_lock = threading.Lock()
    stderr_warnings: list[str] = []
    try:
        try:
            argv = [claude_bin, "-p", prompt]
            if model is not None:
                argv += ["--model", model]
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
                # Expected terminal states for a pipe: EOF or underlying
                # OS error (broken pipe, closed fd). Record + continue.
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
        # timeout. A blocked stdout read would otherwise never time out.
        timed_out = {"hit": False}

        def _on_timeout() -> None:
            if proc is None:  # pragma: no cover
                return
            # Don't flip the flag if the child already exited cleanly —
            # prevents a race where the read loop finishes right as the
            # watchdog fires, yielding a false "timeout" result.
            if proc.poll() is not None:
                return
            timed_out["hit"] = True
            try:
                proc.kill()
            except (OSError, ProcessLookupError) as exc:  # pragma: no cover
                # Child already reaped or kill syscall failed. Record
                # into stderr_warnings (same thread-safe channel) so
                # the main thread surfaces it on SkillResult.warnings.
                with stderr_warnings_lock:
                    stderr_warnings.append(
                        "watchdog kill failed: "
                        f"{type(exc).__name__}: {exc}"
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
                        # stream-json-schema.md rule requires a skip+warn
                        # contract); ALSO record to warnings so callers
                        # can detect data loss programmatically without
                        # scraping stderr.
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
                        # Defensive: a well-formed JSON scalar / array is
                        # not a valid stream-json message.
                        continue

                    raw_messages.append(msg)
                    if "type" in msg:
                        stream_events.append(msg)
                    mtype = msg.get("type")
                    # US-004 / DEC-017: capture ``apiKeySource`` from
                    # the FIRST ``system/init`` message. DEC-015: later
                    # init messages are ignored. DEC-012: absence
                    # stays ``None`` (no stderr line here — emitted
                    # once after the loop).
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
                        # Classify is_error: true payload per DEC-001 /
                        # DEC-008 / DEC-010. Only overwrite the
                        # accumulator when the classifier reports an
                        # error, so a benign later result message does
                        # not erase a prior error classification
                        # (defensive — in practice one result per run).
                        err_text, err_category = _classify_result_message(msg)
                        if err_text is not None:
                            stream_json_error_text = err_text
                            stream_json_error_category = err_category
                        usage = msg.get("usage") or {}
                        if isinstance(usage, dict):
                            # Defensive int() casts — if the CLI ever
                            # emits None/str/float, don't abort the run.
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
            # Preserve any captured stderr as a warning for operators
            # debugging why the subprocess ran past the deadline.
            # Parallel to the normal-exit path's stderr-to-warnings
            # pattern (see below), but kept here because the timeout
            # branch returns early and would otherwise drop it.
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
            # Early return is load-bearing: a post-timeout stream-json
            # is_error:true must not clobber the "timeout" error. Keep
            # this as an early return; do not fall through to the
            # normal-exit path below.
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
        # Issue #107: when ``subject`` is provided (callers like the
        # L2 extraction grader, the L3 grading judge, the suggest
        # proposer) append ``" (<subject>)"`` so operators running
        # ``grade --transport cli`` can attribute each line to a
        # specific internal LLM call instead of seeing identical
        # lines from multiple grader subprocesses. Sanitize any
        # embedded newlines / carriage returns and cap the length so
        # a hostile or buggy caller cannot break the "one line per
        # run" invariant that log-scraping tools rely on.
        if api_key_source is not None:
            sanitized_subject = None
            if subject:
                sanitized_subject = (
                    subject.replace("\r", " ").replace("\n", " ").strip()
                )
                if sanitized_subject:
                    sanitized_subject = sanitized_subject[:200]
            suffix = (
                f" ({sanitized_subject})" if sanitized_subject else ""
            )
            print(
                f"clauditor.runner: apiKeySource={api_key_source}{suffix}",
                file=sys.stderr,
            )

        # DEC-005 / DEC-010: interactive-hang heuristic. Only run the
        # detector when the escape hatch is enabled AND no API-error
        # classification already landed (stream-json error wins). When
        # the detector fires, append the prefixed warning and mark
        # ``error_category = "interactive"`` WITHOUT setting an error
        # text — the run's ``output`` and ``exit_code`` still reflect
        # the nominally-successful stream.
        final_text = "\n".join(text_chunks)
        if (
            allow_hang_heuristic
            and stream_json_error_text is None
            and _detect_interactive_hang(stream_events, final_text)
        ):
            warnings.append(_INTERACTIVE_HANG_WARNING)
            stream_json_error_category = "interactive"

        # GitHub #97: background-task non-completion heuristic. Runs
        # only when interactive-hang did NOT fire (categories are
        # mutually exclusive — a run that ends with a trailing ``?``
        # is classified as interactive-hang even if it also launched
        # background tasks). Same shape as interactive-hang: warning
        # prefix + category, no error text, output/exit_code preserved.
        # Suppressed when ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` is
        # set in the subprocess env: the caller forced sync mode, so
        # detecting the pattern is expected and not a user concern
        # (Tier 1.5 of GitHub #103).
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
        # When classified, stderr (if any) moves into warnings so it
        # is still observable to callers without shadowing the
        # authoritative provider error on ``error``.
        if stream_json_error_text is not None:
            final_error: str | None = stream_json_error_text
            final_category: str | None = stream_json_error_category
            if stderr_text:
                warnings.append(stderr_text)
        elif stream_json_error_category in ("interactive", "background-task"):
            # Heuristic set the category without an error text. Stderr
            # may still carry subprocess diagnostics (e.g. a retry
            # notice); preserve it in warnings so it's observable to
            # callers, parallel to the stream-json error branch above.
            final_error = None
            final_category = stream_json_error_category
            if stderr_text:
                warnings.append(stderr_text)
        else:
            final_error = (
                stderr_text if returncode != 0 and stderr_text else None
            )
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
        # inner try, the subprocess could still be running. Always try
        # to reap it so we never leak a claude process. Each step is
        # guarded independently and records its failure into
        # ``warnings`` so lost cleanup errors surface on the result.
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except (OSError, ProcessLookupError) as exc:
                warnings.append(
                    f"cleanup terminate failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                # terminate() didn't finish in time; escalate to kill.
                warnings.append(
                    f"cleanup wait after terminate timed out: {exc}"
                )
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
        duration = time.monotonic() - start
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


class SkillRunner:
    """Executes Claude Code skills via the CLI and captures output."""

    def __init__(
        self,
        project_dir: str | Path | None = None,
        timeout: int = 300,
        claude_bin: str = "claude",
    ):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.timeout = timeout
        self.claude_bin = claude_bin

    def run(
        self,
        skill_name: str,
        args: str = "",
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        """Run a skill and capture its output.

        Args:
            skill_name: Name of the skill (e.g., "find-kid-activities")
            args: Pre-filled arguments to skip interactive prompts
            cwd: Optional override for the subprocess working directory.
                When ``None``, falls back to ``self.project_dir``.
            env: Optional env dict forwarded to ``subprocess.Popen``.
                When ``None`` (default), ``Popen`` inherits ``os.environ``
                — today's behavior. When a dict, it replaces the child's
                environment entirely (DEC-013; mirrors ``cwd`` shape per
                ``.claude/rules/subprocess-cwd.md``).
            timeout: Optional per-invocation watchdog timeout in seconds.
                When ``None`` (default), falls back to ``self.timeout``
                (DEC-010).
            allow_hang_heuristic: When False, skip the interactive-hang
                heuristic (DEC-005). Threaded here from
                ``EvalSpec.allow_hang_heuristic`` so authors can opt out
                when the heuristic is wrong for a particular skill.

        Returns:
            SkillResult with captured output
        """
        prompt = f"/{skill_name}"
        if args:
            prompt += f" {args}"
        return self._invoke(
            prompt=prompt,
            skill_name=skill_name,
            args=args,
            cwd=cwd,
            env=env,
            timeout=timeout,
            allow_hang_heuristic=allow_hang_heuristic,
        )

    def run_raw(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        """Run a raw prompt without skill prefix for baseline comparison.

        Args:
            prompt: The raw prompt to send to Claude (no /{skill} prefix).
            cwd: Optional override for the subprocess working directory.
                When ``None``, falls back to ``self.project_dir`` — see
                ``.claude/rules/subprocess-cwd.md`` for the rationale.
            env: Optional subprocess env dict. When ``None``, Popen
                inherits ``os.environ``; when a dict, replaces verbatim.
                Mirrors the ``env`` kwarg on :meth:`run`; callers that
                want to strip credentials use
                :func:`env_without_api_key`.
            timeout: Optional per-invocation timeout (seconds). When
                ``None``, falls back to ``self.timeout``.
            allow_hang_heuristic: When False, skip the interactive-hang
                heuristic (DEC-005).

        Returns:
            SkillResult with skill_name="__baseline__"
        """
        return self._invoke(
            prompt=prompt,
            skill_name="__baseline__",
            args=prompt,
            cwd=cwd,
            env=env,
            timeout=timeout,
            allow_hang_heuristic=allow_hang_heuristic,
        )

    # ------------------------------------------------------------------ #
    # Stream-json Popen implementation                                    #
    # ------------------------------------------------------------------ #

    def _invoke(
        self,
        *,
        prompt: str,
        skill_name: str,
        args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        """Thin wrapper around :func:`_invoke_claude_cli`.

        Resolves the per-call ``timeout`` sentinel against
        ``self.timeout`` (DEC-010), defaults ``cwd`` to
        ``self.project_dir`` when unset, delegates to the transport
        primitive, and projects the returned :class:`InvokeResult`
        onto :class:`SkillResult` by copying every field and adding
        the caller-owned ``skill_name`` / ``args`` context.

        ``env`` is forwarded verbatim: ``None`` means "inherit
        ``os.environ``" (Popen's default); a dict replaces the
        child's environment entirely (DEC-013 of
        ``plans/super/64-runner-auth-timeout.md``).
        """
        effective_timeout = timeout if timeout is not None else self.timeout
        effective_cwd = cwd if cwd is not None else self.project_dir
        invoke = _invoke_claude_cli(
            prompt,
            cwd=effective_cwd,
            env=env,
            timeout=effective_timeout,
            claude_bin=self.claude_bin,
            allow_hang_heuristic=allow_hang_heuristic,
        )
        return SkillResult(
            output=invoke.output,
            exit_code=invoke.exit_code,
            skill_name=skill_name,
            args=args,
            duration_seconds=invoke.duration_seconds,
            error=invoke.error,
            error_category=invoke.error_category,
            input_tokens=invoke.input_tokens,
            output_tokens=invoke.output_tokens,
            raw_messages=invoke.raw_messages,
            stream_events=invoke.stream_events,
            warnings=invoke.warnings,
            api_key_source=invoke.api_key_source,
        )
