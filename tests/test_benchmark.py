"""Tests for ``clauditor.benchmark`` — pure delta computation.

Covers the seven TDD cases from US-001 of plans/super/28-baseline-pair-delta.md.
Traces: DEC-001 (L3 owns pass_rate), DEC-006 (without_skill stddev is None),
DEC-007 (asymmetric variance), DEC-010 (no TTY, just data).
"""

from __future__ import annotations

import json
import statistics

import pytest

from clauditor.benchmark import Benchmark, compute_benchmark
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.runner import SkillResult


def _make_grading_report(
    *,
    skill_name: str = "test-skill",
    pass_fractions: tuple[bool, ...] = (True, True, False),
    duration: float = 10.0,
    input_tokens: int = 1000,
    output_tokens: int = 500,
) -> GradingReport:
    results = [
        GradingResult(
            criterion=f"c{i}",
            passed=p,
            score=1.0 if p else 0.0,
            evidence="",
            reasoning="",
            id=f"c{i}",
        )
        for i, p in enumerate(pass_fractions)
    ]
    return GradingReport(
        skill_name=skill_name,
        results=results,
        model="test-model",
        duration_seconds=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_skill_result(
    *,
    duration: float = 10.0,
    input_tokens: int = 1000,
    output_tokens: int = 500,
) -> SkillResult:
    return SkillResult(
        output="ok",
        exit_code=0,
        skill_name="test-skill",
        args="",
        duration_seconds=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class TestComputeBenchmark:
    def test_single_rep_primary_single_shot_baseline(self):
        """Case 1: single rep → with_skill stddev 0.0, without_skill stddev None."""
        primary = _make_grading_report(pass_fractions=(True, True, False))
        baseline = _make_grading_report(pass_fractions=(True, False, False))
        primary_res = _make_skill_result(
            duration=20.0, input_tokens=1000, output_tokens=500
        )
        baseline_res = _make_skill_result(
            duration=15.0, input_tokens=800, output_tokens=400
        )

        bm = compute_benchmark(
            skill_name="test-skill",
            primary_reports=[primary],
            baseline_report=baseline,
            primary_results=[primary_res],
            baseline_result=baseline_res,
        )

        assert bm.run_summary.with_skill.pass_rate.stddev == 0.0
        assert bm.run_summary.with_skill.time_seconds.stddev == 0.0
        assert bm.run_summary.with_skill.tokens.stddev == 0.0
        assert bm.run_summary.without_skill.pass_rate.stddev is None
        assert bm.run_summary.without_skill.time_seconds.stddev is None
        assert bm.run_summary.without_skill.tokens.stddev is None
        # pass_rate mean comes from L3 (DEC-001)
        assert bm.run_summary.with_skill.pass_rate.mean == pytest.approx(2 / 3)
        assert bm.run_summary.without_skill.pass_rate.mean == pytest.approx(1 / 3)

    def test_multi_rep_primary(self):
        """Case 2: N=3 primary reps → mean/stddev computed over reps."""
        r1 = _make_grading_report(pass_fractions=(True, True, True))  # 1.0
        r2 = _make_grading_report(pass_fractions=(True, True, False))  # 2/3
        r3 = _make_grading_report(pass_fractions=(True, False, False))  # 1/3
        rates = [1.0, 2 / 3, 1 / 3]

        baseline = _make_grading_report(pass_fractions=(False, False, True))
        pr_results = [
            _make_skill_result(duration=10.0, input_tokens=100, output_tokens=50),
            _make_skill_result(duration=20.0, input_tokens=200, output_tokens=50),
            _make_skill_result(duration=30.0, input_tokens=300, output_tokens=50),
        ]
        base_result = _make_skill_result(
            duration=5.0, input_tokens=50, output_tokens=25
        )

        bm = compute_benchmark(
            skill_name="test-skill",
            primary_reports=[r1, r2, r3],
            baseline_report=baseline,
            primary_results=pr_results,
            baseline_result=base_result,
        )

        assert bm.run_summary.with_skill.pass_rate.mean == pytest.approx(
            sum(rates) / 3
        )
        assert bm.run_summary.with_skill.pass_rate.stddev == pytest.approx(
            statistics.stdev(rates)
        )
        assert bm.run_summary.with_skill.time_seconds.mean == pytest.approx(20.0)
        assert bm.run_summary.with_skill.time_seconds.stddev == pytest.approx(
            statistics.stdev([10.0, 20.0, 30.0])
        )
        assert bm.run_summary.with_skill.tokens.mean == pytest.approx(
            (150 + 250 + 350) / 3
        )
        assert bm.run_summary.with_skill.tokens.stddev == pytest.approx(
            statistics.stdev([150, 250, 350])
        )
        assert bm.run_summary.without_skill.pass_rate.stddev is None
        assert bm.run_summary.without_skill.time_seconds.stddev is None
        assert bm.run_summary.without_skill.tokens.stddev is None

    def test_delta_arithmetic(self):
        """Case 3: delta fields equal with_skill.mean - without_skill.mean."""
        primary = _make_grading_report(
            pass_fractions=(True, True, True, True),  # 1.0
            duration=40.0,
            input_tokens=2000,
            output_tokens=1000,
        )
        baseline = _make_grading_report(
            pass_fractions=(True, False, False, False),  # 0.25
            duration=25.0,
            input_tokens=1500,
            output_tokens=500,
        )
        pr_res = _make_skill_result(
            duration=40.0, input_tokens=2000, output_tokens=1000
        )
        base_res = _make_skill_result(
            duration=25.0, input_tokens=1500, output_tokens=500
        )

        bm = compute_benchmark(
            skill_name="s",
            primary_reports=[primary],
            baseline_report=baseline,
            primary_results=[pr_res],
            baseline_result=base_res,
        )

        rs = bm.run_summary
        assert rs.delta.pass_rate == pytest.approx(
            rs.with_skill.pass_rate.mean - rs.without_skill.pass_rate.mean
        )
        assert rs.delta.time_seconds == pytest.approx(
            rs.with_skill.time_seconds.mean - rs.without_skill.time_seconds.mean
        )
        assert rs.delta.tokens == pytest.approx(
            rs.with_skill.tokens.mean - rs.without_skill.tokens.mean
        )
        assert rs.delta.pass_rate == pytest.approx(0.75)
        assert rs.delta.time_seconds == pytest.approx(15.0)
        assert rs.delta.tokens == pytest.approx(1000)


class TestBenchmarkJson:
    def _simple_benchmark(self) -> Benchmark:
        primary = _make_grading_report(pass_fractions=(True, True, False))
        baseline = _make_grading_report(pass_fractions=(True, False, False))
        pr = _make_skill_result(duration=12.5, input_tokens=1000, output_tokens=500)
        base = _make_skill_result(duration=8.0, input_tokens=700, output_tokens=300)
        return compute_benchmark(
            skill_name="the-skill",
            primary_reports=[primary],
            baseline_report=baseline,
            primary_results=[pr],
            baseline_result=base,
        )

    def test_schema_version_is_first_key(self):
        """Case 4: schema_version is the first top-level key."""
        bm = self._simple_benchmark()
        raw = bm.to_json()
        # Python's json module preserves dict insertion order on parse.
        parsed = json.loads(raw)
        keys = list(parsed.keys())
        assert keys[0] == "schema_version"
        assert parsed["schema_version"] == 1
        # And the expected top-level shape is present.
        assert "skill_name" in parsed
        assert "run_summary" in parsed
        rs = parsed["run_summary"]
        assert set(rs.keys()) == {"with_skill", "without_skill", "delta"}
        for arm in ("with_skill", "without_skill"):
            assert set(rs[arm].keys()) == {"pass_rate", "time_seconds", "tokens"}
            for metric in ("pass_rate", "time_seconds", "tokens"):
                assert set(rs[arm][metric].keys()) == {"mean", "stddev"}
        assert rs["without_skill"]["pass_rate"]["stddev"] is None

    def test_roundtrip_precision(self):
        """Case 5: from_json(to_json(bm)) == bm for all numeric fields."""
        bm = self._simple_benchmark()
        round_tripped = Benchmark.from_json(bm.to_json())

        assert round_tripped.skill_name == bm.skill_name
        assert round_tripped.schema_version == bm.schema_version

        for arm_name in ("with_skill", "without_skill"):
            orig_arm = getattr(bm.run_summary, arm_name)
            new_arm = getattr(round_tripped.run_summary, arm_name)
            for metric in ("pass_rate", "time_seconds", "tokens"):
                o = getattr(orig_arm, metric)
                n = getattr(new_arm, metric)
                assert n.mean == o.mean
                assert n.stddev == o.stddev

        assert (
            round_tripped.run_summary.delta.pass_rate
            == bm.run_summary.delta.pass_rate
        )
        assert (
            round_tripped.run_summary.delta.time_seconds
            == bm.run_summary.delta.time_seconds
        )
        assert round_tripped.run_summary.delta.tokens == bm.run_summary.delta.tokens


class TestComputeBenchmarkErrors:
    def test_empty_results_in_primary_report_raises(self):
        """Case 6: a primary GradingReport with results=[] → ValueError 'primary'."""
        primary = _make_grading_report(pass_fractions=())  # empty results
        baseline = _make_grading_report(pass_fractions=(True, False))
        pr = _make_skill_result()
        base = _make_skill_result()
        with pytest.raises(ValueError, match="primary"):
            compute_benchmark(
                skill_name="s",
                primary_reports=[primary],
                baseline_report=baseline,
                primary_results=[pr],
                baseline_result=base,
            )

    def test_empty_primary_reports_list_raises(self):
        """Case 7: primary_reports=[] → ValueError."""
        baseline = _make_grading_report(pass_fractions=(True, False))
        base = _make_skill_result()
        with pytest.raises(ValueError):
            compute_benchmark(
                skill_name="s",
                primary_reports=[],
                baseline_report=baseline,
                primary_results=[],
                baseline_result=base,
            )

    def test_empty_baseline_results_raises(self):
        """Case 8 (bonus): empty baseline results → ValueError 'baseline'."""
        primary = _make_grading_report(pass_fractions=(True, False))
        baseline = _make_grading_report(pass_fractions=())
        pr = _make_skill_result()
        base = _make_skill_result()
        with pytest.raises(ValueError, match="baseline"):
            compute_benchmark(
                skill_name="s",
                primary_reports=[primary],
                baseline_report=baseline,
                primary_results=[pr],
                baseline_result=base,
            )

    def test_from_json_rejects_wrong_schema_version(self, capsys):
        """Benchmark.from_json hard-fails on schema_version != 1 per
        .claude/rules/json-schema-version.md. The underlying
        _check_schema_version also emits a stderr warning so operators
        can triage the mismatch."""
        payload = json.dumps(
            {
                "schema_version": 2,
                "skill_name": "test-skill",
                "run_summary": {
                    "with_skill": {
                        "pass_rate": {"mean": 1.0, "stddev": 0.0},
                        "time_seconds": {"mean": 1.0, "stddev": 0.0},
                        "tokens": {"mean": 100, "stddev": 0.0},
                    },
                    "without_skill": {
                        "pass_rate": {"mean": 0.5, "stddev": None},
                        "time_seconds": {"mean": 0.5, "stddev": None},
                        "tokens": {"mean": 50, "stddev": None},
                    },
                    "delta": {
                        "pass_rate": 0.5,
                        "time_seconds": 0.5,
                        "tokens": 50,
                    },
                },
            }
        )
        with pytest.raises(ValueError, match="schema_version"):
            Benchmark.from_json(payload)
        err = capsys.readouterr().err
        assert "schema_version" in err
        assert "2" in err
