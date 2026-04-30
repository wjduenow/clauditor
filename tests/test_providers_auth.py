"""Tests for the centralized auth-helper surface in ``clauditor._providers._auth``.

Covers the relaxed and strict pre-flight auth guards
(``check_any_auth_available`` / ``check_api_key_only``), the implicit-
no-api-key announcement family, and the back-compat shim deprecation
announcement. Also pins the cross-module class-identity invariant for
``AnthropicAuthMissingError`` (same class object whether imported from
the back-compat shim or the canonical ``clauditor._providers`` seam).

These tests live in a sibling file to ``test_providers_anthropic.py``
because the production-code split between ``_providers/_anthropic.py``
(SDK seam) and ``_providers/_auth.py`` (auth helpers + announcement
family) is mirrored here at the test layer per DEC-003 of
``plans/super/144-providers-call-model.md``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clauditor._anthropic import (
    AnthropicAuthMissingError,
    AnthropicResult,
    announce_implicit_no_api_key,
    check_any_auth_available,
    check_api_key_only,
)


def _patch_which(monkeypatch: pytest.MonkeyPatch, path: str | None) -> None:
    """Pin ``shutil.which("claude")`` to a deterministic value.

    The autouse ``_force_api_transport_in_tests`` fixture in
    ``tests/conftest.py`` patches ``shutil.which`` on
    ``clauditor._anthropic`` to return ``None``. Tests that exercise
    the CLI-available branch of :func:`check_any_auth_available`
    override that with an explicit path.
    """
    import clauditor._anthropic as _anthropic

    monkeypatch.setattr(
        _anthropic.shutil, "which", lambda name: path
    )


class TestCheckAnyAuthAvailable:
    """Unit tests for the relaxed pre-flight auth guard (US-005 / DEC-008).

    Pins DEC-008 of ``plans/super/86-claude-cli-transport.md`` (relax
    the pre-flight guard to accept either ``ANTHROPIC_API_KEY`` or a
    ``claude`` CLI binary on PATH) and DEC-015 (error-message copy
    committed with four test-asserted durable substrings:
    ``ANTHROPIC_API_KEY``, ``Claude Pro``, ``console.anthropic.com``,
    ``claude CLI``).
    """

    def test_key_present_cli_absent_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, None)
        assert check_any_auth_available("grade") is None

    def test_key_absent_cli_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_both_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_both_absent_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_any_auth_available("grade")
        message = str(exc_info.value)
        # DEC-015 four durable anchors.
        assert "ANTHROPIC_API_KEY" in message
        assert "Claude Pro" in message
        assert "console.anthropic.com" in message
        assert "claude CLI" in message
        # DEC-011 command-name interpolation preserved.
        assert "clauditor grade" in message

    def test_empty_string_key_no_cli_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_any_auth_available("grade")

    def test_whitespace_only_key_no_cli_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   \t\n")
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_any_auth_available("grade")

    def test_empty_string_key_cli_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI presence overrides an empty-string API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_whitespace_only_key_cli_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI presence overrides a whitespace-only API key."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_any_auth_available("grade") is None

    def test_auth_token_only_no_cli_still_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-001 (#83) preserved: only ANTHROPIC_API_KEY counts for the key branch."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "some-token")
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_any_auth_available("grade")

    def test_key_whitespace_surrounded_no_cli_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-empty value with surrounding whitespace counts as present."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-test  ")
        _patch_which(monkeypatch, None)
        assert check_any_auth_available("grade") is None

    def test_cmd_name_interpolation_compare_blind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``compare --blind`` is the only multi-word cmd_name; interpolation works."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_any_auth_available("compare --blind")
        assert "clauditor compare --blind" in str(exc_info.value)

    def test_cmd_name_interpolation_propose_eval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_any_auth_available("propose-eval")
        assert "clauditor propose-eval" in str(exc_info.value)


class TestCheckApiKeyOnly:
    """Unit tests for the strict API-key-only pre-flight guard (US-005 / DEC-009).

    Pins DEC-009 of ``plans/super/86-claude-cli-transport.md``: pytest
    fixtures stay stricter than the CLI by default. The strict variant
    only accepts ``ANTHROPIC_API_KEY``; CLI presence does not help.
    """

    def test_key_present_passes_regardless_of_cli(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, None)
        assert check_api_key_only("grader") is None

    def test_key_present_cli_also_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_api_key_only("grader") is None

    def test_key_absent_cli_present_still_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-009: strict variant does NOT accept CLI as a fallback."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        with pytest.raises(AnthropicAuthMissingError) as exc_info:
            check_api_key_only("grader")
        message = str(exc_info.value)
        # Preserves #83 DEC-012's three durable anchors.
        assert "ANTHROPIC_API_KEY" in message
        assert "Claude Pro" in message
        assert "console.anthropic.com" in message
        # Mentions the opt-in escape hatch so users know how to enable
        # CLI transport in fixture mode.
        assert "CLAUDITOR_FIXTURE_ALLOW_CLI" in message
        # Command-name interpolation.
        assert "clauditor grader" in message

    def test_key_empty_string_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(AnthropicAuthMissingError):
            check_api_key_only("grader")


class TestAnnounceImplicitNoApiKey:
    """DEC-003 / DEC-009 / DEC-011 (#95 US-002): one-shot stderr notice
    emitted when ``--transport cli`` strips ``ANTHROPIC_API_KEY`` /
    ``ANTHROPIC_AUTH_TOKEN`` from the skill subprocess env.

    Parallel to :class:`TestStderrAnnouncement` — same autouse-reset
    pattern; same one-shot-per-process contract.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False."""
        monkeypatch.setattr(
            "clauditor._providers._auth._announced_implicit_no_api_key", False
        )

    def test_first_call_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert _IMPLICIT_NO_API_KEY_ANNOUNCEMENT in captured.err

    def test_second_call_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        announce_implicit_no_api_key()
        # Drain the first emission.
        capsys.readouterr()
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_constant_names_both_env_vars(self) -> None:
        """Prose-presence check — both env-var names must appear so
        users know which variables got stripped."""
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert "ANTHROPIC_API_KEY" in _IMPLICIT_NO_API_KEY_ANNOUNCEMENT
        assert "ANTHROPIC_AUTH_TOKEN" in _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

    def test_constant_names_escape_hatch(self) -> None:
        """DEC-011: users must see the explicit-opt-out path."""
        from clauditor._anthropic import _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

        assert "--transport api" in _IMPLICIT_NO_API_KEY_ANNOUNCEMENT

    def test_autouse_fixture_resets_flag_between_tests_first_half(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """First test of a pair — proves first-call emission works."""
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        assert captured.err != ""

    def test_autouse_fixture_resets_flag_between_tests_second_half(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second test of the pair — if the autouse fixture did not
        reset the flag, this test would see silence (the flag would
        still be ``True`` from the first test). Seeing an emission
        here proves the fixture reset works."""
        announce_implicit_no_api_key()
        captured = capsys.readouterr()
        assert captured.err != ""


class TestCallAnthropicDeprecationAnnouncement:
    """DEC-004 (#144 US-007): one-shot stderr deprecation notice when
    the back-compat shim ``clauditor._anthropic.call_anthropic`` is
    invoked.

    Parallel to :class:`TestStderrAnnouncement` /
    :class:`TestAnnounceImplicitNoApiKey` — same autouse-reset
    pattern; same one-shot-per-process contract. Tests pin three
    durable substrings (``clauditor._anthropic``,
    ``clauditor._providers``, ``will be removed``) per
    ``.claude/rules/precall-env-validation.md``'s durable-substring
    discipline so stylistic copy edits don't churn tests.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False."""
        monkeypatch.setattr(
            "clauditor._providers._auth._announced_call_anthropic_deprecation",
            False,
        )

    def test_first_call_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from clauditor._providers._auth import (
            announce_call_anthropic_deprecation,
        )

        announce_call_anthropic_deprecation()
        captured = capsys.readouterr()
        from clauditor._anthropic import _CALL_ANTHROPIC_DEPRECATION_NOTICE

        assert _CALL_ANTHROPIC_DEPRECATION_NOTICE in captured.err

    def test_second_call_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from clauditor._providers._auth import (
            announce_call_anthropic_deprecation,
        )

        announce_call_anthropic_deprecation()
        # Drain the first emission.
        capsys.readouterr()
        announce_call_anthropic_deprecation()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_constant_names_deprecated_path(self) -> None:
        """First durable substring — users must see the deprecated
        import path so they know which module triggered the notice."""
        from clauditor._anthropic import _CALL_ANTHROPIC_DEPRECATION_NOTICE

        assert "clauditor._anthropic" in _CALL_ANTHROPIC_DEPRECATION_NOTICE

    def test_constant_names_canonical_path(self) -> None:
        """Second durable substring — users must see the canonical
        replacement path so they have an immediate next step."""
        from clauditor._anthropic import _CALL_ANTHROPIC_DEPRECATION_NOTICE

        assert "clauditor._providers" in _CALL_ANTHROPIC_DEPRECATION_NOTICE

    def test_constant_names_future_removal(self) -> None:
        """Third durable substring — users must see that the
        deprecation is on a clock (one-release horizon)."""
        from clauditor._anthropic import _CALL_ANTHROPIC_DEPRECATION_NOTICE

        assert "will be removed" in _CALL_ANTHROPIC_DEPRECATION_NOTICE

    @pytest.mark.asyncio
    async def test_call_anthropic_emits_deprecation_warning_first_call(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Invoking the shim's ``call_anthropic`` triggers the
        announcement before delegating to the canonical seam."""
        from clauditor._anthropic import call_anthropic as shim_call_anthropic

        canned = AnthropicResult(response_text="ok", provider="anthropic")
        with patch(
            "clauditor._providers._anthropic.call_anthropic",
            new=AsyncMock(return_value=canned),
        ):
            await shim_call_anthropic("p", model="m")
        captured = capsys.readouterr()
        assert "clauditor._anthropic" in captured.err
        assert "will be removed" in captured.err

    @pytest.mark.asyncio
    async def test_call_anthropic_announcement_only_fires_once(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second call within the same process is silent — the
        one-shot flag stays flipped."""
        from clauditor._anthropic import call_anthropic as shim_call_anthropic

        canned = AnthropicResult(response_text="ok", provider="anthropic")
        with patch(
            "clauditor._providers._anthropic.call_anthropic",
            new=AsyncMock(return_value=canned),
        ):
            await shim_call_anthropic("p1", model="m")
            # Drain the first emission.
            capsys.readouterr()
            await shim_call_anthropic("p2", model="m")
        captured = capsys.readouterr()
        assert "will be removed" not in captured.err

    @pytest.mark.asyncio
    async def test_call_anthropic_delegates_to_canonical_seam(
        self,
    ) -> None:
        """Shim's ``call_anthropic`` delegates to
        :func:`clauditor._providers._anthropic.call_anthropic` and
        threads through ``model``, ``transport``, ``max_tokens``,
        ``subject`` verbatim. Delegating directly (rather than via
        :func:`call_model`) preserves the ``subject`` kwarg for the
        CLI transport's ``apiKeySource`` telemetry line, which the
        :func:`call_model` dispatcher signature drops per DEC-001."""
        from clauditor._anthropic import call_anthropic as shim_call_anthropic

        canned = AnthropicResult(response_text="ok", provider="anthropic")
        mock_canonical = AsyncMock(return_value=canned)
        with patch(
            "clauditor._providers._anthropic.call_anthropic",
            new=mock_canonical,
        ):
            result = await shim_call_anthropic(
                "the-prompt",
                model="claude-sonnet-4-6",
                transport="api",
                max_tokens=2048,
                subject="L2 extraction",
            )

        assert result is canned
        mock_canonical.assert_awaited_once()
        kwargs = mock_canonical.await_args.kwargs
        assert kwargs["model"] == "claude-sonnet-4-6"
        assert kwargs["transport"] == "api"
        assert kwargs["max_tokens"] == 2048
        assert kwargs["subject"] == "L2 extraction"
        # Positional prompt
        assert mock_canonical.await_args.args == ("the-prompt",)


class TestExceptionClassIdentity:
    """Regression: ``AnthropicAuthMissingError`` is the same class object
    when imported from either ``clauditor._anthropic`` (back-compat shim)
    or ``clauditor._providers`` (canonical). Defining the class twice
    would silently break ``except AnthropicAuthMissingError`` at any
    call site that imported from the other module.

    Traces to: DEC-005 + Architecture Review "Security — concern" #1
    of plans/super/144-providers-call-model.md.
    """

    def test_auth_missing_error_class_identity(self) -> None:
        from clauditor._anthropic import (
            AnthropicAuthMissingError as ShimClass,
        )
        from clauditor._providers import (
            AnthropicAuthMissingError as CanonicalClass,
        )

        assert ShimClass is CanonicalClass

    def test_helper_error_class_identity(self) -> None:
        """``AnthropicHelperError`` — used in ``except`` ladders across
        grader / CLI / triggers code; must be the same class regardless
        of import path."""
        from clauditor._anthropic import (
            AnthropicHelperError as ShimClass,
        )
        from clauditor._providers import (
            AnthropicHelperError as CanonicalClass,
        )

        assert ShimClass is CanonicalClass

    def test_claude_cli_error_class_identity(self) -> None:
        """``ClaudeCLIError`` — subclass of ``AnthropicHelperError``;
        same identity invariant applies."""
        from clauditor._anthropic import ClaudeCLIError as ShimClass
        from clauditor._providers import ClaudeCLIError as CanonicalClass

        assert ShimClass is CanonicalClass

    def test_model_result_class_identity(self) -> None:
        """``ModelResult`` — used by every grader; ``isinstance`` checks
        across both import paths must agree."""
        from clauditor._anthropic import ModelResult as ShimClass
        from clauditor._providers import ModelResult as CanonicalClass

        assert ShimClass is CanonicalClass

    def test_anthropic_result_aliases_model_result(self) -> None:
        """``AnthropicResult is ModelResult`` — the back-compat alias is
        the same class object, not a subclass or wrapper. Existing
        fixtures and docstrings naming ``AnthropicResult`` keep
        working."""
        from clauditor._anthropic import AnthropicResult, ModelResult

        assert AnthropicResult is ModelResult


class TestApiKeyIsSet:
    """Direct unit coverage for ``_api_key_is_set()``.

    Helper is exercised transitively through ``TestCheckAnyAuthAvailable``
    and ``TestCheckApiKeyOnly``, but the plan's US-009 acceptance bullet
    calls out a dedicated test class for the helper. These tests pin
    the whitespace-only-is-absent contract documented in the helper's
    docstring.
    """

    def test_returns_true_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _api_key_is_set

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key")
        assert _api_key_is_set() is True

    def test_returns_false_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _api_key_is_set

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _api_key_is_set() is False

    def test_returns_false_when_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _api_key_is_set

        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert _api_key_is_set() is False

    def test_returns_false_when_whitespace_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whitespace-only counts as absent — the SDK's own "could not
        resolve authentication method" path triggers on these shapes,
        and the pre-flight guard's whole point is to catch that with
        an actionable message upstream."""
        from clauditor._providers._auth import _api_key_is_set

        monkeypatch.setenv("ANTHROPIC_API_KEY", "   \t\n  ")
        assert _api_key_is_set() is False


class TestClaudeCliIsAvailable:
    """Direct unit coverage for ``_claude_cli_is_available()``.

    Helper is exercised transitively through ``TestCheckAnyAuthAvailable``,
    but the plan's US-009 acceptance bullet calls out a dedicated test
    class. These tests pin the presence-only contract — the helper does
    NOT verify the CLI is authenticated or functional, only that the
    binary is on PATH.
    """

    def test_returns_true_when_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _claude_cli_is_available

        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert _claude_cli_is_available() is True

    def test_returns_false_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _claude_cli_is_available

        _patch_which(monkeypatch, None)
        assert _claude_cli_is_available() is False
