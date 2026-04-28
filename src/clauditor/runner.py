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

import os
import warnings as _warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from clauditor._harnesses import Harness

# Documented Anthropic env var that forces ``Task(run_in_background=true)``
# spawns to run synchronously (see
# https://docs.claude.com/en/docs/claude-code/sub-agents — "Run
# subagents in foreground or background"). Setting this to ``"1"`` in
# the subprocess env makes the parent agent wait for each sub-agent
# before emitting its ``result`` message. Tier 1.5 workaround for
# GitHub #103 (see ``docs/adr/transport-research-103.md``).
_SYNC_TASKS_ENV_VAR = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"


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
    # DEC-007 of issue #148: additive forward-compat surface for
    # harness-specific observability. ``ClaudeCodeHarness`` leaves
    # this empty; ``CodexHarness`` (per #149) and a future raw-API
    # harness use it to surface their native message-shape data
    # (Codex ``reasoning`` items, raw-API reasoning tokens, etc.)
    # without forcing a sidecar-schema breaking change.
    harness_metadata: dict[str, Any] = field(default_factory=dict)

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

    Pure data container emitted by
    :meth:`clauditor._harnesses._claude_code.ClaudeCodeHarness.invoke` —
    the subprocess + stream-json parse primitive that both
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

    ``harness_metadata`` is the additive forward-compat surface
    introduced by DEC-007 of issue #148: each harness uses it to
    surface its native transport-specific shape (Codex's ``reasoning``
    items, a raw-API harness's reasoning tokens, etc.) without forcing
    a sidecar-schema breaking change on existing call sites. Today,
    :class:`ClaudeCodeHarness` leaves it empty.
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


class SkillRunner:
    """Executes Claude Code skills via the CLI and captures output."""

    def __init__(
        self,
        project_dir: str | Path | None = None,
        timeout: int = 300,
        *,
        claude_bin: str = "claude",
        harness: Harness | None = None,
    ):
        # Deferred import: ``ClaudeCodeHarness`` lives in
        # ``_harnesses/_claude_code.py`` which itself imports from this
        # module (``InvokeResult`` + warning prefixes). Keep the import
        # local so the package init order stays well-defined.
        from clauditor._harnesses._claude_code import ClaudeCodeHarness

        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.timeout = timeout

        # DEC-002 / Q2 → C: callers can either pass a custom ``harness``
        # (e.g. tests or future Codex per #149) or rely on the default
        # ``ClaudeCodeHarness`` constructed from the legacy
        # ``claude_bin`` kwarg. The legacy kwarg + an explicit harness
        # is a soft deprecation: emit a ``DeprecationWarning`` and
        # honour the harness, since the harness owns the binary path.
        if harness is not None:
            if claude_bin != "claude":
                _warnings.warn(
                    "Pass claude_bin via ClaudeCodeHarness(claude_bin=...) "
                    "instead; SkillRunner.claude_bin will be removed in a "
                    "future release.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            self.harness = harness
        else:
            self.harness = ClaudeCodeHarness(claude_bin=claude_bin)

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
        """Thin wrapper around :meth:`Harness.invoke`.

        Resolves the per-call ``timeout`` sentinel against
        ``self.timeout`` (DEC-010), defaults ``cwd`` to
        ``self.project_dir`` when unset, delegates to ``self.harness``
        (US-004 of ``plans/super/148-extract-harness-protocol.md`` —
        replaces the prior CLI-helper call), and projects
        the returned :class:`InvokeResult` onto :class:`SkillResult`
        by copying every field and adding the caller-owned
        ``skill_name`` / ``args`` context.

        ``env`` is forwarded verbatim: ``None`` means "inherit
        ``os.environ``" (Popen's default); a dict replaces the
        child's environment entirely (DEC-013 of
        ``plans/super/64-runner-auth-timeout.md``).

        ``allow_hang_heuristic`` is configured at harness-construction
        time per DEC-008 of issue #148, so the per-call kwarg is now
        a no-op for the default :class:`ClaudeCodeHarness` path.
        Existing callers preserve the kwarg shape; future harnesses
        ignore it. The kwarg is retained on the :class:`SkillRunner`
        public API for source-compatibility with the
        ``EvalSpec.allow_hang_heuristic`` thread-through.
        """
        effective_timeout = timeout if timeout is not None else self.timeout
        effective_cwd = cwd if cwd is not None else self.project_dir
        # ``allow_hang_heuristic`` is a Claude-Code-specific knob now
        # owned at harness construction (DEC-008). Per-call overrides
        # threaded from ``EvalSpec.allow_hang_heuristic`` are honoured
        # by temporarily flipping the attribute on a
        # ``ClaudeCodeHarness`` and restoring it after the call.
        # Non-Claude-Code harnesses simply ignore the per-call value.
        original_hang = getattr(self.harness, "allow_hang_heuristic", None)
        toggled = original_hang is not None and original_hang != allow_hang_heuristic
        if toggled:
            self.harness.allow_hang_heuristic = allow_hang_heuristic
        try:
            invoke = self.harness.invoke(
                prompt,
                cwd=effective_cwd,
                env=env,
                timeout=effective_timeout,
            )
        finally:
            if toggled:
                self.harness.allow_hang_heuristic = original_hang
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
            harness_metadata=invoke.harness_metadata,
        )
