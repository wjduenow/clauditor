"""Pure compute core for ``clauditor badge`` — shields.io endpoint JSON.

Aggregates per-iteration L1 assertions, L3 grading, and (optional)
variance sidecars into a shields.io endpoint-schema JSON payload plus
a nested ``clauditor`` extension block carrying full state. The CLI
layer (:mod:`clauditor.cli.badge`) owns all I/O — sidecar reads,
output writes, stderr warnings, exit-code mapping, and git subprocess
calls; this module is pure per ``.claude/rules/pure-compute-vs-io-split.md``.

Decisions traced (see ``plans/super/77-clauditor-badge.md``):

- **DEC-003** — ``clauditor.layers.variance`` block is optional;
  omitted entirely when ``variance=None`` (the always-absent steady
  state today).
- **DEC-009** — L3 all parse-failed (empty ``results`` OR no result
  carries a numeric score) renders the badge ``red`` with the L3
  fragment omitted from the message.
- **DEC-010** — Both L1 and L3 layer blocks carry a ``passed: bool``
  field; they mean different things. L1 ``passed`` = "every
  assertion passed". L3 ``passed`` = "pass rate ≥ min_pass_rate AND
  mean score ≥ min_mean_score" (i.e., the grade met its thresholds).
- **DEC-012** — ``generated_at`` uses the ``Z`` suffix form
  (``2026-04-21T14:00:00Z``) rather than ``+00:00``. The CLI layer
  constructs the string via
  ``datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")``
  and passes it in; this module does no timestamp formatting.
- **DEC-013** — Nested dataclasses mirror the ``Benchmark`` idiom:
  :class:`L1Summary`, :class:`L3Summary`, :class:`VarianceSummary`,
  :class:`ClauditorExtension`, :class:`Badge`. Raw-dict passthrough
  only for the ``thresholds`` block copied verbatim from
  ``grading.json``.
- **DEC-020** — Zero L1 assertions (``assertions=None`` OR a
  sidecar dict with ``runs=[]`` / all-empty ``results``) renders
  ``color=lightgrey`` + ``message="no data"``. Applies uniformly to
  DEC-001 (no iteration) and DEC-007 (spec declares zero L1
  assertions).
- **DEC-024** — Message format:
    * L1 only → ``"{N}/{M}"`` (e.g. ``"8/8"``).
    * L1 + L3 → ``"{N}/{M} · L3 {round(pr*100)}%"``.
    * L1 + L3 + variance → ``"{N}/{M} · L3 {pr}% · {stab}% stable"``.
    * Zero-L1 (lightgrey) → ``"no data"``.
- **DEC-026** — Pure compute vs. I/O split; this module takes
  pre-parsed dicts and returns a :class:`Badge` dataclass ready to
  serialize.
- **DEC-027** — Two independent schema-version fields on the JSON
  payload:
    * Top-level ``schemaVersion: 1`` — shields.io's contract
      (camelCase per their docs), first top-level key of the endpoint
      JSON.
    * Nested ``clauditor.schema_version: 1`` — our extension
      contract, first key of the ``clauditor`` block per
      ``.claude/rules/json-schema-version.md``.
  The two versions bump independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Badge",
    "ClauditorExtension",
    "L1Summary",
    "L3Summary",
    "VarianceSummary",
    "compute_badge",
]


# ---------------------------------------------------------------------------
# Schema versions (see DEC-027).
# ---------------------------------------------------------------------------

# shields.io's endpoint-JSON schema version (their contract, camelCase
# key at the top of the emitted dict).
_SHIELDS_SCHEMA_VERSION: int = 1

# The ``clauditor`` extension block's own version — first key of the
# block per ``.claude/rules/json-schema-version.md``.
_CLAUDITOR_EXTENSION_SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Color constants (see DEC-020 / the ticket's color table).
#
# Kept as module-level strings rather than a dict/enum: downstream
# consumers (tests, audit readers) grep for these exact strings, and
# the set is small + stable.
# ---------------------------------------------------------------------------

_COLOR_BRIGHT_GREEN: str = "brightgreen"
_COLOR_YELLOW: str = "yellow"
_COLOR_RED: str = "red"
_COLOR_LIGHTGREY: str = "lightgrey"

# Message fragment for the no-data case (DEC-020).
_NO_DATA_MESSAGE: str = "no data"


# ---------------------------------------------------------------------------
# Nested dataclasses (see DEC-013).
# ---------------------------------------------------------------------------


@dataclass
class L1Summary:
    """Layer 1 (assertion) summary for the badge.

    ``passed`` semantic (DEC-010): ``all(r.passed for r in results)``
    — i.e., every declared assertion passed in the iteration being
    reported. Contrast with :attr:`L3Summary.passed`, which means
    "the grade met its thresholds".
    """

    count: int
    total: int
    pass_rate: float
    passed: bool


@dataclass
class L3Summary:
    """Layer 3 (quality grading) summary for the badge.

    ``passed`` semantic (DEC-010): ``pass_rate ≥ min_pass_rate AND
    mean_score ≥ min_mean_score`` — the threshold-gated "this grade
    is good enough" signal. Contrast with :attr:`L1Summary.passed`,
    which means "every assertion passed".

    ``thresholds`` is the raw passthrough dict from ``grading.json``'s
    own ``thresholds`` block (DEC-004 — the badge shows what the
    grade already decided, no re-interpretation).
    """

    pass_rate: float
    mean_score: float
    passed: bool
    thresholds: dict[str, Any]


@dataclass
class VarianceSummary:
    """Variance sidecar summary for the badge.

    ``passed`` semantic: ``stability ≥ min_stability`` (the variance
    writer, when it exists, sets this field; the badge consumes it
    verbatim). ``n_runs`` and ``stability`` are copied from the
    sidecar's own fields.
    """

    n_runs: int
    stability: float
    passed: bool


@dataclass
class ClauditorExtension:
    """The nested ``clauditor`` block on the badge JSON.

    ``schema_version`` is the first field and is emitted as the first
    key of the serialized block per
    ``.claude/rules/json-schema-version.md``. ``layers`` is built on
    the fly at serialization time from the (optional) summary fields
    — omit any layer whose summary is ``None``.
    """

    skill_name: str
    generated_at: str
    iteration: int | None
    l1: L1Summary | None = None
    l3: L3Summary | None = None
    variance: VarianceSummary | None = None
    schema_version: int = _CLAUDITOR_EXTENSION_SCHEMA_VERSION


@dataclass
class Badge:
    """Serializable shields.io endpoint-JSON payload.

    Top-level fields match the shields.io endpoint schema:
    ``schemaVersion``, ``label``, ``message``, ``color``. Any
    ``style_overrides`` land alphabetically between ``color`` and the
    ``clauditor`` extension block per the DEC-015 passthrough rule.
    """

    label: str
    message: str
    color: str
    clauditor: ClauditorExtension
    style_overrides: dict[str, str] = field(default_factory=dict)
    schema_version: int = _SHIELDS_SCHEMA_VERSION

    def to_endpoint_json(self) -> dict[str, Any]:
        """Return the shields.io-compatible dict with canonical key order.

        Top-level keys in order: ``schemaVersion``, ``label``,
        ``message``, ``color``, then ``style_overrides`` sorted
        alphabetically, then ``clauditor``. Inside ``clauditor``,
        first key is ``schema_version`` (per
        ``.claude/rules/json-schema-version.md``), followed by
        ``skill_name``, ``generated_at``, ``iteration``, ``layers``.

        Python 3.7+ preserves dict insertion order, so building the
        dict literal-by-literal in the desired order is the entire
        mechanism.
        """
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "label": self.label,
            "message": self.message,
            "color": self.color,
        }
        for key in sorted(self.style_overrides):
            payload[key] = self.style_overrides[key]
        payload["clauditor"] = _extension_to_dict(self.clauditor)
        return payload


def _extension_to_dict(ext: ClauditorExtension) -> dict[str, Any]:
    """Serialize the ``clauditor`` block with ``schema_version`` first.

    Layers are omitted entirely when their summary is ``None``
    (DEC-003 for variance; DEC-020 for L1-when-no-data; absent L3
    when grading sidecar is missing).
    """
    block: dict[str, Any] = {
        "schema_version": ext.schema_version,
        "skill_name": ext.skill_name,
        "generated_at": ext.generated_at,
        "iteration": ext.iteration,
    }
    layers: dict[str, Any] = {}
    if ext.l1 is not None:
        layers["l1"] = {
            "count": ext.l1.count,
            "total": ext.l1.total,
            "pass_rate": ext.l1.pass_rate,
            "passed": ext.l1.passed,
        }
    if ext.l3 is not None:
        layers["l3"] = {
            "pass_rate": ext.l3.pass_rate,
            "mean_score": ext.l3.mean_score,
            "passed": ext.l3.passed,
            "thresholds": ext.l3.thresholds,
        }
    if ext.variance is not None:
        layers["variance"] = {
            "n_runs": ext.variance.n_runs,
            "stability": ext.variance.stability,
            "passed": ext.variance.passed,
        }
    block["layers"] = layers
    return block


# ---------------------------------------------------------------------------
# Pure compute: L1 / L3 / variance sidecar classification.
# ---------------------------------------------------------------------------


def _summarize_l1(assertions: dict | None) -> L1Summary | None:
    """Collapse an ``assertions.json`` payload into an :class:`L1Summary`.

    ``assertions=None`` represents DEC-001 (no iteration at all) /
    DEC-008 (caller signaled the no-L1-signal case). Returns ``None``
    to trigger the DEC-020 lightgrey "no data" path.

    A sidecar dict with no results (``runs=[]`` or every run carrying
    an empty ``results`` list) also returns ``None`` — DEC-007's
    "iteration exists but spec declares zero L1 assertions" path.

    Two sidecar layouts are accepted:

    * Modern (from ``cli/grade.py::_write_assertions_sidecar``):
      top-level ``runs: [{"run": 0, "input_tokens": ..., "results":
      [...]}, ...]`` — results are flattened across runs.
    * Flat (from older ``AssertionSet.to_json`` or tests):
      top-level ``results: [...]`` directly.

    Both cases sum ``count`` = ``total`` across all results and set
    ``passed = (count == total)``. A mixed-run sidecar with 8/8
    in run-0 and 7/8 in run-1 collapses to 15/16.
    """
    if assertions is None:
        return None

    results = _collect_assertion_results(assertions)
    if not results:
        return None

    total = len(results)
    count = sum(1 for r in results if _result_passed(r))
    pass_rate = count / total if total > 0 else 0.0
    return L1Summary(
        count=count,
        total=total,
        pass_rate=pass_rate,
        passed=count == total,
    )


def _collect_assertion_results(assertions: dict) -> list[dict]:
    """Extract the flat list of per-assertion result dicts.

    Handles both the ``runs`` (modern) and ``results`` (flat) layouts.
    Tolerates missing / non-list fields by returning ``[]`` — the
    caller treats that as the no-L1-signal case (DEC-007).
    """
    runs = assertions.get("runs")
    if isinstance(runs, list):
        collected: list[dict] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_results = run.get("results")
            if isinstance(run_results, list):
                collected.extend(r for r in run_results if isinstance(r, dict))
        return collected

    # Flat layout.
    flat = assertions.get("results")
    if isinstance(flat, list):
        return [r for r in flat if isinstance(r, dict)]
    return []


def _result_passed(result: dict) -> bool:
    """Strict-``True`` check on an assertion result's ``passed`` field.

    Missing / non-bool / truthy-but-non-bool values count as failed.
    The L1 sidecar is clauditor-owned, so the strict check is
    appropriate — a malformed entry is a corruption signal.
    """
    return result.get("passed") is True


def _summarize_l3(grading: dict | None) -> tuple[L3Summary | None, bool]:
    """Collapse a ``grading.json`` payload into an :class:`L3Summary`.

    Returns ``(summary, parse_failed)`` where:

    * ``summary is None and parse_failed is False`` — grading sidecar
      absent (caller passed ``grading=None``). Caller omits the L3
      block from the badge entirely.
    * ``summary is None and parse_failed is True`` — grading ran but
      no result carries a numeric score, OR the sidecar's
      ``results`` list is empty. DEC-009: this is a red badge; L3
      fragment is omitted from the message.
    * ``summary is not None`` — happy path. ``summary.passed``
      reflects the thresholds-based calculation against the sidecar's
      own ``thresholds`` block (DEC-004).
    """
    if grading is None:
        return None, False

    results = grading.get("results")
    if not isinstance(results, list) or len(results) == 0:
        return None, True

    # "No result carries a score" is the parse-failed signal — a
    # graded-but-all-failed run with real scores is just a failing
    # grade, not a parse failure. The strict ``isinstance(score,
    # (int, float))`` check tolerates integer and float scores while
    # rejecting None / string.
    scored = [
        r
        for r in results
        if isinstance(r, dict) and isinstance(r.get("score"), (int, float))
    ]
    if not scored:
        return None, True

    pass_rate_val = _compute_grading_pass_rate(results)
    mean_score_val = _compute_grading_mean_score(scored)

    thresholds_block = grading.get("thresholds")
    if not isinstance(thresholds_block, dict):
        thresholds_block = {}

    min_pass_rate = _coerce_float(thresholds_block.get("min_pass_rate"), 0.7)
    min_mean_score = _coerce_float(thresholds_block.get("min_mean_score"), 0.5)
    passed = pass_rate_val >= min_pass_rate and mean_score_val >= min_mean_score

    return (
        L3Summary(
            pass_rate=pass_rate_val,
            mean_score=mean_score_val,
            passed=passed,
            thresholds=dict(thresholds_block),
        ),
        False,
    )


def _compute_grading_pass_rate(results: list[Any]) -> float:
    """Fraction of grading results where ``passed is True``."""
    valid = [r for r in results if isinstance(r, dict)]
    if not valid:
        return 0.0
    return sum(1 for r in valid if r.get("passed") is True) / len(valid)


def _compute_grading_mean_score(scored: list[dict]) -> float:
    """Mean of the numeric ``score`` fields across scored results."""
    if not scored:
        return 0.0
    return sum(float(r["score"]) for r in scored) / len(scored)


def _coerce_float(value: Any, default: float) -> float:
    """Tolerant float coercion — returns ``default`` for non-numeric inputs."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _summarize_variance(variance: dict | None) -> VarianceSummary | None:
    """Collapse a ``variance.json`` payload into a :class:`VarianceSummary`.

    DEC-003: returns ``None`` when ``variance is None`` so the caller
    omits the variance block entirely. The variance sidecar format
    is documented (no writer ships today) to carry:

    * ``n_runs: int`` — number of replicate runs.
    * ``stability: float`` — 0.0–1.0 stability score.
    * ``passed: bool`` — ``stability ≥ min_stability``.

    All three fields are optional on read; missing-or-wrong-type
    falls back to ``0``/``0.0``/``False`` rather than raising — the
    badge degrades gracefully on malformed variance data rather than
    failing the whole command.
    """
    if variance is None:
        return None

    n_runs_raw = variance.get("n_runs")
    n_runs = n_runs_raw if isinstance(n_runs_raw, int) and not isinstance(
        n_runs_raw, bool
    ) else 0

    stability = _coerce_float(variance.get("stability"), 0.0)
    passed = variance.get("passed") is True
    return VarianceSummary(n_runs=n_runs, stability=stability, passed=passed)


# ---------------------------------------------------------------------------
# Color + message classification (see DEC-009, DEC-020, DEC-024).
# ---------------------------------------------------------------------------


def _compute_color(
    l1: L1Summary | None,
    l3: L3Summary | None,
    l3_parse_failed: bool,
) -> str:
    """Decide the badge color.

    Precedence (most-specific-first):

    1. No L1 signal → ``lightgrey`` (DEC-020 covers DEC-001 and
       DEC-007).
    2. Any L1 assertion failed → ``red``.
    3. L1 all-pass + L3 parse-failed → ``red`` (DEC-009).
    4. L1 all-pass + L3 present but not passed → ``yellow``.
    5. L1 all-pass + L3 passed OR L3 omitted → ``brightgreen``.
    """
    if l1 is None:
        return _COLOR_LIGHTGREY
    if not l1.passed:
        return _COLOR_RED
    if l3_parse_failed:
        return _COLOR_RED
    if l3 is not None and not l3.passed:
        return _COLOR_YELLOW
    return _COLOR_BRIGHT_GREEN


def _compute_message(
    l1: L1Summary | None,
    l3: L3Summary | None,
    variance: VarianceSummary | None,
) -> str:
    """Render the shields.io ``message`` field per DEC-024.

    Delegates the L3 decision to the caller's classification: when
    ``l3 is None`` (either absent or parse-failed), the L3 fragment
    is omitted.
    """
    if l1 is None:
        return _NO_DATA_MESSAGE

    base = f"{l1.count}/{l1.total}"
    if l3 is None:
        return base

    l3_pct = round(l3.pass_rate * 100)
    with_l3 = f"{base} · L3 {l3_pct}%"
    if variance is None:
        return with_l3

    stab_pct = round(variance.stability * 100)
    return f"{with_l3} · {stab_pct}% stable"


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def compute_badge(
    assertions: dict | None,
    grading: dict | None,
    variance: dict | None,
    *,
    skill_name: str,
    iteration: int | None,
    generated_at: str,
    label: str = "clauditor",
    style_overrides: dict[str, str] | None = None,
) -> Badge:
    """Aggregate per-iteration sidecars into a :class:`Badge`.

    All three sidecar args are optional:

    * ``assertions=None`` represents the DEC-001 / DEC-008 no-L1-
      signal case. An ``assertions`` dict whose collected results
      are empty (DEC-007 — iteration exists but spec declares zero
      L1 assertions) is treated identically: lightgrey badge,
      ``"no data"`` message, no ``layers.l1`` block.
    * ``grading=None`` omits L3 entirely. A grading dict whose
      ``results`` list is empty OR whose results carry no numeric
      ``score`` triggers DEC-009 (L3 parse-failed → red, L3 fragment
      omitted from the message, and ``layers.l3`` still omitted).
    * ``variance=None`` (DEC-003's always-absent steady state) omits
      the variance block entirely.

    ``generated_at`` should use the ISO-8601 ``Z``-suffix form
    (DEC-012); the caller is expected to post-process
    ``datetime.now(timezone.utc).isoformat()`` with
    ``.replace("+00:00", "Z")``. This function performs no timestamp
    formatting — it is pure compute over pre-resolved inputs.

    ``iteration`` may be ``None`` when no iteration has been discovered
    (the DEC-001 placeholder path); the value is passed through
    verbatim to the JSON payload.

    ``style_overrides`` is a dict of shields.io ``--style`` passthrough
    keys (DEC-015); alphabetically serialized between the top-level
    ``color`` and ``clauditor`` keys.
    """
    l1 = _summarize_l1(assertions)
    l3, l3_parse_failed = _summarize_l3(grading)
    var = _summarize_variance(variance)

    color = _compute_color(l1, l3, l3_parse_failed)
    message = _compute_message(l1, l3, var)

    return Badge(
        label=label,
        message=message,
        color=color,
        clauditor=ClauditorExtension(
            skill_name=skill_name,
            generated_at=generated_at,
            iteration=iteration,
            l1=l1,
            l3=l3,
            variance=var,
        ),
        style_overrides=dict(style_overrides) if style_overrides else {},
    )
