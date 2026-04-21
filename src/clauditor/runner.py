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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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
            "rate_limit", "auth", "api", "interactive", "subprocess", "timeout"
        ]
        | None
    ) = None
    outputs: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_messages: list[dict] = field(default_factory=list)
    stream_events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

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
        return True


# Soft cap applied to stream-json ``result`` text surfaced on
# ``SkillResult.error``. Per DEC-008 of
# ``plans/super/63-runner-error-surfacing.md`` — bound the memory
# cost of a pathological multi-MB error payload without truncating
# realistic 100-300 byte provider-error strings.
_RESULT_TEXT_MAX_CHARS = 4096


# DEC-005 / DEC-010: interactive-hang heuristic warning tag. The prefix
# ``"interactive-hang:"`` is load-bearing — :attr:`SkillResult.succeeded_cleanly`
# looks for exactly this prefix in ``warnings`` to down-classify an
# apparently-successful run that actually waited for input.
_INTERACTIVE_HANG_WARNING_PREFIX = "interactive-hang:"
_INTERACTIVE_HANG_WARNING = (
    "interactive-hang: skill may have asked for input — "
    "ensure all parameters are in test_args (heuristic)"
)


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


class SkillRunner:
    """Executes Claude Code skills via the CLI and captures output."""

    def __init__(
        self,
        project_dir: str | Path | None = None,
        timeout: int = 180,
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
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        """Run a skill and capture its output.

        Args:
            skill_name: Name of the skill (e.g., "find-kid-activities")
            args: Pre-filled arguments to skip interactive prompts
            cwd: Optional override for the subprocess working directory.
                When ``None``, falls back to ``self.project_dir``.
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
            allow_hang_heuristic=allow_hang_heuristic,
        )

    def run_raw(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        """Run a raw prompt without skill prefix for baseline comparison.

        Args:
            prompt: The raw prompt to send to Claude (no /{skill} prefix).
            cwd: Optional override for the subprocess working directory.
                When ``None``, falls back to ``self.project_dir`` — see
                ``.claude/rules/subprocess-cwd.md`` for the rationale.
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
        allow_hang_heuristic: bool = True,
    ) -> SkillResult:
        """Run ``claude`` with stream-json output and parse the NDJSON stream.

        Uses ``try/finally`` so ``duration_seconds`` is populated on every
        exit path (success, timeout, CalledProcessError, FileNotFoundError).
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
        result: SkillResult | None = None
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
                proc = subprocess.Popen(
                    [
                        self.claude_bin,
                        "-p",
                        prompt,
                        "--output-format",
                        "stream-json",
                        "--verbose",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(cwd) if cwd is not None else str(self.project_dir),
                )
            except FileNotFoundError:
                result = SkillResult(
                    output="",
                    exit_code=-1,
                    skill_name=skill_name,
                    args=args,
                    error=f"Claude CLI not found: {self.claude_bin}",
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

            watchdog = threading.Timer(self.timeout, _on_timeout)
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
                result = SkillResult(
                    output="\n".join(text_chunks),
                    exit_code=-1,
                    skill_name=skill_name,
                    args=args,
                    error="timeout",
                    error_category="timeout",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    raw_messages=raw_messages,
                    stream_events=stream_events,
                    warnings=list(warnings),
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

            # DEC-001: stream-json ``is_error: true`` wins over stderr.
            # When classified, stderr (if any) moves into warnings so it
            # is still observable to callers without shadowing the
            # authoritative provider error on ``error``.
            if stream_json_error_text is not None:
                final_error: str | None = stream_json_error_text
                final_category: str | None = stream_json_error_category
                if stderr_text:
                    warnings.append(stderr_text)
            elif stream_json_error_category == "interactive":
                # Hang heuristic set the category without an error text.
                # Stderr may still carry subprocess diagnostics (e.g. a
                # retry notice); preserve it in warnings so it's
                # observable to callers, parallel to the stream-json
                # error branch above.
                final_error = None
                final_category = "interactive"
                if stderr_text:
                    warnings.append(stderr_text)
            else:
                final_error = (
                    stderr_text if returncode != 0 and stderr_text else None
                )
                final_category = None

            result = SkillResult(
                output=final_text,
                exit_code=returncode,
                skill_name=skill_name,
                args=args,
                error=final_error,
                error_category=final_category,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_messages=raw_messages,
                stream_events=stream_events,
                warnings=list(warnings),
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
