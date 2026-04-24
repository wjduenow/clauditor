"""Tests for pure helper functions in ``clauditor.cli``.

These helpers are pure (no I/O other than ``os.environ`` reads, no
stderr, no side effects) and therefore easy to unit-test in isolation
without argparse/subparser plumbing. See
``.claude/rules/pure-compute-vs-io-split.md``.
"""

from __future__ import annotations

import argparse


class TestShouldStripApiKeyForSkillSubprocess:
    """Regression tests for ``should_strip_api_key_for_skill_subprocess``.

    Traces to DEC-001, DEC-002, DEC-006 of
    ``plans/super/95-subscription-auth-flag.md``. The helper returns True
    iff the operator explicitly selected CLI transport — either via the
    ``--transport cli`` flag or the ``CLAUDITOR_TRANSPORT=cli`` env var.
    Author-intent (``EvalSpec.transport``) and auto-resolution are NOT
    operator-intent signals and must NOT trigger the strip.
    """

    def test_cli_flag_no_env_returns_true(self, monkeypatch):
        """``--transport cli`` with no env var → True."""
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport="cli")
        assert should_strip_api_key_for_skill_subprocess(args) is True

    def test_env_cli_no_flag_returns_true(self, monkeypatch):
        """No ``args.transport`` + ``CLAUDITOR_TRANSPORT=cli`` → True."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "cli")
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport=None)
        assert should_strip_api_key_for_skill_subprocess(args) is True

    def test_cli_flag_wins_over_env_api(self, monkeypatch):
        """``--transport cli`` + ``CLAUDITOR_TRANSPORT=api`` → True.

        CLI flag wins — operator-intent precedence. Either operator-intent
        source saying ``cli`` is enough to trigger the strip.
        """
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "api")
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport="cli")
        assert should_strip_api_key_for_skill_subprocess(args) is True

    def test_transport_api_returns_false(self, monkeypatch):
        """``--transport api`` + no env var → False."""
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport="api")
        assert should_strip_api_key_for_skill_subprocess(args) is False

    def test_transport_none_returns_false(self, monkeypatch):
        """``args.transport is None`` + no env var → False."""
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport=None)
        assert should_strip_api_key_for_skill_subprocess(args) is False

    def test_transport_auto_returns_false(self, monkeypatch):
        """``--transport auto`` + no env var → False.

        Per DEC-002: auto does NOT trigger the strip even when it
        resolves to CLI at runtime. This helper inspects operator-intent
        layers only; it does NOT consult ``shutil.which("claude")`` or
        otherwise resolve auto. Stripping keys on any machine with
        ``claude`` on PATH would surprise users who keep an API key for
        production use.
        """
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport="auto")
        assert should_strip_api_key_for_skill_subprocess(args) is False

    def test_whitespace_env_cli_returns_false(self, monkeypatch):
        """``CLAUDITOR_TRANSPORT='  cli  '`` → False (exact match only).

        The env-var value is NOT whitespace-normalized here: it must be
        exactly ``"cli"``. A whitespace-padded value is rejected
        downstream by :func:`resolve_transport` with ``SystemExit(2)``;
        treating it as ``"cli"`` in this helper would silently strip
        the skill-subprocess key right before the grader call exits,
        which is worse UX than a single clear error. Addresses PR #96
        Copilot feedback (drift between helper and
        ``_resolve_grader_transport`` / ``resolve_transport``).
        """
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "  cli  ")
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport=None)
        assert should_strip_api_key_for_skill_subprocess(args) is False

    def test_empty_env_returns_false(self, monkeypatch):
        """``CLAUDITOR_TRANSPORT=''`` (empty) + no flag → False."""
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "")
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace(transport=None)
        assert should_strip_api_key_for_skill_subprocess(args) is False

    def test_args_without_transport_attr_returns_false(self, monkeypatch):
        """``args`` missing ``transport`` attribute entirely → False.

        Uses ``getattr(args, "transport", None)`` under the hood; must
        not raise ``AttributeError`` when called from a code path where
        the argparse subparser does not register ``--transport`` (e.g.
        a future caller outside the six LLM-mediated commands).
        """
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)
        from clauditor.cli import should_strip_api_key_for_skill_subprocess

        args = argparse.Namespace()  # no transport attribute at all
        assert should_strip_api_key_for_skill_subprocess(args) is False
