"""Tests for shared helpers in ``src/clauditor/cli/__init__.py``.

Covers the per-command-agnostic argparse-type validators and the
four-layer precedence resolvers that all six LLM-mediated CLI
commands share. The transport pair (``_transport_choice`` /
``_resolve_grader_transport``) is exercised in ``tests/test_cli.py``;
this module hosts the grading-provider pair (``_provider_choice``
/ ``_resolve_grading_provider``) introduced in #146 US-004.
"""

from __future__ import annotations

import argparse

import pytest


class TestProviderChoice:
    """Argparse type validator for the ``--grading-provider`` flag.

    Mirrors ``TestTransportChoiceValidator`` in test_cli.py.
    """

    @pytest.mark.parametrize("value", ["anthropic", "openai", "auto"])
    def test_accepts_each_literal(self, value):
        """Every accepted literal round-trips unchanged."""
        from clauditor.cli import _provider_choice

        assert _provider_choice(value) == value

    @pytest.mark.parametrize(
        "value", ["claude", "", "ANTHROPIC", "OpenAI", " auto", "gpt"]
    )
    def test_rejects_invalid(self, value):
        """Anything outside the literal set raises ``ArgumentTypeError``.

        argparse maps the ``ArgumentTypeError`` to exit 2 at parse
        time per ``.claude/rules/llm-cli-exit-code-taxonomy.md``.
        """
        from clauditor.cli import _provider_choice

        with pytest.raises(
            argparse.ArgumentTypeError, match="must be one of"
        ):
            _provider_choice(value)

    def test_signature_returns_str(self):
        """The validator returns ``str``, matching argparse ``type=`` shape."""
        from clauditor.cli import _provider_choice

        result = _provider_choice("anthropic")
        assert isinstance(result, str)
        assert result == "anthropic"


class _FakeEvalSpec:
    """Duck-typed eval-spec stand-in.

    The CLI helper reads ``grading_provider`` and ``grading_model``
    via attribute access, so a tiny stub keeps tests independent of
    the full :class:`EvalSpec` validator surface.
    """

    def __init__(
        self,
        *,
        grading_provider: str | None = None,
        grading_model: str | None = "claude-sonnet-4-6",
    ) -> None:
        self.grading_provider = grading_provider
        self.grading_model = grading_model


class TestResolveGradingProvider:
    """Four-layer precedence for ``_resolve_grading_provider``.

    DEC-001 / DEC-003 / DEC-007 of
    ``plans/super/146-grading-provider-precedence.md``: CLI > env >
    spec > default ``"auto"`` (which auto-infers from the effective
    model). Whitespace-only env values normalize to ``None``.
    """

    def test_cli_wins_over_env_spec_default(self, monkeypatch):
        """CLI flag wins even when env and spec also name a value."""
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "anthropic")
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider="openai")
        eval_spec = _FakeEvalSpec(grading_provider="anthropic")
        result = _resolve_grading_provider(args, eval_spec)
        assert result == "openai"

    def test_env_wins_over_spec_and_default(self, monkeypatch):
        """Env wins when CLI flag is unset."""
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None)
        eval_spec = _FakeEvalSpec(grading_provider="anthropic")
        result = _resolve_grading_provider(args, eval_spec)
        assert result == "openai"

    def test_spec_wins_over_default(self, monkeypatch):
        """Spec wins when CLI and env are both unset."""
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None)
        eval_spec = _FakeEvalSpec(grading_provider="openai")
        result = _resolve_grading_provider(args, eval_spec)
        assert result == "openai"

    def test_default_auto_infers_anthropic_from_claude_model(
        self, monkeypatch
    ):
        """Default ``"auto"`` + claude model → ``"anthropic"``."""
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None)
        eval_spec = _FakeEvalSpec(
            grading_provider=None, grading_model="claude-sonnet-4-6"
        )
        result = _resolve_grading_provider(args, eval_spec)
        assert result == "anthropic"

    def test_auto_infers_openai_from_gpt_model(self, monkeypatch):
        """``--grading-provider auto`` + gpt model → ``"openai"``."""
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider="auto")
        eval_spec = _FakeEvalSpec(
            grading_provider=None, grading_model="gpt-5.4"
        )
        result = _resolve_grading_provider(args, eval_spec)
        assert result == "openai"

    def test_invalid_env_exits_2_with_stderr_message(
        self, monkeypatch, capsys
    ):
        """Invalid ``CLAUDITOR_GRADING_PROVIDER`` value → SystemExit(2)."""
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "bogus")
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None)
        with pytest.raises(SystemExit) as exc_info:
            _resolve_grading_provider(args, None)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err
        assert "bogus" in captured.err
        assert "CLAUDITOR_GRADING_PROVIDER" in captured.err

    def test_whitespace_env_treated_as_unset_falls_through_to_spec(
        self, monkeypatch
    ):
        """Whitespace-only env value normalizes to ``None`` → spec wins."""
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "   ")
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None)
        eval_spec = _FakeEvalSpec(grading_provider="openai")
        result = _resolve_grading_provider(args, eval_spec)
        # whitespace-only env → treated as None → spec wins
        assert result == "openai"

    def test_no_eval_spec_with_args_model_drives_auto_inference(
        self, monkeypatch
    ):
        """No eval_spec but ``args.model`` set → used for auto-inference."""
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None, model="gpt-5.4")
        result = _resolve_grading_provider(args, None)
        assert result == "openai"

    def test_no_eval_spec_no_model_auto_raises_systemexit_2(
        self, monkeypatch, capsys
    ):
        """Default ``"auto"`` + no spec + no ``args.model`` → exit 2.

        The pure resolver delegates to ``infer_provider_from_model``
        with ``model=None``, which raises a ``ValueError`` carrying
        a precise actionable message ("provide grading_provider or
        grading_model"). The CLI wrapper routes that to exit 2.
        """
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(grading_provider=None)
        with pytest.raises(SystemExit) as exc_info:
            _resolve_grading_provider(args, None)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err

    def test_auto_with_spec_grading_model_takes_precedence_over_args_model(
        self, monkeypatch
    ):
        """When both ``eval_spec.grading_model`` and ``args.model`` are
        set, the spec value wins for auto-inference (per DEC-004 /
        US-004 acceptance criteria).
        """
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace(
            grading_provider="auto", model="gpt-5.4"
        )
        eval_spec = _FakeEvalSpec(
            grading_provider=None, grading_model="claude-sonnet-4-6"
        )
        result = _resolve_grading_provider(args, eval_spec)
        # spec.grading_model="claude-..." → "anthropic" wins over
        # args.model="gpt-..." → "openai".
        assert result == "anthropic"

    def test_missing_grading_provider_attr_on_args(self, monkeypatch):
        """``args`` without a ``grading_provider`` attr falls through.

        Pre-US-005, most CLI commands don't yet expose the
        ``--grading-provider`` flag. The helper uses
        ``getattr(args, "grading_provider", None)`` defensively so it
        works on any ``Namespace`` shape.
        """
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        from clauditor.cli import _resolve_grading_provider

        args = argparse.Namespace()  # no grading_provider attr
        eval_spec = _FakeEvalSpec(grading_provider="openai")
        result = _resolve_grading_provider(args, eval_spec)
        assert result == "openai"
