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
last cross-checked against these pages. US-002 of #169 adds a
one-shot stderr warning that fires when the table is older than 90
days; this module ships only the constants and the lookup core, and
the staleness announcement helpers land in the next bead.
"""

from __future__ import annotations

from typing import Final, NamedTuple


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
        # TODO: confirm rate against the pricing page on next refresh.
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

    Pure helper per ``.claude/rules/pure-compute-vs-io-split.md`` —
    no I/O, no logging, no module-level state mutation. Returns
    ``None`` on a graceful lookup miss (unknown provider, unknown
    model, or unknown ``(provider, model)`` pair). Raises
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

    card = _PRICING_TABLE.get(provider, {}).get(model)
    if card is None:
        return None

    effective_output = output_tokens + (reasoning_tokens or 0)
    return (
        input_tokens * card.input_per_mtok
        + effective_output * card.output_per_mtok
    ) / 1_000_000
