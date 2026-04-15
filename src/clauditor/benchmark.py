"""Pure delta computation for ``clauditor grade --baseline``.

Aggregates with-skill vs without-skill runs into the agentskills.io
``run_summary`` + ``delta`` shape and serializes it as
``benchmark.json``. No file I/O, no LLM calls — the CLI layer hands
in already-computed :class:`GradingReport` and :class:`SkillResult`
objects and this module returns a :class:`Benchmark` dataclass ready
to persist.

Decisions traced:

- **DEC-001** — ``pass_rate`` comes from ``GradingReport.pass_rate``
  (Layer 3). L1/L2 deltas stay visible in their own sidecars but do
  not contribute to this gated number.
- **DEC-006** — ``without_skill.*.stddev`` is Python ``None`` /
  JSON ``null``: the baseline arm runs exactly once (single
  observation), so sample stddev is not meaningful; ``0.0`` would
  falsely imply observed zero variance.
- **DEC-007** — Asymmetric variance: N primary reps, 1 baseline run.
  The primary arm accepts a list of reports/results; the baseline
  arm accepts a single pair.
- **DEC-010** — Output is plain data; no TTY awareness lives in
  this module (the printer in ``cli.py`` renders the block
  unconditionally).

The persisted JSON obeys ``.claude/rules/json-schema-version.md``:
``schema_version`` is the first top-level key, and any loader
verifies it via :func:`_check_schema_version` following the
canonical pattern in :mod:`clauditor.audit`.
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from clauditor.quality_grader import GradingReport
from clauditor.runner import SkillResult

__all__ = [
    "Benchmark",
    "Delta",
    "MetricStats",
    "RunArm",
    "RunSummary",
    "compute_benchmark",
]

_BENCHMARK_SCHEMA_VERSION = 1


def _check_schema_version(data: dict, source: Path | str | None = None) -> bool:
    """Verify ``data`` advertises ``schema_version == 1``.

    Mirrors the canonical pattern in ``clauditor.audit._check_schema_version``:
    returns ``True`` on match; on mismatch or absence, logs a one-line
    warning to stderr and returns ``False`` so the caller can skip the file.
    """
    version = data.get("schema_version")
    if version == _BENCHMARK_SCHEMA_VERSION:
        return True
    where = f" {source}" if source is not None else ""
    print(
        f"clauditor.benchmark: skipping{where} — "
        f"schema_version={version!r} (expected {_BENCHMARK_SCHEMA_VERSION})",
        file=sys.stderr,
    )
    return False


@dataclass
class MetricStats:
    """Mean / stddev pair for a single metric on a single arm.

    ``stddev`` is ``None`` on the baseline arm (single observation,
    DEC-006) and ``0.0`` on the primary arm when N=1 (also single
    observation but the primary arm *could* have been sampled with
    N>1, so a numeric zero documents "we sampled once and saw no
    variance" rather than "variance not available").
    """

    mean: float
    stddev: float | None


@dataclass
class RunArm:
    """Aggregated metrics for one side of the baseline pair."""

    pass_rate: MetricStats
    time_seconds: MetricStats
    tokens: MetricStats


@dataclass
class Delta:
    """``with_skill.mean - without_skill.mean`` for the three metrics."""

    pass_rate: float
    time_seconds: float
    tokens: float


@dataclass
class RunSummary:
    with_skill: RunArm
    without_skill: RunArm
    delta: Delta


@dataclass
class Benchmark:
    """Serializable agentskills.io ``benchmark.json`` payload.

    See :func:`compute_benchmark` for construction. The
    ``schema_version`` field is emitted as the first top-level key of
    the JSON object per ``.claude/rules/json-schema-version.md``.
    """

    skill_name: str
    run_summary: RunSummary
    schema_version: int = _BENCHMARK_SCHEMA_VERSION

    def to_json(self) -> str:
        """Serialize to the agentskills.io JSON shape.

        ``schema_version`` is the first key — Python's ``json`` module
        preserves dict insertion order on emit, so we just build the
        dict in the canonical order.
        """
        data = {
            "schema_version": self.schema_version,
            "skill_name": self.skill_name,
            "run_summary": {
                "with_skill": _arm_to_dict(self.run_summary.with_skill),
                "without_skill": _arm_to_dict(self.run_summary.without_skill),
                "delta": {
                    "pass_rate": self.run_summary.delta.pass_rate,
                    "time_seconds": self.run_summary.delta.time_seconds,
                    "tokens": self.run_summary.delta.tokens,
                },
            },
        }
        return json.dumps(data, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str, *, source: Path | str | None = None) -> Benchmark:
        """Parse a ``benchmark.json`` payload, hard-failing on version mismatch.

        Raises :class:`ValueError` when ``schema_version`` is missing or
        not equal to ``1`` — callers that want soft-skip semantics
        should call :func:`_check_schema_version` themselves.
        """
        data = json.loads(text)
        if not _check_schema_version(data, source=source):
            raise ValueError(
                f"benchmark.json schema_version mismatch "
                f"(expected {_BENCHMARK_SCHEMA_VERSION}, got "
                f"{data.get('schema_version')!r})"
            )
        rs = data["run_summary"]
        return cls(
            schema_version=data["schema_version"],
            skill_name=data["skill_name"],
            run_summary=RunSummary(
                with_skill=_arm_from_dict(rs["with_skill"]),
                without_skill=_arm_from_dict(rs["without_skill"]),
                delta=Delta(
                    pass_rate=rs["delta"]["pass_rate"],
                    time_seconds=rs["delta"]["time_seconds"],
                    tokens=rs["delta"]["tokens"],
                ),
            ),
        )


def _arm_to_dict(arm: RunArm) -> dict:
    return {
        "pass_rate": {"mean": arm.pass_rate.mean, "stddev": arm.pass_rate.stddev},
        "time_seconds": {
            "mean": arm.time_seconds.mean,
            "stddev": arm.time_seconds.stddev,
        },
        "tokens": {"mean": arm.tokens.mean, "stddev": arm.tokens.stddev},
    }


def _arm_from_dict(data: dict) -> RunArm:
    return RunArm(
        pass_rate=MetricStats(
            mean=data["pass_rate"]["mean"], stddev=data["pass_rate"]["stddev"]
        ),
        time_seconds=MetricStats(
            mean=data["time_seconds"]["mean"], stddev=data["time_seconds"]["stddev"]
        ),
        tokens=MetricStats(
            mean=data["tokens"]["mean"], stddev=data["tokens"]["stddev"]
        ),
    )


def _primary_stats(values: list[float]) -> MetricStats:
    """Mean + sample stddev for a primary-arm metric.

    Single observation → stddev ``0.0`` (we sampled once, observed
    no variance). N>1 → :func:`statistics.stdev`.
    """
    if not values:
        # Should be guarded against at the call site; defensive only.
        raise ValueError("cannot aggregate empty value list")
    mean = sum(values) / len(values)
    if len(values) == 1:
        return MetricStats(mean=mean, stddev=0.0)
    return MetricStats(mean=mean, stddev=statistics.stdev(values))


def _baseline_stats(value: float) -> MetricStats:
    """Baseline-arm MetricStats: mean is the single observation, stddev is None.

    DEC-006: the baseline runs exactly once; sample stddev is not
    meaningful, and ``None`` / JSON ``null`` correctly signals "no
    variance data available".
    """
    return MetricStats(mean=float(value), stddev=None)


def compute_benchmark(
    *,
    skill_name: str,
    primary_reports: list[GradingReport],
    baseline_report: GradingReport,
    primary_results: list[SkillResult],
    baseline_result: SkillResult,
) -> Benchmark:
    """Aggregate pair-run metrics into a :class:`Benchmark`.

    Parameters are keyword-only to match other clauditor constructors
    and to keep call sites readable when the signature grows.

    ``primary_reports`` must be non-empty and every report (including
    ``baseline_report``) must have a non-empty ``results`` list — an
    empty list would make ``pass_rate`` undefined. The error message
    names the offending arm ("primary" or "baseline") so the operator
    can triage without cross-referencing stack traces.

    ``primary_results`` supplies ``duration_seconds`` /
    ``input_tokens`` / ``output_tokens`` for the primary arm; its
    length must match ``primary_reports`` so per-rep metrics zip
    cleanly. ``baseline_result`` is the single (DEC-007) baseline
    observation.
    """
    if not primary_reports:
        raise ValueError(
            "compute_benchmark: primary_reports is empty — cannot compute "
            "with_skill arm without at least one grading report"
        )
    if len(primary_results) != len(primary_reports):
        raise ValueError(
            f"compute_benchmark: primary_results length ({len(primary_results)}) "
            f"does not match primary_reports length ({len(primary_reports)})"
        )
    for idx, report in enumerate(primary_reports):
        if not report.results:
            raise ValueError(
                f"compute_benchmark: primary report at index {idx} has empty "
                "results — cannot compute pass_rate for the primary arm"
            )
    if not baseline_report.results:
        raise ValueError(
            "compute_benchmark: baseline report has empty results — cannot "
            "compute pass_rate for the baseline arm"
        )

    primary_pass_rates = [r.pass_rate for r in primary_reports]
    primary_times = [float(res.duration_seconds) for res in primary_results]
    primary_tokens = [
        float(res.input_tokens + res.output_tokens) for res in primary_results
    ]

    with_skill = RunArm(
        pass_rate=_primary_stats(primary_pass_rates),
        time_seconds=_primary_stats(primary_times),
        tokens=_primary_stats(primary_tokens),
    )
    without_skill = RunArm(
        pass_rate=_baseline_stats(baseline_report.pass_rate),
        time_seconds=_baseline_stats(baseline_result.duration_seconds),
        tokens=_baseline_stats(
            baseline_result.input_tokens + baseline_result.output_tokens
        ),
    )
    delta = Delta(
        pass_rate=with_skill.pass_rate.mean - without_skill.pass_rate.mean,
        time_seconds=with_skill.time_seconds.mean - without_skill.time_seconds.mean,
        tokens=with_skill.tokens.mean - without_skill.tokens.mean,
    )
    return Benchmark(
        skill_name=skill_name,
        run_summary=RunSummary(
            with_skill=with_skill,
            without_skill=without_skill,
            delta=delta,
        ),
    )
