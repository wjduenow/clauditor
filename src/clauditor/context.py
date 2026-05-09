"""Per-iteration comparability metadata sidecar (``context.json``).

Pure-data module: declares the :class:`IterationContext` dataclass,
its :meth:`IterationContext.to_json` serializer (with
``schema_version`` as the first top-level key per
``.claude/rules/json-schema-version.md``), and a
:meth:`IterationContext.from_dict` loader that hard-rejects
unknown discriminator literals per
``.claude/rules/pre-llm-contract-hard-validate.md``.

Methodless dataclass per ``.claude/rules/data-vs-asserter-split.md``:
only ``to_json`` and the ``from_dict`` classmethod live on the class
— no ``assert_*`` helpers, no rendering helpers. Any future test-
side helpers go in a sibling module wrapped around an instance.

Decisions traced:

- **DEC-001** — ``cost_usd`` ships as a ``float | None`` placeholder
  with default ``None``; the pricing module that populates it lives
  in #169.
- **DEC-002** — ``reasoning_tokens`` ships as an ``int | None``
  placeholder with default ``None``; per-provider reasoning capture
  lives in #170.
- **DEC-007** — ``model_runner: str`` is non-nullable; the harness
  contract (every harness populates ``harness_metadata["model"]``)
  is enforced upstream at the sidecar writer.
- **DEC-008** — ``system_prompt_source`` is a closed-set literal
  validated here; the writer reads it verbatim from
  ``harness_metadata["system_prompt_source"]``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

__all__ = ["IterationContext"]

_CONTEXT_SCHEMA_VERSION = 1

_VALID_HARNESSES: frozenset[str] = frozenset({"claude-code", "codex"})
_VALID_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai"})
_VALID_SYSTEM_PROMPT_SOURCES: frozenset[str] = frozenset(
    {"explicit", "agents_md", "skill_md"}
)
_VALID_SANDBOX_MODES: frozenset[str] = frozenset(
    {"read-only", "workspace-write", "danger-full-access"}
)


@dataclass
class IterationContext:
    """Comparability metadata for a single iteration's run + grading.

    Persisted as ``iteration-N/<skill>/context.json`` alongside the
    other per-iteration sidecars (``assertions.json``,
    ``extraction.json``, ``grading.json``). The ``schema_version``
    field is emitted as the first top-level key on serialization
    per ``.claude/rules/json-schema-version.md``.
    """

    harness: str
    provider: str | None
    model_runner: str
    model_grader: str | None
    system_prompt_source: str
    sandbox_mode: str | None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    schema_version: int = _CONTEXT_SCHEMA_VERSION

    def to_json(self) -> str:
        """Serialize to JSON with ``schema_version`` as the first key."""
        data = {
            "schema_version": self.schema_version,
            "harness": self.harness,
            "provider": self.provider,
            "model_runner": self.model_runner,
            "model_grader": self.model_grader,
            "system_prompt_source": self.system_prompt_source,
            "sandbox_mode": self.sandbox_mode,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": self.cost_usd,
        }
        return json.dumps(data, indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> IterationContext:
        """Construct from a parsed dict, hard-rejecting invalid literals.

        Raises :class:`ValueError` with a message naming both the
        offending value and the valid set for any discriminator
        outside its closed-set, and for ``bool`` values supplied for
        ``reasoning_tokens`` / ``cost_usd`` (per
        ``.claude/rules/constant-with-type-info.md`` — ``bool`` is a
        subclass of ``int`` in Python and would otherwise pass
        ``isinstance(val, int)`` checks).
        """
        harness = data["harness"]
        if harness not in _VALID_HARNESSES:
            raise ValueError(
                f"IterationContext.from_dict: 'harness' must be one of "
                f"{sorted(_VALID_HARNESSES)!r}, got {harness!r}"
            )

        provider = data.get("provider")
        if provider is not None and provider not in _VALID_PROVIDERS:
            raise ValueError(
                f"IterationContext.from_dict: 'provider' must be one of "
                f"{sorted(_VALID_PROVIDERS)!r} or None, got {provider!r}"
            )

        system_prompt_source = data["system_prompt_source"]
        if system_prompt_source not in _VALID_SYSTEM_PROMPT_SOURCES:
            raise ValueError(
                f"IterationContext.from_dict: 'system_prompt_source' must be "
                f"one of {sorted(_VALID_SYSTEM_PROMPT_SOURCES)!r}, "
                f"got {system_prompt_source!r}"
            )

        sandbox_mode = data.get("sandbox_mode")
        if sandbox_mode is not None and sandbox_mode not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"IterationContext.from_dict: 'sandbox_mode' must be one of "
                f"{sorted(_VALID_SANDBOX_MODES)!r} or None, got {sandbox_mode!r}"
            )

        reasoning_tokens = data.get("reasoning_tokens")
        if reasoning_tokens is not None:
            # bool is an int subclass; reject explicitly per
            # .claude/rules/constant-with-type-info.md.
            if isinstance(reasoning_tokens, bool) or not isinstance(
                reasoning_tokens, int
            ):
                raise ValueError(
                    f"IterationContext.from_dict: 'reasoning_tokens' must be "
                    f"int or None, got {type(reasoning_tokens).__name__} "
                    f"{reasoning_tokens!r}"
                )

        cost_usd = data.get("cost_usd")
        if cost_usd is not None:
            # bool is an int subclass; int is float-coercible. Reject bool;
            # accept float OR int (treated as float).
            if isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)):
                raise ValueError(
                    f"IterationContext.from_dict: 'cost_usd' must be float, "
                    f"int, or None, got {type(cost_usd).__name__} {cost_usd!r}"
                )
            cost_usd = float(cost_usd)

        return cls(
            harness=harness,
            provider=provider,
            model_runner=data["model_runner"],
            model_grader=data.get("model_grader"),
            system_prompt_source=system_prompt_source,
            sandbox_mode=sandbox_mode,
            reasoning_tokens=reasoning_tokens,
            cost_usd=cost_usd,
            schema_version=data.get("schema_version", _CONTEXT_SCHEMA_VERSION),
        )
