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
    outputs: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_messages: list[dict] = field(default_factory=list)
    stream_events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.output.strip() != ""


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
    ) -> SkillResult:
        """Run a skill and capture its output.

        Args:
            skill_name: Name of the skill (e.g., "find-kid-activities")
            args: Pre-filled arguments to skip interactive prompts
            cwd: Optional override for the subprocess working directory.
                When ``None``, falls back to ``self.project_dir``.

        Returns:
            SkillResult with captured output
        """
        prompt = f"/{skill_name}"
        if args:
            prompt += f" {args}"
        return self._invoke(prompt=prompt, skill_name=skill_name, args=args, cwd=cwd)

    def run_raw(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
    ) -> SkillResult:
        """Run a raw prompt without skill prefix for baseline comparison.

        Args:
            prompt: The raw prompt to send to Claude (no /{skill} prefix).
            cwd: Optional override for the subprocess working directory.
                When ``None``, falls back to ``self.project_dir`` — see
                ``.claude/rules/subprocess-cwd.md`` for the rationale.

        Returns:
            SkillResult with skill_name="__baseline__"
        """
        return self._invoke(
            prompt=prompt,
            skill_name="__baseline__",
            args=prompt,
            cwd=cwd,
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

            stderr_text = "".join(stderr_chunks)
            # stderr_thread.join + stderr_warnings drain moved to the
            # outer finally so they run on the exception path too —
            # otherwise a parse-loop failure leaves the drainer daemon
            # unjoined and its warnings lost.

            if timed_out["hit"] and returncode != 0:
                result = SkillResult(
                    output="\n".join(text_chunks),
                    exit_code=-1,
                    skill_name=skill_name,
                    args=args,
                    error="timeout",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    raw_messages=raw_messages,
                    stream_events=stream_events,
                    warnings=list(warnings),
                )
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

            result = SkillResult(
                output="\n".join(text_chunks),
                exit_code=returncode,
                skill_name=skill_name,
                args=args,
                error=stderr_text if returncode != 0 and stderr_text else None,
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
