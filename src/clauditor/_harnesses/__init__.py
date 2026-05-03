"""Private ``Harness`` protocol package.

Defines the structural contract every harness implementation
(Claude Code today, Codex tomorrow per #149) must satisfy. The
protocol stays in ``__init__.py`` so the public import is a single
``from clauditor._harnesses import Harness``; concrete harness
implementations land in sibling modules in later stories of issue
#148.

Per DEC-008 of ``plans/super/148-extract-harness-protocol.md``,
``allow_hang_heuristic`` is intentionally NOT on
:meth:`Harness.invoke` — it is a Claude-Code-specific knob that lives
on ``ClaudeCodeHarness.__init__`` (US-004), not the cross-harness
protocol surface.

Per DEC-007, :class:`~clauditor.runner.InvokeResult` carries an
additive ``harness_metadata: dict[str, Any]`` field so future
harnesses can surface harness-specific observability without forcing
a sidecar-schema breaking change on existing call sites.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from clauditor.runner import InvokeResult

__all__ = ["Harness", "InvokeResult", "construct_harness"]


@runtime_checkable
class Harness(Protocol):
    """Structural contract for a Claude-Code-compatible LLM CLI harness.

    Implementations are duck-typed: any class providing the three
    members below satisfies the protocol. Decorated with
    ``@runtime_checkable`` so ``isinstance(obj, Harness)`` is a real
    drift-guard (catches forgotten signature updates that a static
    type-hint check would miss). Note that ``runtime_checkable``
    Protocols only verify member presence by name, not signature shape;
    sibling unit tests use ``inspect.signature`` to lock the parameter
    set when stricter conformance is required.
    """

    name: ClassVar[str]

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
        """Run the harness CLI with ``prompt`` and return an :class:`InvokeResult`.

        Implementations own subprocess invocation, output parsing, and
        any harness-specific observability they want to surface via
        :attr:`InvokeResult.harness_metadata`.

        ``subject`` is an optional human-readable label (e.g.
        ``"L2 extraction"``) that harnesses MAY use to enrich
        observability output (logs, warning suffixes); harnesses that
        do not consume it should still accept and ignore the kwarg.
        """
        ...

    def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]:
        """Return a new env dict with harness-specific auth env vars removed.

        Pure, non-mutating per ``.claude/rules/non-mutating-scrub.md``.
        Each harness knows which env vars carry its own credentials
        (e.g. ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` for
        Claude Code) and scrubs them; non-auth env vars are preserved.
        """
        ...

    def build_prompt(
        self,
        skill_name: str,
        args: str,
        *,
        system_prompt: str | None,
    ) -> str:
        """Compose the prompt string this harness's ``invoke`` expects.

        Pure compute (no I/O, no global state) per
        ``.claude/rules/pure-compute-vs-io-split.md``. Each harness owns
        the wire-shape for how a skill invocation is rendered into a
        single prompt string: Claude Code uses slash-style commands
        (``"/foo bar"``) understood by the ``claude -p`` CLI; future
        raw-API harnesses may instead embed ``args`` plus an explicit
        ``system_prompt`` in a structured message body.

        ``system_prompt`` is keyword-only (per US-001 of issue #150) so
        callers cannot accidentally swap it positionally with ``args``.
        Harnesses that have no notion of a separate system prompt (e.g.
        ``ClaudeCodeHarness``) MUST still accept and ignore the kwarg —
        analogous to how all harnesses accept ``model`` on ``invoke``.
        """
        ...


def construct_harness(name: str) -> Harness:
    """Construct a :class:`Harness` instance from its literal name.

    DEC-009 / DEC-012 of ``plans/super/151-harness-precedence.md``.
    Thin dispatcher mirroring the shape of
    :func:`clauditor._providers.call_model` (the provider-axis
    dispatcher in ``_providers/__init__.py``). The pure resolver
    :func:`clauditor._providers.resolve_harness` returns a literal
    name; this helper turns the name into the concrete harness
    instance.

    Uses **deferred per-call imports** for ``_claude_code`` and
    ``_codex`` per ``.claude/rules/back-compat-shim-discipline.md``
    Pattern 3. Both submodules import ``Harness`` and
    ``InvokeResult`` from this ``__init__.py`` at protocol-class
    load time, so an eager top-level ``from clauditor._harnesses
    import _claude_code`` here would circular-import. Deferring
    inside the function body breaks the cycle without sacrificing
    test-patchability — patches that target
    ``clauditor._harnesses._claude_code.ClaudeCodeHarness`` (or the
    codex sibling) still take effect because the lookup happens at
    call time against the module object.

    The dispatcher rejects ``"auto"`` explicitly: callers must
    resolve auto via :func:`~clauditor._providers.resolve_harness`
    before constructing. Letting ``"auto"`` through here would
    couple the harness package to PATH lookup, which belongs in
    the resolver layer (one seam per concern).

    Args:
        name: One of ``"claude-code"`` or ``"codex"``. ``"auto"``
            and unknown values raise.

    Returns:
        A concrete :class:`Harness` instance with default
        construction kwargs (``claude_bin="claude"`` /
        ``codex_bin="codex"``, ``model=None``,
        ``allow_hang_heuristic=True`` for Claude Code).

    Raises:
        ValueError: ``name`` is ``"auto"`` (not yet resolved) or
            any unknown literal.
    """
    if name == "claude-code":
        # Deferred import: ``_claude_code`` imports ``Harness`` and
        # ``InvokeResult`` from this module at load time, so an
        # eager top-level import here would circular-import.
        from clauditor._harnesses import _claude_code as _claude_code_mod

        return _claude_code_mod.ClaudeCodeHarness()
    if name == "codex":
        # Same deferred-import shape.
        from clauditor._harnesses import _codex as _codex_mod

        return _codex_mod.CodexHarness()
    if name == "auto":
        raise ValueError(
            "construct_harness: 'auto' must be resolved before "
            "construction — call clauditor._providers.resolve_harness "
            "first to pick a concrete harness name."
        )
    raise ValueError(
        f"construct_harness: unknown harness {name!r} — "
        "expected 'claude-code' or 'codex'"
    )
