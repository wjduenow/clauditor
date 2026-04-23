"""#95 US-003: implicit --transport cli → --no-api-key coupling on ``grade``.

Tests for the wiring added to ``cmd_grade``'s ``env_override`` computation
so that ``--transport cli`` (or ``CLAUDITOR_TRANSPORT=cli``) implicitly
strips ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` from the skill
subprocess env. Covers:

- Positive: implicit fires + notice emits (3 cases)
- Positive: implicit fires but no key → no notice (DEC-003)
- Positive: explicit ``--no-api-key`` does NOT fire the notice (DEC-007)
- Negative: ``--transport api`` / ``auto`` / spec-only do NOT fire (DEC-002)
- Edge: notice is once-per-process (US-002 idempotence)

Traces to DEC-001, DEC-002, DEC-003, DEC-006, DEC-007, DEC-008, DEC-009,
DEC-010, DEC-011.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clauditor.cli import main
from tests.conftest import (
    build_eval_spec as _make_eval_spec,
)
from tests.conftest import (
    make_grading_report,
    make_skill_result,
)
from tests.conftest import (
    make_spec as _make_spec,
)


class TestImplicitNoApiKeyCoupling:
    """#95 US-003: --transport cli implicitly strips auth env for grade."""

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot module flag False."""
        monkeypatch.setattr(
            "clauditor._anthropic._announced_implicit_no_api_key", False
        )

    def _run_grade(self, argv, *, eval_spec=None):
        """Drive ``main(argv)`` with SkillSpec.from_file and grade_quality mocked.

        Returns ``(rc, spec_mock, env_override_captured)``. The mocked
        ``spec.run`` always returns a clean :class:`SkillResult`; the mocked
        ``grade_quality`` returns a passing :class:`GradingReport`. The
        pre-flight auth guard (:func:`check_any_auth_available`) is stubbed
        so tests do not depend on the real ``claude`` binary being on PATH
        when only ``ANTHROPIC_AUTH_TOKEN`` (or neither) is set.
        """
        eval_spec = eval_spec if eval_spec is not None else _make_eval_spec()
        spec = _make_spec(eval_spec=eval_spec)
        spec.run.return_value = make_skill_result(
            output="primary output",
            duration_seconds=0.5,
            input_tokens=10,
            output_tokens=5,
        )
        report = make_grading_report(passed=True)
        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.cli.grade.check_any_auth_available",
                return_value=None,
            ),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            rc = main(argv)
        env = (
            spec.run.call_args.kwargs.get("env_override")
            if spec.run.called
            else None
        )
        return rc, spec, env

    def _assert_env_stripped(self, env) -> None:
        assert env is not None, "env_override must be a dict, got None"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    # --- Positive: implicit coupling fires, notice emits --------------

    def test_transport_cli_with_api_key_strips_and_announces(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 1: --transport cli + ANTHROPIC_API_KEY → strip + notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--transport", "cli"]
        )
        assert rc == 0
        self._assert_env_stripped(env)
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.err
        assert "--transport api" in captured.err

    def test_transport_cli_with_auth_token_strips_and_announces(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 2: --transport cli + AUTH_TOKEN only → strip + notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--transport", "cli"]
        )
        assert rc == 0
        self._assert_env_stripped(env)
        captured = capsys.readouterr()
        assert "ANTHROPIC_AUTH_TOKEN" in captured.err

    def test_env_var_cli_with_api_key_strips_and_announces(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 3: CLAUDITOR_TRANSPORT=cli + API_KEY → strip + notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("CLAUDITOR_TRANSPORT", "cli")

        rc, spec, env = self._run_grade(["grade", "skill.md"])
        assert rc == 0
        self._assert_env_stripped(env)
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.err

    # --- Positive: implicit fires, but no key → no notice (DEC-003) ---

    def test_transport_cli_without_keys_strips_silently(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 4: --transport cli + no key vars → strip is a no-op, NO notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--transport", "cli"]
        )
        assert rc == 0
        # env_override is still computed (behavior-consistent: the strip is
        # safe to apply even when there is nothing to strip).
        self._assert_env_stripped(env)
        captured = capsys.readouterr()
        # DEC-003: no notice when nothing was present to strip.
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT not in captured.err

    # --- Positive: explicit --no-api-key → NO new notice (DEC-007) ----

    def test_explicit_no_api_key_alone_does_not_announce(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 5: --no-api-key (no --transport) → strip, no implicit notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--no-api-key"]
        )
        assert rc == 0
        self._assert_env_stripped(env)
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT not in captured.err

    def test_explicit_no_api_key_with_transport_cli_does_not_announce(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 6: --no-api-key + --transport cli → strip, explicit wins, NO notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--no-api-key", "--transport", "cli"]
        )
        assert rc == 0
        self._assert_env_stripped(env)
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT not in captured.err

    # --- Negative: coupling does NOT fire ----------------------------

    def test_transport_api_does_not_strip(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 7: --transport api + key → env NOT stripped, no notice."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--transport", "api"]
        )
        assert rc == 0
        assert env is None, (
            "--transport api must NOT strip the skill subprocess env"
        )
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT not in captured.err

    def test_transport_auto_does_not_strip(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 8: --transport auto + key → env NOT stripped (DEC-002)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        rc, spec, env = self._run_grade(
            ["grade", "skill.md", "--transport", "auto"]
        )
        assert rc == 0
        assert env is None, (
            "--transport auto must NOT trigger the implicit strip per DEC-002"
        )
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT not in captured.err

    def test_eval_spec_transport_cli_alone_does_not_strip(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 9: EvalSpec.transport='cli' (no CLI flag / env) → no strip.

        Author-intent (spec field) must NOT trigger the coupling; only
        operator-intent layers (CLI flag, env var) do (DEC-002, DEC-006).
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        eval_spec = _make_eval_spec(transport="cli")
        rc, spec, env = self._run_grade(
            ["grade", "skill.md"], eval_spec=eval_spec
        )
        assert rc == 0
        assert env is None, (
            "EvalSpec.transport='cli' alone must NOT trigger the implicit "
            "strip (DEC-002, DEC-006)"
        )
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT not in captured.err

    # --- Edge: once-per-process notice -------------------------------

    def test_notice_emits_only_once_across_two_grade_invocations(
        self, tmp_path, monkeypatch, capsys
    ):
        """Case 10: two cmd_grade calls (both implicit + key) → notice ONCE."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDITOR_TRANSPORT", raising=False)

        # First invocation: emits the notice.
        rc1, _, env1 = self._run_grade(
            ["grade", "skill.md", "--transport", "cli"]
        )
        # Second invocation in the SAME test → module flag is still True
        # from the first call, so no stderr emission this time.
        rc2, _, env2 = self._run_grade(
            ["grade", "skill.md", "--transport", "cli"]
        )
        assert rc1 == 0 and rc2 == 0
        self._assert_env_stripped(env1)
        self._assert_env_stripped(env2)
        captured = capsys.readouterr()
        # Count occurrences of the announcement in stderr — must be exactly 1.
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert captured.err.count(_IMPLICIT_NO_API_KEY_ANNOUNCEMENT) == 1


class TestGradeTransportHelpMentionsCoupling:
    """DEC-010: --transport --help must mention the implicit coupling."""

    def test_transport_help_mentions_implicit_coupling(
        self, capsys
    ):
        """``clauditor grade --help`` text for --transport names the coupling."""
        with pytest.raises(SystemExit) as exc_info:
            main(["grade", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # Durable substring anchors — don't over-specify wording.
        assert "--no-api-key" in captured.out
        # Mention at least one of the trigger layers so the user sees
        # how to opt in.
        assert (
            "--transport cli" in captured.out
            or "CLAUDITOR_TRANSPORT" in captured.out
        )
