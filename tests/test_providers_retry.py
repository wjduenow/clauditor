"""Tests for the shared retry helpers in ``clauditor._providers._retry``.

DEC-007 of ``plans/super/145-openai-provider.md`` hoisted the retry
policy (constants + decision logic + backoff curve) out of
``clauditor._providers._anthropic`` so the future OpenAI provider can
share it. The test classes ``TestComputeBackoff``,
``TestComputeRetryDecision``, and ``TestRandUniformDefault`` previously
lived in ``tests/test_providers_anthropic.py`` and patched
``clauditor._providers._anthropic._rand_uniform``; per
``.claude/rules/back-compat-shim-discipline.md`` Pattern 3 they
follow the symbols to their new home and patch
``clauditor._providers._retry._rand_uniform``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clauditor._providers._retry import (
    compute_backoff,
    compute_retry_decision,
)


class TestRandUniformDefault:
    def test_default_path_returns_value_in_range(self) -> None:
        # Most tests patch _rand_uniform; this one exercises the real
        # implementation so the stdlib-random wrapper itself is
        # covered. A few samples pin the contract: values stay inside
        # the requested closed interval.
        from clauditor._providers._retry import _rand_uniform

        for _ in range(10):
            val = _rand_uniform(-0.25, 0.25)
            assert -0.25 <= val <= 0.25


class TestComputeBackoff:
    def test_delay_grows_exponentially(self) -> None:
        # With zero jitter, delays are 1, 2, 4 seconds.
        with patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.0
        ):
            assert compute_backoff(0) == pytest.approx(1.0)
            assert compute_backoff(1) == pytest.approx(2.0)
            assert compute_backoff(2) == pytest.approx(4.0)

    def test_positive_jitter_extends_delay(self) -> None:
        # Max positive jitter (0.25) → base * 1.25
        with patch(
            "clauditor._providers._retry._rand_uniform", return_value=0.25
        ):
            assert compute_backoff(0) == pytest.approx(1.25)
            assert compute_backoff(2) == pytest.approx(5.0)

    def test_negative_jitter_shortens_delay(self) -> None:
        # Max negative jitter (-0.25) → base * 0.75
        with patch(
            "clauditor._providers._retry._rand_uniform", return_value=-0.25
        ):
            assert compute_backoff(0) == pytest.approx(0.75)
            assert compute_backoff(1) == pytest.approx(1.5)

    def test_delay_never_negative(self) -> None:
        # Pathological jitter that tries to push delay negative is
        # floored at 0.
        with patch(
            "clauditor._providers._retry._rand_uniform", return_value=-100.0
        ):
            assert compute_backoff(0) == 0.0


class TestComputeRetryDecision:
    """Pure helper extracted per DEC-005 retry parity (#86) and
    re-homed per DEC-007 retry-helper hoist (#145).

    Shared by SDK and CLI transport branches across every provider so
    a failure with the same category retries the same number of times
    regardless of which transport produced it.
    """

    def test_rate_limit_retries_up_to_three_times(self) -> None:
        assert compute_retry_decision("rate_limit", 0) == "retry"
        assert compute_retry_decision("rate_limit", 1) == "retry"
        assert compute_retry_decision("rate_limit", 2) == "retry"

    def test_rate_limit_raises_after_third_retry(self) -> None:
        assert compute_retry_decision("rate_limit", 3) == "raise"

    def test_auth_never_retries(self) -> None:
        assert compute_retry_decision("auth", 0) == "raise"
        assert compute_retry_decision("auth", 5) == "raise"

    def test_api_retries_once_then_raises(self) -> None:
        assert compute_retry_decision("api", 0) == "retry"
        assert compute_retry_decision("api", 1) == "raise"

    def test_connection_retries_once_then_raises(self) -> None:
        assert compute_retry_decision("connection", 0) == "retry"
        assert compute_retry_decision("connection", 1) == "raise"

    def test_transport_retries_once_then_raises(self) -> None:
        assert compute_retry_decision("transport", 0) == "retry"
        assert compute_retry_decision("transport", 1) == "raise"

    def test_unknown_category_raises(self) -> None:
        """Defensive default: an unknown category is not retried."""
        assert compute_retry_decision("mystery", 0) == "raise"

    def test_empty_string_category_raises(self) -> None:
        """Defensive default: empty-string category is not retried."""
        assert compute_retry_decision("", 0) == "raise"
