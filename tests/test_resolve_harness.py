"""Tests for #151 US-002 pure helper :func:`resolve_harness`.

Covers DEC-001 / DEC-002 / DEC-009 / DEC-011 of
``plans/super/151-harness-precedence.md``: four-layer precedence
(CLI > env > spec > default ``"auto"``), PATH-based auto resolution
preferring ``claude-code`` over ``codex``, ``auto_resolved_to``
flag for the CLI wrapper's announcement, and structurally-routed
``ValueError`` on per-layer invalid values plus the no-binary-on-PATH
case.

Pure helper per ``.claude/rules/pure-compute-vs-io-split.md`` — only
``shutil.which`` is touched (PATH lookup), and tests pin it via
``monkeypatch.setattr`` on ``clauditor._providers.shutil``.
"""

from __future__ import annotations

import pytest

import clauditor._providers as _providers_mod
from clauditor._providers import resolve_harness


def _patch_which(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude: str | None,
    codex: str | None,
) -> None:
    """Pin ``shutil.which`` results for ``"claude"`` and ``"codex"``.

    The pure :func:`resolve_harness` calls ``shutil.which`` from the
    ``clauditor._providers`` module's import binding; patching there
    is the canonical seam.
    """

    def _fake_which(name: str) -> str | None:
        if name == "claude":
            return claude
        if name == "codex":
            return codex
        return None

    monkeypatch.setattr(_providers_mod.shutil, "which", _fake_which)


class TestPrecedence:
    """Four-layer precedence: CLI > env > spec > default auto."""

    def test_cli_explicit_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI=codex beats env=claude-code beats spec=auto."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex="/usr/bin/codex")
        name, auto_to = resolve_harness(
            cli_override="codex",
            env_override="claude-code",
            spec_value="auto",
        )
        assert name == "codex"
        assert auto_to is None

    def test_env_falls_through_when_cli_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI=None → env=codex wins over spec/default."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex="/usr/bin/codex")
        name, auto_to = resolve_harness(
            cli_override=None,
            env_override="codex",
            spec_value="claude-code",
        )
        assert name == "codex"
        assert auto_to is None

    def test_spec_falls_through_when_cli_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI=None, env=None → spec=codex wins."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex="/usr/bin/codex")
        name, auto_to = resolve_harness(
            cli_override=None,
            env_override=None,
            spec_value="codex",
        )
        assert name == "codex"
        assert auto_to is None

    def test_default_auto_when_all_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All layers unset → auto branch with claude on PATH wins."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        name, auto_to = resolve_harness(
            cli_override=None,
            env_override=None,
            spec_value=None,
        )
        assert name == "claude-code"
        assert auto_to is None

    def test_cli_auto_falls_through_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit CLI=auto is treated as no preference at this layer."""
        _patch_which(monkeypatch, claude=None, codex="/usr/bin/codex")
        name, auto_to = resolve_harness(
            cli_override="auto",
            env_override="codex",
            spec_value=None,
        )
        assert name == "codex"
        assert auto_to is None

    def test_spec_auto_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spec='auto' default falls through to PATH-based auto branch."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        name, auto_to = resolve_harness(
            cli_override=None,
            env_override=None,
            spec_value="auto",
        )
        assert name == "claude-code"
        assert auto_to is None

    def test_whitespace_only_env_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whitespace-only env_override falls through (defense-in-depth)."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        name, auto_to = resolve_harness(
            cli_override=None,
            env_override="   ",
            spec_value=None,
        )
        assert name == "claude-code"
        assert auto_to is None


class TestAutoBranch:
    """PATH-based auto resolution: claude first, codex fallback."""

    def test_auto_picks_claude_code_when_claude_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``claude`` on PATH → claude-code wins (no announcement)."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex="/usr/bin/codex")
        name, auto_to = resolve_harness(None, None, None)
        assert name == "claude-code"
        # auto_resolved_to is None when claude wins — no announcement.
        assert auto_to is None

    def test_auto_picks_codex_when_only_codex_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only codex available → returns auto_resolved_to='codex'."""
        _patch_which(monkeypatch, claude=None, codex="/usr/bin/codex")
        name, auto_to = resolve_harness(None, None, None)
        assert name == "codex"
        # Flag set so the CLI wrapper fires announce_auto_codex_harness.
        assert auto_to == "codex"

    def test_auto_raises_when_neither_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Neither binary → ValueError naming all three escape hatches."""
        _patch_which(monkeypatch, claude=None, codex=None)
        with pytest.raises(ValueError) as exc_info:
            resolve_harness(None, None, None)
        msg = str(exc_info.value)
        # Must name all three escape hatches per DEC-002.
        assert "--harness" in msg
        assert "CLAUDITOR_HARNESS" in msg
        assert "eval.json" in msg or "'harness'" in msg
        # Acceptable name set named in the message.
        assert "claude-code" in msg
        assert "codex" in msg


class TestInvalidValues:
    """Per-layer invalid-value rejection (DEC-008 literal-set membership)."""

    def test_invalid_cli_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        with pytest.raises(ValueError) as exc_info:
            resolve_harness("unknown", None, None)
        msg = str(exc_info.value)
        assert "CLI --harness" in msg
        assert "claude-code" in msg
        assert "codex" in msg
        assert "auto" in msg
        assert "'unknown'" in msg

    def test_invalid_env_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        with pytest.raises(ValueError) as exc_info:
            resolve_harness(None, "ClaudeCode", None)
        msg = str(exc_info.value)
        assert "CLAUDITOR_HARNESS" in msg
        assert "'ClaudeCode'" in msg

    def test_invalid_spec_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        with pytest.raises(ValueError) as exc_info:
            resolve_harness(None, None, "wat")
        msg = str(exc_info.value)
        assert "EvalSpec.harness" in msg
        assert "'wat'" in msg

    def test_case_sensitive_rejection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case variants are rejected loudly (Minor note D)."""
        _patch_which(monkeypatch, claude="/usr/bin/claude", codex=None)
        with pytest.raises(ValueError):
            resolve_harness("CodeX", None, None)
