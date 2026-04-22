"""Tests for ``clauditor.badge`` — pure compute for the shields.io endpoint.

Covers the TDD cases from US-001 of
``plans/super/77-clauditor-badge.md``. Traces: DEC-003, DEC-009,
DEC-010, DEC-012, DEC-013, DEC-020, DEC-024, DEC-026, DEC-027.

All tests here are pure — no ``tmp_path``, no subprocess mocks, no
async. Fixture factories produce dicts shaped like the real sidecars
(``AssertionSet.to_json()``, ``GradingReport.to_json()``) per
DEC-019's "generate-in-test via to_json" policy.
"""

from __future__ import annotations

import json

import pytest

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.badge import (
    Badge,
    ClauditorExtension,
    L1Summary,
    L3Summary,
    VarianceSummary,
    compute_badge,
)
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.schemas import GradeThresholds

# ---------------------------------------------------------------------------
# Fixture factories — produce real sidecar shapes.
# ---------------------------------------------------------------------------


def _make_assertions_dict(
    *,
    passed: int = 8,
    total: int = 8,
    runs_layout: bool = True,
) -> dict:
    """Build an ``assertions.json``-shaped dict.

    ``runs_layout=True`` produces the modern two-level
    (``{runs: [{results: [...]}, ...]}``) shape written by
    ``cli/grade.py::_write_assertions_sidecar``. ``runs_layout=False``
    produces the flat ``AssertionSet.to_json()`` shape used by older
    callers and tests.
    """
    results = [
        AssertionResult(
            name=f"check_{i}",
            passed=i < passed,
            message="ok" if i < passed else "fail",
            kind="presence",
        )
        for i in range(total)
    ]
    aset = AssertionSet(results=results, input_tokens=100, output_tokens=50)
    if not runs_layout:
        return aset.to_json()
    return {
        "schema_version": 1,
        "skill": "demo",
        "iteration": 1,
        "runs": [{"run": 0, **aset.to_json()}],
    }


def _make_grading_dict(
    *,
    pass_fractions: tuple[bool, ...] = (True, True, True),
    scores: tuple[float, ...] | None = None,
    thresholds: GradeThresholds | None = None,
    empty_results: bool = False,
    no_scores: bool = False,
) -> dict:
    """Build a ``grading.json``-shaped dict.

    ``empty_results=True`` produces a DEC-009 parse-failed shape
    (``results: []``). ``no_scores=True`` produces results lacking
    numeric ``score`` values (also DEC-009 parse-failed).
    """
    if thresholds is None:
        thresholds = GradeThresholds()
    if empty_results:
        results = []
    else:
        if scores is None:
            scores = tuple(1.0 if p else 0.0 for p in pass_fractions)
        results = [
            GradingResult(
                criterion=f"c{i}",
                passed=p,
                score=s,
                evidence="",
                reasoning="",
                id=f"c{i}",
            )
            for i, (p, s) in enumerate(zip(pass_fractions, scores, strict=False))
        ]
    report = GradingReport(
        skill_name="demo",
        results=results,
        model="test-model",
        thresholds=thresholds,
        metrics={},
        duration_seconds=1.0,
        input_tokens=200,
        output_tokens=100,
    )
    parsed = json.loads(report.to_json())
    if no_scores and not empty_results:
        # Strip score fields to simulate a judge that returned no
        # scorable data (DEC-009).
        for r in parsed["results"]:
            r.pop("score", None)
    return parsed


def _make_variance_dict(
    *,
    n_runs: int = 5,
    stability: float = 0.85,
    passed: bool = True,
) -> dict:
    """Build the expected ``variance.json`` shape.

    No writer ships for this sidecar today (DEC-003), so this factory
    encodes the expected fields by spec alone. When a writer lands,
    this factory may be replaced with a real ``VarianceReport.to_json``.
    """
    return {
        "schema_version": 1,
        "n_runs": n_runs,
        "stability": stability,
        "passed": passed,
    }


_GEN_AT = "2026-04-21T14:00:00Z"


# ---------------------------------------------------------------------------
# Color + message matrix (DEC-009, DEC-020, DEC-024).
# ---------------------------------------------------------------------------


class TestComputeBadge:
    """Parametrized color + message matrix covering each DEC row."""

    def test_assertions_none_is_lightgrey_no_data(self):
        """DEC-001 / DEC-008: assertions=None → lightgrey + ``no data``."""
        badge = compute_badge(
            None,
            None,
            None,
            skill_name="demo",
            iteration=None,
            generated_at=_GEN_AT,
        )
        assert badge.color == "lightgrey"
        assert badge.message == "no data"

    def test_empty_assertions_is_lightgrey(self):
        """DEC-007: iteration with zero L1 assertions → lightgrey."""
        # Flat layout with empty results.
        empty = {"input_tokens": 0, "output_tokens": 0, "results": []}
        badge = compute_badge(
            empty,
            None,
            None,
            skill_name="demo",
            iteration=3,
            generated_at=_GEN_AT,
        )
        assert badge.color == "lightgrey"
        assert badge.message == "no data"

    def test_runs_layout_with_empty_results_is_lightgrey(self):
        """DEC-007: modern ``runs`` layout where every run has no results."""
        payload = {
            "schema_version": 1,
            "skill": "demo",
            "iteration": 2,
            "runs": [{"run": 0, "input_tokens": 0, "output_tokens": 0, "results": []}],
        }
        badge = compute_badge(
            payload, None, None, skill_name="demo", iteration=2, generated_at=_GEN_AT
        )
        assert badge.color == "lightgrey"
        assert badge.message == "no data"

    def test_l1_any_failed_is_red(self):
        """Any L1 failure → red badge, ``N/M`` message."""
        assertions = _make_assertions_dict(passed=7, total=8)
        badge = compute_badge(
            assertions,
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "red"
        assert badge.message == "7/8"

    def test_l1_all_pass_l3_omitted_is_brightgreen(self):
        """L1 all-pass + no L3 sidecar → brightgreen, ``N/M`` message."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "brightgreen"
        assert badge.message == "8/8"

    def test_l1_all_pass_l3_passed_is_brightgreen(self):
        """L1 pass + L3 above thresholds → brightgreen, ``N/M · L3 P%``."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True, True)),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "brightgreen"
        assert badge.message == "8/8 · L3 100%"

    def test_l1_all_pass_l3_below_thresholds_is_yellow(self):
        """L1 pass + L3 below thresholds → yellow, ``N/M · L3 P%``."""
        # 2/4 passed → pass_rate 0.5 < default 0.7 threshold.
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, False, False)),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "yellow"
        assert badge.message == "8/8 · L3 50%"

    def test_l1_pass_l3_parse_failed_empty_results_is_red(self):
        """DEC-009: L3 with empty results → red, L3 fragment omitted."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(empty_results=True),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "red"
        assert badge.message == "8/8"

    def test_l1_pass_l3_parse_failed_no_scores_is_red(self):
        """DEC-009: L3 where no result has a score → red, L3 omitted."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(
                pass_fractions=(True, True, True), no_scores=True
            ),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "red"
        assert badge.message == "8/8"

    def test_l1_pass_l3_pass_variance_present_full_message(self):
        """DEC-024: L1 + L3 + variance → full three-fragment message."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True, True)),
            _make_variance_dict(n_runs=5, stability=0.80, passed=True),
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "brightgreen"
        assert badge.message == "8/8 · L3 100% · 80% stable"

    def test_l1_fail_takes_priority_over_l3_parse_failed(self):
        """L1 failure short-circuits the L3 parse-failed branch."""
        badge = compute_badge(
            _make_assertions_dict(passed=7, total=8),
            _make_grading_dict(empty_results=True),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "red"
        # L3 parse-failed → L3 fragment is also omitted from the message.
        assert badge.message == "7/8"

    def test_flat_assertions_layout_works(self):
        """Flat ``AssertionSet.to_json`` layout (no ``runs`` wrapper) parses."""
        flat = _make_assertions_dict(passed=5, total=5, runs_layout=False)
        badge = compute_badge(
            flat,
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "brightgreen"
        assert badge.message == "5/5"


# ---------------------------------------------------------------------------
# Layer-specific semantics (DEC-010 + thresholds passthrough).
# ---------------------------------------------------------------------------


class TestLayerSemantics:
    def test_l1_passed_means_all_passed(self):
        """DEC-010: L1 ``passed`` mirrors ``all(r.passed)``."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.l1 is not None
        assert badge.clauditor.l1.passed is True

        badge2 = compute_badge(
            _make_assertions_dict(passed=7, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge2.clauditor.l1 is not None
        assert badge2.clauditor.l1.passed is False

    def test_l3_passed_means_met_thresholds(self):
        """DEC-010: L3 ``passed`` mirrors the thresholds-based calc."""
        # 4/4 passed with default thresholds → passed.
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True, True)),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.l3 is not None
        assert badge.clauditor.l3.passed is True

        # 2/4 passed (pass_rate 0.5 < 0.7) → not passed.
        badge2 = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, False, False)),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge2.clauditor.l3 is not None
        assert badge2.clauditor.l3.passed is False

    def test_l3_thresholds_passthrough(self):
        """``grading.json`` thresholds are copied verbatim (DEC-004)."""
        custom_thresholds = GradeThresholds(min_pass_rate=0.9, min_mean_score=0.8)
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(
                pass_fractions=(True, True, True, True),
                thresholds=custom_thresholds,
            ),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.l3 is not None
        assert badge.clauditor.l3.thresholds == {
            "min_pass_rate": 0.9,
            "min_mean_score": 0.8,
        }

    def test_l3_thresholds_block_missing_uses_defaults(self):
        """When grading dict has no ``thresholds`` key, default to 0.7/0.5."""
        grading = _make_grading_dict(pass_fractions=(True, True, True))
        grading.pop("thresholds", None)
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            grading,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        # 3/3 with default thresholds → passed.
        assert badge.clauditor.l3 is not None
        assert badge.clauditor.l3.passed is True
        # Empty thresholds dict passed through (no spurious defaults
        # injected into the serialized payload).
        assert badge.clauditor.l3.thresholds == {}

    def test_variance_summary_fields_from_sidecar(self):
        """Variance fields flow through verbatim."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True)),
            _make_variance_dict(n_runs=5, stability=0.85, passed=True),
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.variance == VarianceSummary(
            n_runs=5, stability=0.85, passed=True
        )

    def test_variance_malformed_degrades_gracefully(self):
        """Malformed variance sidecar → zero-filled summary, not an error."""
        bad_variance = {"schema_version": 1}  # no n_runs, stability, passed
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True)),
            bad_variance,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.variance == VarianceSummary(
            n_runs=0, stability=0.0, passed=False
        )

    def test_pass_rate_rounding_in_message(self):
        """DEC-024: L3 percent uses ``round(pr * 100)`` not floor."""
        # 2/3 → 66.666… → rounds to 67.
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, False)),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert "L3 67%" in badge.message


# ---------------------------------------------------------------------------
# Serialization invariants (DEC-012, DEC-013, DEC-027).
# ---------------------------------------------------------------------------


class TestBadgeSerialization:
    def test_top_level_key_order(self):
        """Endpoint JSON top-level key order is fixed."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        keys = list(result.keys())
        assert keys[0] == "schemaVersion"
        assert keys[1] == "label"
        assert keys[2] == "message"
        assert keys[3] == "color"
        assert keys[-1] == "clauditor"

    def test_shields_schema_version_is_camelcase(self):
        """DEC-027: shields.io's field is camelCase ``schemaVersion``."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        assert result["schemaVersion"] == 1
        # The snake_case form is reserved for the nested block.
        assert "schema_version" not in result

    def test_clauditor_schema_version_is_first_key(self):
        """DEC-027: nested block obeys json-schema-version first-key rule."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        inner_keys = list(result["clauditor"].keys())
        assert inner_keys[0] == "schema_version"
        assert result["clauditor"]["schema_version"] == 1

    def test_clauditor_key_order(self):
        """Inside ``clauditor``: schema_version, skill_name, generated_at,
        iteration, layers."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        keys = list(result["clauditor"].keys())
        assert keys == ["schema_version", "skill_name", "generated_at",
                        "iteration", "layers"]

    def test_generated_at_z_suffix(self):
        """DEC-012: generated_at carries the trailing ``Z``."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        assert result["clauditor"]["generated_at"].endswith("Z")

    def test_variance_omitted_when_absent(self):
        """DEC-003: no variance sidecar → no ``layers.variance`` key."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True)),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        assert "variance" not in result["clauditor"]["layers"]

    def test_l3_omitted_when_grading_absent(self):
        """No grading sidecar → no ``layers.l3`` key."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        assert "l3" not in result["clauditor"]["layers"]
        assert "l1" in result["clauditor"]["layers"]

    def test_l3_omitted_when_parse_failed(self):
        """DEC-009: L3 parse-failed → ``layers.l3`` omitted AND color red."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(empty_results=True),
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        assert "l3" not in result["clauditor"]["layers"]
        assert result["color"] == "red"

    def test_l1_omitted_when_no_l1_signal(self):
        """DEC-020: assertions=None → ``layers.l1`` omitted entirely."""
        badge = compute_badge(
            None,
            None,
            None,
            skill_name="demo",
            iteration=None,
            generated_at=_GEN_AT,
        )
        result = badge.to_endpoint_json()
        assert "l1" not in result["clauditor"]["layers"]
        # And iteration is None (placeholder case).
        assert result["clauditor"]["iteration"] is None

    def test_style_overrides_alphabetized(self):
        """DEC-015: ``--style`` passthroughs land alphabetically."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
            style_overrides={
                "logoSvg": "<svg/>",
                "cacheSeconds": "3600",
                "style": "flat",
            },
        )
        result = badge.to_endpoint_json()
        keys = list(result.keys())
        # After color, before clauditor, in alphabetical order.
        color_idx = keys.index("color")
        clauditor_idx = keys.index("clauditor")
        style_slice = keys[color_idx + 1 : clauditor_idx]
        assert style_slice == ["cacheSeconds", "logoSvg", "style"]
        assert result["style"] == "flat"
        assert result["cacheSeconds"] == "3600"
        assert result["logoSvg"] == "<svg/>"

    def test_default_label_is_clauditor(self):
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.label == "clauditor"
        assert badge.to_endpoint_json()["label"] == "clauditor"

    def test_custom_label(self):
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
            label="review-pr",
        )
        assert badge.label == "review-pr"

    def test_payload_is_json_serializable(self):
        """The returned dict must round-trip through ``json``."""
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True, True)),
            _make_variance_dict(),
            skill_name="demo",
            iteration=7,
            generated_at=_GEN_AT,
            style_overrides={"style": "flat"},
        )
        raw = json.dumps(badge.to_endpoint_json())
        round_trip = json.loads(raw)
        assert round_trip["color"] == "brightgreen"
        assert round_trip["clauditor"]["skill_name"] == "demo"
        assert round_trip["clauditor"]["iteration"] == 7


# ---------------------------------------------------------------------------
# Dataclass construction smoke tests — guard against field drift.
# ---------------------------------------------------------------------------


class TestDataclassShapes:
    def test_l1_summary_fields(self):
        s = L1Summary(count=7, total=8, pass_rate=0.875, passed=False)
        assert s.count == 7
        assert s.total == 8
        assert s.pass_rate == pytest.approx(0.875)
        assert s.passed is False

    def test_l3_summary_fields(self):
        s = L3Summary(
            pass_rate=1.0,
            mean_score=0.9,
            passed=True,
            thresholds={"min_pass_rate": 0.7, "min_mean_score": 0.5},
        )
        assert s.passed is True
        assert s.thresholds["min_pass_rate"] == 0.7

    def test_variance_summary_fields(self):
        s = VarianceSummary(n_runs=5, stability=0.9, passed=True)
        assert s.n_runs == 5
        assert s.stability == pytest.approx(0.9)
        assert s.passed is True

    def test_clauditor_extension_defaults(self):
        ext = ClauditorExtension(
            skill_name="demo",
            generated_at=_GEN_AT,
            iteration=1,
        )
        assert ext.schema_version == 1
        assert ext.l1 is None
        assert ext.l3 is None
        assert ext.variance is None

    def test_badge_defaults(self):
        ext = ClauditorExtension(skill_name="demo", generated_at=_GEN_AT, iteration=1)
        b = Badge(label="demo", message="8/8", color="brightgreen", clauditor=ext)
        assert b.schema_version == 1
        assert b.style_overrides == {}


# ---------------------------------------------------------------------------
# Defensive edge cases — malformed-but-survivable sidecar shapes.
# ---------------------------------------------------------------------------


class TestDefensiveBranches:
    """Cover malformed-but-recoverable input paths in the pure helpers."""

    def test_runs_with_non_dict_entries_skipped(self):
        """Non-dict entries in ``runs`` are skipped without error."""
        payload = {
            "schema_version": 1,
            "runs": [
                "not-a-dict",
                {"run": 0, "results": [{"name": "x", "passed": True}]},
                None,
            ],
        }
        badge = compute_badge(
            payload, None, None, skill_name="demo", iteration=1, generated_at=_GEN_AT
        )
        assert badge.clauditor.l1 == L1Summary(
            count=1, total=1, pass_rate=1.0, passed=True
        )

    def test_results_with_non_dict_entries_skipped(self):
        """Non-dict entries in a flat ``results`` list are skipped."""
        payload = {
            "results": [
                "bogus",
                {"name": "x", "passed": True},
                42,
            ],
        }
        badge = compute_badge(
            payload, None, None, skill_name="demo", iteration=1, generated_at=_GEN_AT
        )
        assert badge.clauditor.l1 == L1Summary(
            count=1, total=1, pass_rate=1.0, passed=True
        )

    def test_neither_runs_nor_results_key(self):
        """Payload with neither ``runs`` nor ``results`` → lightgrey."""
        badge = compute_badge(
            {"schema_version": 1},  # pathological
            None,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "lightgrey"
        assert badge.message == "no data"

    def test_l3_results_key_is_not_a_list(self):
        """Grading dict with ``results`` not a list → parse-failed."""
        payload = {"results": "corrupted"}
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            payload,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.color == "red"
        assert "l3" not in badge.to_endpoint_json()["clauditor"]["layers"]

    def test_l3_thresholds_non_dict_falls_back_to_defaults(self):
        """Grading dict with ``thresholds`` not a dict → defaults applied."""
        grading = _make_grading_dict(pass_fractions=(True, True, True))
        grading["thresholds"] = "garbage"
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            grading,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.l3 is not None
        assert badge.clauditor.l3.passed is True
        assert badge.clauditor.l3.thresholds == {}

    def test_l3_threshold_values_non_numeric_fall_back_to_defaults(self):
        """Non-numeric threshold values (e.g. strings) fall back to defaults."""
        grading = _make_grading_dict(pass_fractions=(True, True, True))
        # 3/3 passed, mean 1.0 — passes default 0.7/0.5, fails only if
        # thresholds push above 1.0. Replace with strings to exercise
        # the coerce_float fallback.
        grading["thresholds"] = {"min_pass_rate": "nope", "min_mean_score": None}
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            grading,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        # Fallback thresholds are 0.7 / 0.5; 3/3 all-pass passes both.
        assert badge.clauditor.l3 is not None
        assert badge.clauditor.l3.passed is True

    def test_l3_threshold_bool_treated_as_non_numeric(self):
        """Bool is an int subclass — ``_coerce_float`` rejects bool explicitly."""
        grading = _make_grading_dict(pass_fractions=(True, True, True))
        grading["thresholds"] = {"min_pass_rate": True, "min_mean_score": False}
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            grading,
            None,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        # Both thresholds fall back to defaults (0.7 / 0.5); still passes.
        assert badge.clauditor.l3 is not None
        assert badge.clauditor.l3.passed is True

    def test_variance_with_bool_n_runs_is_zeroed(self):
        """``n_runs=True`` (bool) is rejected; falls back to 0."""
        bad = {"n_runs": True, "stability": 0.9, "passed": True}
        badge = compute_badge(
            _make_assertions_dict(passed=8, total=8),
            _make_grading_dict(pass_fractions=(True, True, True)),
            bad,
            skill_name="demo",
            iteration=1,
            generated_at=_GEN_AT,
        )
        assert badge.clauditor.variance == VarianceSummary(
            n_runs=0, stability=0.9, passed=True
        )
