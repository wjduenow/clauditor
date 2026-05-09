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
from typing import Literal

from clauditor.context import IterationContext
from clauditor.paths import resolve_clauditor_dir

# US-002 / #147 / DEC-008: per-sidecar maximum accepted schema version.
# Grading and extraction sidecars accept v1..v3:
#   - v1: original shape;
#   - v2 (#86, US-006 of ``plans/super/86-claude-cli-transport.md``):
#     adds ``transport_source``; loader defaults missing field to
#     ``"api"`` at read time;
#   - v3 (#147, US-001): adds ``provider_source``; loader defaults
#     missing field to ``"anthropic"`` at read time.
# Assertions sidecars stay at v1 (no transport_source/provider_source
# fields for L1 assertions).
#
# This is the canonical map; the pure helper :func:`_is_accepted_version`
# answers ``1 <= version <= MAX_SCHEMA_VERSION[base]`` for any
# ``baseline_``-prefixed or plain sidecar filename. Future bumps
# (e.g. #152's ``harness`` field) become a single-number edit per
# filename rather than re-listing the accepted set. See DEC-008 in
# ``plans/super/147-sidecar-provider-field.md``.
MAX_SCHEMA_VERSION: dict[str, int] = {
    "assertions.json": 2,
    "extraction.json": 4,
    "grading.json": 4,
    # #154 US-005 / DEC-010: context.json is a new sidecar family;
    # always-v1 (the v1 dataclass already ships nullable fields for the
    # observability surface, so future ``reasoning_tokens`` /
    # ``cost_usd`` work needs no schema bump).
    "context.json": 1,
}


def _is_accepted_version(filename: str, version: object) -> bool:
    """Return True iff ``version`` is an accepted schema_version for ``filename``.

    Pure helper. Accepts a ``baseline_``-prefixed filename and strips the
    prefix before consulting :data:`MAX_SCHEMA_VERSION`.

    The accept range is ``1 <= version <= MAX_SCHEMA_VERSION[base]`` —
    the loader assumes monotonic forward compatibility within a sidecar
    family. Per-version shape differences are handled by the
    ``_records_from_*`` helpers, not here.

    Raises :class:`KeyError` for any unknown filename — the caller is
    expected to pass one of the three known sidecar names (with or
    without the ``baseline_`` prefix). Non-int/non-bool ``version``
    values (including ``None`` from a missing ``schema_version`` key,
    or stringly-typed values from a malformed sidecar) return ``False``
    rather than raising, so :func:`_check_schema_version` can produce
    a clean stderr warning.
    """
    base = filename
    if base.startswith("baseline_"):
        base = base[len("baseline_"):]
    max_version = MAX_SCHEMA_VERSION[base]  # KeyError on unknown filename
    # Reject non-int and bool (per ``constant-with-type-info.md``: bool
    # is an int subclass in Python; ``True`` would otherwise compare as
    # ``1 <= True <= max_version`` and pass).
    if not isinstance(version, int) or isinstance(version, bool):
        return False
    return 1 <= version <= max_version


def _provider_or_default(value: object) -> str:
    """Return ``value`` when it is a non-blank ``str``, else ``"anthropic"``.

    Defends ``_records_from_extraction`` / ``_records_from_grading``
    against malformed v3 sidecars that store ``provider_source`` as
    ``1``, ``True``, ``None``, or an empty/whitespace string. Without
    this guard, a non-string ``provider`` would propagate into
    ``IterationRecord``/``AuditAggregate`` keys and blow up downstream
    sorting (``sorted({tuple-with-int-and-str})`` raises ``TypeError``)
    or markdown/stdout column rendering. Defaulting in one place keeps
    the audit pipeline structurally string-typed.
    """
    if isinstance(value, str) and value.strip():
        return value
    return "anthropic"


def _harness_or_default(value: object) -> str:
    """Return ``value`` when it is a non-blank ``str``, else ``"claude-code"``.

    US-005 (#152): mirror of :func:`_provider_or_default` for the
    harness axis. Defends the three ``_records_from_*`` helpers
    against malformed v2/v4 sidecars that store ``harness`` as a
    non-string. Defaults to ``"claude-code"`` per DEC-006 so legacy
    v1/v2/v3 reads (with no ``harness`` field) and malformed reads
    both produce structurally-string-typed aggregate keys.
    """
    if isinstance(value, str) and value.strip():
        return value
    return "claude-code"


def detect_mixed_dimension(
    records: list[dict], *, dimension: Literal["harness", "provider"]
) -> tuple[bool, list[str]]:
    """Return ``(is_mixed, sorted_unique_values)`` for ``dimension``.

    US-001 (#153): pure helper consumed by ``trend`` and ``compare``
    to refuse cross-axis averaging unless the operator opts in. Walks
    ``records`` once, reading the ``dimension`` field via ``dict.get``
    (so missing keys default), and routes each value through the
    sibling ``_provider_or_default`` / ``_harness_or_default``
    coercer so non-string / blank / ``None`` entries all collapse to
    the canonical default. Returns the alphabetically-sorted unique
    set and a ``bool`` indicating whether more than one distinct
    value survived coercion.

    No I/O, no side effects, never raises. ``dimension`` is
    constrained to the two axes clauditor groups by; a future axis
    would extend the ``Literal`` and the dispatch table together.

    Traces to: DEC-010 of ``plans/super/153-cross-axis-comparability.md``
    and ``.claude/rules/pure-compute-vs-io-split.md``.
    """
    coercer = (
        _provider_or_default if dimension == "provider"
        else _harness_or_default
    )
    unique = sorted({coercer(rec.get(dimension)) for rec in records})
    return len(unique) > 1, unique


def _check_schema_version(
    data: dict, *, iteration_dir: Path | str, filename: str
) -> bool:
    """Verify the on-disk sidecar advertises an accepted schema_version.

    Delegates to the pure helper :func:`_is_accepted_version`. Sidecars
    with a missing or out-of-range ``schema_version`` are skipped with
    a one-line stderr warning naming the expected accepted range.
    """
    version = data.get("schema_version")
    if _is_accepted_version(filename, version):
        return True
    base = filename
    if base.startswith("baseline_"):
        base = base[len("baseline_"):]
    max_version = MAX_SCHEMA_VERSION[base]
    print(
        f"clauditor.audit: skipping {iteration_dir}/{filename} — "
        f"schema_version={version!r} "
        f"(expected 1..{max_version})",
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
    "detect_mixed_dimension",
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
    """A single per-iteration, per-assertion result.

    US-003 (#147): the ``provider`` field records which model provider's
    SDK produced the underlying L2/L3 grading verdict. Loaded from the
    sidecar's v3 ``provider_source`` field; defaults to ``"anthropic"``
    for legacy v1/v2 sidecars (per DEC-001 of #147). L1
    (``_records_from_assertions``) always carries ``"anthropic"`` as a
    placeholder per DEC-002 — assertions sidecars stay at v1 because L1
    has no LLM call to attribute, but ``IterationRecord`` keeps a
    uniform shape across layers so audit grouping does not have to
    branch by layer.

    US-005 (#152): the ``harness`` field records which harness CLI
    produced the underlying skill output. Loaded from the v2
    assertions / v4 grading-and-extraction sidecars' ``harness``
    field; defaults to ``"claude-code"`` for legacy reads per DEC-006.
    Unlike ``provider`` (which carries an ``"anthropic"`` placeholder
    for L1), ``harness`` is real for every layer because every layer's
    underlying skill ran through some harness.
    """

    iteration: int
    layer: str  # "L1" | "L2" | "L3"
    id: str
    passed: bool
    with_skill: bool  # True for primary, False for baseline sidecar
    provider: str = "anthropic"
    harness: str = "claude-code"


@dataclass
class AuditAggregate:
    """Aggregate stats for one ``(harness, provider, layer, id)`` key.

    US-003 (#147): added the ``provider`` dimension so mixed-provider
    history (the same eval run under both Anthropic and OpenAI)
    groups into separate aggregates instead of being averaged
    together.

    US-005 (#152): added the ``harness`` dimension so mixed-harness
    history (the same eval run under both Claude Code and Codex)
    groups separately too. Aggregate dict keys are now 4-tuples
    ``(harness, provider, layer, id)`` per DEC-007. The ``harness``
    field defaults to ``"claude-code"`` to keep direct constructor
    calls in tests working without per-test edits.
    """

    layer: str
    id: str
    total_with_runs: int
    with_fails: int
    with_pass_rate: float
    total_baseline_runs: int
    baseline_fails: int
    baseline_pass_rate: float | None
    provider: str = "anthropic"
    harness: str = "claude-code"

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


def _read_context(skill_dir: Path) -> IterationContext | None:
    """Read ``context.json`` from ``skill_dir`` and return the parsed
    :class:`IterationContext`, or ``None`` for any failure mode.

    #154 US-005 / DEC-011: context is read parallel to records and
    attached to render output only — does NOT participate in
    :class:`IterationRecord` or :func:`aggregate`. Failure modes:

    - Missing file → ``None`` silently (per the existing ``_read_json``
      convention; defensive read posture per
      ``.claude/rules/stream-json-schema.md``).
    - Malformed JSON → ``None`` silently.
    - Wrong/missing ``schema_version`` → ``None`` PLUS a stderr warning
      via :func:`_check_schema_version` (the canonical seam owning
      sidecar-version stderr emission).
    - Hard-validator ``ValueError`` from
      :meth:`IterationContext.from_dict` (e.g. unknown ``harness``
      literal) → ``None`` PLUS a stderr warning. Do not propagate;
      audit must keep moving across iterations.
    """
    path = skill_dir / "context.json"
    data = _read_json(path)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    if not _check_schema_version(
        data, iteration_dir=str(skill_dir), filename="context.json"
    ):
        return None
    try:
        return IterationContext.from_dict(data)
    except (ValueError, KeyError, TypeError) as exc:
        print(
            f"clauditor.audit: skipping {skill_dir}/context.json — "
            f"malformed payload: {exc}",
            file=sys.stderr,
        )
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
    # DEC-002 (#147): L1 records carry ``provider="anthropic"`` as a
    # placeholder. Assertions sidecars carry no ``provider_source``
    # field even at v2 — L1 has no LLM call to attribute, but
    # ``IterationRecord`` keeps a uniform shape across layers so the
    # ``aggregate()`` group key is always uniform.
    # US-005 (#152): assertions.json bumped to v2 with a top-level
    # ``harness`` field. Read it through the defensive helper; v1
    # legacy reads default to ``"claude-code"`` per DEC-006.
    harness = _harness_or_default(data.get("harness"))
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
                    provider="anthropic",
                    harness=harness,
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
    # US-003 (#147): read v3 ``provider_source`` field; default to
    # ``"anthropic"`` for legacy v1/v2 reads per DEC-001 of #147.
    # ``_provider_or_default`` rejects non-string truthy values like
    # ``1`` or ``True`` (which a malformed v3 sidecar could carry) so
    # downstream sorting/rendering never sees a non-string provider.
    provider = _provider_or_default(data.get("provider_source"))
    # US-005 (#152): extraction.json bumped to v4 with ``harness``
    # field; v1/v2/v3 reads default to ``"claude-code"`` per DEC-006.
    harness = _harness_or_default(data.get("harness"))
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
                    provider=provider,
                    harness=harness,
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
    # US-003 (#147): same default-on-read shape as
    # ``_records_from_extraction`` — v3 sidecars carry
    # ``provider_source``; v1/v2 reads default to ``"anthropic"``.
    # ``_provider_or_default`` rejects non-string truthy values.
    provider = _provider_or_default(data.get("provider_source"))
    # US-005 (#152): grading.json bumped to v4 with ``harness`` field;
    # v1/v2/v3 reads default to ``"claude-code"`` per DEC-006.
    harness = _harness_or_default(data.get("harness"))
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
                provider=provider,
                harness=harness,
            )
        )
    return records


def load_iterations(
    skill: str,
    last: int,
    clauditor_dir: Path | None = None,
) -> tuple[list[IterationRecord], int, dict[int, IterationContext | None]]:
    """Load per-iteration assertion records for ``skill``.

    Walks ``.clauditor/iteration-*/<skill>/`` newest-first, loads the
    L1/L2/L3 sidecars (primary and baseline variants), and flattens
    them into :class:`IterationRecord` entries. Reads ``context.json``
    in parallel and returns a ``dict[iteration_num, IterationContext |
    None]`` for renderers to consume — context is comparability
    metadata only, NOT score data, so it does NOT participate in
    :func:`aggregate` per #154 DEC-011.

    Args:
        skill: Skill name (subdirectory under each iteration dir).
        last: Maximum number of iteration dirs to consider, picking
            the newest by iteration number.
        clauditor_dir: Override for the ``.clauditor`` directory.
            Defaults to :func:`resolve_clauditor_dir`.

    Returns:
        Tuple of ``(records, skipped_count, contexts)``:

        - ``records`` — flat list of :class:`IterationRecord`.
        - ``skipped_count`` — iteration dirs that had no primary and no
          baseline sidecar for this skill (per DEC-002).
        - ``contexts`` — ``{iteration_num: IterationContext | None}``
          for every iteration dir that was visited (selected before the
          skill-dir-missing skip). Iterations whose ``context.json``
          is absent / malformed / wrong-schema map to ``None`` so
          renderers can emit ``context: null`` for legacy iterations
          (per #154 DEC-005).
    """
    if clauditor_dir is None:
        clauditor_dir = resolve_clauditor_dir()

    scanned = _scan_iteration_dirs(clauditor_dir)
    selected = scanned[:last]

    records: list[IterationRecord] = []
    skipped = 0
    contexts: dict[int, IterationContext | None] = {}

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

        # #154 DEC-011: read context.json parallel to records; do NOT
        # attach to ``IterationRecord`` (would force every aggregate
        # call site to branch on presence). Legacy iterations with no
        # ``context.json`` map to ``None`` so renderers can emit a
        # uniform shape.
        contexts[iteration_num] = _read_context(skill_dir)

        # Count as skipped if no records were produced for this iteration —
        # sidecars may exist but be empty / schema-mismatched / unparseable.
        if len(records) == records_before:
            skipped += 1

    return records, skipped, contexts


# --------------------------------------------------------------------------- #
# Aggregator                                                                   #
# --------------------------------------------------------------------------- #


def aggregate(
    records: list[IterationRecord],
) -> dict[tuple[str, str, str, str], AuditAggregate]:
    """Group records by ``(harness, provider, layer, id)`` and compute pass rates.

    US-003 (#147): expanded the grouping key from ``(layer, id)`` to
    ``(provider, layer, id)`` so mixed-provider history groups
    separately.

    US-005 (#152): expanded the grouping key further to
    ``(harness, provider, layer, id)`` so mixed-harness history (the
    same eval run under both Claude Code and Codex) groups separately
    too. Pre-#152 history (no ``harness`` on disk) defaults every
    record's harness to ``"claude-code"`` and produces a single bucket
    per ``(provider, layer, id)`` keyed under
    ``("claude-code", provider, layer, id)``, so single-harness audit
    reports keep their pre-#152 shape.

    With-skill and baseline records are tallied separately so callers
    can compare them (see :attr:`AuditAggregate.discrimination`).
    """
    buckets: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for r in records:
        key = (r.harness, r.provider, r.layer, r.id)
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

    result: dict[tuple[str, str, str, str], AuditAggregate] = {}
    for (harness, provider, layer, rid), b in buckets.items():
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
        result[(harness, provider, layer, rid)] = AuditAggregate(
            layer=layer,
            id=rid,
            total_with_runs=with_total,
            with_fails=b["with_fails"],
            with_pass_rate=with_pass_rate,
            total_baseline_runs=baseline_total,
            baseline_fails=b["baseline_fails"],
            baseline_pass_rate=baseline_pass_rate,
            provider=provider,
            harness=harness,
        )
    return result


# --------------------------------------------------------------------------- #
# Thresholds / verdicts                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class AuditVerdict:
    """A threshold classification over one :class:`AuditAggregate`.

    US-003 (#147): added the ``provider`` field so renderers can
    surface the provider dimension in the audit output.

    US-005 (#152): added the ``harness`` field so renderers (US-006)
    can surface the harness dimension. Defaults to ``"claude-code"``
    so direct test fixture constructions that predate #152 keep
    working without per-test edits.
    """

    layer: str
    id: str
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    aggregate: AuditAggregate | None = None
    provider: str = "anthropic"
    harness: str = "claude-code"

    @property
    def is_flagged(self) -> bool:
        return self.verdict != Verdict.KEEP


def apply_thresholds(
    aggregates: dict[tuple[str, str, str, str], AuditAggregate],
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
    # US-005 (#152): unpack the 4-tuple ``(harness, provider, layer,
    # id)`` key produced by :func:`aggregate`. ``sorted`` orders
    # harness first, then provider, then layer, then id — so an audit
    # report renders all-claude-code rows before all-codex rows, and
    # within a harness all-anthropic rows before openai rows.
    for (harness, provider, layer, rid), agg in sorted(aggregates.items()):
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
                provider=provider,
                harness=harness,
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


def _sorted_verdicts(verdicts: list[AuditVerdict]) -> list[AuditVerdict]:
    """Sort verdicts by ``(harness, provider, layer, id)`` for stable rendering.

    DEC-004 (#147): all three render paths share the same sort order so
    a reader scanning ``stdout`` / markdown / JSON in succession sees
    rows in the same sequence. ``apply_thresholds`` already returns
    verdicts in this order today, but renderers re-sort defensively in
    case a caller hands in a list constructed differently.

    US-006 (#152): widened the sort key to include ``harness`` (first)
    so mixed-harness audits render all-claude-code rows before
    all-codex rows; within a harness, the existing ``(provider, layer,
    id)`` ordering preserves pre-#152 row sequence for single-harness
    audits.
    """
    return sorted(
        verdicts, key=lambda v: (v.harness, v.provider, v.layer, v.id)
    )


def _providers_seen(verdicts: list[AuditVerdict]) -> list[str]:
    """Return the sorted list of distinct providers across ``verdicts``.

    DEC-010 (#147): the audit-output JSON v2 carries a top-level
    ``providers_seen`` array so JSON consumers can detect mixed-provider
    history without iterating ``assertions[]``.
    """
    return sorted({v.provider for v in verdicts})


def _harnesses_seen(verdicts: list[AuditVerdict]) -> list[str]:
    """Return the sorted list of distinct harnesses across ``verdicts``.

    DEC-010 (#152): the audit-output JSON v3 carries a top-level
    ``harnesses_seen`` array (sibling to ``providers_seen``) so JSON
    consumers can detect mixed-harness history without iterating
    ``assertions[]``.
    """
    return sorted({v.harness for v in verdicts})


# DEC-008 (#152): em-dash (U+2014) is the stdout/markdown placeholder
# rendered in L1 rows' PROVIDER column. L1 makes no LLM call so
# "anthropic" is a placeholder, not a real value — the em-dash makes
# the absence visible to humans. The on-disk JSON output keeps the
# ``"provider": "anthropic"`` placeholder for downstream consumers
# (semantically honest for mixed-layer aggregation).
_L1_PROVIDER_DISPLAY = "—"


def _context_field_lines(ctx: IterationContext) -> list[tuple[str, str]]:
    """Return ``[(label, value_str)]`` for the eight captured context fields.

    Pure helper consumed by the verbose stdout/markdown renderers per
    #154 DEC-005. Field order matches the dataclass declaration so the
    on-screen layout stays predictable across renderers.
    """
    def _fmt(v: object) -> str:
        return "-" if v is None else str(v)

    return [
        ("harness", _fmt(ctx.harness)),
        ("provider", _fmt(ctx.provider)),
        ("model_runner", _fmt(ctx.model_runner)),
        ("model_grader", _fmt(ctx.model_grader)),
        ("system_prompt_source", _fmt(ctx.system_prompt_source)),
        ("sandbox_mode", _fmt(ctx.sandbox_mode)),
        ("reasoning_tokens", _fmt(ctx.reasoning_tokens)),
        ("cost_usd", _fmt(ctx.cost_usd)),
    ]


def render_stdout_table(
    verdicts: list[AuditVerdict],
    *,
    iteration_contexts: dict[int, IterationContext | None] | None = None,
    verbose: bool = False,
) -> str:
    """Compact table: harness, provider, layer, id, with%, verdict.

    DEC-009 (#152): leftmost ``HARNESS`` column (~11 chars wide), then
    ``PROVIDER`` (~11 chars wide), then layer/id/with%/verdict. Column
    order matches the grouping-key tuple order
    ``(harness, provider, layer, id)``.

    DEC-008 (#152): L1 rows render the PROVIDER cell as ``"—"``
    (em-dash, U+2014) since L1 makes no LLM call. L2/L3 rows render
    the real provider value.

    #154 US-005 / DEC-005: when ``verbose=True`` and
    ``iteration_contexts`` is provided, append a per-iteration
    ``Context for iteration N`` block listing the eight captured
    fields. Pre-#154 iterations whose ``context.json`` is absent are
    skipped (no block emitted) so the verbose output stays readable.
    """
    header = (
        f"{'HARNESS':<11} {'PROVIDER':<11} {'LAYER':<6} {'ID':<40} "
        f"{'WITH%':>8} {'VERDICT':<24}"
    )
    lines = [header, "-" * len(header)]
    for v in _sorted_verdicts(verdicts):
        agg = v.aggregate
        with_pct = _fmt_pct(agg.with_pass_rate) if agg else "-"
        # DEC-008: L1 PROVIDER cell shows em-dash placeholder; L2/L3
        # render the real provider value.
        provider_display = (
            _L1_PROVIDER_DISPLAY if v.layer == "L1" else v.provider
        )
        lines.append(
            f"{v.harness[:11]:<11} {provider_display[:11]:<11} "
            f"{v.layer:<6} {v.id[:40]:<40} "
            f"{with_pct:>8} {v.verdict.value:<24}"
        )

    if verbose and iteration_contexts:
        # Sort iterations descending (newest-first) to match the
        # iteration-dir scan order in :func:`load_iterations`.
        for iteration_num in sorted(iteration_contexts.keys(), reverse=True):
            ctx = iteration_contexts[iteration_num]
            if ctx is None:
                continue
            lines.append("")
            lines.append(f"Context for iteration {iteration_num}:")
            for label, value in _context_field_lines(ctx):
                lines.append(f"  {label}: {value}")

    return "\n".join(lines)


def render_markdown(
    verdicts: list[AuditVerdict],
    *,
    skill: str,
    iterations_analyzed: int,
    thresholds: dict[str, float | int],
    timestamp: str,
    iteration_contexts: dict[int, IterationContext | None] | None = None,
    verbose: bool = False,
) -> str:
    """Render the audit report as markdown.

    #154 US-005 / DEC-005: when ``verbose=True`` and
    ``iteration_contexts`` is provided, append a ``## Per-iteration
    context`` section with one ``### Iteration N`` subsection per
    iteration that has a populated ``context.json``. Pre-#154
    iterations whose context is ``None`` are skipped so the verbose
    output stays readable.
    """
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
            # #152 N2: include harness in the bullet so mixed-harness
            # audits don't produce visually identical bullets for the
            # same (layer, id) under different harnesses. Provider is
            # included for L2/L3 only — L1's "anthropic" placeholder
            # would just add noise.
            head = f"{v.layer} {v.harness}"
            if v.layer in ("L2", "L3"):
                head = f"{head}/{v.provider}"
            lines.append(
                f"- **{head} `{_md_escape(v.id)}`** — "
                f"{_md_escape(reasons)}"
            )
    lines.append("")

    sorted_verdicts = _sorted_verdicts(verdicts)
    for layer in ("L1", "L2", "L3"):
        layer_rows = [v for v in sorted_verdicts if v.layer == layer]
        lines.append(f"## {layer} detail")
        lines.append("")
        if not layer_rows:
            lines.append("_No data._")
            lines.append("")
            continue
        # DEC-009 (#152): leftmost ``harness`` column, then ``provider``
        # so mixed-harness/mixed-provider audits show both dimensions.
        # DEC-008 (#152): L1 rows render provider as ``—`` (em-dash,
        # U+2014) since L1 makes no LLM call. L2/L3 rows render the
        # real provider value. Provider cell is NOT backtick-quoted
        # for L1 because the em-dash is a typographic placeholder, not
        # a code identifier.
        lines.append(
            "| harness | provider | id | runs | with% | baseline% | "
            "discrimination | verdict |"
        )
        lines.append(
            "|---------|----------|----|------|-------|-----------|"
            "----------------|---------|"
        )
        for v in layer_rows:
            agg = v.aggregate
            if agg is None:
                continue
            if v.layer == "L1":
                provider_cell = _L1_PROVIDER_DISPLAY
            else:
                provider_cell = f"`{_md_escape(v.provider)}`"
            lines.append(
                f"| `{_md_escape(v.harness)}` | {provider_cell} | "
                f"`{_md_escape(v.id)}` | {agg.total_with_runs} | "
                f"{_fmt_pct(agg.with_pass_rate)} | "
                f"{_fmt_pct(agg.baseline_pass_rate)} | "
                f"{_fmt_disc(agg.discrimination)} | "
                f"`{_md_escape(v.verdict.value)}` |"
            )
        lines.append("")

    if verbose and iteration_contexts:
        # #154 DEC-005: emit per-iteration context block under
        # ``--verbose``. Iterations with no ``context.json`` (legacy
        # pre-#154 runs) are skipped so the section stays readable.
        populated = sorted(
            (
                (n, c)
                for n, c in iteration_contexts.items()
                if c is not None
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if populated:
            lines.append("## Per-iteration context")
            lines.append("")
            for iteration_num, ctx in populated:
                lines.append(f"### Iteration {iteration_num}")
                lines.append("")
                lines.append("| field | value |")
                lines.append("|-------|-------|")
                for label, value in _context_field_lines(ctx):
                    lines.append(
                        f"| `{_md_escape(label)}` | `{_md_escape(value)}` |"
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
    iteration_contexts: dict[int, IterationContext | None] | None = None,
) -> dict:
    """Return a JSON-serializable audit payload.

    DEC-005 (#147): ``schema_version`` bumped from 1 to 2 to signal the
    new ``provider`` field on each ``assertions[]`` entry. DEC-010
    (#147): adds a top-level ``providers_seen`` array (sorted
    alphabetically) so JSON consumers can detect mixed-provider history
    without iterating ``assertions[]``.

    DEC-010 (#152): ``schema_version`` bumped from 2 to 3 to signal the
    new ``harness`` field on each ``assertions[]`` entry plus the new
    top-level ``harnesses_seen`` array (sibling to ``providers_seen``).
    Sort order across ``assertions`` matches the stdout/markdown
    renderers: ``(harness, provider, layer, id)``.

    DEC-008 (#152): L1 entries keep ``"provider": "anthropic"``
    placeholder in the JSON output (the em-dash is stdout/markdown
    only) — keeps mixed-layer JSON aggregation semantically honest for
    downstream consumers.

    #154 US-005 / DEC-005: always emits a top-level
    ``iteration_contexts`` array — one record per iteration that was
    visited by :func:`load_iterations`, sorted by iteration number
    descending (newest first). Each record carries ``iteration: <int>``
    and ``context: {...} | null`` (legacy iterations with no
    ``context.json`` get ``null``). Unconditional emission per DEC-005
    — JSON consumers should not need a ``--verbose`` flag to opt into
    a stable field.
    """
    sorted_verdicts = _sorted_verdicts(verdicts)
    assertions_list: list[dict] = []
    for v in sorted_verdicts:
        agg = v.aggregate
        assertions_list.append(
            {
                "harness": v.harness,
                "provider": v.provider,
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

    # #154 DEC-005: always emit ``iteration_contexts`` (list of
    # ``{iteration, context}`` records). Legacy iterations and missing
    # data emit ``context: null``.
    contexts_list: list[dict] = []
    if iteration_contexts:
        for iteration_num in sorted(iteration_contexts.keys(), reverse=True):
            ctx = iteration_contexts[iteration_num]
            if ctx is None:
                payload: dict | None = None
            else:
                payload = {
                    "harness": ctx.harness,
                    "provider": ctx.provider,
                    "model_runner": ctx.model_runner,
                    "model_grader": ctx.model_grader,
                    "system_prompt_source": ctx.system_prompt_source,
                    "sandbox_mode": ctx.sandbox_mode,
                    "reasoning_tokens": ctx.reasoning_tokens,
                    "cost_usd": ctx.cost_usd,
                }
            contexts_list.append(
                {"iteration": iteration_num, "context": payload}
            )

    return {
        "schema_version": 3,
        "skill": skill,
        "timestamp": timestamp,
        "iterations": iterations_analyzed,
        "thresholds": dict(thresholds),
        "providers_seen": _providers_seen(verdicts),
        "harnesses_seen": _harnesses_seen(verdicts),
        "assertions": assertions_list,
        "iteration_contexts": contexts_list,
    }
