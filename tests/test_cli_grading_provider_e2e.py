"""End-to-end tests for `--grading-provider` four-layer precedence + auto-inference.

US-008 of ``plans/super/146-grading-provider-precedence.md``. Exercises
the **full** precedence chain through one CLI command (``grade`` — the
densest seam) end-to-end:

    CLI flag > ``CLAUDITOR_GRADING_PROVIDER`` env > ``EvalSpec.grading_provider``
    > default ``"auto"`` (auto-infers from ``grading_model``).

These complement ``tests/test_cli_grading_provider.py`` (per-command
flag-routing unit tests). The unit tests assert that the resolved
provider reaches ``check_provider_auth``; these E2E tests follow the
provider one layer further — into the dispatcher seam
(``clauditor._providers.call_model``) — so we catch any wiring break
between the auth guard and the actual model call.

Per ``.claude/rules/pytester-inprocess-coverage-hazard.md`` these tests
invoke ``main([...])`` directly with ``monkeypatch`` — no
``pytester.runpytest_inprocess + mock.patch`` combination.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clauditor.cli import main


class _StopE2EError(Exception):
    """Sentinel exception raised by the patched ``call_model``.

    Halts the orchestrator the moment ``call_model`` is invoked so the
    test can inspect ``provider=`` on ``call_args`` without driving a
    full grading round-trip (response parsing, sidecar writes, etc.).
    """


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _write_skill_md(tmp_path: Path, name: str = "greeter") -> Path:
    """Stage a modern-layout SKILL.md under ``tmp_path``."""
    skill_dir = tmp_path / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\n---\n# {name.title()}\n\nSay hi.\n"
    )
    return skill_md


def _write_eval_json(skill_md: Path, eval_data: dict) -> Path:
    """Write a sibling ``<skill_stem>.eval.json`` next to ``skill_md``."""
    eval_path = skill_md.with_suffix(".eval.json")
    eval_path.write_text(json.dumps(eval_data, indent=2))
    return eval_path


def _grade_eval_data(
    *,
    grading_provider: str | None = None,
    grading_model: str | None = "claude-sonnet-4-6",
    include_grading_model: bool = True,
) -> dict:
    """Minimal eval-spec shape acceptable to ``clauditor grade``.

    ``include_grading_model=False`` omits the field entirely (so the
    dataclass default ``"claude-sonnet-4-6"`` lands on the spec); set
    to True with ``grading_model=None`` to write ``"grading_model":
    null`` — distinct from "field absent".
    """
    data: dict = {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
    }
    if include_grading_model:
        data["grading_model"] = grading_model
    if grading_provider is not None:
        data["grading_provider"] = grading_provider
    return data


def _stage_grade_command(
    tmp_path: Path,
    monkeypatch,
    eval_data: dict,
) -> tuple[Path, Path]:
    """Stage skill + eval + captured-output file; cd into ``tmp_path``.

    Returns ``(skill_md, output_file)``. Tests pass ``--output`` to the
    grade command so it skips the subprocess and goes straight to the
    grader call (the layer this file exercises).
    """
    monkeypatch.chdir(tmp_path)
    skill_md = _write_skill_md(tmp_path)
    _write_eval_json(skill_md, eval_data)
    output = tmp_path / "captured.txt"
    output.write_text("hello there, friend!")
    return skill_md, output


# ---------------------------------------------------------------------------
# E2E: precedence resolves through to ``call_model(provider=...)``
# ---------------------------------------------------------------------------


class TestGradingProviderPrecedenceE2E:
    """Precedence chain exercised through to the dispatcher seam.

    Each test mocks ``clauditor._providers.call_model`` with an
    ``AsyncMock`` whose ``side_effect`` raises :class:`_StopE2EError`. The
    grade command's orchestrator (`grade_quality`) propagates the raise
    through ``asyncio.gather``/``asyncio.run``. The CLI surfaces the
    exception as a non-zero return; tests then read
    ``call_model.call_args`` to confirm the resolved provider reached
    the dispatcher.
    """

    @pytest.mark.parametrize(
        "test_id, eval_extra, env, cli_extra, expected_provider",
        [
            # CLI flag wins over spec-declared anthropic.
            (
                "cli_overrides_spec",
                {"grading_provider": "anthropic"},
                {},
                ["--grading-provider", "openai"],
                "openai",
            ),
            # CLI flag wins over env var (which itself disagrees with spec).
            (
                "cli_overrides_env",
                {"grading_provider": "anthropic"},
                {"CLAUDITOR_GRADING_PROVIDER": "anthropic"},
                ["--grading-provider", "openai"],
                "openai",
            ),
            # Env var wins over spec when no CLI flag.
            (
                "env_overrides_spec",
                {"grading_provider": "anthropic"},
                {"CLAUDITOR_GRADING_PROVIDER": "openai"},
                [],
                "openai",
            ),
            # Spec wins over default when no CLI / env.
            (
                "spec_overrides_default",
                {"grading_provider": "openai", "grading_model": "gpt-5.4"},
                {},
                [],
                "openai",
            ),
            # Default ("auto") + claude-* model → infers anthropic.
            (
                "auto_infers_anthropic_from_claude_model",
                {"grading_model": "claude-sonnet-4-6"},
                {},
                [],
                "anthropic",
            ),
            # Default ("auto") + gpt-* model → infers openai.
            (
                "auto_infers_openai_from_gpt_model",
                {"grading_model": "gpt-5.4"},
                {},
                [],
                "openai",
            ),
            # Whitespace-only env var normalizes to None → falls
            # through to spec.
            (
                "whitespace_env_falls_through_to_spec",
                {"grading_provider": "openai", "grading_model": "gpt-5.4"},
                {"CLAUDITOR_GRADING_PROVIDER": "   "},
                [],
                "openai",
            ),
            # Explicit spec="auto" + claude-* model → infers anthropic.
            (
                "explicit_auto_with_claude_model_infers_anthropic",
                {
                    "grading_provider": "auto",
                    "grading_model": "claude-sonnet-4-6",
                },
                {},
                [],
                "anthropic",
            ),
            # Explicit spec="auto" + gpt-* model → infers openai.
            (
                "explicit_auto_with_gpt_model_infers_openai",
                {"grading_provider": "auto", "grading_model": "gpt-5.4"},
                {},
                [],
                "openai",
            ),
        ],
    )
    def test_resolved_provider_reaches_call_model(
        self,
        tmp_path,
        monkeypatch,
        test_id,
        eval_extra,
        env,
        cli_extra,
        expected_provider,
    ):
        """Resolved provider per precedence layer reaches ``call_model``."""
        # Both keys present so the auth guard does not short-circuit
        # before reaching the dispatcher seam regardless of which
        # provider wins. Drop CLAUDITOR_GRADING_PROVIDER first; the
        # parametrized ``env`` dict opts back in when needed.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        eval_data = _grade_eval_data(**eval_extra)
        skill_md, output = _stage_grade_command(
            tmp_path, monkeypatch, eval_data
        )

        call_model_mock = AsyncMock(side_effect=_StopE2EError("halt"))
        with patch(
            "clauditor._providers.call_model", new=call_model_mock
        ):
            # The grade orchestrator propagates _StopE2EError through
            # asyncio.run; cmd_grade does not catch arbitrary
            # exceptions, so it surfaces via the CLI's main wrapper.
            with pytest.raises(_StopE2EError):
                main(
                    [
                        "grade",
                        str(skill_md),
                        "--output",
                        str(output),
                        *cli_extra,
                    ]
                )

        assert call_model_mock.call_count >= 1, (
            f"[{test_id}] expected call_model to be invoked"
        )
        _args, kwargs = call_model_mock.call_args
        assert kwargs.get("provider") == expected_provider, (
            f"[{test_id}] expected provider={expected_provider!r}, "
            f"got kwargs={kwargs!r}"
        )


# ---------------------------------------------------------------------------
# E2E: error paths surface as exit 2 BEFORE call_model is invoked
# ---------------------------------------------------------------------------


class TestGradingProviderResolutionFailsE2E:
    """Error paths surfacing as exit 2 — call_model must NOT be reached."""

    def test_invalid_env_var_exits_2_without_calling_model(
        self, tmp_path, monkeypatch, capsys
    ):
        """``CLAUDITOR_GRADING_PROVIDER=foo`` exits 2; ``call_model`` not invoked."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "foo")
        eval_data = _grade_eval_data()
        skill_md, output = _stage_grade_command(
            tmp_path, monkeypatch, eval_data
        )

        call_model_mock = AsyncMock(side_effect=_StopE2EError("halt"))
        with patch(
            "clauditor._providers.call_model", new=call_model_mock
        ):
            with pytest.raises(SystemExit) as excinfo:
                main(["grade", str(skill_md), "--output", str(output)])

        assert excinfo.value.code == 2
        assert call_model_mock.call_count == 0
        err = capsys.readouterr().err
        assert "CLAUDITOR_GRADING_PROVIDER" in err
        assert "foo" in err

    def test_unknown_model_prefix_exits_2_without_calling_model(
        self, tmp_path, monkeypatch, capsys
    ):
        """``grading_model="unknown-foo"`` + ``"auto"`` provider → exit 2.

        The ``infer_provider_from_model`` helper raises ``ValueError``
        for unknown prefixes per DEC-003; the CLI seam routes the
        ``ValueError`` to ``SystemExit(2)``.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        eval_data = _grade_eval_data(
            grading_provider="auto", grading_model="unknown-foo"
        )
        skill_md, output = _stage_grade_command(
            tmp_path, monkeypatch, eval_data
        )

        call_model_mock = AsyncMock(side_effect=_StopE2EError("halt"))
        with patch(
            "clauditor._providers.call_model", new=call_model_mock
        ):
            with pytest.raises(SystemExit) as excinfo:
                main(["grade", str(skill_md), "--output", str(output)])

        assert excinfo.value.code == 2
        assert call_model_mock.call_count == 0
        err = capsys.readouterr().err
        assert "unknown-foo" in err

    def test_auto_with_null_model_exits_2_without_calling_model(
        self, tmp_path, monkeypatch, capsys
    ):
        """``grading_provider="auto"`` + ``grading_model=null`` exits 2.

        The CLI's ``_resolve_grading_provider`` falls through to
        ``args.model`` (also ``None`` when ``--model`` is absent), so
        the pure resolver receives ``model=None`` and
        ``infer_provider_from_model`` raises with the actionable
        ``"provide grading_provider or grading_model"`` message.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        eval_data = _grade_eval_data(
            grading_provider="auto", grading_model=None
        )
        skill_md, output = _stage_grade_command(
            tmp_path, monkeypatch, eval_data
        )

        call_model_mock = AsyncMock(side_effect=_StopE2EError("halt"))
        with patch(
            "clauditor._providers.call_model", new=call_model_mock
        ):
            with pytest.raises(SystemExit) as excinfo:
                main(["grade", str(skill_md), "--output", str(output)])

        assert excinfo.value.code == 2
        assert call_model_mock.call_count == 0
        err = capsys.readouterr().err
        assert "grading_provider" in err or "grading_model" in err


# ---------------------------------------------------------------------------
# E2E: auth guard fires per resolved provider
# ---------------------------------------------------------------------------


class TestGradingProviderAuthGuardE2E:
    """Auth guard fires correctly for the *resolved* provider, not a hard-coded one."""

    def test_resolved_openai_missing_openai_key_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        """``--grading-provider openai`` + missing ``OPENAI_API_KEY`` → exit 2.

        Anthropic key is present but irrelevant; the guard must
        consult the resolved provider.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        eval_data = _grade_eval_data()
        skill_md, output = _stage_grade_command(
            tmp_path, monkeypatch, eval_data
        )

        call_model_mock = AsyncMock(side_effect=_StopE2EError("halt"))
        with patch(
            "clauditor._providers.call_model", new=call_model_mock
        ):
            rc = main(
                [
                    "grade",
                    str(skill_md),
                    "--output",
                    str(output),
                    "--grading-provider",
                    "openai",
                ]
            )

        assert rc == 2
        assert call_model_mock.call_count == 0
        err = capsys.readouterr().err
        assert "OPENAI_API_KEY" in err
        assert "platform.openai.com" in err

    def test_resolved_anthropic_missing_anthropic_auth_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        """``--grading-provider anthropic`` + no anthropic auth → exit 2.

        ``ANTHROPIC_API_KEY`` is absent and the ``claude`` CLI is
        unavailable (we patch the PATH probe to ``False``), so the
        relaxed guard fails. ``OPENAI_API_KEY`` is set but irrelevant
        — the guard must consult the resolved provider.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        eval_data = _grade_eval_data()
        skill_md, output = _stage_grade_command(
            tmp_path, monkeypatch, eval_data
        )

        call_model_mock = AsyncMock(side_effect=_StopE2EError("halt"))
        # Force the relaxed-guard's "claude CLI on PATH" probe to
        # return False so the only-CLI fallback does not satisfy auth.
        with (
            patch(
                "clauditor._providers._auth._claude_cli_is_available",
                return_value=False,
            ),
            patch(
                "clauditor._providers.call_model", new=call_model_mock
            ),
        ):
            rc = main(
                [
                    "grade",
                    str(skill_md),
                    "--output",
                    str(output),
                    "--grading-provider",
                    "anthropic",
                ]
            )

        assert rc == 2
        assert call_model_mock.call_count == 0
        err = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY" in err
        assert "console.anthropic.com" in err
