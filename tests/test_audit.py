"""Tests for clauditor.audit — iteration loader + aggregator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clauditor.audit import (
    AuditAggregate,
    AuditVerdict,
    IterationRecord,
    Verdict,
    aggregate,
    apply_thresholds,
    load_iterations,
    render_json,
    render_markdown,
    render_stdout_table,
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


def _agg(
    layer: str = "L1",
    rid: str = "a",
    *,
    with_runs: int = 4,
    with_fails: int = 0,
    baseline_runs: int = 0,
    baseline_fails: int = 0,
) -> AuditAggregate:
    with_pass_rate = (
        (with_runs - with_fails) / with_runs if with_runs else 0.0
    )
    baseline_pass_rate: float | None
    if baseline_runs:
        baseline_pass_rate = (baseline_runs - baseline_fails) / baseline_runs
    else:
        baseline_pass_rate = None
    return AuditAggregate(
        layer=layer,
        id=rid,
        total_with_runs=with_runs,
        with_fails=with_fails,
        with_pass_rate=with_pass_rate,
        total_baseline_runs=baseline_runs,
        baseline_fails=baseline_fails,
        baseline_pass_rate=baseline_pass_rate,
    )


class TestApplyThresholds:
    def test_threshold_flags_100_percent_pass(self) -> None:
        aggs = {("L1", "a"): _agg(with_runs=20, with_fails=0)}
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert len(verdicts) == 1
        assert verdicts[0].is_flagged
        assert verdicts[0].verdict == Verdict.FLAG_ALWAYS_PASS

    def test_threshold_flags_zero_failures(self) -> None:
        # 100% pass is the priority reason, but zero-failures still in reasons.
        aggs = {("L1", "a"): _agg(with_runs=5, with_fails=0)}
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert any("zero recorded failures" in r for r in verdicts[0].reasons)

    def test_threshold_flags_low_discrimination_when_baseline_present(
        self,
    ) -> None:
        # With-rate 0.8, baseline 0.78 → discrimination 0.02 < 0.05
        aggs = {
            ("L1", "a"): _agg(
                with_runs=10,
                with_fails=2,
                baseline_runs=50,
                baseline_fails=11,
            )
        }
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert verdicts[0].verdict == Verdict.FLAG_NO_DISCRIMINATION

    def test_threshold_passes_discriminating_assertion(self) -> None:
        # with 0.75 pass, baseline 0.25 pass → 0.5 discrimination, keeps.
        aggs = {
            ("L1", "a"): _agg(
                with_runs=4,
                with_fails=1,
                baseline_runs=4,
                baseline_fails=3,
            )
        }
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert verdicts[0].verdict == Verdict.KEEP
        assert not verdicts[0].is_flagged

    def test_threshold_override_via_cli_args(self) -> None:
        # 95% pass, not 100%; default 0.0 min_fail_rate wouldn't flag,
        # but 0.1 min_fail_rate (threshold 0.9) flags it.
        aggs = {("L1", "a"): _agg(with_runs=20, with_fails=1)}
        lax = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        strict = apply_thresholds(
            aggs, min_fail_rate=0.1, min_discrimination=0.05
        )
        assert lax[0].verdict == Verdict.KEEP
        assert strict[0].verdict == Verdict.FLAG_ALWAYS_PASS


class TestRenderers:
    def _verdicts(self) -> list[AuditVerdict]:
        flagged = _agg("L1", "always_pass", with_runs=20, with_fails=0)
        kept = _agg(
            "L2",
            "sometimes",
            with_runs=10,
            with_fails=3,
            baseline_runs=10,
            baseline_fails=9,
        )
        return apply_thresholds(
            {("L1", "always_pass"): flagged, ("L2", "sometimes"): kept},
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )

    def test_render_markdown_contains_suggest_removal_section(self) -> None:
        md = render_markdown(
            self._verdicts(),
            skill="my_skill",
            iterations_analyzed=20,
            thresholds={"last": 20, "min_fail_rate": 0.0},
            timestamp="20260101T000000Z",
        )
        assert "## Suggest removal" in md
        assert "always_pass" in md
        assert "my_skill" in md

    def test_render_markdown_contains_per_layer_tables(self) -> None:
        md = render_markdown(
            self._verdicts(),
            skill="s",
            iterations_analyzed=20,
            thresholds={"last": 20},
            timestamp="t",
        )
        assert "## L1 detail" in md
        assert "## L2 detail" in md
        assert "## L3 detail" in md
        assert "| id | runs |" in md

    def test_render_json_shape_stable(self) -> None:
        payload = render_json(
            self._verdicts(),
            skill="my_skill",
            iterations_analyzed=20,
            thresholds={"last": 20, "min_fail_rate": 0.0},
            timestamp="20260101T000000Z",
        )
        assert set(payload.keys()) == {
            "skill",
            "timestamp",
            "iterations",
            "thresholds",
            "assertions",
        }
        assert isinstance(payload["assertions"], list)
        first = payload["assertions"][0]
        for key in (
            "layer",
            "id",
            "with_runs",
            "with_pass_rate",
            "baseline_runs",
            "baseline_pass_rate",
            "discrimination",
            "verdict",
            "reasons",
        ):
            assert key in first
        # Must round-trip through json.
        json.dumps(payload)

    def test_render_stdout_table_has_verdict_column(self) -> None:
        table = render_stdout_table(self._verdicts())
        assert "VERDICT" in table
        assert "always_pass" in table


class TestCmdAuditExitCode:
    def test_cmd_audit_exit_1_when_any_flagged(
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
            {i: {"l1": [{"id": "always", "passed": True}]} for i in range(1, 6)},
        )
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "my_skill"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "always" in out

    def test_cmd_audit_exit_0_when_all_clean(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        _make_iteration_fixture(
            project,
            "my_skill",
            {
                1: {
                    "l1": [{"id": "a", "passed": True}],
                    "baseline_l1": [{"id": "a", "passed": False}],
                },
                2: {
                    "l1": [{"id": "a", "passed": False}],
                    "baseline_l1": [{"id": "a", "passed": False}],
                },
                3: {
                    "l1": [{"id": "a", "passed": True}],
                    "baseline_l1": [{"id": "a", "passed": False}],
                },
                4: {
                    "l1": [{"id": "a", "passed": False}],
                    "baseline_l1": [{"id": "a", "passed": False}],
                },
            },
        )
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "my_skill"])
        assert rc == 0

    def test_cmd_audit_json_mode_does_not_write_markdown_file(
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
            "s",
            {i: {"l1": [{"id": "a", "passed": True}]} for i in range(1, 4)},
        )
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "s", "--json"])
        assert rc == 1
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["skill"] == "s"
        assert not (project / ".clauditor" / "audit").exists()

    def test_cmd_audit_writes_markdown_to_output_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        _make_iteration_fixture(
            project,
            "s",
            {1: {"l1": [{"id": "a", "passed": True}]}},
        )
        custom_out = tmp_path / "reports"
        monkeypatch.chdir(project)
        rc = cli_main(
            ["audit", "s", "--output-dir", str(custom_out)]
        )
        assert rc == 1
        written = list(custom_out.glob("s-*.md"))
        assert len(written) == 1
        content = written[0].read_text()
        assert "Suggest removal" in content
        assert "`a`" in content

    def test_cmd_audit_finds_always_pass_assertions_20_runs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Canonical Done-when: 20 always-passing runs → flagged."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        _make_iteration_fixture(
            project,
            "find-restaurants",
            {
                i: {"l1": [{"id": "has_header", "passed": True}]}
                for i in range(1, 21)
            },
        )
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "find-restaurants"])
        assert rc == 1
        report = next(
            (project / ".clauditor" / "audit").glob(
                "find-restaurants-*.md"
            )
        )
        text = report.read_text()
        assert "Suggest removal" in text
        assert "has_header" in text
        # must appear under the removal section.
        suggest_section = text.split("## Suggest removal", 1)[1]
        assert "has_header" in suggest_section.split("## ", 1)[0]


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
