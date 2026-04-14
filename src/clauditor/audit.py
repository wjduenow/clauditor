"""Iteration loader + aggregator for ``clauditor audit``.

Walks ``.clauditor/iteration-N/<skill>/`` sidecars produced by
``cmd_grade`` (US-002/US-003) and ``cmd_grade``'s baseline variant
(US-004), loads per-assertion pass/fail records keyed by stable spec
id (DEC-001), and aggregates pass rates per ``(layer, id)`` across the
last N iterations.

Responsibilities:

- :func:`load_iterations` — scan the newest N iteration dirs and
  build a flat list of :class:`IterationRecord` entries. Silently
  skips iteration dirs that are missing every sidecar; tolerates
  partially-populated dirs (e.g. L1 present but L2 absent).
- :func:`aggregate` — group records by ``(layer, id)`` and compute
  with-skill / baseline pass rates.

Threshold gating, flag classification, and markdown rendering are
handled in a sibling bead (US-006 / clauditor-8qo); this module only
produces the raw + aggregated data. Traces to DEC-002, DEC-005, and
DEC-007 in ``plans/super/25-assertion-auditor.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from clauditor.paths import resolve_clauditor_dir

__all__ = [
    "AuditAggregate",
    "IterationRecord",
    "aggregate",
    "load_iterations",
]

_ITERATION_RE = re.compile(r"^iteration-(\d+)$")


@dataclass
class IterationRecord:
    """A single per-iteration, per-assertion result."""

    iteration: int
    layer: str  # "L1" | "L2" | "L3"
    id: str
    passed: bool
    with_skill: bool  # True for primary, False for baseline sidecar


@dataclass
class AuditAggregate:
    """Aggregate pass-rate statistics for one ``(layer, id)`` pair."""

    layer: str
    id: str
    total_with_runs: int
    with_fails: int
    with_pass_rate: float
    total_baseline_runs: int
    baseline_fails: int
    baseline_pass_rate: float | None

    @property
    def discrimination(self) -> float | None:
        """With-skill pass rate minus baseline pass rate, or ``None``.

        Returns ``None`` when either side has no runs (can't compare).
        """
        if self.baseline_pass_rate is None or self.total_with_runs == 0:
            return None
        return self.with_pass_rate - self.baseline_pass_rate


# --------------------------------------------------------------------------- #
# Loader                                                                       #
# --------------------------------------------------------------------------- #


def _scan_iteration_dirs(clauditor_dir: Path) -> list[tuple[int, Path]]:
    """Return ``[(iteration_num, dir_path), ...]`` sorted descending."""
    if not clauditor_dir.exists():
        return []
    found: list[tuple[int, Path]] = []
    for child in clauditor_dir.iterdir():
        if not child.is_dir():
            continue
        match = _ITERATION_RE.match(child.name)
        if match is not None:
            found.append((int(match.group(1)), child))
    found.sort(key=lambda x: x[0], reverse=True)
    return found


def _read_json(path: Path) -> dict | None:
    """Best-effort JSON read. Returns ``None`` if the file is absent or
    malformed — the auditor treats it the same as "no data here"."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _records_from_assertions(
    data: dict, *, iteration: int, with_skill: bool
) -> list[IterationRecord]:
    records: list[IterationRecord] = []
    for run in data.get("runs", []) or []:
        for result in run.get("results", []) or []:
            rid = result.get("id") or result.get("name")
            if not rid:
                continue
            records.append(
                IterationRecord(
                    iteration=iteration,
                    layer="L1",
                    id=str(rid),
                    passed=bool(result.get("passed", False)),
                    with_skill=with_skill,
                )
            )
    return records


def _records_from_extraction(
    data: dict, *, iteration: int, with_skill: bool
) -> list[IterationRecord]:
    records: list[IterationRecord] = []
    for field_id, entries in (data.get("fields") or {}).items():
        for entry in entries or []:
            # The on-disk shape stores pre-computed ``passed`` combining
            # presence + format; prefer that, fall back to presence only.
            if "passed" in entry:
                passed = bool(entry["passed"])
            else:
                passed = bool(entry.get("presence_passed", False))
                fmt_passed = entry.get("format_passed")
                if fmt_passed is False:
                    passed = False
            records.append(
                IterationRecord(
                    iteration=iteration,
                    layer="L2",
                    id=str(field_id),
                    passed=passed,
                    with_skill=with_skill,
                )
            )
    return records


def _records_from_grading(
    data: dict, *, iteration: int, with_skill: bool
) -> list[IterationRecord]:
    records: list[IterationRecord] = []
    for result in data.get("results", []) or []:
        # After US-001 the criterion carries a stable id; the current
        # on-disk shape stores it under ``criterion`` as the canonical
        # key. Prefer an explicit ``id`` field if a future writer adds
        # it, otherwise fall back to the criterion text itself.
        rid = result.get("id") or result.get("criterion")
        if not rid:
            continue
        records.append(
            IterationRecord(
                iteration=iteration,
                layer="L3",
                id=str(rid),
                passed=bool(result.get("passed", False)),
                with_skill=with_skill,
            )
        )
    return records


def load_iterations(
    skill: str,
    last: int,
    clauditor_dir: Path | None = None,
) -> tuple[list[IterationRecord], int]:
    """Load per-iteration assertion records for ``skill``.

    Walks ``.clauditor/iteration-*/<skill>/`` newest-first, loads the
    L1/L2/L3 sidecars (primary and baseline variants), and flattens
    them into :class:`IterationRecord` entries.

    Args:
        skill: Skill name (subdirectory under each iteration dir).
        last: Maximum number of iteration dirs to consider, picking
            the newest by iteration number.
        clauditor_dir: Override for the ``.clauditor`` directory.
            Defaults to :func:`resolve_clauditor_dir`.

    Returns:
        Tuple of ``(records, skipped_count)`` where ``skipped_count``
        is the number of iteration dirs that had no primary and no
        baseline sidecar for this skill (per DEC-002).
    """
    if clauditor_dir is None:
        clauditor_dir = resolve_clauditor_dir()

    scanned = _scan_iteration_dirs(clauditor_dir)
    selected = scanned[:last]

    records: list[IterationRecord] = []
    skipped = 0

    for iteration_num, iteration_dir in selected:
        skill_dir = iteration_dir / skill
        if not skill_dir.is_dir():
            skipped += 1
            continue

        loaded_any = False

        for prefix, with_skill in (("", True), ("baseline_", False)):
            assertions = _read_json(skill_dir / f"{prefix}assertions.json")
            extraction = _read_json(skill_dir / f"{prefix}extraction.json")
            grading = _read_json(skill_dir / f"{prefix}grading.json")

            if assertions is not None:
                records.extend(
                    _records_from_assertions(
                        assertions,
                        iteration=iteration_num,
                        with_skill=with_skill,
                    )
                )
                loaded_any = True
            if extraction is not None:
                records.extend(
                    _records_from_extraction(
                        extraction,
                        iteration=iteration_num,
                        with_skill=with_skill,
                    )
                )
                loaded_any = True
            if grading is not None:
                records.extend(
                    _records_from_grading(
                        grading,
                        iteration=iteration_num,
                        with_skill=with_skill,
                    )
                )
                loaded_any = True

        if not loaded_any:
            skipped += 1

    return records, skipped


# --------------------------------------------------------------------------- #
# Aggregator                                                                   #
# --------------------------------------------------------------------------- #


def aggregate(
    records: list[IterationRecord],
) -> dict[tuple[str, str], AuditAggregate]:
    """Group records by ``(layer, id)`` and compute pass rates.

    With-skill and baseline records are tallied separately so callers
    can compare them (see :attr:`AuditAggregate.discrimination`).
    """
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for r in records:
        key = (r.layer, r.id)
        bucket = buckets.setdefault(
            key,
            {
                "with_total": 0,
                "with_fails": 0,
                "baseline_total": 0,
                "baseline_fails": 0,
            },
        )
        if r.with_skill:
            bucket["with_total"] += 1
            if not r.passed:
                bucket["with_fails"] += 1
        else:
            bucket["baseline_total"] += 1
            if not r.passed:
                bucket["baseline_fails"] += 1

    result: dict[tuple[str, str], AuditAggregate] = {}
    for (layer, rid), b in buckets.items():
        with_total = b["with_total"]
        baseline_total = b["baseline_total"]
        with_pass_rate = (
            (with_total - b["with_fails"]) / with_total
            if with_total
            else 0.0
        )
        baseline_pass_rate: float | None
        if baseline_total:
            baseline_pass_rate = (
                (baseline_total - b["baseline_fails"]) / baseline_total
            )
        else:
            baseline_pass_rate = None
        result[(layer, rid)] = AuditAggregate(
            layer=layer,
            id=rid,
            total_with_runs=with_total,
            with_fails=b["with_fails"],
            with_pass_rate=with_pass_rate,
            total_baseline_runs=baseline_total,
            baseline_fails=b["baseline_fails"],
            baseline_pass_rate=baseline_pass_rate,
        )
    return result
