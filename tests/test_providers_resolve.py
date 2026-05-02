"""Tests for #146 US-001 pure helpers: ``infer_provider_from_model``,
``resolve_grading_provider``, ``resolve_grading_model``.

Covers DEC-001 / DEC-003 / DEC-004 of
``plans/super/146-grading-provider-precedence.md``: strict
prefix-match auto-inference, four-layer precedence with auto
delegation, provider-aware default-model picking. Pure helpers per
``.claude/rules/pure-compute-vs-io-split.md`` — no env reads, no
SDK calls, no patching needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clauditor._providers import (
    infer_provider_from_model,
    resolve_grading_model,
    resolve_grading_provider,
)


class TestInferProviderFromModel:
    """DEC-003: strict prefix match. Unknown prefixes raise ValueError."""

    def test_infer_anthropic_for_claude_prefix(self) -> None:
        assert infer_provider_from_model("claude-sonnet-4-6") == "anthropic"

    def test_infer_anthropic_for_claude_haiku(self) -> None:
        assert infer_provider_from_model("claude-haiku-3-5") == "anthropic"

    def test_infer_openai_for_gpt_prefix(self) -> None:
        assert infer_provider_from_model("gpt-5.4") == "openai"

    def test_infer_openai_for_gpt_4o(self) -> None:
        assert infer_provider_from_model("gpt-4o") == "openai"

    def test_infer_openai_for_o1_prefix(self) -> None:
        """O-series reasoning model: ``o1`` → openai."""
        assert infer_provider_from_model("o1") == "openai"

    def test_infer_openai_for_o4_mini(self) -> None:
        """O-series with suffix: ``o4-mini`` → openai."""
        assert infer_provider_from_model("o4-mini") == "openai"

    def test_infer_openai_for_o3_pro(self) -> None:
        assert infer_provider_from_model("o3-pro") == "openai"

    def test_infer_raises_for_unknown_prefix(self) -> None:
        """Typo case: ``gtp-5.4`` (transposed g/t) raises rather than
        silently routing to anthropic."""
        with pytest.raises(
            ValueError, match=r"cannot infer provider from unknown model"
        ):
            infer_provider_from_model("gtp-5.4")

    def test_infer_raises_for_gemini(self) -> None:
        with pytest.raises(ValueError, match=r"unknown model prefix"):
            infer_provider_from_model("gemini-pro")

    def test_infer_raises_for_llama(self) -> None:
        with pytest.raises(ValueError, match=r"unknown model prefix"):
            infer_provider_from_model("llama-3-70b")

    def test_infer_raises_for_bare_o(self) -> None:
        """``o`` alone (no digits) is NOT an o-series match — must
        have at least one digit. Falls through to unknown-prefix
        ValueError."""
        with pytest.raises(ValueError, match=r"unknown model prefix"):
            infer_provider_from_model("o")

    def test_infer_raises_for_oa_prefix(self) -> None:
        """``oai`` (letter, not digit) is NOT an o-series match."""
        with pytest.raises(ValueError, match=r"unknown model prefix"):
            infer_provider_from_model("oai-2")

    def test_infer_raises_for_none_with_actionable_message(self) -> None:
        """None case: caller has no model AND provider auto-resolves.
        Message names both layers ("grading_provider", "grading_model")."""
        with pytest.raises(
            ValueError, match=r"provide grading_provider or grading_model"
        ):
            infer_provider_from_model(None)

    def test_infer_raises_for_empty_string(self) -> None:
        with pytest.raises(ValueError, match=r"non-empty string"):
            infer_provider_from_model("")

    def test_infer_raises_for_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match=r"non-empty string"):
            infer_provider_from_model("   ")

    def test_infer_strips_surrounding_whitespace(self) -> None:
        """Whitespace-padded model names still match."""
        assert infer_provider_from_model("  claude-sonnet-4-6  ") == "anthropic"
        assert infer_provider_from_model("\tgpt-5.4\n") == "openai"

    def test_infer_raises_for_non_string(self) -> None:
        """Type guard: int / list / dict are rejected."""
        with pytest.raises(ValueError, match=r"must be str or None"):
            infer_provider_from_model(123)  # type: ignore[arg-type]


class TestResolveGradingProvider:
    """DEC-001 / DEC-003: four-layer precedence (CLI > env > spec >
    default 'auto') with auto-inference delegation."""

    def test_cli_wins_over_env_spec_default(self) -> None:
        """CLI override beats every other layer, even when all are set."""
        result = resolve_grading_provider(
            "openai", "anthropic", "anthropic", "claude-sonnet-4-6"
        )
        assert result == "openai"

    def test_env_wins_over_spec_default(self) -> None:
        assert (
            resolve_grading_provider(None, "openai", "anthropic", "claude-sonnet-4-6")
            == "openai"
        )

    def test_spec_wins_over_default(self) -> None:
        assert (
            resolve_grading_provider(None, None, "openai", "claude-sonnet-4-6")
            == "openai"
        )

    def test_default_is_auto_delegates_to_inference(self) -> None:
        """All layers None → default 'auto' → infer from model."""
        assert resolve_grading_provider(None, None, None, "gpt-5.4") == "openai"

    def test_default_auto_infers_anthropic_from_claude_model(self) -> None:
        assert (
            resolve_grading_provider(None, None, None, "claude-sonnet-4-6")
            == "anthropic"
        )

    def test_cli_auto_delegates_to_inference(self) -> None:
        """``--grading-provider auto`` is a SET value (short-circuits
        env / spec) but still delegates to inference."""
        result = resolve_grading_provider("auto", "openai", "openai", "gpt-5.4")
        assert result == "openai"

    def test_cli_auto_infers_anthropic(self) -> None:
        assert (
            resolve_grading_provider("auto", None, None, "claude-sonnet-4-6")
            == "anthropic"
        )

    def test_env_auto_delegates_to_inference(self) -> None:
        """Env-set 'auto' wins over spec 'anthropic' but still infers
        from model."""
        assert (
            resolve_grading_provider(None, "auto", "anthropic", "gpt-5.4") == "openai"
        )

    def test_spec_auto_delegates_to_inference(self) -> None:
        assert (
            resolve_grading_provider(None, None, "auto", "gpt-5.4") == "openai"
        )

    def test_invalid_cli_value_names_layer(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"CLI --grading-provider must be one of "
            r"'anthropic', 'openai', 'auto', got 'foo'",
        ):
            resolve_grading_provider("foo", None, None, "claude-sonnet-4-6")

    def test_invalid_env_value_names_layer(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"CLAUDITOR_GRADING_PROVIDER must be one of "
            r"'anthropic', 'openai', 'auto', got 'foo'",
        ):
            resolve_grading_provider(None, "foo", None, "claude-sonnet-4-6")

    def test_invalid_spec_value_names_layer(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"EvalSpec\.grading_provider must be one of "
            r"'anthropic', 'openai', 'auto', got 'foo'",
        ):
            resolve_grading_provider(None, None, "foo", "claude-sonnet-4-6")

    def test_cli_override_short_circuits_invalid_env(self) -> None:
        """CLI is checked first — if CLI is set and valid, env validity
        is not consulted (cheap-out optimization preserves DEC-007's
        first-non-None-wins semantics)."""
        # Note: this is a behavior assertion, not a contract guarantee.
        # The CLI seam should still validate env separately to give a
        # clean error message before resolution. But the helper itself
        # short-circuits.
        result = resolve_grading_provider("openai", "totally-invalid", None, None)
        assert result == "openai"

    def test_default_auto_with_none_model_raises(self) -> None:
        """All layers None AND model None → resolver delegates to
        inference → infer raises 'provide grading_provider or
        grading_model' (CLI maps to exit 2)."""
        with pytest.raises(
            ValueError, match=r"provide grading_provider or grading_model"
        ):
            resolve_grading_provider(None, None, None, None)

    def test_explicit_anthropic_with_none_model_skips_inference(self) -> None:
        """Explicit 'anthropic' at any layer means the resolver does
        NOT need a model — auto-inference is bypassed."""
        assert resolve_grading_provider("anthropic", None, None, None) == "anthropic"
        assert resolve_grading_provider(None, "anthropic", None, None) == "anthropic"
        assert resolve_grading_provider(None, None, "anthropic", None) == "anthropic"

    def test_explicit_openai_with_none_model_skips_inference(self) -> None:
        assert resolve_grading_provider("openai", None, None, None) == "openai"

    def test_default_auto_with_unknown_model_raises(self) -> None:
        """Default-auto resolution + unknown model prefix raises with
        the typo-hint message from infer_provider_from_model."""
        with pytest.raises(
            ValueError, match=r"cannot infer provider from unknown model"
        ):
            resolve_grading_provider(None, None, None, "gtp-5.4")


class TestResolveGradingModel:
    """DEC-004: provider-aware default-model resolution. Explicit
    ``eval_spec.grading_model`` wins; otherwise pick the
    provider-default constant."""

    def test_explicit_grading_model_wins(self) -> None:
        spec = SimpleNamespace(grading_model="claude-opus-4-1")
        assert resolve_grading_model(spec, "anthropic") == "claude-opus-4-1"

    def test_explicit_openai_grading_model_wins(self) -> None:
        spec = SimpleNamespace(grading_model="gpt-4o-mini")
        assert resolve_grading_model(spec, "openai") == "gpt-4o-mini"

    def test_explicit_model_wins_even_when_provider_mismatch(self) -> None:
        """The helper does NOT cross-validate provider/model — that's
        DEC-002's job (no load-time cross-validation; provider-side
        SDK catches at API call time)."""
        spec = SimpleNamespace(grading_model="claude-sonnet-4-6")
        assert resolve_grading_model(spec, "openai") == "claude-sonnet-4-6"

    def test_anthropic_default_when_grading_model_none(self) -> None:
        spec = SimpleNamespace(grading_model=None)
        assert resolve_grading_model(spec, "anthropic") == "claude-sonnet-4-6"

    def test_openai_default_when_grading_model_none(self) -> None:
        from clauditor._providers import _openai as _openai_mod

        spec = SimpleNamespace(grading_model=None)
        assert resolve_grading_model(spec, "openai") == _openai_mod.DEFAULT_MODEL_L3

    def test_anthropic_default_when_eval_spec_none(self) -> None:
        """``None`` eval_spec is treated as 'no spec, no model' — the
        provider-aware default fires (used by propose-eval and
        suggest, which have no eval_spec at the CLI seam)."""
        assert resolve_grading_model(None, "anthropic") == "claude-sonnet-4-6"

    def test_openai_default_when_eval_spec_none(self) -> None:
        from clauditor._providers import _openai as _openai_mod

        assert resolve_grading_model(None, "openai") == _openai_mod.DEFAULT_MODEL_L3

    def test_unknown_provider_raises(self) -> None:
        spec = SimpleNamespace(grading_model=None)
        with pytest.raises(
            ValueError, match=r"unknown provider 'vertex'"
        ):
            resolve_grading_model(spec, "vertex")

    def test_unknown_provider_raises_even_with_eval_spec_none(self) -> None:
        with pytest.raises(ValueError, match=r"unknown provider"):
            resolve_grading_model(None, "bedrock")

    def test_eval_spec_without_grading_model_attribute(self) -> None:
        """Duck-typed eval_spec: a SimpleNamespace without a
        ``grading_model`` attribute behaves like None (provider-aware
        default fires)."""
        spec = SimpleNamespace()
        assert resolve_grading_model(spec, "anthropic") == "claude-sonnet-4-6"
