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


class TestCheckOpenAiAuth:
    """Unit tests for the OpenAI pre-flight auth guard (US-006 / DEC-006).

    Mirrors :class:`TestCheckApiKeyOnly`'s shape — the OpenAI guard is
    unconditionally strict (no CLI-fallback branch since OpenAI has no
    equivalent of the ``claude`` CLI subscription path). Pins DEC-006
    of ``plans/super/145-openai-provider.md``: pure helper raising
    :class:`OpenAIAuthMissingError` when ``OPENAI_API_KEY`` is absent,
    empty, or whitespace-only.
    """

    def test_key_present_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import check_openai_auth

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert check_openai_auth("grade") is None

    def test_key_absent_raises_with_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_openai_auth,
        )

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(OpenAIAuthMissingError) as exc_info:
            check_openai_auth("grade")
        message = str(exc_info.value)
        # DEC-006 durable substrings.
        assert "OPENAI_API_KEY" in message
        assert "platform.openai.com" in message
        # Command-name interpolation.
        assert "clauditor grade" in message

    def test_empty_string_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_openai_auth,
        )

        monkeypatch.setenv("OPENAI_API_KEY", "")
        with pytest.raises(OpenAIAuthMissingError):
            check_openai_auth("grade")

    def test_whitespace_only_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_openai_auth,
        )

        monkeypatch.setenv("OPENAI_API_KEY", "   \t\n")
        with pytest.raises(OpenAIAuthMissingError):
            check_openai_auth("grade")

    def test_key_whitespace_surrounded_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-empty value with surrounding whitespace counts as present."""
        from clauditor._providers import check_openai_auth

        monkeypatch.setenv("OPENAI_API_KEY", "  sk-test  ")
        assert check_openai_auth("grade") is None

    def test_cmd_name_interpolation_extract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_openai_auth,
        )

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(OpenAIAuthMissingError) as exc_info:
            check_openai_auth("extract")
        assert "clauditor extract" in str(exc_info.value)

    def test_cmd_name_interpolation_propose_eval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_openai_auth,
        )

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(OpenAIAuthMissingError) as exc_info:
            check_openai_auth("propose-eval")
        assert "clauditor propose-eval" in str(exc_info.value)

    def test_openai_auth_missing_error_is_not_anthropic_subclass(
        self,
    ) -> None:
        """DEC-006 (#145) + ``.claude/rules/llm-cli-exit-code-taxonomy.md``:
        ``OpenAIAuthMissingError`` is a direct subclass of
        :class:`Exception`, NOT of :class:`AnthropicAuthMissingError`.
        A common ancestor would defeat the structural-routing
        invariant CLI dispatchers depend on.
        """
        from clauditor._providers import (
            AnthropicAuthMissingError,
            OpenAIAuthMissingError,
        )

        assert not issubclass(OpenAIAuthMissingError, AnthropicAuthMissingError)
        # And the converse — neither inherits from the other.
        assert not issubclass(AnthropicAuthMissingError, OpenAIAuthMissingError)
        # Direct base is Exception.
        assert OpenAIAuthMissingError.__bases__ == (Exception,)

    def test_constant_substrings(self) -> None:
        """Prose-presence check on the message template."""
        from clauditor._providers import _OPENAI_AUTH_MISSING_TEMPLATE

        assert "OPENAI_API_KEY" in _OPENAI_AUTH_MISSING_TEMPLATE
        assert "platform.openai.com" in _OPENAI_AUTH_MISSING_TEMPLATE
        assert "{cmd_name}" in _OPENAI_AUTH_MISSING_TEMPLATE


class TestCheckProviderAuth:
    """Unit tests for the multi-provider dispatcher (US-006 / DEC-006).

    Pins DEC-006 of ``plans/super/145-openai-provider.md``: single
    public seam ``check_provider_auth(provider, cmd_name)`` that
    routes to the provider-specific guard. Distinct exception classes
    propagate through (preserving the structural-routing invariant
    every CLI dispatcher depends on per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``).
    """

    def test_anthropic_delegates_to_check_any_auth_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``provider="anthropic"`` routes to
        :func:`check_any_auth_available`. Verified by the behavioral
        contract: when both ``ANTHROPIC_API_KEY`` is unset AND the
        ``claude`` CLI is absent, the dispatcher raises
        :class:`AnthropicAuthMissingError` — exactly the relaxed
        guard's contract.
        """
        from clauditor._providers import (
            AnthropicAuthMissingError,
            check_provider_auth,
        )

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(AnthropicAuthMissingError):
            check_provider_auth("anthropic", "grade")

    def test_anthropic_passes_when_key_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import check_provider_auth

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _patch_which(monkeypatch, None)
        assert check_provider_auth("anthropic", "grade") is None

    def test_anthropic_passes_when_cli_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEC-006 explicitly preserves #86 DEC-008's key-OR-CLI
        semantics for the anthropic branch. CLI presence alone passes
        the dispatcher's anthropic route."""
        from clauditor._providers import check_provider_auth

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, "/usr/local/bin/claude")
        assert check_provider_auth("anthropic", "grade") is None

    def test_anthropic_delegates_via_module_attribute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tightening test: patch ``check_any_auth_available`` on the
        canonical ``_providers._auth`` module path and verify the
        dispatcher actually called it. Catches accidental inline-
        re-implementation."""
        import clauditor._providers._auth as _auth

        called_with: list[str] = []

        def _stub(cmd_name: str) -> None:
            called_with.append(cmd_name)
            return None

        monkeypatch.setattr(_auth, "check_any_auth_available", _stub)
        _auth.check_provider_auth("anthropic", "grade")
        assert called_with == ["grade"]

    def test_openai_delegates_to_check_openai_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``provider="openai"`` routes to :func:`check_openai_auth`.
        Verified behaviorally: when ``OPENAI_API_KEY`` is unset, the
        dispatcher raises :class:`OpenAIAuthMissingError`."""
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_provider_auth,
        )

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(OpenAIAuthMissingError):
            check_provider_auth("openai", "grade")

    def test_openai_passes_when_key_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import check_provider_auth

        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        assert check_provider_auth("openai", "grade") is None

    def test_openai_delegates_via_module_attribute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tightening test: patch ``check_openai_auth`` on the
        canonical ``_providers._auth`` module path and verify the
        dispatcher actually called it."""
        import clauditor._providers._auth as _auth

        called_with: list[str] = []

        def _stub(cmd_name: str) -> None:
            called_with.append(cmd_name)
            return None

        monkeypatch.setattr(_auth, "check_openai_auth", _stub)
        _auth.check_provider_auth("openai", "extract")
        assert called_with == ["extract"]

    def test_unknown_provider_raises_value_error(self) -> None:
        from clauditor._providers import check_provider_auth

        with pytest.raises(ValueError) as exc_info:
            check_provider_auth("vertex", "grade")
        # Helpful error message names the unknown value.
        assert "vertex" in str(exc_info.value)

    def test_empty_string_provider_raises_value_error(self) -> None:
        from clauditor._providers import check_provider_auth

        with pytest.raises(ValueError):
            check_provider_auth("", "grade")

    def test_anthropic_propagates_distinct_class(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Structural-routing invariant: the anthropic branch raises
        :class:`AnthropicAuthMissingError` and NOT
        :class:`OpenAIAuthMissingError`, so a CLI ``except`` ladder
        keyed on the OpenAI class would NOT catch the anthropic
        failure."""
        from clauditor._providers import (
            OpenAIAuthMissingError,
            check_provider_auth,
        )

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _patch_which(monkeypatch, None)
        with pytest.raises(Exception) as exc_info:
            check_provider_auth("anthropic", "grade")
        # The raised class is NOT OpenAIAuthMissingError.
        assert not isinstance(exc_info.value, OpenAIAuthMissingError)

    def test_openai_propagates_distinct_class(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Symmetric structural-routing invariant for the openai branch."""
        from clauditor._providers import (
            AnthropicAuthMissingError,
            check_provider_auth,
        )

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(Exception) as exc_info:
            check_provider_auth("openai", "grade")
        # The raised class is NOT AnthropicAuthMissingError.
        assert not isinstance(exc_info.value, AnthropicAuthMissingError)


class TestOpenAiApiKeyIsSet:
    """Direct unit coverage for ``_openai_api_key_is_set()``.

    Helper is exercised transitively through ``TestCheckOpenAiAuth``,
    but a dedicated test class pins the whitespace-only-is-absent
    contract documented in the helper's docstring (mirrors
    :class:`TestApiKeyIsSet` for the Anthropic side).
    """

    def test_returns_true_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _openai_api_key_is_set

        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
        assert _openai_api_key_is_set() is True

    def test_returns_false_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _openai_api_key_is_set

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert _openai_api_key_is_set() is False

    def test_returns_false_when_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _openai_api_key_is_set

        monkeypatch.setenv("OPENAI_API_KEY", "")
        assert _openai_api_key_is_set() is False

    def test_returns_false_when_whitespace_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers._auth import _openai_api_key_is_set

        monkeypatch.setenv("OPENAI_API_KEY", "   \t\n")
        assert _openai_api_key_is_set() is False


class TestCheckCodexAuth:
    """Unit tests for the Codex pre-flight auth guard (US-003 / DEC-003 / DEC-010).

    Pins DEC-003 of ``plans/super/151-harness-precedence.md``: strict-OR
    pre-flight guard raising :class:`CodexAuthMissingError` when neither
    ``CODEX_API_KEY`` nor ``OPENAI_API_KEY`` is set (both checked via
    whitespace-trimmed non-empty). No CLI-fallback branch — Codex has no
    documented "subscription only" auth analog like Claude Pro/Max.

    Codex is a HARNESS axis, not a PROVIDER axis (DEC-010): the
    :func:`check_provider_auth` dispatcher is unchanged; the CLI seam
    directly calls :func:`check_codex_auth` when the resolved harness
    is ``"codex"``.
    """

    def test_only_codex_api_key_set_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import check_codex_auth

        monkeypatch.setenv("CODEX_API_KEY", "sk-codex-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert check_codex_auth("grade") is None

    def test_only_openai_api_key_set_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import check_codex_auth

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        assert check_codex_auth("grade") is None

    def test_both_keys_set_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import check_codex_auth

        monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert check_codex_auth("grade") is None

    def test_neither_key_set_raises_with_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            CodexAuthMissingError,
            check_codex_auth,
        )

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(CodexAuthMissingError) as exc_info:
            check_codex_auth("grade")
        message = str(exc_info.value)
        # DEC-003 / DEC-010 durable substrings.
        assert "CODEX_API_KEY" in message
        assert "OPENAI_API_KEY" in message
        assert "platform.openai.com" in message
        # Command-name interpolation.
        assert "clauditor grade" in message

    def test_both_empty_strings_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            CodexAuthMissingError,
            check_codex_auth,
        )

        monkeypatch.setenv("CODEX_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        with pytest.raises(CodexAuthMissingError):
            check_codex_auth("grade")

    def test_both_whitespace_only_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whitespace-only values are treated as unset for both env vars."""
        from clauditor._providers import (
            CodexAuthMissingError,
            check_codex_auth,
        )

        monkeypatch.setenv("CODEX_API_KEY", "   \t\n")
        monkeypatch.setenv("OPENAI_API_KEY", "  ")
        with pytest.raises(CodexAuthMissingError):
            check_codex_auth("grade")

    def test_codex_whitespace_openai_set_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Strict-OR: whitespace-only CODEX_API_KEY but valid OPENAI_API_KEY
        passes — at least one env var is set."""
        from clauditor._providers import check_codex_auth

        monkeypatch.setenv("CODEX_API_KEY", "   ")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        assert check_codex_auth("grade") is None

    def test_cmd_name_interpolation_validate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clauditor._providers import (
            CodexAuthMissingError,
            check_codex_auth,
        )

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(CodexAuthMissingError) as exc_info:
            check_codex_auth("validate")
        assert "clauditor validate" in str(exc_info.value)

    def test_codex_auth_missing_error_is_direct_exception_subclass(
        self,
    ) -> None:
        """DEC-010 + ``.claude/rules/llm-cli-exit-code-taxonomy.md``:
        ``CodexAuthMissingError`` is a direct subclass of
        :class:`Exception`, NOT of :class:`AnthropicAuthMissingError`,
        :class:`OpenAIAuthMissingError`, or any helper-error class. A
        common ancestor would defeat the structural-routing invariant
        CLI dispatchers depend on.
        """
        from clauditor._providers import (
            AnthropicAuthMissingError,
            CodexAuthMissingError,
            OpenAIAuthMissingError,
        )

        assert not issubclass(CodexAuthMissingError, AnthropicAuthMissingError)
        assert not issubclass(CodexAuthMissingError, OpenAIAuthMissingError)
        # And the converse — the existing classes do not inherit from Codex.
        assert not issubclass(AnthropicAuthMissingError, CodexAuthMissingError)
        assert not issubclass(OpenAIAuthMissingError, CodexAuthMissingError)
        # Direct base is Exception.
        assert CodexAuthMissingError.__bases__ == (Exception,)

    def test_constant_substrings(self) -> None:
        """Prose-presence check on the message template."""
        from clauditor._providers import _CODEX_AUTH_MISSING_TEMPLATE

        assert "CODEX_API_KEY" in _CODEX_AUTH_MISSING_TEMPLATE
        assert "OPENAI_API_KEY" in _CODEX_AUTH_MISSING_TEMPLATE
        assert "platform.openai.com" in _CODEX_AUTH_MISSING_TEMPLATE
        assert "{cmd_name}" in _CODEX_AUTH_MISSING_TEMPLATE

    def test_codex_auth_missing_error_class_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per ``.claude/rules/back-compat-shim-discipline.md`` Pattern 2:
        the ``CodexAuthMissingError`` instance raised by
        :func:`check_codex_auth` (which imports the class locally from
        ``clauditor._providers`` inside the function body) MUST be an
        instance of the same class object users import from
        ``clauditor._providers``. Defining the class twice (e.g. a
        local re-declaration in ``_auth.py``) would silently break
        ``except CodexAuthMissingError`` at any call site that imported
        from the canonical seam.
        """
        from clauditor._providers import (
            CodexAuthMissingError,
            check_codex_auth,
        )

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(CodexAuthMissingError) as exc_info:
            check_codex_auth("grade")
        # The raised instance must be an exact instance of the canonical
        # class, not a same-named-but-distinct re-declaration.
        assert type(exc_info.value) is CodexAuthMissingError


class TestCheckProviderAuthCodexUnchanged:
    """Regression: per DEC-010, ``check_provider_auth`` does NOT grow a
    Codex branch. Codex is a HARNESS axis, not a PROVIDER axis. Calling
    ``check_provider_auth("codex", ...)`` must still raise ``ValueError``
    (unknown provider) — the CLI seam directly calls
    :func:`check_codex_auth` when the resolved harness is ``"codex"``.
    """

    def test_codex_provider_string_raises_value_error(self) -> None:
        from clauditor._providers import check_provider_auth

        with pytest.raises(ValueError) as exc_info:
            check_provider_auth("codex", "grade")
        assert "unknown provider" in str(exc_info.value)
        assert "'codex'" in str(exc_info.value)


class TestAnnounceAutoCodexHarness:
    """DEC-007 / DEC-011 (#151 US-003): one-shot stderr notice emitted
    on the first auto→codex resolution per Python process.

    Parallel to :class:`TestAnnounceImplicitNoApiKey` and
    :class:`TestCallAnthropicDeprecationAnnouncement` — same autouse-
    reset pattern; same one-shot-per-process contract. Tests pin two
    durable substrings (``CODEX_API_KEY``, ``OPENAI_API_KEY``) per
    ``.claude/rules/precall-env-validation.md``'s durable-substring
    discipline so stylistic copy edits don't churn tests.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False."""
        monkeypatch.setattr(
            "clauditor._providers._auth._announced_auto_codex_harness",
            False,
        )

    def test_first_call_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from clauditor._providers import announce_auto_codex_harness

        announce_auto_codex_harness()
        captured = capsys.readouterr()
        from clauditor._providers import _AUTO_CODEX_ANNOUNCEMENT

        assert _AUTO_CODEX_ANNOUNCEMENT in captured.err

    def test_second_call_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from clauditor._providers import announce_auto_codex_harness

        announce_auto_codex_harness()
        # Drain the first emission.
        capsys.readouterr()
        announce_auto_codex_harness()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_constant_names_codex_env_var(self) -> None:
        """First durable substring — users must see ``CODEX_API_KEY``
        so they know which env var Codex prefers."""
        from clauditor._providers import _AUTO_CODEX_ANNOUNCEMENT

        assert "CODEX_API_KEY" in _AUTO_CODEX_ANNOUNCEMENT

    def test_constant_names_openai_env_var(self) -> None:
        """Second durable substring — users must see ``OPENAI_API_KEY``
        so they know the fallback env var Codex accepts."""
        from clauditor._providers import _AUTO_CODEX_ANNOUNCEMENT

        assert "OPENAI_API_KEY" in _AUTO_CODEX_ANNOUNCEMENT

    def test_constant_does_not_interpolate_values(self) -> None:
        """Auth review #7 (#151): the announcement names env-var names
        only; it MUST NOT interpolate values. A leaked secret in the
        constant would surface in the test text — fail closed."""
        from clauditor._providers import _AUTO_CODEX_ANNOUNCEMENT

        # No format placeholders for values.
        assert "{value" not in _AUTO_CODEX_ANNOUNCEMENT
        # No literal "sk-" prefixed tokens (canonical OpenAI/Codex key
        # shape).
        assert "sk-" not in _AUTO_CODEX_ANNOUNCEMENT

    def test_autouse_fixture_resets_flag_between_tests_first_half(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """First test of a pair — proves first-call emission works."""
        from clauditor._providers import announce_auto_codex_harness

        announce_auto_codex_harness()
        captured = capsys.readouterr()
        assert captured.err != ""

    def test_autouse_fixture_resets_flag_between_tests_second_half(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second test of the pair — if the autouse fixture did not
        reset the flag, this test would see silence (the flag would
        still be ``True`` from the first test). Seeing an emission
        here proves the fixture reset works."""
        from clauditor._providers import announce_auto_codex_harness

        announce_auto_codex_harness()
        captured = capsys.readouterr()
        assert captured.err != ""


class TestCodexCliIsAvailable:
    """Direct unit coverage for ``_codex_cli_is_available()`` (#175 US-001).

    Parallel to :class:`TestClaudeCliIsAvailable`. The helper is the
    presence-only PATH probe that #175 uses as the load-bearing third
    branch of :func:`check_codex_auth` (DEC-001 / DEC-002), accepting
    pre-flight when neither ``CODEX_API_KEY`` nor ``OPENAI_API_KEY`` is
    set but the ``codex`` binary itself is on PATH (i.e. the user is
    authenticated via ChatGPT login persisted in
    ``~/.codex/auth.json``).

    Presence-only contract: the helper does NOT verify the CLI is
    authenticated or functional. Codex itself produces crisp
    "Please log out and sign in again" downstream when a stale
    ``auth.json`` is present.
    """

    def test_returns_true_when_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Override the autouse ``shutil.which → None`` pin from
        ``conftest.py`` so the helper sees codex on PATH."""
        from clauditor._providers import _auth as _auth_mod
        from clauditor._providers._auth import _codex_cli_is_available

        monkeypatch.setattr(
            _auth_mod.shutil,
            "which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        assert _codex_cli_is_available() is True

    def test_returns_false_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The autouse ``shutil.which → None`` pin already returns
        ``None`` for every name; this test confirms the helper honors
        it."""
        from clauditor._providers import _auth as _auth_mod
        from clauditor._providers._auth import _codex_cli_is_available

        monkeypatch.setattr(
            _auth_mod.shutil, "which", lambda name: None
        )
        assert _codex_cli_is_available() is False


class TestAnnounceCodexCliOnPath:
    """DEC-003 / DEC-004 / DEC-009 (#175 US-001): one-shot stderr notice
    emitted on the first ``check_codex_auth`` call where the codex CLI
    on PATH is the load-bearing acceptance signal (no env vars set).

    Parallel to :class:`TestAnnounceAutoCodexHarness`,
    :class:`TestAnnounceImplicitNoApiKey`, and
    :class:`TestCallAnthropicDeprecationAnnouncement` — same autouse-
    reset pattern; same one-shot-per-process contract. Tests pin three
    durable substrings (``codex``, ``PATH``, ``~/.codex/auth.json``)
    per ``.claude/rules/precall-env-validation.md``'s durable-substring
    discipline so stylistic copy edits don't churn tests.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False."""
        monkeypatch.setattr(
            "clauditor._providers._auth._announced_codex_cli_on_path",
            False,
        )

    def test_first_call_emits_announcement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from clauditor._providers import announce_codex_cli_on_path

        announce_codex_cli_on_path()
        captured = capsys.readouterr()
        from clauditor._providers import _CODEX_CLI_ON_PATH_ANNOUNCEMENT

        assert _CODEX_CLI_ON_PATH_ANNOUNCEMENT in captured.err

    def test_second_call_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from clauditor._providers import announce_codex_cli_on_path

        announce_codex_cli_on_path()
        # Drain the first emission.
        capsys.readouterr()
        announce_codex_cli_on_path()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_constant_names_codex(self) -> None:
        """First durable substring — users must see ``codex`` so they
        know which CLI was detected on PATH."""
        from clauditor._providers import _CODEX_CLI_ON_PATH_ANNOUNCEMENT

        assert "codex" in _CODEX_CLI_ON_PATH_ANNOUNCEMENT

    def test_constant_names_path(self) -> None:
        """Second durable substring — users must see ``PATH`` so they
        know the helper is doing PATH discovery (not env-var check)."""
        from clauditor._providers import _CODEX_CLI_ON_PATH_ANNOUNCEMENT

        assert "PATH" in _CODEX_CLI_ON_PATH_ANNOUNCEMENT

    def test_constant_names_auth_json(self) -> None:
        """Third durable substring — users must see
        ``~/.codex/auth.json`` so they know where codex looks for
        credentials when neither env var is set."""
        from clauditor._providers import _CODEX_CLI_ON_PATH_ANNOUNCEMENT

        assert "~/.codex/auth.json" in _CODEX_CLI_ON_PATH_ANNOUNCEMENT

    def test_constant_does_not_interpolate_values(self) -> None:
        """Auth review #7 (#151 precedent): the announcement names
        env-var names / paths only; it MUST NOT interpolate values. A
        leaked secret in the constant would surface in the test text —
        fail closed."""
        from clauditor._providers import _CODEX_CLI_ON_PATH_ANNOUNCEMENT

        # No format placeholders for values.
        assert "{value" not in _CODEX_CLI_ON_PATH_ANNOUNCEMENT
        # No literal "sk-" prefixed tokens (canonical OpenAI/Codex key
        # shape).
        assert "sk-" not in _CODEX_CLI_ON_PATH_ANNOUNCEMENT

    def test_autouse_resets_between_tests_pair_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """First test of a pair — proves first-call emission works."""
        from clauditor._providers import announce_codex_cli_on_path

        announce_codex_cli_on_path()
        captured = capsys.readouterr()
        assert captured.err != ""

    def test_autouse_resets_between_tests_pair_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second test of the pair — if the autouse fixture did not
        reset the flag, this test would see silence (the flag would
        still be ``True`` from the first test). Seeing an emission
        here proves the fixture reset works."""
        from clauditor._providers import announce_codex_cli_on_path

        announce_codex_cli_on_path()
        captured = capsys.readouterr()
        assert captured.err != ""


class TestCheckCodexAuthPathBranch:
    """Three-branch strict-OR for :func:`check_codex_auth` (#175 US-002).

    DEC-001 / DEC-002 / DEC-004 / DEC-009 / DEC-010 of
    ``plans/super/175-codex-chatgpt-login-auth.md``. The pre-flight guard
    now accepts on any of:

    1. ``CODEX_API_KEY`` set (whitespace-trimmed non-empty) — pass silently.
    2. ``OPENAI_API_KEY`` set (whitespace-trimmed non-empty) — pass silently.
    3. ``codex`` binary on PATH (the new branch) — pass + fire the
       :func:`announce_codex_cli_on_path` one-shot stderr notice.

    Per DEC-009 the announcement fires only when the PATH branch is the
    load-bearing acceptance signal. Per DEC-010 the env-var branches
    short-circuit BEFORE the PATH probe so a CI run with ``CODEX_API_KEY``
    set never sees a noisy "codex CLI on PATH" notice even when the CLI
    happens to be installed.

    Tests use ``monkeypatch.setattr`` to override the autouse
    ``shutil.which → None`` pin from ``tests/conftest.py`` per
    ``.claude/rules/test-infra-shutil-which-coupling.md``. Each test
    that exercises the PATH branch also resets the
    :data:`_announced_codex_cli_on_path` flag.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to False so
        announcement assertions are deterministic."""
        monkeypatch.setattr(
            "clauditor._providers._auth._announced_codex_cli_on_path",
            False,
        )

    def test_codex_on_path_no_env_vars_passes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DEC-001 / DEC-002: PATH-only acceptance succeeds AND fires
        the announcement (DEC-009 — PATH is the load-bearing signal)."""
        from clauditor._providers import (
            _CODEX_CLI_ON_PATH_ANNOUNCEMENT,
            check_codex_auth,
        )
        from clauditor._providers import _auth as _auth_mod

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(
            _auth_mod.shutil,
            "which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        assert check_codex_auth("grade") is None
        captured = capsys.readouterr()
        assert _CODEX_CLI_ON_PATH_ANNOUNCEMENT in captured.err

    def test_codex_on_path_with_codex_env_silent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DEC-010: env-var branch short-circuits BEFORE the PATH probe.
        ``CODEX_API_KEY`` set + codex on PATH must pass silently — no
        announcement, since the env-var branch is the load-bearing
        accept signal, not PATH."""
        from clauditor._providers import _auth as _auth_mod
        from clauditor._providers import check_codex_auth

        monkeypatch.setenv("CODEX_API_KEY", "sk-codex-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(
            _auth_mod.shutil,
            "which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        assert check_codex_auth("grade") is None
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_codex_on_path_with_openai_env_silent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """DEC-010 sibling: ``OPENAI_API_KEY`` set + codex on PATH must
        also pass silently. The env-var short-circuit applies to either
        env-var branch."""
        from clauditor._providers import _auth as _auth_mod
        from clauditor._providers import check_codex_auth

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        monkeypatch.setattr(
            _auth_mod.shutil,
            "which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        assert check_codex_auth("grade") is None
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_codex_whitespace_env_on_path_passes_with_announcement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Whitespace-only env vars count as absent (existing helper
        contract); the PATH branch should fire and announce since it's
        the load-bearing signal."""
        from clauditor._providers import (
            _CODEX_CLI_ON_PATH_ANNOUNCEMENT,
            check_codex_auth,
        )
        from clauditor._providers import _auth as _auth_mod

        monkeypatch.setenv("CODEX_API_KEY", "   \t\n")
        monkeypatch.setenv("OPENAI_API_KEY", "  ")
        monkeypatch.setattr(
            _auth_mod.shutil,
            "which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        assert check_codex_auth("grade") is None
        captured = capsys.readouterr()
        assert _CODEX_CLI_ON_PATH_ANNOUNCEMENT in captured.err

    def test_neither_env_nor_path_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All three branches fail → raise. Verify the four durable
        substrings: the three pre-#175 anchors (``CODEX_API_KEY``,
        ``OPENAI_API_KEY``, ``platform.openai.com``) plus the NEW
        substring naming the codex CLI install path so users learn
        about the third acceptance path from the error message."""
        from clauditor._providers import (
            CodexAuthMissingError,
            check_codex_auth,
        )
        from clauditor._providers import _auth as _auth_mod

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Override autouse pin defensively; this test asserts the
        # raise path AND the new substring naming the codex CLI route.
        monkeypatch.setattr(_auth_mod.shutil, "which", lambda name: None)
        with pytest.raises(CodexAuthMissingError) as exc_info:
            check_codex_auth("grade")
        message = str(exc_info.value)
        # Three pre-#175 durable substrings — must still hold per DEC-004.
        assert "CODEX_API_KEY" in message
        assert "OPENAI_API_KEY" in message
        assert "platform.openai.com" in message
        # NEW DEC-004 durable substring: a mention of the codex CLI as
        # a third acceptance path. The literal ``"codex CLI"`` is the
        # pin; stylistic copy around it may change.
        assert "codex CLI" in message
        # Command-name interpolation still works.
        assert "clauditor grade" in message

    def test_announcement_one_shot_across_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Repeat PATH-branch acceptance in the same process fires the
        announcement exactly once (one-shot contract — same shape as
        :func:`announce_auto_codex_harness`)."""
        from clauditor._providers import _auth as _auth_mod
        from clauditor._providers import check_codex_auth

        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(
            _auth_mod.shutil,
            "which",
            lambda name: "/usr/bin/codex" if name == "codex" else None,
        )
        check_codex_auth("grade")
        # Drain first emission.
        first = capsys.readouterr().err
        assert first != ""
        check_codex_auth("validate")
        # Second call must be silent.
        second = capsys.readouterr().err
        assert second == ""


class TestCodexAuthMissingTemplateAcceptsPathBranch:
    """The :data:`_CODEX_AUTH_MISSING_TEMPLATE` must mention the codex
    CLI install path as a third acceptance route (DEC-004 of
    ``plans/super/175-codex-chatgpt-login-auth.md``) while preserving
    the three pre-#175 durable substrings."""

    def test_template_mentions_codex_cli(self) -> None:
        """New durable substring: ``codex CLI`` — users learn the
        third acceptance path from the missing-auth error message."""
        from clauditor._providers import _CODEX_AUTH_MISSING_TEMPLATE

        assert "codex CLI" in _CODEX_AUTH_MISSING_TEMPLATE

    def test_template_preserves_pre_175_substrings(self) -> None:
        """Three pre-#175 anchors (``CODEX_API_KEY``, ``OPENAI_API_KEY``,
        ``platform.openai.com``) must still hold so existing CI parsers
        and the substring-based test in :class:`TestCheckCodexAuth` keep
        matching."""
        from clauditor._providers import _CODEX_AUTH_MISSING_TEMPLATE

        assert "CODEX_API_KEY" in _CODEX_AUTH_MISSING_TEMPLATE
        assert "OPENAI_API_KEY" in _CODEX_AUTH_MISSING_TEMPLATE
        assert "platform.openai.com" in _CODEX_AUTH_MISSING_TEMPLATE
        # Command-name interpolation preserved.
        assert "{cmd_name}" in _CODEX_AUTH_MISSING_TEMPLATE


class TestCodexAuthJsonHelpers:
    """Unit coverage for the three pure helpers added in #177 US-001:
    ``_codex_auth_json_path``, ``_parse_codex_auth_json``, and
    ``_auth_mode_is_acceptable``.

    Traces to DEC-004, DEC-005, DEC-008, DEC-009, DEC-012, DEC-013,
    DEC-014 of ``plans/super/177-codex-auth-mode-conflict.md``.

    - ``_codex_auth_json_path()`` resolves ``$CODEX_HOME/auth.json`` when
      ``CODEX_HOME`` is set to a non-empty (whitespace-trimmed) string,
      else ``Path.home() / ".codex" / "auth.json"`` (DEC-009).
    - ``_parse_codex_auth_json(path)`` is a defensive read: returns
      ``None`` on file-not-found, ``OSError``, oversize (>1 MB),
      ``json.JSONDecodeError``, non-``dict`` root, or unicode-decode
      failure. Never raises. UTF-8 strict decode (DEC-005, DEC-012).
    - ``_auth_mode_is_acceptable(parsed)`` is a pure verdict: returns
      ``True`` when ``parsed is None``, or when ``parsed.get("auth_mode")``
      is missing / not a ``str`` / a ``str`` other than ``"chatgpt"``.
      Returns ``False`` only when
      ``isinstance(auth_mode, str) and auth_mode == "chatgpt"``
      (DEC-004, DEC-013).

    Helpers are private (leading ``_``) and NOT re-exported from
    ``_providers/__init__.py`` per Pattern 1 of
    ``.claude/rules/back-compat-shim-discipline.md``.
    """

    # ----- _parse_codex_auth_json: happy-path cases -----

    def test_parse_happy_path_apikey(self, tmp_path) -> None:
        """Case 1: well-formed ``auth.json`` with ``auth_mode=apikey``
        returns the parsed dict verbatim."""
        import json

        from clauditor._providers._auth import _parse_codex_auth_json

        payload = {"auth_mode": "apikey", "api_key": "sk-redacted"}
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _parse_codex_auth_json(auth_path)

        assert result == payload

    def test_parse_happy_path_chatgpt(self, tmp_path) -> None:
        """Case 2: well-formed ``auth.json`` with ``auth_mode=chatgpt``
        returns the parsed dict verbatim. The caller (the pure verdict
        helper) decides whether ``chatgpt`` is acceptable; the parser
        is content-agnostic."""
        import json

        from clauditor._providers._auth import _parse_codex_auth_json

        payload = {"auth_mode": "chatgpt", "tokens": {"access_token": "x"}}
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _parse_codex_auth_json(auth_path)

        assert result == payload

    # ----- _parse_codex_auth_json: failure-open cases (DEC-005) -----

    def test_parse_file_not_found_returns_none(self, tmp_path) -> None:
        """Case 3: missing file returns ``None`` (never raises). Most
        common case in CI where the user has not run ``codex login``."""
        from clauditor._providers._auth import _parse_codex_auth_json

        result = _parse_codex_auth_json(tmp_path / "does-not-exist.json")

        assert result is None

    def test_parse_malformed_json_returns_none(self, tmp_path) -> None:
        """Case 4: malformed JSON returns ``None``. Real-world failure
        mode: partially written file, truncated by a crash mid-write."""
        from clauditor._providers._auth import _parse_codex_auth_json

        auth_path = tmp_path / "auth.json"
        auth_path.write_text("{this is not valid json", encoding="utf-8")

        result = _parse_codex_auth_json(auth_path)

        assert result is None

    def test_parse_oversize_returns_none(self, tmp_path) -> None:
        """Case 5: file > 1 MB returns ``None`` (DEC-012). Real codex
        ``auth.json`` files are well under 4 KB; cap defends against
        symlink-bomb / accidental oversize."""
        from clauditor._providers._auth import _parse_codex_auth_json

        auth_path = tmp_path / "auth.json"
        # 1 MB + 1 byte of valid-looking JSON wrapping a giant string.
        # We don't need it to *parse* — the size check must fire before
        # the json.loads call.
        oversize_blob = '{"auth_mode": "apikey", "padding": "' + (
            "x" * (1024 * 1024 + 1)
        ) + '"}'
        auth_path.write_text(oversize_blob, encoding="utf-8")

        result = _parse_codex_auth_json(auth_path)

        assert result is None

    def test_parse_non_dict_root_returns_none(self, tmp_path) -> None:
        """Case 6: JSON whose top-level value is not a ``dict`` (e.g. a
        list) returns ``None``. Downstream verdict helper is typed
        ``dict | None``; a list would crash a ``.get(...)`` call."""
        import json

        from clauditor._providers._auth import _parse_codex_auth_json

        auth_path = tmp_path / "auth.json"
        auth_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        result = _parse_codex_auth_json(auth_path)

        assert result is None

    # ----- _auth_mode_is_acceptable: verdict cases -----

    def test_verdict_missing_auth_mode_returns_true(self) -> None:
        """Case 7: parsed dict without an ``auth_mode`` key is
        acceptable (failure-open per DEC-005). A future codex auth
        schema that drops the field should not block clauditor."""
        from clauditor._providers._auth import _auth_mode_is_acceptable

        assert _auth_mode_is_acceptable({"unrelated": "value"}) is True

    def test_verdict_non_string_auth_mode_returns_true(self) -> None:
        """Case 8: ``auth_mode = true`` (JSON bool) is acceptable —
        the defensive ``isinstance(..., str)`` guard (DEC-013) returns
        before the ``== "chatgpt"`` comparison so a bool / int / None
        never enters the string comparison."""
        from clauditor._providers._auth import _auth_mode_is_acceptable

        assert _auth_mode_is_acceptable({"auth_mode": True}) is True

    def test_verdict_chatgpt_returns_false(self) -> None:
        """Case 9: ``auth_mode = "chatgpt"`` is the ONE refused value
        (DEC-004 — conservative enumeration). The caller raises
        ``CodexAuthMissingError`` with the chatgpt-mode template."""
        from clauditor._providers._auth import _auth_mode_is_acceptable

        assert _auth_mode_is_acceptable({"auth_mode": "chatgpt"}) is False

    def test_verdict_apikey_returns_true(self) -> None:
        """Case 10: ``auth_mode = "apikey"`` is acceptable. Any string
        other than ``"chatgpt"`` (exact match) passes."""
        from clauditor._providers._auth import _auth_mode_is_acceptable

        assert _auth_mode_is_acceptable({"auth_mode": "apikey"}) is True

    def test_verdict_none_parsed_returns_true(self) -> None:
        """Verdict's ``parsed is None`` branch (parse failure
        failure-open per DEC-005). The caller passes ``None`` from
        ``_parse_codex_auth_json`` when the file is missing /
        malformed / oversize."""
        from clauditor._providers._auth import _auth_mode_is_acceptable

        assert _auth_mode_is_acceptable(None) is True

    # ----- _codex_auth_json_path: env-var override (DEC-009) -----

    def test_path_codex_home_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Case 11: ``CODEX_HOME`` set to a non-empty value resolves
        to ``$CODEX_HOME/auth.json`` (mirrors the codex CLI's own
        behavior — DEC-009)."""
        from clauditor._providers._auth import _codex_auth_json_path

        codex_home = tmp_path / "custom-codex-home"
        codex_home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        result = _codex_auth_json_path()

        assert result == codex_home / "auth.json"

    def test_path_codex_home_whitespace_only_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case 12: ``CODEX_HOME`` set to a whitespace-only string is
        treated as unset (same shape as ``_api_key_is_set`` /
        ``_codex_api_key_is_set``); falls back to
        ``~/.codex/auth.json``."""
        from pathlib import Path

        from clauditor._providers._auth import _codex_auth_json_path

        monkeypatch.setenv("CODEX_HOME", "   ")

        result = _codex_auth_json_path()

        assert result == Path.home() / ".codex" / "auth.json"

    def test_path_codex_home_unset_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``CODEX_HOME`` unset falls back to ``~/.codex/auth.json``
        (the canonical codex CLI credential location)."""
        from pathlib import Path

        from clauditor._providers._auth import _codex_auth_json_path

        monkeypatch.delenv("CODEX_HOME", raising=False)

        result = _codex_auth_json_path()

        assert result == Path.home() / ".codex" / "auth.json"

    # ----- back-compat-shim-discipline: helpers NOT re-exported -----

    def test_helpers_not_reexported_from_package_init(self) -> None:
        """Per Pattern 1 of
        ``.claude/rules/back-compat-shim-discipline.md``, the three
        new private helpers (leading ``_``) MUST NOT be re-exported
        from ``clauditor._providers/__init__.py``. They are
        I/O-bearing private helpers with no need for cross-module
        identity invariants."""
        import clauditor._providers as providers_pkg

        assert not hasattr(providers_pkg, "_codex_auth_json_path")
        assert not hasattr(providers_pkg, "_parse_codex_auth_json")
        assert not hasattr(providers_pkg, "_auth_mode_is_acceptable")
