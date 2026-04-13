"""Token + timing metrics aggregation for clauditor runs.

See plans/super/21-timing-tokens.md DEC-002 and DEC-014 for the canonical
bucketed shape. This module is pure — no I/O, no SDK calls, no imports
from cli/history. Import only from typing stdlib and dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def build_metrics(
    skill: TokenUsage,
    duration_seconds: float,
    grader: TokenUsage | None = None,
    quality: TokenUsage | None = None,
    triggers: TokenUsage | None = None,
) -> dict:
    """Build the canonical nested metrics dict.

    Bucket keys (``grader``, ``quality``, ``triggers``) are absent from the
    returned dict when their source arg is ``None``. ``skill`` is always
    present because every command runs the skill. ``total`` and
    ``duration_seconds`` are always present.

    ``None`` is the absence signal: passing ``TokenUsage(0, 0)`` explicitly
    still causes the bucket to appear (with zero values). Only ``None``
    omits the bucket.

    ``total`` is the sum of ``input_tokens`` and ``output_tokens`` across
    all present buckets, with a ``total`` sub-key equal to
    ``input_tokens + output_tokens``.
    """
    buckets: dict[str, TokenUsage] = {"skill": skill}
    if grader is not None:
        buckets["grader"] = grader
    if quality is not None:
        buckets["quality"] = quality
    if triggers is not None:
        buckets["triggers"] = triggers

    total_in = sum(b.input_tokens for b in buckets.values())
    total_out = sum(b.output_tokens for b in buckets.values())

    result: dict = {name: b.to_dict() for name, b in buckets.items()}
    result["total"] = {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total": total_in + total_out,
    }
    result["duration_seconds"] = duration_seconds
    return result
