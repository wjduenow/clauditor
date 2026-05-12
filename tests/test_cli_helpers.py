"""Tests for pure helper functions in ``clauditor.cli``.

These helpers are pure (no I/O other than ``os.environ`` reads, no
stderr, no side effects) and therefore easy to unit-test in isolation
without argparse/subparser plumbing. See
``.claude/rules/pure-compute-vs-io-split.md``.
"""

from __future__ import annotations

import argparse

import pytest


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


class TestHarnessChoice:
    """Tests for the ``_harness_choice`` argparse type validator.

    Sibling to :class:`TestTransportChoice` patterns in ``test_cli.py``.
    Traces to DEC-002 / DEC-011 of
    ``plans/super/151-harness-precedence.md``.
    """

    @pytest.mark.parametrize("value", ["claude-code", "codex", "auto"])
    def test_accepts_each_valid_literal(self, value):
        """Each of the three valid literals returns unchanged."""
        from clauditor.cli import _harness_choice

        assert _harness_choice(value) == value

    @pytest.mark.parametrize(
        "value", ["claude", "openai", "", "Claude-Code", " codex", "anthropic"]
    )
    def test_rejects_invalid_value(self, value):
        """Anything outside the three-literal set raises
        ``ArgumentTypeError`` with a "must be one of" message.
        argparse maps this to exit 2 at parse time.
        """
        from clauditor.cli import _harness_choice

        with pytest.raises(
            argparse.ArgumentTypeError, match="must be one of"
        ):
            _harness_choice(value)


class TestResolveHarness:
    """Tests for the ``_resolve_harness`` four-layer precedence wrapper.

    Sibling to :class:`TestResolveGraderTransport` in ``test_cli.py``.
    Patches the canonical seam ``clauditor._providers.resolve_harness``
    and the announcement helper
    ``clauditor._providers._auth.announce_auto_codex_harness`` per
    ``.claude/rules/back-compat-shim-discipline.md`` Pattern 3.

    Traces to DEC-002 / DEC-006 / DEC-007 / DEC-011 of
    ``plans/super/151-harness-precedence.md``.
    """

    def test_cli_value_passed_through(self, monkeypatch):
        """``args.harness`` is forwarded to the pure resolver as
        ``cli_override``. This is an integration sanity check —
        the pure resolver decides precedence; the wrapper just
        plumbs through."""
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)
        captured: dict = {}

        def fake_resolve(cli, env, spec):
            captured["cli"] = cli
            captured["env"] = env
            captured["spec"] = spec
            return ("codex", None)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness="codex")
        result = _resolve_harness(args, None)
        assert result == "codex"
        assert captured["cli"] == "codex"
        assert captured["env"] is None
        assert captured["spec"] is None

    def test_env_value_read_when_cli_unset(self, monkeypatch):
        """When ``args.harness`` is None, env is forwarded to the
        pure resolver as ``env_override``."""
        monkeypatch.setenv("CLAUDITOR_HARNESS", "codex")
        captured: dict = {}

        def fake_resolve(cli, env, spec):
            captured["cli"] = cli
            captured["env"] = env
            captured["spec"] = spec
            return ("codex", None)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness=None)
        result = _resolve_harness(args, None)
        assert result == "codex"
        assert captured["cli"] is None
        assert captured["env"] == "codex"

    def test_whitespace_only_env_normalizes_to_none(self, monkeypatch):
        """``CLAUDITOR_HARNESS='   '`` is treated as unset (passed as
        ``None`` to the pure resolver)."""
        monkeypatch.setenv("CLAUDITOR_HARNESS", "   ")
        captured: dict = {}

        def fake_resolve(cli, env, spec):
            captured["env"] = env
            return ("claude-code", None)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness=None)
        _resolve_harness(args, None)
        assert captured["env"] is None

    def test_spec_value_read_when_cli_and_env_unset(self, monkeypatch):
        """When CLI + env are both unset, ``eval_spec.harness`` is
        forwarded as ``spec_value``."""
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)
        captured: dict = {}

        def fake_resolve(cli, env, spec):
            captured["spec"] = spec
            return ("codex", None)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        eval_spec = argparse.Namespace(harness="codex")
        args = argparse.Namespace(harness=None)
        result = _resolve_harness(args, eval_spec)
        assert result == "codex"
        assert captured["spec"] == "codex"

    def test_eval_spec_none_falls_through_cleanly(self, monkeypatch):
        """``eval_spec=None`` (e.g. ``cli/run.py`` path) does not
        raise. ``spec_value`` is forwarded as ``None``."""
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)
        captured: dict = {}

        def fake_resolve(cli, env, spec):
            captured["spec"] = spec
            return ("claude-code", None)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness=None)
        result = _resolve_harness(args, None)
        assert result == "claude-code"
        assert captured["spec"] is None

    def test_args_without_harness_attr_falls_back_to_none(
        self, monkeypatch
    ):
        """``args`` missing ``harness`` attribute → ``cli=None``.

        Defensive: ``getattr(args, "harness", None)`` so call sites
        that have not yet registered ``--harness`` (e.g. cli/run.py
        before US-005) still work.
        """
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)
        captured: dict = {}

        def fake_resolve(cli, env, spec):
            captured["cli"] = cli
            return ("claude-code", None)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace()  # no `harness` attribute
        result = _resolve_harness(args, None)
        assert result == "claude-code"
        assert captured["cli"] is None

    def test_value_error_routes_to_systemexit_2(
        self, monkeypatch, capsys
    ):
        """``ValueError`` from the pure resolver → stderr ``ERROR:``
        line + ``SystemExit(2)`` per
        ``.claude/rules/llm-cli-exit-code-taxonomy.md``.
        """
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)

        def fake_resolve(cli, env, spec):
            raise ValueError("harness=auto: neither claude nor codex on PATH")

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness=None)
        with pytest.raises(SystemExit) as exc_info:
            _resolve_harness(args, None)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "neither claude nor codex" in captured.err

    def test_announce_fired_when_auto_resolved_to_codex(
        self, monkeypatch
    ):
        """When the pure resolver returns
        ``auto_resolved_to == "codex"``, the wrapper fires
        ``announce_auto_codex_harness`` BEFORE returning."""
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)

        def fake_resolve(cli, env, spec):
            return ("codex", "codex")

        announce_calls: list[int] = []

        def fake_announce():
            announce_calls.append(1)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        monkeypatch.setattr(
            "clauditor._providers._auth.announce_auto_codex_harness",
            fake_announce,
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness=None)
        result = _resolve_harness(args, None)
        assert result == "codex"
        assert announce_calls == [1]

    def test_announce_not_fired_when_auto_resolved_to_claude_code(
        self, monkeypatch
    ):
        """When the auto branch picks ``"claude-code"`` (i.e.
        ``auto_resolved_to is None`` per the pure resolver's
        contract), no announcement fires."""
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)

        def fake_resolve(cli, env, spec):
            return ("claude-code", None)

        announce_calls: list[int] = []

        def fake_announce():
            announce_calls.append(1)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        monkeypatch.setattr(
            "clauditor._providers._auth.announce_auto_codex_harness",
            fake_announce,
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness=None)
        result = _resolve_harness(args, None)
        assert result == "claude-code"
        assert announce_calls == []

    def test_announce_not_fired_when_explicit_codex(
        self, monkeypatch
    ):
        """When an explicit non-``"auto"`` value at any layer
        selects ``codex`` (``auto_resolved_to is None``), the
        announcement does NOT fire — operators who pin a harness
        accept the binary-availability responsibility silently."""
        monkeypatch.delenv("CLAUDITOR_HARNESS", raising=False)

        def fake_resolve(cli, env, spec):
            return ("codex", None)

        announce_calls: list[int] = []

        def fake_announce():
            announce_calls.append(1)

        monkeypatch.setattr(
            "clauditor._providers.resolve_harness", fake_resolve
        )
        monkeypatch.setattr(
            "clauditor._providers._auth.announce_auto_codex_harness",
            fake_announce,
        )
        from clauditor.cli import _resolve_harness

        args = argparse.Namespace(harness="codex")
        result = _resolve_harness(args, None)
        assert result == "codex"
        assert announce_calls == []
