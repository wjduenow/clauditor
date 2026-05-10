"""Tests for the pricing helpers in ``clauditor._providers._pricing``.

US-001 of ``plans/super/169-pricing-cost-estimator.md``: covers the
core ``estimate_cost`` helper plus the price-table metadata. US-002
adds :class:`TestStaleAnnouncement` covering the one-shot stderr
warning that fires when the rate table is older than 90 days. The
unknown-model announcement lands in US-003 with its own class.

Mirror shape: ``tests/test_providers_retry.py`` — pure-compute
sibling with no SDK or I/O concerns. Every test calls the public
helper or reads a module-level constant; no patches needed beyond
the bool-vs-int validation cases (and the per-test
flag/date-pin patches in :class:`TestStaleAnnouncement`).
"""

from __future__ import annotations

import datetime

import pytest

from clauditor._providers._pricing import (
    _LAST_VERIFIED,
    _PRICING_TABLE,
    _PRICING_TABLE_VERSION,
    announce_pricing_table_stale_if_old,
    announce_unknown_model,
    compute_iteration_cost_usd,
    estimate_cost,
)
from clauditor.grader import ExtractionReport
from clauditor.quality_grader import GradingReport
from clauditor.schemas import GradeThresholds

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


class TestStaleAnnouncement:
    """DEC-003 (#169 US-002): one-shot stderr warning when the pricing
    rate table is older than 90 days at ``estimate_cost`` call time.

    Parallel to
    ``tests/test_providers_auth.py::TestAnnounceImplicitNoApiKey`` —
    same autouse-reset pattern; same one-shot-per-process contract.
    The :data:`_today` indirection alias is patched per-test to pin
    the wall-clock date deterministically per
    ``.claude/rules/monotonic-time-indirection.md``.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every test starts with the one-shot flag set to ``False``."""
        monkeypatch.setattr(
            "clauditor._providers._pricing._announced_pricing_table_stale",
            False,
        )

    def test_no_warning_when_within_90_days(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # _LAST_VERIFIED == "2026-05-09"; pin today to the day after so
        # days_old == 1 (well under the 90-day threshold) and the
        # announcement does NOT fire.
        monkeypatch.setattr(
            "clauditor._providers._pricing._today",
            lambda: datetime.date(2026, 5, 10),
        )
        result = estimate_cost("anthropic", "claude-sonnet-4-6", 100, 50)
        assert result is not None
        captured = capsys.readouterr()
        # Anchor on the durable substring rather than full-message
        # equality: the announcement is absent, not just shaped
        # differently.
        assert "pricing" not in captured.err.lower()
        assert "90 days" not in captured.err

    def test_warning_fires_when_over_90_days(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # _LAST_VERIFIED == "2026-05-09"; pin today to ~7 months later
        # so days_old > 90 and the warning fires. Pin the durable
        # substrings per the docstring contract on
        # _PRICING_TABLE_STALE_ANNOUNCEMENT.
        monkeypatch.setattr(
            "clauditor._providers._pricing._today",
            lambda: datetime.date(2027, 1, 1),
        )
        result = estimate_cost("anthropic", "claude-sonnet-4-6", 100, 50)
        assert result is not None
        captured = capsys.readouterr()
        assert "90 days" in captured.err
        assert "pricing" in captured.err.lower()
        assert (
            "platform.claude.com" in captured.err
            or "openai.com/api/pricing" in captured.err
        )

    def test_warning_fires_only_once_per_process(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Stale-date pin; two calls; the warning text appears exactly
        # once across both calls' combined stderr.
        monkeypatch.setattr(
            "clauditor._providers._pricing._today",
            lambda: datetime.date(2027, 1, 1),
        )
        estimate_cost("anthropic", "claude-sonnet-4-6", 100, 50)
        estimate_cost("anthropic", "claude-sonnet-4-6", 100, 50)
        captured = capsys.readouterr()
        # Count occurrences of a narrow durable substring — using a
        # phrase distinctive to this announcement so unrelated stderr
        # could not match.
        assert captured.err.count("pricing table") == 1

    def test_malformed_last_verified_treats_as_stale(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A typo in _LAST_VERIFIED ("2026-05-9" — single-digit day, not
        # ISO-8601) must NOT crash the production grading run; the
        # defensive fallback in announce_pricing_table_stale_if_old
        # treats parse failure as "stale" so the maintainer sees a
        # warning that points them at the file to fix.
        monkeypatch.setattr(
            "clauditor._providers._pricing._LAST_VERIFIED", "2026-05-9"
        )
        # Pin today to a known date so the test does not depend on
        # the wall-clock; the code path under test does not consult
        # _today() in the parse-failure branch (days_old hardcoded to
        # 9999), but pinning keeps the test deterministic.
        monkeypatch.setattr(
            "clauditor._providers._pricing._today",
            lambda: datetime.date(2026, 5, 10),
        )
        result = estimate_cost("anthropic", "claude-sonnet-4-6", 100, 50)
        assert result is not None
        captured = capsys.readouterr()
        assert "pricing" in captured.err.lower()
        assert "90 days" in captured.err

    def test_announce_helper_directly(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Standalone-helper invocation must behave the same as the
        # estimate_cost-routed path: stale-date pin produces the
        # warning. Confirms the helper is independently testable per
        # the announcement-family canonical shape (#95 US-002 et al.).
        monkeypatch.setattr(
            "clauditor._providers._pricing._today",
            lambda: datetime.date(2027, 1, 1),
        )
        announce_pricing_table_stale_if_old()
        captured = capsys.readouterr()
        assert "90 days" in captured.err
        assert "pricing" in captured.err.lower()


def _make_grading_report(
    *,
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 1000,
    output_tokens: int = 500,
) -> GradingReport:
    """Minimal :class:`GradingReport` fixture for cost-composition tests."""
    return GradingReport(
        skill_name="test",
        results=[],
        model=model,
        thresholds=GradeThresholds(),
        metrics={},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_extraction_report(
    *,
    model: str = "claude-haiku-4-5",
    input_tokens: int = 200,
    output_tokens: int = 100,
) -> ExtractionReport:
    """Minimal :class:`ExtractionReport` fixture for cost-composition tests."""
    return ExtractionReport(
        skill_name="test",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class TestComputeIterationCostUsd:
    def test_grading_report_only_returns_cost(self) -> None:
        # Per DEC-001: grader-only scope. With extraction_report=None,
        # the L2 contribution is 0.0 (Layer 2 didn't run) and the
        # composition equals the L3 cost alone.
        grading = _make_grading_report(input_tokens=1000, output_tokens=500)
        result = compute_iteration_cost_usd(grading, None, "anthropic")
        expected = estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 500)
        assert result == expected
        assert result is not None and result > 0.0

    def test_with_extraction_report_sums_costs(self) -> None:
        # L2 + L3 sum: when both reports are present, the composition
        # routes each through estimate_cost and returns the sum.
        grading = _make_grading_report(
            model="claude-sonnet-4-6", input_tokens=1000, output_tokens=500
        )
        extraction = _make_extraction_report(
            model="claude-haiku-4-5", input_tokens=200, output_tokens=100
        )
        result = compute_iteration_cost_usd(grading, extraction, "anthropic")
        l3 = estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 500)
        l2 = estimate_cost("anthropic", "claude-haiku-4-5", 200, 100)
        assert l2 is not None and l3 is not None
        assert result == pytest.approx(l2 + l3)

    def test_unknown_grading_model_returns_none_no_extraction(self) -> None:
        # Per DEC-002: any internal estimate_cost None → composition
        # None. Unknown grading model with no extraction is the
        # simplest miss path.
        grading = _make_grading_report(model="claude-3-5-sonnet-old")
        result = compute_iteration_cost_usd(grading, None, "anthropic")
        assert result is None

    def test_unknown_grading_model_returns_none_with_extraction(self) -> None:
        # Per DEC-002 / all-or-nothing: an unknown grading model
        # short-circuits to None even when the extraction lookup
        # would succeed.
        grading = _make_grading_report(model="claude-3-5-sonnet-old")
        extraction = _make_extraction_report(model="claude-haiku-4-5")
        result = compute_iteration_cost_usd(grading, extraction, "anthropic")
        assert result is None

    def test_unknown_extraction_model_returns_none(self) -> None:
        # Per DEC-002 / all-or-nothing: an unknown extraction model
        # produces None even when the grading lookup succeeds.
        grading = _make_grading_report(model="claude-sonnet-4-6")
        extraction = _make_extraction_report(model="claude-haiku-2-old")
        result = compute_iteration_cost_usd(grading, extraction, "anthropic")
        assert result is None

    def test_unknown_provider_returns_none(self) -> None:
        # An unknown provider misses for both layers (estimate_cost
        # short-circuits on the first lookup); composition returns
        # None per DEC-002.
        grading = _make_grading_report()
        result = compute_iteration_cost_usd(grading, None, "vertex")
        assert result is None

    def test_extraction_report_with_zero_tokens(self) -> None:
        # An ExtractionReport with zero tokens contributes 0.0 to the
        # sum (a valid lookup, not a miss); total equals L3 cost.
        # Distinct from extraction_report=None which means "Layer 2
        # didn't run" — both shapes are legal but reach the same
        # numeric outcome here.
        grading = _make_grading_report(input_tokens=1000, output_tokens=500)
        extraction = _make_extraction_report(
            model="claude-haiku-4-5", input_tokens=0, output_tokens=0
        )
        result = compute_iteration_cost_usd(grading, extraction, "anthropic")
        l3 = estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 500)
        assert l3 is not None
        assert result == pytest.approx(l3)


class TestUnknownModelAnnouncement:
    """One-shot stderr warning per (provider, model) pair when the
    provider is recognized but the model is not in the rate table.

    DEC-006 of ``plans/super/169-pricing-cost-estimator.md`` (US-003).
    Mirror shape to :class:`TestStaleAnnouncement` but keyed on a
    ``set[tuple[str, str]]`` of pairs already announced rather than a
    single boolean. Distinct unknown models each warn once; the same
    pair on a repeat call is silent. Unknown providers do NOT trigger
    this warning per DEC-006 (different code path).

    Each test pins ``_today`` close to ``_LAST_VERIFIED`` to suppress
    the unrelated staleness warning from US-002, AND resets the
    ``_announced_unknown_models`` set per the announcement-family
    canonical reset pattern.
    """

    @pytest.fixture(autouse=True)
    def _reset_announcement_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reset per-pair set + suppress US-002 stale warning noise."""
        # Fresh empty set per test so prior-test pairs do not bleed.
        monkeypatch.setattr(
            "clauditor._providers._pricing._announced_unknown_models",
            set(),
        )
        # Also flip the staleness flag so the US-002 warning never
        # fires from the top of estimate_cost during these tests; this
        # keeps capsys.err containing only the unknown-model notice.
        monkeypatch.setattr(
            "clauditor._providers._pricing._announced_pricing_table_stale",
            True,
        )

    def test_unknown_model_emits_warning_first_call(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Known provider, unknown model → returns None and emits the
        # warning to stderr. All four durable substrings must appear:
        # "pricing:", "not in rate table", the literal model, and the
        # literal provider.
        result = estimate_cost("anthropic", "claude-fake-1", 100, 50)
        assert result is None
        captured = capsys.readouterr()
        assert "pricing:" in captured.err
        assert "not in rate table" in captured.err
        assert "claude-fake-1" in captured.err
        assert "anthropic" in captured.err

    def test_unknown_model_silent_on_repeated_call(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Same (provider, model) twice — the announcement fires only on
        # the first call. Count occurrences of the literal model name
        # in captured stderr; expect exactly one even though
        # estimate_cost was called twice.
        estimate_cost("anthropic", "claude-fake-1", 100, 50)
        estimate_cost("anthropic", "claude-fake-1", 200, 100)
        captured = capsys.readouterr()
        # The load-bearing assertion: the announcement fired exactly
        # once across both calls. Counting "not in rate table" (a phrase
        # unique to this announcement) avoids false positives from
        # unrelated stderr and is robust to template changes that move
        # the {model!r} interpolation around.
        assert captured.err.count("not in rate table") == 1

    def test_different_unknown_models_each_warn(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Two distinct unknown models — each gets its own first-call
        # warning. Different from the staleness flag (single bool); the
        # set-keyed contract means distinct pairs do not mute each other.
        estimate_cost("anthropic", "claude-fake-1", 100, 50)
        estimate_cost("anthropic", "claude-fake-2", 100, 50)
        captured = capsys.readouterr()
        assert "claude-fake-1" in captured.err
        assert "claude-fake-2" in captured.err
        # Two distinct first-call announcements landed.
        assert captured.err.count("not in rate table") == 2

    def test_known_provider_known_model_no_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Happy path: known (provider, model) pair returns a positive
        # float and emits NO unknown-model warning. The staleness flag
        # is pinned to True via the autouse fixture so the US-002
        # warning also stays silent.
        result = estimate_cost("anthropic", "claude-sonnet-4-6", 100, 50)
        assert result is not None and result > 0.0
        captured = capsys.readouterr()
        assert "not in rate table" not in captured.err

    def test_unknown_provider_no_unknown_model_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Per DEC-006: unknown provider is a DIFFERENT code path that
        # silently returns None — the unknown-model warning is NOT
        # triggered. A typo'd provider must not flood stderr with
        # every model the caller subsequently tries.
        result = estimate_cost("vertex", "anything-model", 100, 50)
        assert result is None
        captured = capsys.readouterr()
        assert "not in rate table" not in captured.err

    def test_announce_helper_directly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Standalone-helper invocation must behave the same as the
        # estimate_cost-routed path. First call per pair emits;
        # second call with the same pair is silent; third call with a
        # distinct pair emits its own first-call warning.
        announce_unknown_model("openai", "gpt-fake")
        first = capsys.readouterr().err
        assert "gpt-fake" in first
        assert "not in rate table" in first

        # Same pair → silent.
        announce_unknown_model("openai", "gpt-fake")
        second = capsys.readouterr().err
        assert second == ""

        # Different pair → emits.
        announce_unknown_model("openai", "gpt-other")
        third = capsys.readouterr().err
        assert "gpt-other" in third
        assert "not in rate table" in third
