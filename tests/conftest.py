"""Shared test fixtures for clauditor tests.

Provides reusable fixtures for eval data, specs, temp skill files, and mock runners.
IMPORTANT: Do NOT define fixtures named clauditor_runner, clauditor_spec,
clauditor_grader, clauditor_blind_compare, or clauditor_triggers -- those
are defined by the pytest plugin.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult, SkillRunner
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    GradeThresholds,
    SectionRequirement,
)
from clauditor.spec import SkillSpec


class _FakePopen:
    """Minimal ``subprocess.Popen`` stand-in for stream-json runner tests.

    Exposes a ``stdout`` that yields the provided NDJSON lines (each
    terminated by ``\\n``), a ``stderr`` that is an empty iterator (so the
    runner's background ``for chunk in proc.stderr`` drain loop is a
    no-op), plus ``wait``/``kill``/``poll`` methods. The ``returncode`` is
    set on construction and returned from ``wait``.
    """

    def __init__(self, lines: list[str], returncode: int = 0):
        body = "\n".join(lines)
        if body and not body.endswith("\n"):
            body += "\n"
        self.stdout = io.StringIO(body)
        # iter(()) makes `for chunk in proc.stderr:` a no-op drain loop.
        self.stderr = iter(())
        self.returncode = returncode
        self.kill_called = False
        self._killed = False

    def wait(self, timeout=None):  # noqa: ARG002 — timeout ignored for fake
        return self.returncode

    def kill(self):
        self.kill_called = True
        self._killed = True
        # After kill, poll reports a non-None exit signal.
        if self.returncode == 0:
            self.returncode = -9

    def terminate(self):
        # Default terminate: mark as dead so the outer-finally cleanup
        # short-circuits instead of cascading to kill+wait. Tests that need
        # to exercise the terminate→kill fallback override this attribute.
        self._killed = True
        if self.returncode == 0:
            self.returncode = -15

    def poll(self) -> int | None:
        # Immediate-timer tests want poll() to report "still running" so the
        # watchdog sets timed_out=True. Production code only calls poll from
        # the watchdog callback; returning None mimics a live child.
        if self._killed:
            return self.returncode
        return None


def make_fake_skill_stream(
    text: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
    extra_messages: list[dict] | None = None,
    error_text: str | None = None,
    init_message: dict | None = None,
) -> _FakePopen:
    """Build a ``_FakePopen`` emitting a realistic stream-json sequence.

    Produces:
      1. optional ``init_message`` verbatim as the FIRST message
         (typically a ``{"type": "system", "subtype": "init", ...}``
         event — see US-004 of
         ``plans/super/64-runner-auth-timeout.md``)
      2. one assistant message with a single ``text`` block containing
         ``text``
      3. any ``extra_messages`` verbatim, in order
      4. a final ``result`` message carrying token usage

    When ``error_text`` is not ``None``, the final ``result`` message
    carries ``is_error: True`` and ``result: <error_text>`` (per DEC-014
    of ``plans/super/63-runner-error-surfacing.md``). The default
    (``error_text=None``) preserves today's ``is_error: False`` output
    byte-for-byte so every pre-existing test keeps working.
    """
    messages: list[dict] = []
    if init_message is not None:
        messages.append(init_message)
    messages.append(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    )
    if extra_messages:
        messages.extend(extra_messages)
    result_msg: dict = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
    if error_text is not None:
        result_msg["is_error"] = True
        result_msg["result"] = error_text
    messages.append(result_msg)
    return _FakePopen([json.dumps(m) for m in messages])


def make_fake_interactive_hang_stream(
    text: str = "What would you like?",
    use_tool_use: bool = False,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> _FakePopen:
    """Build a ``_FakePopen`` emitting an interactive-hang stream-json sequence.

    Models the failure mode where a skill ends its single turn by
    asking the user a clarifying question (per DEC-014 of
    ``plans/super/63-runner-error-surfacing.md``):

      - A single ``assistant`` message with ``stop_reason: "end_turn"``.
        Its content is either a single ``text`` block ending in ``?``
        (when ``use_tool_use=False``) or a ``text`` block *and* a
        ``tool_use`` block for ``AskUserQuestion`` (when
        ``use_tool_use=True``).
      - A final ``result`` message with ``is_error: False``,
        ``subtype: "success"``, ``num_turns: 1`` (so downstream
        detection can check the turn count), and the usual
        ``usage`` block.

    The caller is responsible for the ``text`` shape; passing a
    non-``?`` string will still emit, but the interactive-hang
    detector may not fire.
    """
    content: list[dict] = [{"type": "text", "text": text}]
    if use_tool_use:
        content.append(
            {
                "type": "tool_use",
                "id": "toolu_fake",
                "name": "AskUserQuestion",
                "input": {"questions": [{"question": text}]},
            }
        )
    messages: list[dict] = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": content,
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    ]
    return _FakePopen([json.dumps(m) for m in messages])


class _CodexFakeStdin:
    """Tiny stand-in for ``proc.stdin`` that captures every write into
    an in-memory buffer accessible AFTER ``close()`` (unlike a plain
    :class:`io.StringIO`, whose ``getvalue()`` raises after close).
    """

    def __init__(self) -> None:
        self._buf: list[str] = []
        self.closed = False

    def write(self, data: str) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        self._buf.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True

    def getvalue(self) -> str:
        return "".join(self._buf)


class _FakeCodexPopen:
    """Minimal ``subprocess.Popen`` stand-in for Codex NDJSON tests.

    Mirrors :class:`_FakePopen` but adds a writable ``stdin`` (so the
    harness can write the prompt then close) and tracks construction
    kwargs so tests can assert argv shape, ``cwd``, ``env``, and the
    POSIX ``start_new_session`` flag without spinning up a real Codex
    subprocess.

    Stderr defaults to an empty iterator; tests that need to exercise
    the stderr-drainer / filter path pass a non-empty ``stderr_lines``
    list which is materialized into an in-memory iterator.
    """

    def __init__(
        self,
        lines: list[str],
        returncode: int = 0,
        stderr_lines: list[str] | None = None,
        pid: int = 12345,
        wait_raises_timeout_count: int = 0,
    ) -> None:
        body = "\n".join(lines)
        if body and not body.endswith("\n"):
            body += "\n"
        self.stdout = io.StringIO(body)
        if stderr_lines:
            self.stderr = iter(line + "\n" for line in stderr_lines)
        else:
            self.stderr = iter(())
        self.stdin = _CodexFakeStdin()
        self.returncode = returncode
        self.kill_called = False
        self.terminate_called = False
        self._killed = False
        # ``pid`` is only consulted by the POSIX kill path (``os.getpgid(pid)``
        # → ``os.killpg(...)``). Tests that exercise the timeout/kill branch
        # patch ``os.getpgid`` and ``os.killpg`` so the value is opaque.
        self.pid = pid
        # Counter so tests can drive ``wait(timeout)`` to raise
        # ``subprocess.TimeoutExpired`` a deterministic number of times
        # before settling. Each timed wait decrements the counter; once
        # zero, ``wait`` returns ``returncode`` normally. This unblocks
        # the SIGKILL-after-SIGTERM-grace-period escalation path tests
        # that need ``proc.wait(timeout=0.25)`` to time out at least once.
        self._wait_raises_timeout_count = wait_raises_timeout_count

    def wait(self, timeout=None):
        if (
            timeout is not None
            and self._wait_raises_timeout_count > 0
        ):
            self._wait_raises_timeout_count -= 1
            import subprocess as _sp

            raise _sp.TimeoutExpired(cmd="codex", timeout=timeout)
        return self.returncode

    def kill(self) -> None:
        self.kill_called = True
        self._killed = True
        if self.returncode == 0:
            self.returncode = -9

    def terminate(self) -> None:
        self.terminate_called = True
        self._killed = True
        if self.returncode == 0:
            self.returncode = -15

    def poll(self) -> int | None:
        if self._killed:
            return self.returncode
        return None


def make_fake_codex_agent_message_item(text: str, item_id: str = "agent_1") -> dict:
    """Build a Codex ``item.completed`` event with item.type=agent_message."""
    return {
        "type": "item.completed",
        "item": {"id": item_id, "type": "agent_message", "text": text},
    }


def make_fake_codex_reasoning_item(text: str, item_id: str = "reasoning_1") -> dict:
    """Build a Codex ``item.completed`` event with item.type=reasoning."""
    return {
        "type": "item.completed",
        "item": {"id": item_id, "type": "reasoning", "text": text},
    }


def make_fake_codex_command_execution_item(
    command: str = "ls",
    aggregated_output: str = "",
    exit_code: int = 0,
    status: str = "completed",
    item_id: str = "cmd_1",
) -> dict:
    """Build a Codex ``item.completed`` event with item.type=command_execution."""
    return {
        "type": "item.completed",
        "item": {
            "id": item_id,
            "type": "command_execution",
            "command": command,
            "aggregated_output": aggregated_output,
            "exit_code": exit_code,
            "status": status,
        },
    }


def make_fake_codex_file_change_item(
    path: str = "foo.txt",
    kind: str = "update",
    status: str = "completed",
    item_id: str = "fc_1",
) -> dict:
    """Build a Codex ``item.completed`` event with item.type=file_change."""
    return {
        "type": "item.completed",
        "item": {
            "id": item_id,
            "type": "file_change",
            "changes": [{"path": path, "kind": kind}],
            "status": status,
        },
    }


def make_fake_codex_mcp_tool_call_item(
    server: str = "fs",
    tool: str = "read",
    item_id: str = "mcp_1",
) -> dict:
    """Build a Codex ``item.completed`` event with item.type=mcp_tool_call."""
    return {
        "type": "item.completed",
        "item": {
            "id": item_id,
            "type": "mcp_tool_call",
            "server": server,
            "tool": tool,
        },
    }


def make_fake_codex_web_search_item(
    query: str = "weather", item_id: str = "ws_1"
) -> dict:
    """Build a Codex ``item.completed`` event with item.type=web_search."""
    return {
        "type": "item.completed",
        "item": {"id": item_id, "type": "web_search", "query": query},
    }


def make_fake_codex_todo_list_item(
    items: list[str] | None = None, item_id: str = "todo_1"
) -> dict:
    """Build a Codex ``item.completed`` event with item.type=todo_list."""
    return {
        "type": "item.completed",
        "item": {
            "id": item_id,
            "type": "todo_list",
            "items": items or ["step a", "step b"],
        },
    }


def make_fake_codex_stream(
    text: str = "answer",
    thread_id: str = "thread-1",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    extra_items: list[dict] | None = None,
    returncode: int = 0,
    stderr_lines: list[str] | None = None,
) -> _FakeCodexPopen:
    """Build a ``_FakeCodexPopen`` emitting a realistic Codex NDJSON sequence.

    Produces (in order):
      1. ``thread.started`` with the given ``thread_id``
      2. ``turn.started``
      3. one ``item.completed[agent_message]`` carrying ``text``
      4. any extra item-completed events from ``extra_items``
      5. ``turn.completed`` with the given usage counters

    For tests that need a different shape (no agent message, multiple
    turns, ``turn.failed``, malformed lines) compose with the per-item
    builders or assemble the lines list directly.
    """
    messages: list[dict] = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        make_fake_codex_agent_message_item(text),
    ]
    if extra_items:
        messages.extend(extra_items)
    messages.append(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "reasoning_output_tokens": reasoning_output_tokens,
            },
        }
    )
    return _FakeCodexPopen(
        [json.dumps(m) for m in messages],
        returncode=returncode,
        stderr_lines=stderr_lines,
    )


def make_fake_codex_turn_failed(
    error_message: str = "rate limit exceeded",
    thread_id: str = "thread-1",
    input_tokens: int = 0,
    output_tokens: int = 0,
    returncode: int = 1,
    stderr_lines: list[str] | None = None,
) -> _FakeCodexPopen:
    """Build a ``_FakeCodexPopen`` whose stream contains a ``turn.failed``
    event carrying an ``error.message`` string.

    Per DEC-007: ``turn.failed.error.message`` substring-classifies into
    ``"rate_limit"``, ``"auth"``, or ``"api"``. Unlike ``turn.completed``,
    a failed turn typically does NOT carry token usage; tests that need
    token assertions on a failure path can override ``input_tokens`` /
    ``output_tokens``.
    """
    messages: list[dict] = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {
            "type": "turn.failed",
            "error": {"message": error_message},
        },
    ]
    if input_tokens or output_tokens:
        messages.append(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            }
        )
    return _FakeCodexPopen(
        [json.dumps(m) for m in messages],
        returncode=returncode,
        stderr_lines=stderr_lines,
    )


def make_fake_codex_top_level_error(
    error_message: str = "internal server error",
    returncode: int = 1,
) -> _FakeCodexPopen:
    """Build a ``_FakeCodexPopen`` whose stream contains a top-level
    ``error`` event (fatal stream-level failure per DEC-007).

    The ``error`` event has no ``thread.started`` / ``turn.started``
    parents — Codex emits it when something goes wrong before any turn
    can run (e.g. auth failure on the first request).
    """
    messages: list[dict] = [
        {"type": "error", "message": error_message},
    ]
    return _FakeCodexPopen(
        [json.dumps(m) for m in messages],
        returncode=returncode,
    )


def make_fake_codex_with_lagged_event(
    text: str = "answer",
    dropped_count: int = 17,
    thread_id: str = "thread-1",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> _FakeCodexPopen:
    """Build a stream that includes a synthetic ``Lagged`` event per DEC-018.

    Codex surfaces in-process channel overflow as a synthetic
    ``item.completed`` event whose ``item.type == "error"`` and whose
    ``item.message`` carries a leading integer count. The detector
    :func:`_detect_codex_dropped_events` parses the leading integer
    and sums across all such events.
    """
    messages: list[dict] = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        make_fake_codex_agent_message_item(text),
        {
            "type": "item.completed",
            "item": {
                "id": "lagged_1",
                "type": "error",
                "message": f"{dropped_count} events were dropped due to lag",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    ]
    return _FakeCodexPopen([json.dumps(m) for m in messages])


def make_fake_codex_malformed_line_in_stream(
    text: str = "answer",
    thread_id: str = "thread-1",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> _FakeCodexPopen:
    """Build a stream that includes a single non-JSON line between valid events.

    The defensive parser must skip the malformed line, append a warning,
    and keep reading subsequent valid events per
    ``.claude/rules/stream-json-schema.md``.
    """
    valid_lines = [
        json.dumps({"type": "thread.started", "thread_id": thread_id}),
        json.dumps({"type": "turn.started"}),
        # MALFORMED — invalid JSON that the parser must skip + warn on.
        "{this is not valid json",
        json.dumps(make_fake_codex_agent_message_item(text)),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            }
        ),
    ]
    return _FakeCodexPopen(valid_lines)


def make_fake_codex_no_agent_message(
    thread_id: str = "thread-1",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> _FakeCodexPopen:
    """Build a stream with no ``agent_message`` items (truncated-output case).

    Per DEC-018, when the stream has no ``agent_message`` items but the
    ``--output-last-message`` tempfile contains text, the harness falls
    back to reading the tempfile and emits a ``last-message-empty:``
    advisory warning.
    """
    messages: list[dict] = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        # No agent_message item — only reasoning.
        make_fake_codex_reasoning_item("internal scratchpad"),
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    ]
    return _FakeCodexPopen([json.dumps(m) for m in messages])


def make_fake_background_task_stream(
    text: str = "Waiting on editorial agent.",
    launches: int = 1,
    num_turns: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> _FakePopen:
    """Build a ``_FakePopen`` emitting a background-task non-completion stream.

    Models the GitHub #97 failure mode: the skill fires one or more
    ``Task(run_in_background=true)`` tool_use blocks, emits a final
    ``text`` block that references the background work (e.g.
    "Waiting on editorial agent."), and the ``result`` message carries
    a ``num_turns`` value consistent with "did not poll".

    - A single ``assistant`` message whose ``content`` contains
      ``launches`` ``tool_use`` blocks (``name="Task"``,
      ``input.run_in_background=True``) followed by one ``text``
      block carrying ``text``.
    - A final ``result`` message with ``is_error: False``,
      ``subtype: "success"``, the provided ``num_turns``, and the
      usual ``usage`` block.
    """
    content: list[dict] = []
    for i in range(launches):
        content.append(
            {
                "type": "tool_use",
                "id": f"toolu_bg_{i}",
                "name": "Task",
                "input": {
                    "description": f"background agent {i}",
                    "prompt": f"do work {i}",
                    "run_in_background": True,
                },
            }
        )
    content.append({"type": "text", "text": text})
    messages: list[dict] = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": content,
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": num_turns,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    ]
    return _FakePopen([json.dumps(m) for m in messages])


@pytest.fixture(autouse=True)
def _isolate_clauditor_history(tmp_path, monkeypatch):
    """Redirect history.jsonl writes to a per-test tmp dir so running the
    suite never writes ``.clauditor/history.jsonl`` in the real cwd.

    ``history.append_record`` / ``read_records`` resolve ``_DEFAULT_PATH``
    at call time, so monkeypatching the module attribute is sufficient.
    """
    from clauditor import history as _history

    monkeypatch.setattr(
        _history, "_DEFAULT_PATH", tmp_path / ".clauditor" / "history.jsonl"
    )


@pytest.fixture(autouse=True)
def _dummy_anthropic_api_key(monkeypatch):
    """Set a dummy ``ANTHROPIC_API_KEY`` for every test.

    #83 added a pre-flight ``check_anthropic_auth`` guard (relaxed in #86
    to ``check_any_auth_available``) that fires exit 2 whenever no usable
    auth is available. The vast majority of clauditor tests mock the
    Anthropic seam (``call_anthropic``) and do not hit the network —
    they never needed a real key, and historically ran in CI with
    ``ANTHROPIC_API_KEY`` unset. This autouse fixture sets a dummy value
    so the guard passes cleanly for those tests.

    Tests that specifically exercise the guard (``TestAuthGuardMissingKey``
    in ``tests/test_cli_auth_guard.py``, ``TestCheckAnyAuthAvailable``,
    ``TestCheckApiKeyOnly``, and ``TestCallAnthropicTypeError`` in
    ``tests/test_providers_anthropic.py``, ``TestClauditorFixturesAuthGuard``
    in ``tests/test_pytest_plugin.py``, and ``TestRegressionNoApiKey`` in
    ``tests/test_cli_auth_guard.py``) call
    ``monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)`` inside
    the test body — same ``monkeypatch`` instance as this fixture, so
    ``delenv`` cleanly removes what ``setenv`` just set.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key-for-ci")


@pytest.fixture(autouse=True)
def _clear_fixture_allow_cli(monkeypatch):
    """Ensure ``CLAUDITOR_FIXTURE_ALLOW_CLI`` is unset for every test.

    #86 DEC-009 / US-005: the three grading fixtures
    (``clauditor_grader``, ``clauditor_triggers``,
    ``clauditor_blind_compare``) default to the strict API-key-only
    guard unless ``CLAUDITOR_FIXTURE_ALLOW_CLI`` is set in the env. If
    a user has it exported in their shell, every fixture test would
    silently switch to the relaxed guard and mask a CI-config
    regression. This autouse fixture deletes it so every test starts
    from a deterministic baseline; tests that exercise the opt-in
    branch set it explicitly via ``monkeypatch.setenv`` inside the
    test body.
    """
    monkeypatch.delenv("CLAUDITOR_FIXTURE_ALLOW_CLI", raising=False)


@pytest.fixture(autouse=True)
def _force_api_transport_in_tests(monkeypatch):
    """Force ``call_anthropic(transport="auto")`` to resolve to API in tests.

    #86 US-003 added a CLI transport branch to ``call_anthropic`` that
    routes through ``claude -p`` when ``shutil.which("claude")``
    returns a path (DEC-001 subscription-first). On developer machines
    where ``claude`` is installed, the ``auto`` default would otherwise
    spawn a real subprocess during tests that mock only the SDK seam
    (``anthropic.AsyncAnthropic``), producing wildly different results
    and hanging the suite.

    This autouse fixture patches ``clauditor._anthropic.shutil.which``
    to return ``None`` so the ``auto`` branch deterministically resolves
    to API. Tests that exercise the CLI transport specifically
    (``TestCallViaClaudeCli``, ``TestAutoTransportResolution``,
    ``TestStderrAnnouncement`` in ``tests/test_providers_anthropic.py``)
    re-patch ``shutil.which`` inside the test body to override this
    default.
    """
    import clauditor._anthropic as _anthropic

    monkeypatch.setattr(
        _anthropic.shutil, "which", lambda name: None
    )


@pytest.fixture
def sample_eval_data() -> dict:
    """Return a dict matching eval.json format with all fields populated."""
    return {
        "skill_name": "find-kid-activities",
        "description": "Eval for /find-kid-activities",
        "test_args": '"Cupertino, CA" --dates today --cost Free --depth quick',
        "assertions": [
            {"type": "contains", "needle": "Venues"},
            {"type": "has_entries", "count": 3},
            {"type": "has_urls", "count": 2},
            {"type": "not_contains", "needle": "ERROR"},
            {"type": "min_length", "length": 500},
        ],
        "sections": [
            {
                "name": "Venues",
                "min_entries": 3,
                "fields": [
                    {"name": "name", "required": True},
                    {"name": "address", "required": True},
                    {"name": "hours", "required": True},
                    {"name": "website", "required": True},
                    {"name": "phone", "required": False},
                ],
            },
            {
                "name": "Events",
                "min_entries": 0,
                "fields": [
                    {"name": "name", "required": True},
                    {"name": "date", "required": True},
                    {"name": "event_url", "required": True},
                ],
            },
        ],
        "grading_criteria": [
            "Are venues within the specified distance?",
            "Are hours accurate for the requested dates?",
        ],
        "grading_model": "claude-sonnet-4-6",
        "trigger_tests": {
            "should_trigger": [
                "find kid activities in Cupertino",
                "things to do with kids near me",
            ],
            "should_not_trigger": [
                "what is the weather today",
                "write me a poem",
            ],
        },
        "variance": {
            "n_runs": 5,
            "min_stability": 0.8,
        },
    }


@pytest.fixture
def make_eval_spec():
    """Factory fixture that creates EvalSpec instances from optional overrides.

    Usage:
        def test_something(make_eval_spec):
            spec = make_eval_spec(skill_name="my-skill")
    """

    def _factory(system_prompt: str | None = None, **overrides) -> EvalSpec:
        defaults = {
            "skill_name": "test-skill",
            "description": "A test eval spec",
            "test_args": "--depth quick",
            "assertions": [{"type": "contains", "needle": "test"}],
            "sections": [
                SectionRequirement(
                    name="Results",
                    min_entries=1,
                    fields=[
                        FieldRequirement(name="name", required=True),
                        FieldRequirement(name="url", required=False),
                    ],
                )
            ],
            "grading_criteria": ["Is the output relevant?"],
            "grading_model": "claude-sonnet-4-6",
            "trigger_tests": None,
            "variance": None,
            "system_prompt": system_prompt,
        }
        defaults.update(overrides)
        return EvalSpec(**defaults)

    return _factory


@pytest.fixture
def tmp_skill_file(tmp_path):
    """Factory fixture that creates a temporary skill file.

    Supports two layouts (DEC-011 of ``plans/super/62-skill-md-layout.md``):

    - ``layout="legacy"`` (default): writes ``tmp_path/<name>.md``. The
      sibling eval lives at ``tmp_path/<name>.eval.json``. Byte-identical
      to the pre-DEC-011 behavior so every existing test keeps working.
    - ``layout="modern"``: writes
      ``tmp_path/.claude/skills/<name>/SKILL.md``. The sibling eval lives
      at ``tmp_path/.claude/skills/<name>/SKILL.eval.json`` — next to the
      SKILL.md, which is what :func:`SkillSpec.from_file` auto-discovers.

    Usage:
        def test_something(tmp_skill_file):
            skill_path = tmp_skill_file("my-skill", content="# My Skill")
            skill_path, eval_path = tmp_skill_file(
                "my-skill",
                content="# My Skill",
                eval_data={"skill_name": "my-skill", "assertions": []},
            )
            # Modern layout:
            skill_path = tmp_skill_file("foo", layout="modern")
    """

    def _factory(
        name: str = "test-skill",
        content: str = "# Test Skill\n\nA test skill for unit tests.",
        layout: str = "legacy",
        eval_data: dict | None = None,
    ) -> Path | tuple[Path, Path]:
        if layout == "legacy":
            skill_path = tmp_path / f"{name}.md"
        elif layout == "modern":
            skill_dir = tmp_path / ".claude" / "skills" / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "SKILL.md"
        else:
            raise ValueError(
                f"tmp_skill_file: layout must be 'legacy' or 'modern', "
                f"got {layout!r}"
            )

        skill_path.write_text(content)

        if eval_data is not None:
            eval_path = skill_path.with_suffix(".eval.json")
            eval_path.write_text(json.dumps(eval_data, indent=2))
            return skill_path, eval_path

        return skill_path

    return _factory


@pytest.fixture
def mock_runner():
    """Factory fixture returning a MagicMock SkillRunner.

    The mock's .run() returns a configurable SkillResult.

    Usage:
        def test_something(mock_runner):
            runner = mock_runner(output="some output", exit_code=0)
            result = runner.run("my-skill")
            assert result.output == "some output"
    """

    def _factory(
        output: str = "mock output",
        exit_code: int = 0,
        skill_name: str = "test-skill",
        args: str = "",
        duration_seconds: float = 1.0,
        error: str | None = None,
    ) -> MagicMock:
        mock = MagicMock(spec=SkillRunner)
        mock.project_dir = Path.cwd()
        result = SkillResult(
            output=output,
            exit_code=exit_code,
            skill_name=skill_name,
            args=args,
            duration_seconds=duration_seconds,
            error=error,
        )
        mock.run.return_value = result
        mock.run_raw.return_value = result
        return mock

    return _factory


# ---------------------------------------------------------------------------
# Factories used by the CLI tests. These live as module-level helpers (not
# fixtures) so they can be imported directly into tests/test_cli.py and used
# inside @pytest.mark.parametrize data and class helpers.
# ---------------------------------------------------------------------------


def make_skill_result(
    *,
    output: str = "mock output",
    exit_code: int = 0,
    skill_name: str = "test-skill",
    args: str = "",
    duration_seconds: float = 1.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    error: str | None = None,
    stream_events: list[dict] | None = None,
) -> SkillResult:
    """Build a real SkillResult with sensible defaults.

    Prefer this over MagicMock for tests that only need a value object;
    keeping it a real dataclass means attribute typos fail loudly.
    """
    return SkillResult(
        output=output,
        exit_code=exit_code,
        skill_name=skill_name,
        args=args,
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=error,
        stream_events=stream_events if stream_events is not None else [],
    )


def build_eval_spec(
    system_prompt: str | None = None, **overrides
) -> EvalSpec:
    """Minimal EvalSpec with sensible defaults for CLI tests.

    Accepts any EvalSpec field as keyword overrides.
    """
    defaults = dict(
        skill_name="test-skill",
        description="A test skill",
        test_args="--depth quick",
        assertions=[{"type": "contains", "needle": "hello"}],
        sections=[],
        grading_criteria=["Is the output relevant?"],
        grading_model="claude-sonnet-4-6",
        trigger_tests=None,
        variance=None,
        system_prompt=system_prompt,
    )
    defaults.update(overrides)
    return EvalSpec(**defaults)


def make_spec(eval_spec=None, skill_name: str = "test-skill") -> MagicMock:
    """Build a MagicMock SkillSpec carrying an optional EvalSpec."""
    spec = MagicMock(spec=SkillSpec)
    spec.skill_name = skill_name
    spec.eval_spec = eval_spec
    return spec


def make_grading_report(
    *,
    skill_name: str = "test-skill",
    passed: bool = True,
    score: float | None = None,
    criterion: str = "Is the output relevant?",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_seconds: float = 1.0,
    thresholds: GradeThresholds | None = None,
    extra_results: list[GradingResult] | None = None,
) -> GradingReport:
    """Build a GradingReport with one criterion result (extra_results appended)."""
    actual_score = score if score is not None else (0.9 if passed else 0.3)
    results: list[GradingResult] = [
        GradingResult(
            criterion=criterion,
            passed=passed,
            score=actual_score,
            evidence="Found relevant content",
            reasoning="Output addresses the query",
        )
    ]
    if extra_results:
        results.extend(extra_results)
    return GradingReport(
        skill_name=skill_name,
        model=model,
        results=results,
        duration_seconds=duration_seconds,
        thresholds=thresholds if thresholds is not None else GradeThresholds(),
        metrics={},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def assert_iteration_dir(
    skill_dir: Path,
    *,
    has_grading: bool = True,
    has_assertions: bool = False,
    has_extraction: bool = False,
    has_timing: bool = True,
    has_run0: bool = True,
    n_runs: int | None = None,
) -> None:
    """Assert the structure of a post-grade iteration directory.

    ``skill_dir`` is ``.clauditor/iteration-N/<skill>/``. Pass
    ``n_runs`` to assert exactly N run-K subdirs are present (and no
    extras). Otherwise ``has_run0`` is the weaker "run-0/ exists"
    check.
    """
    assert skill_dir.is_dir(), f"{skill_dir} is not a directory"
    if has_grading:
        assert (skill_dir / "grading.json").is_file(), "missing grading.json"
    if has_assertions:
        assert (skill_dir / "assertions.json").is_file(), (
            "missing assertions.json"
        )
    if has_extraction:
        assert (skill_dir / "extraction.json").is_file(), (
            "missing extraction.json"
        )
    if has_timing:
        assert (skill_dir / "timing.json").is_file(), "missing timing.json"
    if n_runs is not None:
        present = sorted(
            p.name for p in skill_dir.iterdir() if p.name.startswith("run-")
        )
        expected = [f"run-{i}" for i in range(n_runs)]
        assert present == expected, (
            f"expected run-dirs {expected!r}, got {present!r}"
        )
        for i in range(n_runs):
            assert (skill_dir / f"run-{i}" / "output.txt").is_file()
            assert (skill_dir / f"run-{i}" / "output.jsonl").is_file()
    elif has_run0:
        assert (skill_dir / "run-0").is_dir(), "missing run-0/"
