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
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from clauditor.paths import resolve_clauditor_dir

# US-006 / #86: per-sidecar accepted schema versions. Grading and
# extraction sidecars bumped to v2 to carry the ``transport_source``
# field added by the CLI-transport feature; the loader accepts both
# versions and defaults missing ``transport_source`` to ``"api"`` at
# read time. Assertions sidecars stay at v1 (no transport_source field
# for L1 assertions). See DEC-007 and DEC-018 in
# ``plans/super/86-claude-cli-transport.md``.
_ACCEPTED_SCHEMA_VERSIONS: dict[str, frozenset[int]] = {
    "assertions.json": frozenset({1}),
    "extraction.json": frozenset({1, 2}),
    "grading.json": frozenset({1, 2}),
}


def _accepted_versions_for(filename: str) -> frozenset[int]:
    """Map a sidecar filename (with optional ``baseline_`` prefix) to its
    accepted schema versions. Falls back to ``{1}`` for unknown filenames.
    """
    base = filename
    if base.startswith("baseline_"):
        base = base[len("baseline_"):]
    return _ACCEPTED_SCHEMA_VERSIONS.get(base, frozenset({1}))


def _check_schema_version(
    data: dict, *, iteration_dir: Path | str, filename: str
) -> bool:
    """Verify the on-disk sidecar advertises an accepted schema_version.

    Grading and extraction sidecars accept ``{1, 2}`` (US-006 of
    ``plans/super/86-claude-cli-transport.md`` bumps them to v2 to
    carry ``transport_source``); assertions sidecars accept ``{1}``.
    A missing ``schema_version`` is treated as unknown and skipped
    with a one-line stderr warning.
    """
    accepted = _accepted_versions_for(filename)
    version = data.get("schema_version")
    if version in accepted:
        return True
    print(
        f"clauditor.audit: skipping {iteration_dir}/{filename} — "
        f"schema_version={version!r} "
        f"(expected one of {sorted(accepted)})",
        file=sys.stderr,
    )
    return False

__all__ = [
    "AuditAggregate",
    "AuditVerdict",
    "IterationRecord",
    "Verdict",
    "aggregate",
    "apply_thresholds",
    "load_iterations",
    "render_json",
    "render_markdown",
    "render_stdout_table",
]


class Verdict(StrEnum):
    """Audit verdict for a single (layer, id) aggregate."""

    KEEP = "keep"
    FLAG_ALWAYS_PASS = "flag-always-pass"
    FLAG_ZERO_FAILURES = "flag-zero-failures"
    FLAG_NO_DISCRIMINATION = "flag-no-discrimination"

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
    data: dict,
    *,
    iteration: int,
    with_skill: bool,
    iteration_dir: Path | str = "",
    filename: str = "assertions.json",
) -> list[IterationRecord]:
    if not _check_schema_version(
        data, iteration_dir=iteration_dir, filename=filename
    ):
        return []
    records: list[IterationRecord] = []
    for run in data.get("runs", []) or []:
        for result in run.get("results", []) or []:
            rid = result.get("id")
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
    data: dict,
    *,
    iteration: int,
    with_skill: bool,
    iteration_dir: Path | str = "",
    filename: str = "extraction.json",
) -> list[IterationRecord]:
    if not _check_schema_version(
        data, iteration_dir=iteration_dir, filename=filename
    ):
        return []
    records: list[IterationRecord] = []
    for field_id, entries in (data.get("fields") or {}).items():
        for entry in entries or []:
            if "passed" not in entry:
                continue
            passed = bool(entry["passed"])
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
    data: dict,
    *,
    iteration: int,
    with_skill: bool,
    iteration_dir: Path | str = "",
    filename: str = "grading.json",
) -> list[IterationRecord]:
    if not _check_schema_version(
        data, iteration_dir=iteration_dir, filename=filename
    ):
        return []
    records: list[IterationRecord] = []
    for result in data.get("results", []) or []:
        # DEC-001 / #25: L3 results are keyed by their stable spec id.
        # Drop records missing an ``id`` entirely — falling back to the
        # criterion text would silently reset audit history the moment a
        # criterion's wording changed (which is the whole point of the
        # stable-id contract).
        rid = result.get("id")
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

        records_before = len(records)

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
                        iteration_dir=str(skill_dir),
                        filename=f"{prefix}assertions.json",
                    )
                )
            if extraction is not None:
                records.extend(
                    _records_from_extraction(
                        extraction,
                        iteration=iteration_num,
                        with_skill=with_skill,
                        iteration_dir=str(skill_dir),
                        filename=f"{prefix}extraction.json",
                    )
                )
            if grading is not None:
                records.extend(
                    _records_from_grading(
                        grading,
                        iteration=iteration_num,
                        with_skill=with_skill,
                        iteration_dir=str(skill_dir),
                        filename=f"{prefix}grading.json",
                    )
                )

        # Count as skipped if no records were produced for this iteration —
        # sidecars may exist but be empty / schema-mismatched / unparseable.
        if len(records) == records_before:
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


# --------------------------------------------------------------------------- #
# Thresholds / verdicts                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class AuditVerdict:
    """A threshold classification over one :class:`AuditAggregate`."""

    layer: str
    id: str
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    aggregate: AuditAggregate | None = None

    @property
    def is_flagged(self) -> bool:
        return self.verdict != Verdict.KEEP


def apply_thresholds(
    aggregates: dict[tuple[str, str], AuditAggregate],
    *,
    min_fail_rate: float,
    min_discrimination: float,
) -> list[AuditVerdict]:
    """Classify each aggregate against the audit thresholds (DEC-005).

    Rules, in priority order (the first match drives the verdict; all
    matches are recorded in ``reasons``):

    1. ``with_pass_rate >= 1.0 - min_fail_rate`` → FLAG_ALWAYS_PASS.
       When ``min_fail_rate == 0`` this collapses to ``pass_rate == 1.0``.
    2. ``with_fails == 0`` → FLAG_ZERO_FAILURES.
    3. ``discrimination is not None`` and ``abs(discrimination) <
       min_discrimination`` → FLAG_NO_DISCRIMINATION.

    Otherwise the verdict is :attr:`Verdict.KEEP`.

    FIX-13: Aggregates with ``total_with_runs == 0`` are skipped — those
    represent assertions that only ever appeared in baseline sidecars
    (e.g. the spec dropped the assertion but historical baseline data
    is still on disk). Emitting a verdict for them would misleadingly
    show ``with% = 0.0%`` with a ``KEEP`` verdict. The raw records are
    preserved on disk; only the verdict stream filters them out.
    """
    verdicts: list[AuditVerdict] = []
    for (layer, rid), agg in sorted(aggregates.items()):
        if agg.total_with_runs == 0:
            continue
        reasons: list[str] = []
        verdict = Verdict.KEEP

        threshold = 1.0 - min_fail_rate
        if agg.total_with_runs > 0 and agg.with_pass_rate >= threshold:
            if min_fail_rate > 0 and agg.with_pass_rate < 1.0:
                reasons.append(
                    f"pass rate {agg.with_pass_rate:.2%} exceeds "
                    f"{threshold:.2%} (min-fail-rate={min_fail_rate})"
                )
            else:
                reasons.append(
                    f"passes on every run ({agg.total_with_runs}/"
                    f"{agg.total_with_runs})"
                )
            if verdict == Verdict.KEEP:
                verdict = Verdict.FLAG_ALWAYS_PASS

        if agg.total_with_runs > 0 and agg.with_fails == 0:
            reasons.append(
                f"zero recorded failures across {agg.total_with_runs} runs"
            )
            if verdict == Verdict.KEEP:
                verdict = Verdict.FLAG_ZERO_FAILURES

        disc = agg.discrimination
        if disc is not None and abs(disc) < min_discrimination:
            reasons.append(
                f"discrimination {disc:+.2%} below "
                f"{min_discrimination:.2%} threshold"
            )
            if verdict == Verdict.KEEP:
                verdict = Verdict.FLAG_NO_DISCRIMINATION

        verdicts.append(
            AuditVerdict(
                layer=layer,
                id=rid,
                verdict=verdict,
                reasons=reasons,
                aggregate=agg,
            )
        )
    return verdicts


# --------------------------------------------------------------------------- #
# Renderers                                                                    #
# --------------------------------------------------------------------------- #


def _md_escape(s: str) -> str:
    """FIX-12: escape markdown-special characters in user-derived cells.

    Ids and reason strings can contain ``|`` or backticks, which break
    table rows. Escape both. Pipes become ``\\|``; backticks become
    ``\\```. Nothing else needs escaping for our current table shape.
    """
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def _fmt_disc(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.1f}%"


def render_stdout_table(verdicts: list[AuditVerdict]) -> str:
    """Compact table: layer, id, with%, verdict."""
    header = f"{'LAYER':<6} {'ID':<40} {'WITH%':>8} {'VERDICT':<24}"
    lines = [header, "-" * len(header)]
    for v in verdicts:
        agg = v.aggregate
        with_pct = _fmt_pct(agg.with_pass_rate) if agg else "-"
        lines.append(
            f"{v.layer:<6} {v.id[:40]:<40} {with_pct:>8} {v.verdict.value:<24}"
        )
    return "\n".join(lines)


def render_markdown(
    verdicts: list[AuditVerdict],
    *,
    skill: str,
    iterations_analyzed: int,
    thresholds: dict[str, float | int],
    timestamp: str,
) -> str:
    """Render the audit report as markdown."""
    flagged = [v for v in verdicts if v.is_flagged]
    kept = [v for v in verdicts if not v.is_flagged]

    lines: list[str] = []
    lines.append(f"# Clauditor audit — {skill}")
    lines.append("")
    lines.append(f"- **Skill:** `{skill}`")
    lines.append(f"- **Timestamp:** {timestamp}")
    lines.append(f"- **Iterations analyzed:** {iterations_analyzed}")
    lines.append("- **Thresholds:**")
    for key, value in thresholds.items():
        lines.append(f"  - `{key}` = {value}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total assertions: {len(verdicts)}")
    lines.append(f"- Flagged: {len(flagged)}")
    lines.append(f"- Keep: {len(kept)}")
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict.value] = counts.get(v.verdict.value, 0) + 1
    for verdict_name, count in sorted(counts.items()):
        lines.append(f"  - `{verdict_name}`: {count}")
    lines.append("")

    lines.append("## Suggest removal")
    lines.append("")
    if not flagged:
        lines.append("_No assertions flagged — nothing to remove._")
    else:
        for v in flagged:
            reasons = "; ".join(v.reasons) if v.reasons else v.verdict.value
            lines.append(
                f"- **{v.layer} `{_md_escape(v.id)}`** — "
                f"{_md_escape(reasons)}"
            )
    lines.append("")

    for layer in ("L1", "L2", "L3"):
        layer_rows = [v for v in verdicts if v.layer == layer]
        lines.append(f"## {layer} detail")
        lines.append("")
        if not layer_rows:
            lines.append("_No data._")
            lines.append("")
            continue
        lines.append(
            "| id | runs | with% | baseline% | discrimination | verdict |"
        )
        lines.append("|----|------|-------|-----------|----------------|---------|")
        for v in layer_rows:
            agg = v.aggregate
            if agg is None:
                continue
            lines.append(
                f"| `{_md_escape(v.id)}` | {agg.total_with_runs} | "
                f"{_fmt_pct(agg.with_pass_rate)} | "
                f"{_fmt_pct(agg.baseline_pass_rate)} | "
                f"{_fmt_disc(agg.discrimination)} | "
                f"`{_md_escape(v.verdict.value)}` |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(
    verdicts: list[AuditVerdict],
    *,
    skill: str,
    iterations_analyzed: int,
    thresholds: dict[str, float | int],
    timestamp: str,
) -> dict:
    """Return a JSON-serializable audit payload."""
    assertions_list: list[dict] = []
    for v in verdicts:
        agg = v.aggregate
        assertions_list.append(
            {
                "layer": v.layer,
                "id": v.id,
                "with_runs": agg.total_with_runs if agg else 0,
                "with_pass_rate": agg.with_pass_rate if agg else None,
                "baseline_runs": agg.total_baseline_runs if agg else 0,
                "baseline_pass_rate": (
                    agg.baseline_pass_rate if agg else None
                ),
                "discrimination": agg.discrimination if agg else None,
                "verdict": v.verdict.value,
                "reasons": list(v.reasons),
            }
        )
    return {
        "schema_version": 1,
        "skill": skill,
        "timestamp": timestamp,
        "iterations": iterations_analyzed,
        "thresholds": dict(thresholds),
        "assertions": assertions_list,
    }
