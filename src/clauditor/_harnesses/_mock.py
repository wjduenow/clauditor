"""Test-only :class:`MockHarness` that satisfies the ``Harness`` protocol.

Records every :meth:`invoke` call into ``invoke_calls`` and returns the
:class:`~clauditor.runner.InvokeResult` configured at construction time
so unit tests can drive :class:`~clauditor.runner.SkillRunner` without
spawning a ``claude`` subprocess. Lives in the private ``_harnesses``
package alongside :class:`ClaudeCodeHarness` because it is a harness
implementation, not a pytest fixture — tests import it directly per
US-005 of ``plans/super/148-extract-harness-protocol.md``.

Per DEC-008 the cross-harness :class:`Harness.invoke` protocol does NOT
carry ``allow_hang_heuristic``; ``MockHarness`` mirrors the protocol
signature exactly (``prompt``, ``cwd``, ``env``, ``timeout``, ``model``)
and intentionally omits any Claude-Code-specific kwargs. The
``strip_auth_keys`` helper returns a verbatim copy of ``env`` because a
mock harness has no auth env vars of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from clauditor.runner import InvokeResult


@dataclass
class MockHarness:
    """Test helper that satisfies the ``Harness`` protocol.

    Records every :meth:`invoke` call into ``invoke_calls`` and returns
    the :class:`InvokeResult` instance the caller configured at
    construction. The default ``result`` is a minimal valid
    :class:`InvokeResult` (empty output, ``exit_code=0``, zero
    duration); pre-configure a non-trivial ``result`` to observe the
    field-copy projection in :meth:`SkillRunner._invoke`.
    """

    name: ClassVar[str] = "mock"
    result: InvokeResult = field(
        default_factory=lambda: InvokeResult(
            output="", exit_code=0, duration_seconds=0.0
        )
    )
    invoke_calls: list[dict[str, Any]] = field(default_factory=list)

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
        """Record the call and return the pre-configured ``result``."""
        self.invoke_calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "env": env,
                "timeout": timeout,
                "model": model,
                "subject": subject,
            }
        )
        return self.result

    def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]:
        """Return a verbatim copy of ``env``.

        ``MockHarness`` has no auth env vars of its own, so the
        ``Harness`` contract is satisfied with a non-mutating identity
        copy per ``.claude/rules/non-mutating-scrub.md``.
        """
        return dict(env)
