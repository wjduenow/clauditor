"""Tests for #151 US-002 :func:`clauditor._harnesses.construct_harness`.

Covers DEC-009 / DEC-012 of ``plans/super/151-harness-precedence.md``:
the literal-name dispatcher with deferred per-call imports of
``_claude_code`` and ``_codex`` per
``.claude/rules/back-compat-shim-discipline.md`` Pattern 3. Pure
helper — construction only, no I/O.

``isinstance`` checks resolve ``ClaudeCodeHarness`` /
``CodexHarness`` via the module attribute at test-method invocation
time, NOT via top-of-file ``from`` imports. ``tests/test_runner.py``
and ``tests/test_codex_harness.py`` ``importlib.reload`` the
``_claude_code`` / ``_codex`` submodules during their own collection;
a top-of-file ``from clauditor._harnesses._claude_code import
ClaudeCodeHarness`` here would bind a stale pre-reload class object
that ``construct_harness``'s deferred import no longer resolves to.
Looking up ``_claude_code_mod.ClaudeCodeHarness`` at test time
re-resolves through the live module attribute, matching the canonical
class ``construct_harness`` returns.
"""

from __future__ import annotations

import pytest

import clauditor._harnesses._claude_code as _claude_code_mod
import clauditor._harnesses._codex as _codex_mod
from clauditor._harnesses import Harness, construct_harness


class TestConstructHarness:
    def test_construct_claude_code_returns_claude_code_harness(self) -> None:
        harness = construct_harness("claude-code")
        # Re-resolve through the module attribute at call time — see the
        # module docstring for why the top-of-file ``from`` import would
        # bind a stale class object after sibling-test reloads.
        assert isinstance(harness, _claude_code_mod.ClaudeCodeHarness)
        # Protocol drift-guard: still satisfies the structural Harness.
        assert isinstance(harness, Harness)
        assert harness.name == "claude-code"

    def test_construct_codex_returns_codex_harness(self) -> None:
        harness = construct_harness("codex")
        assert isinstance(harness, _codex_mod.CodexHarness)
        assert isinstance(harness, Harness)
        assert harness.name == "codex"

    def test_construct_auto_raises(self) -> None:
        """``"auto"`` must be resolved before construction (DEC-009)."""
        with pytest.raises(ValueError) as exc_info:
            construct_harness("auto")
        msg = str(exc_info.value)
        # Message points the caller at the resolver layer.
        assert "auto" in msg
        assert "resolve_harness" in msg

    def test_construct_unknown_raises(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            construct_harness("unknown-harness")
        msg = str(exc_info.value)
        assert "'unknown-harness'" in msg
        # Names the acceptable set so the error message is actionable.
        assert "claude-code" in msg
        assert "codex" in msg

    def test_construct_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            construct_harness("")

    def test_construct_case_sensitive(self) -> None:
        """Case variants rejected — mirrors resolver's literal-set."""
        with pytest.raises(ValueError):
            construct_harness("Claude-Code")

    def test_returns_default_constructed_instances(self) -> None:
        """Default kwargs: ``claude_bin="claude"`` / ``codex_bin="codex"``."""
        cc = construct_harness("claude-code")
        assert isinstance(cc, _claude_code_mod.ClaudeCodeHarness)
        assert cc.claude_bin == "claude"
        assert cc.model is None
        assert cc.allow_hang_heuristic is True

        cx = construct_harness("codex")
        assert isinstance(cx, _codex_mod.CodexHarness)
        assert cx.codex_bin == "codex"
        assert cx.model is None
