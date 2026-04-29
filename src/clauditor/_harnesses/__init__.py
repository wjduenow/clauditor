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

__all__ = ["Harness", "InvokeResult"]


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
