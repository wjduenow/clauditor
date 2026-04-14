"""Tests for clauditor.audit — iteration loader + aggregator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clauditor.audit import (
    AuditAggregate,
    IterationRecord,
    aggregate,
    load_iterations,
)
from clauditor.cli import main as cli_main

# --------------------------------------------------------------------------- #
# Fixture helper                                                               #
# --------------------------------------------------------------------------- #


def _make_iteration_fixture(
    tmp_path: Path,
    skill: str,
    iterations: dict[int, dict],
) -> Path:
    """Build a fake ``.clauditor/iteration-N/<skill>/`` tree.

    ``iterations`` maps iteration number to a dict with optional keys:

    - ``l1``: list of {id, passed} dicts → assertions.json
    - ``l2``: list of {id, passed} dicts → extraction.json
    - ``l3``: list of {id, passed} dicts → grading.json
    - ``baseline_l1`` / ``baseline_l2`` / ``baseline_l3``: same shapes →
      baseline_*.json sidecars
    - ``empty``: if True, create the skill dir with no sidecar files
    """
    clauditor_dir = tmp_path / ".clauditor"
    for i, payload in iterations.items():
        skill_dir = clauditor_dir / f"iteration-{i}" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        if payload.get("empty"):
            continue
        _write_sidecars(skill_dir, skill, i, payload, prefix="")
        _write_sidecars(skill_dir, skill, i, payload, prefix="baseline_")
    return clauditor_dir


def _write_sidecars(
    skill_dir: Path, skill: str, iteration: int, payload: dict, *, prefix: str
) -> None:
    l1_key = f"{prefix}l1" if prefix else "l1"
    l2_key = f"{prefix}l2" if prefix else "l2"
    l3_key = f"{prefix}l3" if prefix else "l3"

    if l1_key in payload:
        runs = payload[l1_key]
        # ``runs`` may be a list of runs (list of list of dicts) or a single
        # list of dicts treated as run-0 for ergonomic fixtures.
        if runs and isinstance(runs[0], dict):
            runs = [runs]
        assertions_payload = {
            "skill": skill,
            "iteration": iteration,
            "runs": [
                {
                    "run": r_idx,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "results": [
                        {
                            "id": entry["id"],
                            "name": entry.get("name", entry["id"]),
                            "passed": bool(entry["passed"]),
                            "message": "",
                            "kind": entry.get("kind", "custom"),
                            "evidence": None,
                            "raw_data": None,
                        }
                        for entry in run
                    ],
                }
                for r_idx, run in enumerate(runs)
            ],
        }
        (skill_dir / f"{prefix}assertions.json").write_text(
            json.dumps(assertions_payload, indent=2) + "\n"
        )

    if l2_key in payload:
        fields: dict[str, list[dict]] = {}
        for entry in payload[l2_key]:
            fields.setdefault(entry["id"], []).append(
                {
                    "field_name": entry.get("name", entry["id"]),
                    "section": entry.get("section", "s"),
                    "tier": entry.get("tier", "required"),
                    "entry_index": 0,
                    "required": True,
                    "passed": bool(entry["passed"]),
                    "presence_passed": bool(entry["passed"]),
                    "format_passed": None,
                    "evidence": None,
                }
            )
        extraction_payload = {
            "skill_name": skill,
            "model": "test",
            "input_tokens": 0,
            "output_tokens": 0,
            "parse_errors": [],
            "fields": fields,
        }
        (skill_dir / f"{prefix}extraction.json").write_text(
            json.dumps(extraction_payload, indent=2) + "\n"
        )

    if l3_key in payload:
        grading_payload = {
            "skill_name": skill,
            "model": "test",
            "duration_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "results": [
                {
                    "criterion": entry["id"],
                    "passed": bool(entry["passed"]),
                    "score": 1.0 if entry["passed"] else 0.0,
                    "evidence": "",
                    "reasoning": "",
                }
                for entry in payload[l3_key]
            ],
        }
        (skill_dir / f"{prefix}grading.json").write_text(
            json.dumps(grading_payload, indent=2) + "\n"
        )


# --------------------------------------------------------------------------- #
# load_iterations                                                              #
# --------------------------------------------------------------------------- #


class TestLoadIterations:
    def test_last_n_ordered_newest_first(self, tmp_path: Path) -> None:
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "skillX",
            {
                i: {"l1": [{"id": f"a{i}", "passed": True}]}
                for i in range(1, 6)
            },
        )
        records, skipped = load_iterations(
            "skillX", last=3, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        iters = sorted({r.iteration for r in records})
        assert iters == [3, 4, 5]

    def test_skips_dirs_missing_sidecars(self, tmp_path: Path) -> None:
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {
                1: {"l1": [{"id": "a", "passed": True}]},
                2: {"empty": True},
                3: {"l1": [{"id": "a", "passed": False}]},
            },
        )
        records, skipped = load_iterations(
            "s", last=10, clauditor_dir=clauditor_dir
        )
        assert skipped == 1
        iters = sorted({r.iteration for r in records})
        assert iters == [1, 3]

    def test_handles_partial_sidecars(self, tmp_path: Path) -> None:
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {
                1: {
                    "l1": [{"id": "a", "passed": True}],
                    # no l2, no l3
                },
            },
        )
        records, skipped = load_iterations(
            "s", last=10, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L1"
        assert records[0].id == "a"

    def test_returns_empty_when_no_data(self, tmp_path: Path) -> None:
        records, skipped = load_iterations(
            "s", last=10, clauditor_dir=tmp_path / ".clauditor"
        )
        assert records == []
        assert skipped == 0

    def test_reads_baseline_sidecars_when_present(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {
                1: {
                    "l1": [{"id": "a", "passed": True}],
                    "baseline_l1": [{"id": "a", "passed": False}],
                },
            },
        )
        records, _ = load_iterations(
            "s", last=10, clauditor_dir=clauditor_dir
        )
        with_skill = [r for r in records if r.with_skill]
        baseline = [r for r in records if not r.with_skill]
        assert len(with_skill) == 1 and with_skill[0].passed
        assert len(baseline) == 1 and not baseline[0].passed

    def test_loads_all_three_layers(self, tmp_path: Path) -> None:
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {
                1: {
                    "l1": [{"id": "l1a", "passed": True}],
                    "l2": [{"id": "l2a", "passed": True}],
                    "l3": [{"id": "l3a", "passed": True}],
                },
            },
        )
        records, _ = load_iterations(
            "s", last=10, clauditor_dir=clauditor_dir
        )
        layers = {r.layer for r in records}
        assert layers == {"L1", "L2", "L3"}


# --------------------------------------------------------------------------- #
# aggregate                                                                    #
# --------------------------------------------------------------------------- #


class TestAggregate:
    def test_computes_with_rate_per_id(self) -> None:
        records = [
            IterationRecord(i, "L1", "a", passed=(i != 2), with_skill=True)
            for i in range(1, 5)
        ]
        agg = aggregate(records)
        entry = agg[("L1", "a")]
        assert entry.total_with_runs == 4
        assert entry.with_fails == 1
        assert entry.with_pass_rate == 0.75
        assert entry.baseline_pass_rate is None
        assert entry.discrimination is None

    def test_computes_baseline_rate_when_baseline_present(self) -> None:
        records = [
            IterationRecord(1, "L1", "a", passed=True, with_skill=True),
            IterationRecord(2, "L1", "a", passed=True, with_skill=True),
            IterationRecord(1, "L1", "a", passed=False, with_skill=False),
            IterationRecord(2, "L1", "a", passed=True, with_skill=False),
        ]
        agg = aggregate(records)
        entry = agg[("L1", "a")]
        assert entry.with_pass_rate == 1.0
        assert entry.baseline_pass_rate == 0.5
        assert entry.discrimination == pytest.approx(0.5)

    def test_discrimination_none_when_no_baseline_data(self) -> None:
        records = [
            IterationRecord(1, "L1", "a", passed=True, with_skill=True),
        ]
        agg = aggregate(records)
        assert agg[("L1", "a")].discrimination is None

    def test_groups_by_layer_and_id(self) -> None:
        records = [
            IterationRecord(1, "L1", "shared", passed=True, with_skill=True),
            IterationRecord(1, "L3", "shared", passed=False, with_skill=True),
        ]
        agg = aggregate(records)
        assert ("L1", "shared") in agg
        assert ("L3", "shared") in agg
        assert agg[("L1", "shared")].with_pass_rate == 1.0
        assert agg[("L3", "shared")].with_pass_rate == 0.0


# --------------------------------------------------------------------------- #
# cmd_audit (smoke)                                                            #
# --------------------------------------------------------------------------- #


class TestCmdAudit:
    def test_prints_aggregate_table(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        _make_iteration_fixture(
            project,
            "my_skill",
            {
                1: {"l1": [{"id": "has_header", "passed": True}]},
                2: {"l1": [{"id": "has_header", "passed": False}]},
            },
        )
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "my_skill"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "has_header" in out
        assert "L1" in out


class TestAuditAggregateDataclass:
    def test_discrimination_property(self) -> None:
        agg = AuditAggregate(
            layer="L1",
            id="x",
            total_with_runs=2,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=2,
            baseline_fails=2,
            baseline_pass_rate=0.0,
        )
        assert agg.discrimination == 1.0
