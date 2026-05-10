"""Tests for the pricing helpers in ``clauditor._providers._pricing``.

US-001 of ``plans/super/169-pricing-cost-estimator.md``: covers the
core ``estimate_cost`` helper plus the price-table metadata. The
announcement helpers (staleness, unknown-model) land in US-002 and
US-003 and have their own dedicated test classes there.

Mirror shape: ``tests/test_providers_retry.py`` — pure-compute
sibling with no SDK or I/O concerns. Every test calls the public
helper or reads a module-level constant; no patches needed beyond
the bool-vs-int validation cases.
"""

from __future__ import annotations

import datetime

import pytest

from clauditor._providers._pricing import (
    _LAST_VERIFIED,
    _PRICING_TABLE,
    _PRICING_TABLE_VERSION,
    estimate_cost,
)

_ANTHROPIC_MODELS = ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"]
_OPENAI_MODELS = ["gpt-5.4", "gpt-5.4-mini", "o4-mini"]


class TestEstimateCost:
    @pytest.mark.parametrize("model", _ANTHROPIC_MODELS)
    def test_known_anthropic_models_return_positive_float(self, model: str) -> None:
        # Per DEC-004: Anthropic table coverage. Known (provider, model)
        # pairs must produce a positive float for non-zero token input.
        result = estimate_cost("anthropic", model, 1000, 500)
        assert isinstance(result, float)
        assert result > 0.0

    @pytest.mark.parametrize("model", _OPENAI_MODELS)
    def test_known_openai_models_return_positive_float(self, model: str) -> None:
        # Per DEC-004: OpenAI table coverage. Same shape as the
        # Anthropic counterpart.
        result = estimate_cost("openai", model, 1000, 500)
        assert isinstance(result, float)
        assert result > 0.0

    def test_unknown_provider_returns_none(self) -> None:
        # Per DEC-002 / DEC-005: unknown provider is a graceful
        # lookup miss, not a contract violation.
        assert estimate_cost("vertex", "claude-sonnet-4-6", 1000, 500) is None

    def test_unknown_model_returns_none(self) -> None:
        # Per DEC-002 / DEC-005: known provider + unknown model is also
        # a graceful lookup miss.
        assert (
            estimate_cost("anthropic", "claude-3-5-sonnet-old", 1000, 500) is None
        )

    def test_zero_tokens_returns_zero_cost(self) -> None:
        # Edge case: a known model with no tokens reported costs $0.0.
        assert estimate_cost("anthropic", "claude-sonnet-4-6", 0, 0) == 0.0

    def test_reasoning_tokens_billed_at_output_rate(self) -> None:
        # Per DEC-001 / Research notes: reasoning tokens are billed at
        # the model's output rate. The two computations below must be
        # numerically equal.
        with_reasoning = estimate_cost(
            "openai", "o4-mini", 100, 50, reasoning_tokens=200
        )
        rolled_into_output = estimate_cost(
            "openai", "o4-mini", 100, 250, reasoning_tokens=None
        )
        assert with_reasoning == pytest.approx(rolled_into_output)

    def test_reasoning_tokens_zero_is_equivalent_to_none(self) -> None:
        # Defensive: passing reasoning_tokens=0 must equal not passing
        # it at all (both add 0 to the effective output count).
        a = estimate_cost("openai", "o4-mini", 100, 50, reasoning_tokens=0)
        b = estimate_cost("openai", "o4-mini", 100, 50, reasoning_tokens=None)
        assert a == pytest.approx(b)


class TestEstimateCostInputValidation:
    @pytest.mark.parametrize(
        "kwarg", ["input_tokens", "output_tokens", "reasoning_tokens"]
    )
    def test_bool_int_arg_raises(self, kwarg: str) -> None:
        # Per DEC-005 + .claude/rules/constant-with-type-info.md: bool
        # is an int subclass in Python; reject explicitly so a
        # ``True`` / ``False`` value cannot sneak through as 1/0.
        kwargs = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        kwargs[kwarg] = True
        with pytest.raises(ValueError):
            estimate_cost(**kwargs)
        # And the other bool value too.
        kwargs[kwarg] = False
        with pytest.raises(ValueError):
            estimate_cost(**kwargs)

    @pytest.mark.parametrize(
        "kwarg", ["input_tokens", "output_tokens", "reasoning_tokens"]
    )
    def test_int_one_does_not_raise(self, kwarg: str) -> None:
        # Sanity counterpart to the bool test: a real int (not bool)
        # value of 1 must NOT raise. Confirms the bool guard does not
        # over-reject.
        kwargs = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        kwargs[kwarg] = 1
        result = estimate_cost(**kwargs)
        assert result is not None and result >= 0.0

    @pytest.mark.parametrize(
        "kwarg", ["input_tokens", "output_tokens", "reasoning_tokens"]
    )
    def test_negative_tokens_raises(self, kwarg: str) -> None:
        # Per DEC-005: negative token counts are a contract violation.
        kwargs = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        kwargs[kwarg] = -1
        with pytest.raises(ValueError):
            estimate_cost(**kwargs)

    def test_non_int_tokens_raises(self) -> None:
        # Per DEC-005: a string-typed token count is a contract
        # violation, not a lookup miss.
        with pytest.raises(ValueError):
            estimate_cost("anthropic", "claude-sonnet-4-6", "100", 50)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            estimate_cost("anthropic", "claude-sonnet-4-6", 100, "50")  # type: ignore[arg-type]

    def test_non_string_provider_raises(self) -> None:
        # Per DEC-005: non-string provider is a contract violation.
        with pytest.raises(ValueError):
            estimate_cost(42, "claude-sonnet-4-6", 100, 50)  # type: ignore[arg-type]

    def test_non_string_model_raises(self) -> None:
        # Per DEC-005: non-string model is a contract violation.
        with pytest.raises(ValueError):
            estimate_cost("anthropic", None, 100, 50)  # type: ignore[arg-type]


class TestPricingTableMetadata:
    def test_pricing_table_version_is_int(self) -> None:
        assert isinstance(_PRICING_TABLE_VERSION, int)
        assert _PRICING_TABLE_VERSION >= 1

    def test_last_verified_is_iso_date(self) -> None:
        # Robustness: the constant must round-trip through
        # date.fromisoformat so the staleness helper (US-002) can
        # parse it without a special case.
        parsed = datetime.date.fromisoformat(_LAST_VERIFIED)
        assert isinstance(parsed, datetime.date)

    def test_table_contains_expected_models(self) -> None:
        # Per DEC-004: every model named in the plan must be present.
        assert "anthropic" in _PRICING_TABLE
        assert "openai" in _PRICING_TABLE
        for model in _ANTHROPIC_MODELS:
            assert model in _PRICING_TABLE["anthropic"], (
                f"missing Anthropic model {model!r}"
            )
        for model in _OPENAI_MODELS:
            assert model in _PRICING_TABLE["openai"], (
                f"missing OpenAI model {model!r}"
            )
