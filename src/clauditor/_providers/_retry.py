"""Shared retry helpers for provider backends (DEC-007 of #145).

The retry policy for the Anthropic and OpenAI provider backends is
byte-identical: same exception taxonomy (rate-limit / auth /
api-5xx / connection), same per-category retry caps, same
exponential backoff curve with ±25% jitter. Hoisting the helpers
into one shared module is DRY and keeps both providers in lockstep
when the policy evolves.

Public API:

- :data:`RATE_LIMIT_MAX_RETRIES` (= 3)
- :data:`SERVER_MAX_RETRIES` (= 1)
- :data:`CONN_MAX_RETRIES` (= 1)
- :func:`compute_backoff(retry_index)` — pure helper returning the
  delay (seconds) before the ``retry_index``-th retry. Formula:
  ``2 ** retry_index`` plus uniform ``±25%`` jitter, floored at 0.
- :func:`compute_retry_decision(category, retry_index)` — pure
  helper returning ``"retry"`` or ``"raise"`` per the shared ladder.

Per-provider concerns stay per-provider. The ``_sleep`` /
``_monotonic`` / ``_rand_uniform`` / ``_rng`` test-indirection
aliases live on each provider module so a
``monkeypatch.setattr("clauditor._providers._openai._sleep", ...)``
patches only OpenAI's sleeping, not Anthropic's. This module owns
the *policy* (constants + decision logic); each provider owns its
own clock / RNG indirection.

Note on jitter: this module exposes its own ``_rand_uniform``
indirection so :func:`compute_backoff` is patchable in isolation
(see :class:`tests.test_providers_retry.TestComputeBackoff`). Each
provider's call loop still uses the shared :func:`compute_backoff`
and inherits the same jitter contract.
"""

from __future__ import annotations

import random
from typing import Literal

# Per-exception retry caps. Public per DEC-007 (renamed to drop the
# leading underscore at the new module boundary).
RATE_LIMIT_MAX_RETRIES = 3
SERVER_MAX_RETRIES = 1
CONN_MAX_RETRIES = 1


# Module-local RNG so patching ``random.random`` globally does not
# affect this helper, and vice versa. Mirrors the per-provider
# pattern so tests can pin jitter deterministically here without
# clobbering provider-specific RNG state.
_rng = random.Random()


def _rand_uniform(lo: float, hi: float) -> float:
    """Return a uniform random float in ``[lo, hi]``.

    Indirected so tests can patch jitter deterministically without
    clobbering ``random.random`` globally.
    """
    return _rng.uniform(lo, hi)


def compute_backoff(retry_index: int) -> float:
    """Return the sleep duration for the ``retry_index``-th retry.

    Formula: ``2 ** retry_index`` seconds with ``±25%`` uniform
    jitter. Retry indices start at 0, so the first retry waits
    ``1 s`` (plus jitter), the second ``2 s``, the third ``4 s``.
    """
    base = float(2**retry_index)
    jitter = _rand_uniform(-0.25, 0.25) * base
    delay = base + jitter
    # Floor at 0 defensively; negative jitter at retry_index=0 with
    # deterministic seeds that push to the lower bound could otherwise
    # bottom out near 0.75 — still positive, but we keep the guard in
    # case future formula changes flip the sign.
    return max(delay, 0.0)


def compute_retry_decision(
    category: str, retry_index: int
) -> Literal["retry", "raise"]:
    """Return whether to retry a failure given its category + retry index.

    Pure helper per ``.claude/rules/pure-compute-vs-io-split.md``.
    Shared by every provider backend so a failure with the same
    category retries the same number of times regardless of which
    provider produced it (DEC-007 retry parity).

    Ladder (retry indices are 0-based — index ``i`` is "the decision
    made before the ``i+1``-th attempt's delay"):

    - ``"rate_limit"``: retry at indices 0, 1, 2; raise at 3 (matches
      :data:`RATE_LIMIT_MAX_RETRIES` = 3 — up to 3 retries ≡ 4
      total attempts).
    - ``"auth"``: always raise (no retry at any index).
    - ``"api"``: retry at index 0; raise at 1 (one retry, matches
      :data:`SERVER_MAX_RETRIES` = 1 — used for 5xx SDK errors and
      the analogous CLI ``api`` category).
    - ``"connection"``: retry at index 0; raise at 1 (matches
      :data:`CONN_MAX_RETRIES` = 1 — SDK ``APIConnectionError``).
    - ``"transport"``: retry at index 0; raise at 1 (CLI-only;
      covers subprocess binary-missing, timeout, malformed output).
    - Any other category: always raise (defensive default — an
      unknown category is not something we should retry blindly).
    """
    if category == "rate_limit":
        return "retry" if retry_index < RATE_LIMIT_MAX_RETRIES else "raise"
    if category == "auth":
        return "raise"
    if category == "api":
        return "retry" if retry_index < SERVER_MAX_RETRIES else "raise"
    if category == "connection":
        return "retry" if retry_index < CONN_MAX_RETRIES else "raise"
    if category == "transport":
        return "retry" if retry_index < CONN_MAX_RETRIES else "raise"
    return "raise"
