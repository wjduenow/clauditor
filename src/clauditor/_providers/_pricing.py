"""Pricing-table lookup for ``IterationContext.cost_usd``.

Pure-compute helpers backing the ``cost_usd`` field on
``IterationContext`` (see ``src/clauditor/context.py``). Given a
``(provider, model, input_tokens, output_tokens, reasoning_tokens)``
tuple this module returns a USD cost estimate via a hardcoded
per-provider rate table. Unknown ``(provider, model)`` pairs return
``None`` so callers can write ``cost_usd: null`` cleanly without
raising — see DEC-002 / DEC-005 of
``plans/super/169-pricing-cost-estimator.md``.

**Reasoning-tokens contract.** Both Anthropic and OpenAI bill
reasoning tokens at the model's output rate; there is NO separate
reasoning rate exposed by either API (verified during the #169
pre-plan research, 2026-05-09). ``estimate_cost`` therefore folds
``reasoning_tokens`` into the effective output-token count before
applying the output rate; the caller does not need a per-provider
branch.

**Scope.** Grader-only cost (Layer 2 + Layer 3 calls). Runner-side
cost is out of scope for this ticket per DEC-001 of #169 — the
ClaudeCodeHarness only reliably populates ``model_runner`` when
``--model`` is pinned, so a runner-cost wiring would silently null
out for the common case anyway.

**Cache-token deferral.** Cache-read and cache-write rates are
genuinely different from base input rates on both providers, but
``IterationContext`` does not yet record a cache breakdown; cache
pricing support is deferred until that field lands. Today the table
prices every input token at the base input rate.

**Source-of-truth URLs** (consult before refreshing the table):

- https://platform.claude.com/docs/en/about-claude/pricing
- https://openai.com/api/pricing/

The :data:`_LAST_VERIFIED` constant records the date the table was
last cross-checked against these pages. :func:`announce_pricing_table_stale_if_old`
(US-002 of #169) emits a one-shot stderr warning on the first
:func:`estimate_cost` call per process when ``today - _LAST_VERIFIED``
exceeds 90 days. The :data:`_today` indirection alias lets tests
pin the wall-clock date deterministically per
``.claude/rules/monotonic-time-indirection.md``.
"""

from __future__ import annotations

import datetime
import sys
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from clauditor.grader import ExtractionReport
    from clauditor.quality_grader import GradingReport

# Indirection alias per ``.claude/rules/monotonic-time-indirection.md``:
# tests patch ``clauditor._providers._pricing._today`` to pin the
# wall-clock date deterministically without clobbering ``datetime.date.today``
# on the stdlib module (which other code may consult).
_today = datetime.date.today


class _PriceCard(NamedTuple):
    """Per-model rate card.

    Both rates are USD per million tokens, matching the published units
    on both providers' pricing pages. There is intentionally NO
    separate reasoning rate — reasoning tokens are billed at the
    model's output rate (see module docstring for the contract).
    """

    input_per_mtok: float
    output_per_mtok: float


_PRICING_TABLE_VERSION: Final[int] = 1
"""Bumps when the rate-card schema changes (e.g. when a future
revision adds a cache-token rate). Today's shape: a flat
``dict[provider, dict[model, _PriceCard]]``. A schema bump would
follow ``.claude/rules/json-schema-version.md`` semantics."""


_LAST_VERIFIED: Final[str] = "2026-05-09"
"""ISO-8601 calendar date the rate table was last cross-checked
against the source-of-truth URLs in the module docstring. US-002
of #169 reads this constant via ``date.fromisoformat`` and emits
a one-shot stderr warning when ``today - _LAST_VERIFIED > 90 days``.
A maintainer who refreshes the table MUST bump this date in the
same commit."""


_PRICING_TABLE: Final[dict[str, dict[str, _PriceCard]]] = {
    # Anthropic published rates per
    # https://platform.claude.com/docs/en/about-claude/pricing
    # Rates are USD per million tokens; verified 2026-05-09.
    "anthropic": {
        # Sonnet tier — workhorse grader model.
        "claude-sonnet-4-6": _PriceCard(3.00, 15.00),
        # Opus tier — flagship; ~5x Sonnet pricing per published
        # tables.
        "claude-opus-4-7": _PriceCard(15.00, 75.00),
        # Haiku tier — small/cheap model used for L2 extraction.
        "claude-haiku-4-5": _PriceCard(0.80, 4.00),
    },
    # OpenAI published rates per https://openai.com/api/pricing/
    # Rates are USD per million tokens; verified 2026-05-09.
    "openai": {
        # GPT-5.4 — Anthropic-Sonnet-equivalent tier.
        "gpt-5.4": _PriceCard(2.50, 10.00),
        # GPT-5.4-mini — small/cheap tier.
        "gpt-5.4-mini": _PriceCard(0.15, 0.60),
        # o-series reasoning model — billed at standard input/output
        # rates (reasoning tokens roll into the output rate per the
        # research-note contract).
        "o4-mini": _PriceCard(1.10, 4.40),
    },
}
"""Per-provider rate card. Keyed ``provider → model → _PriceCard``.

Coverage matches DEC-004 of #169: only the models we currently
grade with. Unknown ``(provider, model)`` pairs miss the lookup
and return ``None`` from :func:`estimate_cost` rather than falling
back to a family heuristic — a "roughly right" guess is silently
wrong for a model that bills very differently (Opus is ~5x Sonnet),
so null-on-unknown is the safe default per DEC-002.
"""


# DEC-003 (#169 US-002): one-shot stderr warning when the rate table
# is older than 90 days. Flipped to ``True`` after the first emission
# per Python process. Sibling to the announcement-family flags in
# ``clauditor._providers._auth`` (``_announced_implicit_no_api_key``,
# ``_announced_call_anthropic_deprecation``, ``_announced_auto_codex_harness``)
# per ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
# announcements — an emerging family". Tests reset via the
# ``monkeypatch.setattr(..., False)`` autouse fixture pattern,
# targeting the canonical flag location at
# ``clauditor._providers._pricing._announced_pricing_table_stale``.
_announced_pricing_table_stale: bool = False


# DEC-003 (#169 US-002): the staleness notice emitted on the first
# ``estimate_cost`` call per Python process when
# ``today - _LAST_VERIFIED > 90 days``. Three durable substrings are
# test-asserted:
#   1. ``"90 days"`` — the threshold the warning trips on, so
#      maintainers know the budget the table is operating against.
#   2. ``"pricing"`` — anchors the topic so users searching their
#      stderr for "pricing" find the notice.
#   3. At least one of ``"platform.claude.com"`` /
#      ``"openai.com/api/pricing"`` — the source-of-truth URLs a
#      maintainer must consult when refreshing the table.
# The ``{days}`` placeholder is interpolated at emit time so the
# message names the actual age, not just "stale".
_PRICING_TABLE_STALE_ANNOUNCEMENT: Final[str] = (
    "clauditor: pricing table v{version} in "
    "src/clauditor/_providers/_pricing.py is {days} days old "
    "(>90 days threshold); cost_usd values may diverge from current "
    "provider rates. Refresh against "
    "https://platform.claude.com/docs/en/about-claude/pricing and "
    "https://openai.com/api/pricing/ and bump _LAST_VERIFIED "
    "(plus _PRICING_TABLE_VERSION when the table shape changes)."
)


def announce_pricing_table_stale_if_old() -> None:
    """Emit the pricing-table-stale notice to stderr once per process.

    DEC-003 of ``plans/super/169-pricing-cost-estimator.md`` (US-002).
    Called from the top of :func:`estimate_cost` so the warning fires
    on the first cost-estimation call per Python process when the
    rate table is older than 90 days. The one-shot module flag
    :data:`_announced_pricing_table_stale` ensures a single
    announcement per process regardless of how many subsequent
    ``estimate_cost`` calls land.

    Parallel to the announcement-family helpers in
    ``clauditor._providers._auth`` (:func:`announce_implicit_no_api_key`,
    :func:`announce_call_anthropic_deprecation`,
    :func:`announce_auto_codex_harness`) — same shape, same one-shot-
    per-process contract, same ``monkeypatch.setattr(..., False)``
    test-reset pattern per
    ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
    announcements — an emerging family".

    Defensive: a typo or otherwise unparseable :data:`_LAST_VERIFIED`
    treats the table as stale (``days_old = 9999``) so a maintainer's
    typo in the constant cannot crash a production grading run. The
    fallback is loud-but-safe: a stale warning is preferable to a
    silent skip, and the message itself guides the maintainer to the
    canonical refresh location.
    """
    global _announced_pricing_table_stale
    if _announced_pricing_table_stale:
        return
    try:
        last_verified = datetime.date.fromisoformat(_LAST_VERIFIED)
        days_old = (_today() - last_verified).days
    except ValueError:
        # Defensive: a typo in _LAST_VERIFIED treats the table as
        # stale rather than crashing the production grading run.
        days_old = 9999
    if days_old > 90:
        print(
            _PRICING_TABLE_STALE_ANNOUNCEMENT.format(
                days=days_old, version=_PRICING_TABLE_VERSION
            ),
            file=sys.stderr,
        )
        _announced_pricing_table_stale = True


# DEC-006 (#169 US-003): one-shot stderr warning per (provider, model)
# pair when ``estimate_cost`` is called with a recognized provider but
# an unknown model. Different shape from the staleness flag above:
# keyed on a ``set[tuple[str, str]]`` of pairs already announced, so
# multiple distinct unknown models each warn once rather than the
# first miss muting all subsequent ones. Sibling to the announcement-
# family flags in ``clauditor._providers._auth`` per
# ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
# announcements — an emerging family". Tests reset via
# ``monkeypatch.setattr(..., set())`` autouse fixture, targeting the
# canonical location at
# ``clauditor._providers._pricing._announced_unknown_models``.
_announced_unknown_models: set[tuple[str, str]] = set()


# DEC-006 (#169 US-003): the unknown-model notice emitted on the first
# ``estimate_cost`` call per (provider, model) pair when the provider
# is recognized but the model is absent from the rate table. Four
# durable substrings are test-asserted:
#   1. ``"pricing:"`` — anchors the topic so users searching their
#      stderr for "pricing" find the notice.
#   2. ``"not in rate table"`` — names the specific failure mode
#      (vs the staleness warning's "X days old" framing).
#   3. The literal ``{provider}`` value — so the message names the
#      provider-axis context.
#   4. The literal ``{model}`` value — so the maintainer can grep
#      for the missing model name in the rate table.
# The hint points at ``src/clauditor/_providers/_pricing.py`` so the
# maintainer knows where to add the missing rate card.
_UNKNOWN_MODEL_ANNOUNCEMENT_TEMPLATE: Final[str] = (
    "clauditor: pricing: model {model!r} (provider {provider!r}) is "
    "not in rate table; cost_usd will be null for this call.\n"
    "Add a rate card for {model!r} to _PRICING_TABLE in "
    "src/clauditor/_providers/_pricing.py to enable cost estimation."
)


def announce_unknown_model(provider: str, model: str) -> None:
    """Emit the unknown-model notice to stderr once per (provider, model) pair.

    DEC-006 of ``plans/super/169-pricing-cost-estimator.md`` (US-003).
    Called from :func:`estimate_cost` at the known-provider/unknown-
    model branch so the warning fires on the first cost-estimation
    call per process per ``(provider, model)`` pair. The one-shot
    module set :data:`_announced_unknown_models` ensures a single
    announcement per pair regardless of how many subsequent
    ``estimate_cost`` calls land for the same pair; distinct
    ``(provider, model)`` pairs each emit once.

    Different from :func:`announce_pricing_table_stale_if_old` (US-002):
    keyed on a ``set`` of pairs rather than a single boolean flag, so
    multiple unknown models each get their own first-call warning.
    Otherwise the announcement-family contract is identical — same
    shape, same one-shot semantics, same
    ``monkeypatch.setattr(..., set())`` test-reset pattern per
    ``.claude/rules/centralized-sdk-call.md`` "Implicit-coupling
    announcements — an emerging family".

    Per DEC-006: the warning fires ONLY when the provider is known
    but the model is unknown. An unknown provider does NOT trigger
    this warning (different code path; the unknown-provider case
    stays silent so a typo'd provider does not flood stderr with
    every model the caller subsequently tries).
    """
    key = (provider, model)
    if key in _announced_unknown_models:
        return
    print(
        _UNKNOWN_MODEL_ANNOUNCEMENT_TEMPLATE.format(
            provider=provider, model=model
        ),
        file=sys.stderr,
    )
    _announced_unknown_models.add(key)


def _validate_token_arg(name: str, value: object) -> None:
    """Reject bool / non-int / negative values for a token-count arg.

    Per DEC-005 + ``.claude/rules/constant-with-type-info.md``:
    ``bool`` is an int subclass in Python, so a bare
    ``isinstance(value, int)`` check would silently accept
    ``True`` / ``False``. Guard explicitly.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"estimate_cost: {name!r} must be int (not bool), got "
            f"{type(value).__name__} {value!r}"
        )
    if not isinstance(value, int):
        raise ValueError(
            f"estimate_cost: {name!r} must be int, got "
            f"{type(value).__name__} {value!r}"
        )
    if value < 0:
        raise ValueError(
            f"estimate_cost: {name!r} must be >= 0, got {value!r}"
        )


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int | None = None,
) -> float | None:
    """Return the USD cost estimate for one grader call, or ``None``.

    Pure-compute helper per
    ``.claude/rules/pure-compute-vs-io-split.md``: the **returned
    value** is a deterministic function of the inputs (no network
    I/O, no file I/O, no subprocess, no logging library). The two
    documented side-effects are one-shot stderr announcements from
    the announcement family per
    ``.claude/rules/centralized-sdk-call.md``:

    - :func:`announce_pricing_table_stale_if_old` fires once per
      process when ``_LAST_VERIFIED`` is older than 90 days. Mutates
      the module-level ``_announced_pricing_table_stale`` flag.
    - :func:`announce_unknown_model` fires once per
      ``(provider, model)`` pair when the provider IS known but the
      model is missing from its sub-table. Mutates the module-level
      ``_announced_unknown_models`` set.

    Both announcements are idempotent (subsequent calls are no-ops)
    and never affect the returned cost value. Tests reset the
    module-level flags via autouse fixtures so the pure-compute
    return value is observable in isolation.

    Returns ``None`` on a graceful lookup miss (unknown provider,
    unknown model, or unknown ``(provider, model)`` pair). Raises
    ``ValueError`` on a contract violation: non-string
    ``provider`` / ``model``, or token args that are bool, non-int,
    or negative. The two-category split mirrors DEC-005 of #169 —
    programmer errors fail loudly; lookup misses do not crash a
    production grading run.

    Reasoning tokens are billed at the model's output rate per the
    module-docstring contract. The implementation folds
    ``reasoning_tokens`` into ``effective_output`` and applies the
    single output-rate multiplier; the caller does not need to
    pre-merge.

    Args:
        provider: Provider key (e.g. ``"anthropic"``, ``"openai"``).
        model: Model name (e.g. ``"claude-sonnet-4-6"``).
        input_tokens: Non-negative input-token count.
        output_tokens: Non-negative output-token count.
        reasoning_tokens: Optional non-negative reasoning-token count.
            Folded into the effective output count when present.

    Returns:
        The cost in USD as a ``float``, or ``None`` when the
        ``(provider, model)`` pair is not in :data:`_PRICING_TABLE`.
    """
    # DEC-003 (#169 US-002): one-shot stderr warning if the rate
    # table is older than 90 days. Fires before validation so a
    # caller that hits a contract violation still gets the staleness
    # cue on the same run; subsequent calls are no-ops per the
    # announcement-family contract.
    announce_pricing_table_stale_if_old()
    if not isinstance(provider, str):
        raise ValueError(
            f"estimate_cost: 'provider' must be str, got "
            f"{type(provider).__name__} {provider!r}"
        )
    if not isinstance(model, str):
        raise ValueError(
            f"estimate_cost: 'model' must be str, got "
            f"{type(model).__name__} {model!r}"
        )
    _validate_token_arg("input_tokens", input_tokens)
    _validate_token_arg("output_tokens", output_tokens)
    if reasoning_tokens is not None:
        _validate_token_arg("reasoning_tokens", reasoning_tokens)

    # DEC-006 (#169 US-003): the unknown-model warning fires only when
    # the provider IS known but the model is missing from its sub-table.
    # Unknown providers stay silent (different code path) so a typo'd
    # provider does not flood stderr with every subsequent model.
    provider_table = _PRICING_TABLE.get(provider)
    if provider_table is None:
        return None
    card = provider_table.get(model)
    if card is None:
        announce_unknown_model(provider, model)
        return None

    effective_output = output_tokens + (reasoning_tokens or 0)
    return (
        input_tokens * card.input_per_mtok
        + effective_output * card.output_per_mtok
    ) / 1_000_000


def compute_iteration_cost_usd(
    grading_report: GradingReport,
    extraction_report: ExtractionReport | None,
    provider: str,
) -> float | None:
    """Sum L2 + L3 grader-call cost for one iteration.

    Pure-compute composition helper per
    ``.claude/rules/pure-compute-vs-io-split.md``: the **returned
    value** is a deterministic function of the input reports. The
    helper itself performs no I/O, no logging, and no module-level
    state mutation; it routes both layers through
    :func:`estimate_cost` and sums the results. Note that
    :func:`estimate_cost` may transitively fire the one-shot stderr
    announcements documented on its own docstring (stale pricing
    table, unknown ``(provider, model)`` pair) — those announcements
    do not affect the cost value returned here.

    Per DEC-001 of ``plans/super/169-pricing-cost-estimator.md``,
    scope is grader-only: Layer 2 (extraction) + Layer 3 (grading).
    Runner-side cost is out of scope. Per DEC-002, the helper is
    all-or-nothing: when any internal :func:`estimate_cost` lookup
    misses (returns ``None``), the composition returns ``None``
    rather than partial cost. A "roughly right" partial estimate is
    silently wrong for budgeting and trend reads, so null-on-any-
    miss is the safe default.

    Args:
        grading_report: Layer 3 :class:`GradingReport`. Always
            present at the grade write seam.
        extraction_report: Layer 2 :class:`ExtractionReport` when
            sections were declared on the eval spec; ``None`` when
            Layer 2 did not run. ``None`` contributes ``0.0`` to the
            sum — that is NOT a lookup miss, it is a genuine
            "no Layer-2 call happened" signal.
        provider: Provider key (e.g. ``"anthropic"``, ``"openai"``)
            resolved at the CLI seam per
            ``.claude/rules/multi-provider-dispatch.md``.

    Returns:
        Sum of L2 + L3 cost in USD as a ``float``, or ``None`` when
        any internal lookup misses (unknown provider, unknown
        grading model, or — when extraction is present — unknown
        extraction model).
    """
    l3_cost = estimate_cost(
        provider,
        grading_report.model,
        grading_report.input_tokens,
        grading_report.output_tokens,
    )
    if l3_cost is None:
        return None
    if extraction_report is None:
        return l3_cost
    l2_cost = estimate_cost(
        provider,
        extraction_report.model,
        extraction_report.input_tokens,
        extraction_report.output_tokens,
    )
    if l2_cost is None:
        return None
    return l2_cost + l3_cost
