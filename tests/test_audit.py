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
from clauditor.context import IterationContext

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
        assertions_payload = {"schema_version": 1, **assertions_payload}
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
            "schema_version": 1,
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
            "schema_version": 1,
            "skill_name": skill,
            "model": "test",
            "duration_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "results": [
                {
                    "id": entry["id"],
                    "criterion": entry.get("criterion", entry["id"]),
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
        records, skipped, _ = load_iterations(
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
        records, skipped, _ = load_iterations(
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
        records, skipped, _ = load_iterations(
            "s", last=10, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L1"
        assert records[0].id == "a"

    def test_returns_empty_when_no_data(self, tmp_path: Path) -> None:
        records, skipped, _ = load_iterations(
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
        records, _, _ = load_iterations(
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
        records, _, _ = load_iterations(
            "s", last=10, clauditor_dir=clauditor_dir
        )
        layers = {r.layer for r in records}
        assert layers == {"L1", "L2", "L3"}


    def test_extraction_entry_missing_passed_skipped(self, tmp_path: Path) -> None:
        """Extraction entries without a 'passed' key are silently skipped."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        extraction = {
            "schema_version": 1,
            "skill_name": "s",
            "model": "test",
            "input_tokens": 0,
            "output_tokens": 0,
            "parse_errors": [],
            "fields": {
                "f1": [
                    {"field_name": "f1", "section": "s", "tier": "required",
                     "entry_index": 0, "required": True, "passed": True,
                     "presence_passed": True, "format_passed": None,
                     "evidence": None},
                    {"field_name": "f2", "section": "s", "tier": "required",
                     "entry_index": 1, "required": True,
                     "presence_passed": False, "format_passed": None,
                     "evidence": None},
                ],
            },
        }
        (skill_dir / "extraction.json").write_text(
            json.dumps(extraction, indent=2) + "\n"
        )
        records, _, _ = load_iterations("s", last=5, clauditor_dir=clauditor_dir)
        l2 = [r for r in records if r.layer == "L2"]
        assert len(l2) == 1
        assert l2[0].passed is True


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
        entry = agg[("anthropic", "L1", "a")]
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
        entry = agg[("anthropic", "L1", "a")]
        assert entry.with_pass_rate == 1.0
        assert entry.baseline_pass_rate == 0.5
        assert entry.discrimination == pytest.approx(0.5)

    def test_discrimination_none_when_no_baseline_data(self) -> None:
        records = [
            IterationRecord(1, "L1", "a", passed=True, with_skill=True),
        ]
        agg = aggregate(records)
        assert agg[("anthropic", "L1", "a")].discrimination is None

    def test_groups_by_layer_and_id(self) -> None:
        records = [
            IterationRecord(1, "L1", "shared", passed=True, with_skill=True),
            IterationRecord(1, "L3", "shared", passed=False, with_skill=True),
        ]
        agg = aggregate(records)
        assert ("anthropic", "L1", "shared") in agg
        assert ("anthropic", "L3", "shared") in agg
        assert agg[("anthropic", "L1", "shared")].with_pass_rate == 1.0
        assert agg[("anthropic", "L3", "shared")].with_pass_rate == 0.0


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

    def test_cmd_audit_returns_2_on_unwritable_output_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FIX-14: IO errors on report write yield exit code 2."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        _make_iteration_fixture(
            project,
            "my_skill",
            {
                1: {"l1": [{"id": "has_header", "passed": True}]},
            },
        )
        monkeypatch.chdir(project)
        # Point output-dir at a path whose parent is an existing file —
        # mkdir(parents=True) will raise NotADirectoryError.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x")
        bad = blocker / "nested" / "audit"
        rc = cli_main(
            ["audit", "my_skill", "--output-dir", str(bad)]
        )
        assert rc == 2


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
        aggs = {("anthropic", "L1", "a"): _agg(with_runs=20, with_fails=0)}
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert len(verdicts) == 1
        assert verdicts[0].is_flagged
        assert verdicts[0].verdict == Verdict.FLAG_ALWAYS_PASS

    def test_threshold_flags_zero_failures(self) -> None:
        # 100% pass is the priority reason, but zero-failures still in reasons.
        aggs = {("anthropic", "L1", "a"): _agg(with_runs=5, with_fails=0)}
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert any("zero recorded failures" in r for r in verdicts[0].reasons)

    def test_threshold_flags_low_discrimination_when_baseline_present(
        self,
    ) -> None:
        # With-rate 0.8, baseline 0.78 → discrimination 0.02 < 0.05
        aggs = {
            ("anthropic", "L1", "a"): _agg(
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
            ("anthropic", "L1", "a"): _agg(
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
        aggs = {("anthropic", "L1", "a"): _agg(with_runs=20, with_fails=1)}
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
            {
                ("anthropic", "L1", "always_pass"): flagged,
                ("anthropic", "L2", "sometimes"): kept,
            },
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
        # US-004 (#147): bumped to schema_version 2 + ``providers_seen``.
        # #154 US-005 / DEC-005: always emits ``iteration_contexts`` array.
        assert set(payload.keys()) == {
            "schema_version",
            "skill",
            "timestamp",
            "iterations",
            "thresholds",
            "providers_seen",
            "assertions",
            "iteration_contexts",
        }
        assert payload["schema_version"] == 2
        assert isinstance(payload["assertions"], list)
        first = payload["assertions"][0]
        for key in (
            "provider",
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


class TestSchemaVersion:
    def test_audit_render_json_has_schema_version_2(self) -> None:
        """US-004 (#147): audit JSON output bumped from v1 to v2 to
        signal the new ``provider`` per-assertion field + top-level
        ``providers_seen`` array (DEC-005, DEC-010)."""
        payload = render_json(
            [],
            skill="s",
            iterations_analyzed=0,
            thresholds={"last": 20},
            timestamp="t",
        )
        assert payload["schema_version"] == 2


class TestIsAcceptedVersion:
    """US-002 (#147): pure helper :func:`_is_accepted_version` answers
    ``1 <= version <= MAX_SCHEMA_VERSION[base]`` per DEC-008. Tests
    pin the v1..v3 acceptance for grading/extraction sidecars and the
    v1-only acceptance for assertions.json, plus the v4-rejection
    branch and the unknown-filename ``KeyError`` contract.
    """

    def test_is_accepted_version_grading_json_accepts_1_2_3(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("grading.json", 1) is True
        assert _is_accepted_version("grading.json", 2) is True
        assert _is_accepted_version("grading.json", 3) is True

    def test_is_accepted_version_grading_json_rejects_4(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("grading.json", 4) is False

    def test_is_accepted_version_extraction_json_accepts_1_2_3(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("extraction.json", 1) is True
        assert _is_accepted_version("extraction.json", 2) is True
        assert _is_accepted_version("extraction.json", 3) is True

    def test_is_accepted_version_extraction_json_rejects_4(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("extraction.json", 4) is False

    def test_is_accepted_version_assertions_json_accepts_only_1(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("assertions.json", 1) is True
        assert _is_accepted_version("assertions.json", 2) is False
        assert _is_accepted_version("assertions.json", 3) is False

    def test_is_accepted_version_rejects_zero_and_negative(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("grading.json", 0) is False
        assert _is_accepted_version("grading.json", -1) is False

    def test_is_accepted_version_rejects_non_int(self) -> None:
        """Non-int values (None from a missing key, stringly-typed
        values, bool sneaking through as int subclass) all return
        False, so :func:`_check_schema_version` produces a clean
        warning rather than crashing."""
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("grading.json", None) is False
        assert _is_accepted_version("grading.json", "1") is False
        # bool is an int subclass in Python — guard explicitly.
        assert _is_accepted_version("grading.json", True) is False
        assert _is_accepted_version("grading.json", False) is False

    def test_is_accepted_version_baseline_prefix_strips_correctly(self) -> None:
        """``baseline_grading.json`` shares acceptance with
        ``grading.json`` (the loader treats baseline sidecars as the
        same family)."""
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("baseline_grading.json", 3) is True
        assert _is_accepted_version("baseline_extraction.json", 3) is True
        assert _is_accepted_version("baseline_assertions.json", 1) is True
        assert _is_accepted_version("baseline_grading.json", 4) is False
        assert _is_accepted_version("baseline_assertions.json", 2) is False

    def test_is_accepted_version_unknown_filename_raises_key_error(
        self,
    ) -> None:
        """Unknown filename → ``KeyError``. Per DEC-008 / US-002
        acceptance criterion 3, the helper does not silently fall
        through; the loader is expected to call with one of the three
        known sidecar names."""
        from clauditor.audit import _is_accepted_version

        with pytest.raises(KeyError):
            _is_accepted_version("unknown.json", 1)


class TestAuditLegacyCompat:
    """US-006 (#86): the audit loader accepts both schema_version=1 and
    schema_version=2 for grading.json and extraction.json sidecars. A
    v1 sidecar (no ``transport_source``) loads cleanly; a v2 sidecar
    (with ``transport_source``) also loads cleanly. Pre-#86 iterations
    produce identical audit reports (backward compat)."""

    def _write_grading_sidecar(
        self, skill_dir: Path, *, version: int,
        transport_source: str | None,
    ) -> None:
        payload: dict = {
            "schema_version": version,
            "skill_name": "s",
            "model": "claude-sonnet-4-6",
            "results": [
                {
                    "id": "quality",
                    "criterion": "is good",
                    "passed": True,
                    "score": 0.9,
                    "evidence": "e",
                    "reasoning": "r",
                },
            ],
        }
        if transport_source is not None:
            payload["transport_source"] = transport_source
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "grading.json").write_text(
            json.dumps(payload, indent=2)
        )

    def _write_extraction_sidecar(
        self, skill_dir: Path, *, version: int,
        transport_source: str | None,
    ) -> None:
        payload: dict = {
            "schema_version": version,
            "skill_name": "s",
            "model": "haiku",
            "input_tokens": 0,
            "output_tokens": 0,
            "parse_errors": [],
            "fields": {
                "v1": [
                    {
                        "field_name": "a",
                        "section": "Venues",
                        "tier": "primary",
                        "entry_index": 0,
                        "required": True,
                        "passed": True,
                        "presence_passed": True,
                        "format_passed": None,
                        "evidence": "v",
                    },
                ],
            },
        }
        if transport_source is not None:
            payload["transport_source"] = transport_source
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "extraction.json").write_text(
            json.dumps(payload, indent=2)
        )

    def test_loads_v1_grading_no_transport_source(
        self, tmp_path: Path
    ) -> None:
        """A legacy v1 grading.json (no ``transport_source``) loads
        cleanly. The record shows up in the audit aggregate as
        ``(L3, quality)``."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_grading_sidecar(
            skill_dir, version=1, transport_source=None
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L3"
        assert records[0].id == "quality"
        assert records[0].passed is True

    def test_loads_v1_extraction_no_transport_source(
        self, tmp_path: Path
    ) -> None:
        """A legacy v1 extraction.json (no ``transport_source``) loads
        cleanly."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_extraction_sidecar(
            skill_dir, version=1, transport_source=None
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L2"
        assert records[0].id == "v1"
        assert records[0].passed is True

    def test_loads_v2_grading_with_transport_source_cli(
        self, tmp_path: Path
    ) -> None:
        """A v2 grading.json with ``transport_source="cli"`` loads
        cleanly."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_grading_sidecar(
            skill_dir, version=2, transport_source="cli"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L3"
        assert records[0].id == "quality"

    def test_loads_v2_extraction_with_transport_source_cli(
        self, tmp_path: Path
    ) -> None:
        """A v2 extraction.json with ``transport_source="cli"`` loads
        cleanly."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_extraction_sidecar(
            skill_dir, version=2, transport_source="cli"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L2"

    def test_loads_v2_grading_with_transport_source_api(
        self, tmp_path: Path
    ) -> None:
        """A v2 grading.json with ``transport_source="api"`` loads
        cleanly (the common case post-#86)."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_grading_sidecar(
            skill_dir, version=2, transport_source="api"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1

    def test_loads_v3_grading_with_transport_source_api(
        self, tmp_path: Path
    ) -> None:
        """US-002 (#147): a v3 grading.json (with ``provider_source``
        added by US-001) loads cleanly through the
        ``MAX_SCHEMA_VERSION``-driven loader."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_grading_sidecar(
            skill_dir, version=3, transport_source="api"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L3"
        assert records[0].id == "quality"

    def test_loads_v3_extraction_with_transport_source_api(
        self, tmp_path: Path
    ) -> None:
        """US-002 (#147): a v3 extraction.json loads cleanly."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_extraction_sidecar(
            skill_dir, version=3, transport_source="api"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L2"

    def test_grading_unknown_schema_version_still_skipped(
        self, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """A grading.json with ``schema_version=4`` (out of accepted
        range 1..3 per US-002 / DEC-008 of #147) must be skipped with a
        stderr warning."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_grading_sidecar(
            skill_dir, version=4, transport_source="api"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert records == []
        assert skipped == 1
        err = capsys.readouterr().err
        assert "schema_version=4" in err

    def test_extraction_unknown_schema_version_still_skipped(
        self, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """An extraction.json with ``schema_version=4`` (out of accepted
        range 1..3) must be skipped with a stderr warning."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        self._write_extraction_sidecar(
            skill_dir, version=4, transport_source="api"
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert records == []
        assert skipped == 1
        err = capsys.readouterr().err
        assert "schema_version=4" in err

    def test_baseline_sidecar_v4_skipped_with_baseline_prefix_stripped(
        self, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """A ``baseline_grading.json`` with v4 must be skipped with the
        same warning as canonical ``grading.json``. Exercises the
        ``base.startswith("baseline_")`` branch in
        ``_check_schema_version`` so the rejection-path's
        ``MAX_SCHEMA_VERSION[base]`` lookup uses the family's max
        rather than crashing on an unknown ``baseline_*`` key."""
        import json
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        payload = {
            "schema_version": 4,
            "skill_name": "s",
            "model": "claude-sonnet-4-6",
            "transport_source": "api",
            "results": [],
        }
        (skill_dir / "baseline_grading.json").write_text(
            json.dumps(payload)
        )
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert records == []
        err = capsys.readouterr().err
        assert "schema_version=4" in err


class TestCmdAuditInvalidSkillName:
    def test_rejects_traversal_skill_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """FIX-2 (#25): ``../..`` skill names must be rejected before
        any filesystem use — otherwise the markdown report write escapes
        the output dir."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        output_dir = tmp_path / "out"
        monkeypatch.chdir(project)
        rc = cli_main(
            ["audit", "../../evil", "--output-dir", str(output_dir)]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "invalid skill name" in err
        # Nothing written outside the tmp output dir.
        assert not output_dir.exists() or not any(output_dir.iterdir())

    def test_rejects_out_of_range_thresholds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FIX-4 (#25): ``--min-fail-rate`` / ``--min-discrimination``
        outside [0.0, 1.0] must cause an argparse exit (2)."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        monkeypatch.chdir(project)
        with pytest.raises(SystemExit) as exc_info:
            cli_main(["audit", "my_skill", "--min-fail-rate", "1.5"])
        assert exc_info.value.code == 2

        with pytest.raises(SystemExit) as exc_info:
            cli_main(
                ["audit", "my_skill", "--min-discrimination", "-0.1"]
            )
        assert exc_info.value.code == 2

        with pytest.raises(SystemExit) as exc_info:
            cli_main(["audit", "my_skill", "--min-fail-rate", "nan"])
        assert exc_info.value.code == 2


class TestAuditL3StableId:
    def test_audit_l3_keyed_by_id_not_text(self, tmp_path: Path) -> None:
        """FIX-1 (#25): L3 aggregate is keyed by stable id, not the
        criterion text. Editing the criterion's wording (while keeping
        the id) must not reset audit history."""
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {
                1: {"l3": [{"id": "quality", "passed": True}]},
                2: {"l3": [{"id": "quality", "passed": False}]},
            },
        )
        # Second iteration's criterion text differs even though id matches
        # — rewrite the grading.json file inline to simulate an edit.
        gj = clauditor_dir / "iteration-2" / "s" / "grading.json"
        data = json.loads(gj.read_text())
        data["results"][0]["criterion"] = "Totally different wording"
        gj.write_text(json.dumps(data))

        records, _, _ = load_iterations("s", last=5, clauditor_dir=clauditor_dir)
        l3 = [r for r in records if r.layer == "L3"]
        assert len(l3) == 2
        assert {r.id for r in l3} == {"quality"}
        agg = aggregate(records)
        assert ("anthropic", "L3", "quality") in agg
        # One pass, one fail → 0.5 with_pass_rate.
        assert agg[("anthropic", "L3", "quality")].with_pass_rate == pytest.approx(0.5)

    def test_loader_skips_unknown_schema_version(
        self, tmp_path: Path
    ) -> None:
        """FIX-11: loaders must skip sidecars with unknown schema_version."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "assertions.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "runs": [
                        {
                            "results": [
                                {
                                    "id": "x",
                                    "name": "x",
                                    "passed": True,
                                    "message": "",
                                    "kind": "custom",
                                    "evidence": None,
                                    "raw_data": None,
                                }
                            ]
                        }
                    ],
                }
            )
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert records == []
        # Copilot fix: schema-mismatched sidecars must count as skipped,
        # not as "loaded" — otherwise the skipped counter undercounts
        # iteration dirs that contributed zero usable data.
        assert skipped == 1

    def test_loader_counts_unparseable_sidecars_as_skipped(
        self, tmp_path: Path
    ) -> None:
        """Copilot fix (PR #34): an iteration dir whose sidecars exist
        but are all malformed JSON must increment ``skipped``, not
        silently loaded as zero records."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "assertions.json").write_text("{not valid json")
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert records == []
        assert skipped == 1

    def test_audit_drops_l3_result_without_id(
        self, tmp_path: Path
    ) -> None:
        """L3 records missing the stable id are dropped (no fallback
        to the criterion text)."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "grading.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "criterion": "legacy no-id",
                            "passed": True,
                            "score": 1.0,
                        }
                    ]
                }
            )
        )
        records, _, _ = load_iterations("s", last=5, clauditor_dir=clauditor_dir)
        assert [r for r in records if r.layer == "L3"] == []


class TestFix12Fix13:
    def test_render_markdown_escapes_pipe_in_id(self) -> None:
        """FIX-12: ids containing ``|`` must be escaped in md tables."""
        agg = AuditAggregate(
            layer="L1",
            id="a|b",
            total_with_runs=3,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
        )
        verdicts = apply_thresholds(
            {("anthropic", "L1", "a|b"): agg},
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )
        md = render_markdown(
            verdicts,
            skill="s",
            iterations_analyzed=1,
            thresholds={},
            timestamp="t",
        )
        assert "a\\|b" in md
        assert "| a|b " not in md  # raw pipe never leaks into table cell

    def test_apply_thresholds_skips_baseline_only_aggregates(self) -> None:
        """FIX-13: aggregates with no primary runs must not yield a verdict."""
        primary_only = AuditAggregate(
            layer="L1",
            id="live",
            total_with_runs=3,
            with_fails=1,
            with_pass_rate=2 / 3,
            total_baseline_runs=3,
            baseline_fails=3,
            baseline_pass_rate=0.0,
        )
        baseline_only = AuditAggregate(
            layer="L1",
            id="stale",
            total_with_runs=0,
            with_fails=0,
            with_pass_rate=0.0,
            total_baseline_runs=3,
            baseline_fails=0,
            baseline_pass_rate=1.0,
        )
        verdicts = apply_thresholds(
            {
                ("anthropic", "L1", "live"): primary_only,
                ("anthropic", "L1", "stale"): baseline_only,
            },
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )
        ids = {v.id for v in verdicts}
        assert ids == {"live"}


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


# --------------------------------------------------------------------------- #
# US-003 (#147): provider dimension on IterationRecord/AuditAggregate         #
# --------------------------------------------------------------------------- #


def _write_grading_v3(
    skill_dir: Path,
    *,
    rid: str,
    passed: bool,
    provider_source: str | None,
) -> None:
    """Write a v3 grading.json sidecar with optional ``provider_source``.

    Helper for US-003 mixed-provider tests. ``provider_source=None``
    omits the field on disk so the loader exercises the v2-style
    default-to-anthropic branch even on a v3-marked sidecar.
    """
    payload: dict = {
        "schema_version": 3,
        "skill_name": "s",
        "model": "claude-sonnet-4-6",
        "results": [
            {
                "id": rid,
                "criterion": rid,
                "passed": passed,
                "score": 1.0 if passed else 0.0,
                "evidence": "",
                "reasoning": "",
            },
        ],
    }
    if provider_source is not None:
        payload["provider_source"] = provider_source
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "grading.json").write_text(
        json.dumps(payload, indent=2)
    )


def _write_extraction_v3(
    skill_dir: Path,
    *,
    field_id: str,
    passed: bool,
    provider_source: str | None,
) -> None:
    payload: dict = {
        "schema_version": 3,
        "skill_name": "s",
        "model": "haiku",
        "input_tokens": 0,
        "output_tokens": 0,
        "parse_errors": [],
        "fields": {
            field_id: [
                {
                    "field_name": field_id,
                    "section": "s",
                    "tier": "primary",
                    "entry_index": 0,
                    "required": True,
                    "passed": passed,
                    "presence_passed": passed,
                    "format_passed": None,
                    "evidence": "",
                },
            ],
        },
    }
    if provider_source is not None:
        payload["provider_source"] = provider_source
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "extraction.json").write_text(
        json.dumps(payload, indent=2)
    )


class TestProviderDimension:
    """US-003 (#147): ``IterationRecord``/``AuditAggregate`` carry
    ``provider``; aggregation groups by ``(provider, layer, id)`` so
    mixed-provider history splits cleanly. Pre-#147 history (v1/v2
    sidecars without ``provider_source``) defaults the provider to
    ``"anthropic"`` per DEC-001.
    """

    def test_iteration_record_defaults_provider_to_anthropic(self) -> None:
        rec = IterationRecord(
            iteration=1,
            layer="L3",
            id="x",
            passed=True,
            with_skill=True,
        )
        assert rec.provider == "anthropic"

    def test_iteration_record_explicit_provider(self) -> None:
        rec = IterationRecord(
            iteration=1,
            layer="L3",
            id="x",
            passed=True,
            with_skill=True,
            provider="openai",
        )
        assert rec.provider == "openai"

    def test_records_from_grading_reads_provider_source(
        self, tmp_path: Path
    ) -> None:
        """A v3 grading.json with ``provider_source: "openai"`` produces
        records whose ``provider`` field is ``"openai"``."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        _write_grading_v3(
            skill_dir,
            rid="quality",
            passed=True,
            provider_source="openai",
        )
        records, skipped, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert skipped == 0
        assert len(records) == 1
        assert records[0].layer == "L3"
        assert records[0].id == "quality"
        assert records[0].provider == "openai"

    def test_records_from_grading_v2_defaults_provider_to_anthropic(
        self, tmp_path: Path
    ) -> None:
        """A v3 grading.json with no ``provider_source`` field defaults
        the record's provider to ``"anthropic"`` (matching legacy v1/v2
        reads)."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        _write_grading_v3(
            skill_dir,
            rid="quality",
            passed=True,
            provider_source=None,
        )
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert len(records) == 1
        assert records[0].provider == "anthropic"

    def test_records_from_extraction_reads_provider_source(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        _write_extraction_v3(
            skill_dir,
            field_id="f1",
            passed=True,
            provider_source="openai",
        )
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert len(records) == 1
        assert records[0].layer == "L2"
        assert records[0].provider == "openai"

    def test_records_from_assertions_uses_anthropic_placeholder(
        self, tmp_path: Path
    ) -> None:
        """DEC-002 (#147): L1 records always carry
        ``provider="anthropic"`` regardless of which provider produced
        the underlying skill output. Assertions sidecars stay at v1."""
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {
                1: {"l1": [{"id": "has_header", "passed": True}]},
            },
        )
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        l1 = [r for r in records if r.layer == "L1"]
        assert len(l1) >= 1
        for r in l1:
            assert r.provider == "anthropic"

    def test_aggregate_groups_by_provider_layer_id(self) -> None:
        """Mixed-provider records with the same (layer, id) split into
        two distinct aggregates keyed on (anthropic, L3, x) and
        (openai, L3, x)."""
        records = [
            IterationRecord(
                1, "L3", "x", passed=True, with_skill=True,
                provider="anthropic",
            ),
            IterationRecord(
                2, "L3", "x", passed=False, with_skill=True,
                provider="anthropic",
            ),
            IterationRecord(
                3, "L3", "x", passed=True, with_skill=True,
                provider="openai",
            ),
            IterationRecord(
                4, "L3", "x", passed=True, with_skill=True,
                provider="openai",
            ),
        ]
        agg = aggregate(records)
        assert ("anthropic", "L3", "x") in agg
        assert ("openai", "L3", "x") in agg
        assert agg[("anthropic", "L3", "x")].with_pass_rate == 0.5
        assert agg[("openai", "L3", "x")].with_pass_rate == 1.0
        assert agg[("anthropic", "L3", "x")].provider == "anthropic"
        assert agg[("openai", "L3", "x")].provider == "openai"

    def test_aggregate_single_provider_history_unchanged_shape(
        self,
    ) -> None:
        """Single-provider history continues to render the single
        bucket (default ``"anthropic"`` provider)."""
        records = [
            IterationRecord(1, "L1", "a", passed=True, with_skill=True),
            IterationRecord(2, "L1", "a", passed=False, with_skill=True),
        ]
        agg = aggregate(records)
        assert list(agg.keys()) == [("anthropic", "L1", "a")]
        assert agg[("anthropic", "L1", "a")].with_pass_rate == 0.5

    def test_apply_thresholds_consumes_three_tuple_key(self) -> None:
        """``apply_thresholds`` must unpack the new 3-tuple key without
        raising and must propagate ``provider`` into the resulting
        ``AuditVerdict``."""
        aggs = {
            ("openai", "L3", "x"): AuditAggregate(
                layer="L3",
                id="x",
                total_with_runs=20,
                with_fails=0,
                with_pass_rate=1.0,
                total_baseline_runs=0,
                baseline_fails=0,
                baseline_pass_rate=None,
                provider="openai",
            ),
        }
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert len(verdicts) == 1
        assert verdicts[0].provider == "openai"
        assert verdicts[0].verdict == Verdict.FLAG_ALWAYS_PASS

    def test_apply_thresholds_sorts_provider_first(self) -> None:
        """Sort order: provider, then layer, then id. Anthropic
        ``("L3", "x")`` renders before openai ``("L3", "x")``."""
        anthropic_agg = AuditAggregate(
            layer="L3",
            id="x",
            total_with_runs=5,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="anthropic",
        )
        openai_agg = AuditAggregate(
            layer="L3",
            id="x",
            total_with_runs=5,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="openai",
        )
        aggs = {
            ("openai", "L3", "x"): openai_agg,
            ("anthropic", "L3", "x"): anthropic_agg,
        }
        verdicts = apply_thresholds(
            aggs, min_fail_rate=0.0, min_discrimination=0.05
        )
        assert [v.provider for v in verdicts] == ["anthropic", "openai"]

    def test_mixed_provider_end_to_end_two_aggregates(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: two iteration dirs, one with provider_source
        anthropic and one with openai, share the same (layer, id) but
        produce two distinct aggregates."""
        clauditor_dir = tmp_path / ".clauditor"
        _write_grading_v3(
            clauditor_dir / "iteration-1" / "s",
            rid="x",
            passed=True,
            provider_source="anthropic",
        )
        _write_grading_v3(
            clauditor_dir / "iteration-2" / "s",
            rid="x",
            passed=True,
            provider_source="openai",
        )
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        agg = aggregate(records)
        assert ("anthropic", "L3", "x") in agg
        assert ("openai", "L3", "x") in agg
        assert len(agg) == 2

    def test_v2_sidecar_defaults_provider_to_anthropic(
        self, tmp_path: Path
    ) -> None:
        """A v2 grading.json (no ``provider_source`` field on disk) →
        records default ``provider="anthropic"`` so pre-#147 history
        renders identically to today (acceptance criterion 5)."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        # v2 shape: schema_version=2 + transport_source, no provider_source.
        payload = {
            "schema_version": 2,
            "skill_name": "s",
            "model": "claude-sonnet-4-6",
            "transport_source": "api",
            "results": [
                {
                    "id": "x",
                    "criterion": "x",
                    "passed": True,
                    "score": 1.0,
                    "evidence": "",
                    "reasoning": "",
                },
            ],
        }
        (skill_dir / "grading.json").write_text(json.dumps(payload))
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert len(records) == 1
        assert records[0].provider == "anthropic"
        agg = aggregate(records)
        assert ("anthropic", "L3", "x") in agg


class TestProviderOrDefault:
    """Defense-in-depth helper guards malformed v3 sidecars.

    A v3 ``grading.json`` / ``extraction.json`` is supposed to carry a
    string ``provider_source``, but the loader cannot trust hand-edited
    or future-bumped sidecars. ``_provider_or_default`` is the single
    coercion seam — these tests pin its branches so a regression that
    re-admits non-strings into ``IterationRecord``/``AuditAggregate``
    keys cannot land silently.
    """

    def test_valid_string_passes_through(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default("openai") == "openai"
        assert _provider_or_default("anthropic") == "anthropic"

    def test_none_falls_back_to_anthropic(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default(None) == "anthropic"

    def test_empty_string_falls_back(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default("") == "anthropic"

    def test_whitespace_only_falls_back(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default("   ") == "anthropic"

    def test_int_falls_back(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default(1) == "anthropic"
        assert _provider_or_default(0) == "anthropic"

    def test_bool_falls_back(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default(True) == "anthropic"
        assert _provider_or_default(False) == "anthropic"

    def test_list_falls_back(self) -> None:
        from clauditor.audit import _provider_or_default
        assert _provider_or_default([]) == "anthropic"
        assert _provider_or_default(["openai"]) == "anthropic"

    def test_malformed_provider_source_in_grading_sidecar(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: a v3 grading.json with ``provider_source: 1``
        loads cleanly with ``provider="anthropic"`` rather than raising
        ``TypeError`` during aggregation."""
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        payload = {
            "schema_version": 3,
            "skill_name": "s",
            "model": "claude-sonnet-4-6",
            "provider_source": 1,
            "results": [
                {
                    "id": "x",
                    "criterion": "x",
                    "passed": True,
                    "score": 1.0,
                    "evidence": "",
                    "reasoning": "",
                },
            ],
        }
        (skill_dir / "grading.json").write_text(json.dumps(payload))
        records, _, _ = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert len(records) == 1
        assert records[0].provider == "anthropic"
        agg = aggregate(records)
        assert ("anthropic", "L3", "x") in agg


class TestRenderProviderColumn:
    """US-004 (#147): the three audit render paths surface provider.

    - ``render_stdout_table`` adds a leftmost ``PROVIDER`` column.
    - ``render_markdown`` adds a leftmost ``| provider |`` column.
    - ``render_json`` bumps to schema_version 2; each ``assertions[]``
      entry carries ``provider``; top-level ``providers_seen`` is sorted
      alphabetically.
    Sort order across all three: ``(provider, layer, id)``.

    Traces to: DEC-004, DEC-005, DEC-010.
    """

    def _mixed_verdicts(self) -> list[AuditVerdict]:
        """Two aggregates sharing ``(L3, x)`` under different providers,
        plus an ``(L1, ant_only)`` under anthropic only."""
        ant_l3 = AuditAggregate(
            layer="L3",
            id="x",
            total_with_runs=10,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="anthropic",
        )
        openai_l3 = AuditAggregate(
            layer="L3",
            id="x",
            total_with_runs=10,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="openai",
        )
        ant_l1 = AuditAggregate(
            layer="L1",
            id="ant_only",
            total_with_runs=5,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="anthropic",
        )
        return apply_thresholds(
            {
                ("openai", "L3", "x"): openai_l3,
                ("anthropic", "L3", "x"): ant_l3,
                ("anthropic", "L1", "ant_only"): ant_l1,
            },
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )

    def _single_provider_verdicts(self) -> list[AuditVerdict]:
        """Single-provider history: only anthropic rows."""
        ant = AuditAggregate(
            layer="L3",
            id="x",
            total_with_runs=5,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="anthropic",
        )
        return apply_thresholds(
            {("anthropic", "L3", "x"): ant},
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )

    def test_render_json_v2_schema_version_first_key(self) -> None:
        """Acceptance criterion 1: ``schema_version`` is the first key
        and equals 2 (DEC-005)."""
        payload = render_json(
            self._single_provider_verdicts(),
            skill="s",
            iterations_analyzed=5,
            thresholds={"last": 5},
            timestamp="t",
        )
        first_key = next(iter(payload.keys()))
        assert first_key == "schema_version"
        assert payload["schema_version"] == 2

    def test_render_json_v2_includes_provider_per_assertion(self) -> None:
        """Acceptance criterion 2: every ``assertions[]`` entry carries
        ``provider``."""
        payload = render_json(
            self._mixed_verdicts(),
            skill="s",
            iterations_analyzed=10,
            thresholds={"last": 10},
            timestamp="t",
        )
        assert len(payload["assertions"]) == 3
        for entry in payload["assertions"]:
            assert "provider" in entry
            assert entry["provider"] in {"anthropic", "openai"}

    def test_render_json_v2_includes_providers_seen_sorted(self) -> None:
        """Acceptance criteria 3 + 5: ``providers_seen`` is at the top
        level, sorted alphabetically; mixed history yields
        ``["anthropic", "openai"]``."""
        payload = render_json(
            self._mixed_verdicts(),
            skill="s",
            iterations_analyzed=10,
            thresholds={"last": 10},
            timestamp="t",
        )
        assert "providers_seen" in payload
        assert payload["providers_seen"] == ["anthropic", "openai"]

    def test_render_json_v2_providers_seen_sorted_not_insertion_order(
        self,
    ) -> None:
        """Stronger sort guard: feed providers whose alphabetical order
        differs from insertion order. ``["anthropic", "openai"]`` is
        tautological (already alphabetical = insertion order on
        CPython sets), so a regression that returned ``list(set(...))``
        would still pass that test. This pins true alphabetical sort.
        """
        agg_factory = lambda layer, rid, prov: AuditAggregate(  # noqa: E731
            layer=layer,
            id=rid,
            total_with_runs=5,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider=prov,
        )
        verdicts = apply_thresholds(
            {
                ("zebra", "L3", "x"): agg_factory("L3", "x", "zebra"),
                ("openai", "L3", "x"): agg_factory("L3", "x", "openai"),
                ("alpha", "L3", "x"): agg_factory("L3", "x", "alpha"),
                ("anthropic", "L3", "x"): agg_factory("L3", "x", "anthropic"),
            },
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )
        payload = render_json(
            verdicts,
            skill="s",
            iterations_analyzed=10,
            thresholds={"last": 10},
            timestamp="t",
        )
        assert payload["providers_seen"] == [
            "alpha",
            "anthropic",
            "openai",
            "zebra",
        ]

    def test_render_json_v2_single_provider_seen(self) -> None:
        """Acceptance criterion 4: single-provider history →
        ``providers_seen == ["anthropic"]``."""
        payload = render_json(
            self._single_provider_verdicts(),
            skill="s",
            iterations_analyzed=5,
            thresholds={"last": 5},
            timestamp="t",
        )
        assert payload["providers_seen"] == ["anthropic"]

    def test_render_json_v2_empty_providers_seen_when_no_verdicts(
        self,
    ) -> None:
        """Edge case: zero verdicts → empty ``providers_seen``."""
        payload = render_json(
            [],
            skill="s",
            iterations_analyzed=0,
            thresholds={"last": 5},
            timestamp="t",
        )
        assert payload["providers_seen"] == []
        assert payload["assertions"] == []

    def test_render_json_assertions_sorted_by_provider_layer_id(
        self,
    ) -> None:
        """``assertions[]`` is sorted by ``(provider, layer, id)`` so
        anthropic rows render before openai, then by layer, then id."""
        payload = render_json(
            self._mixed_verdicts(),
            skill="s",
            iterations_analyzed=10,
            thresholds={"last": 10},
            timestamp="t",
        )
        keys = [
            (e["provider"], e["layer"], e["id"])
            for e in payload["assertions"]
        ]
        assert keys == sorted(keys)
        # Concretely: anthropic L1 < anthropic L3 < openai L3.
        assert keys == [
            ("anthropic", "L1", "ant_only"),
            ("anthropic", "L3", "x"),
            ("openai", "L3", "x"),
        ]

    def test_render_stdout_table_has_provider_column(self) -> None:
        """Acceptance criterion 6: ``PROVIDER`` column header + one row
        per ``(provider, layer, id)``."""
        table = render_stdout_table(self._mixed_verdicts())
        # Header has PROVIDER as the leftmost column.
        first_line = table.splitlines()[0]
        assert first_line.startswith("PROVIDER")
        assert "PROVIDER" in table
        assert "anthropic" in table
        assert "openai" in table

    def test_render_stdout_table_sorted_provider_layer_id(self) -> None:
        """Anthropic rows render before openai; layer + id within."""
        table = render_stdout_table(self._mixed_verdicts())
        body_lines = [
            line
            for line in table.splitlines()
            if line and not line.startswith(("PROVIDER", "-"))
        ]
        # The anthropic L1 row should come before the anthropic L3 row,
        # which should come before the openai L3 row.
        ant_l1_idx = next(
            i for i, ln in enumerate(body_lines)
            if "ant_only" in ln and "anthropic" in ln
        )
        openai_idx = next(
            i for i, ln in enumerate(body_lines) if "openai" in ln
        )
        assert ant_l1_idx < openai_idx

    def test_render_markdown_has_provider_column(self) -> None:
        """Acceptance criterion 7: per-layer markdown table has
        ``| provider |`` as the first column."""
        md = render_markdown(
            self._mixed_verdicts(),
            skill="s",
            iterations_analyzed=10,
            thresholds={"last": 10},
            timestamp="t",
        )
        assert "| provider |" in md
        # Header column ordering: provider must come before id.
        header_line = next(
            line for line in md.splitlines()
            if line.startswith("| provider |")
        )
        assert header_line.index("provider") < header_line.index("id")

    def test_render_markdown_renders_provider_value_in_row(self) -> None:
        """Mixed-provider markdown surfaces both ``anthropic`` and
        ``openai`` in row cells under L3 detail."""
        md = render_markdown(
            self._mixed_verdicts(),
            skill="s",
            iterations_analyzed=10,
            thresholds={"last": 10},
            timestamp="t",
        )
        # Both providers appear inside backtick-quoted cells under
        # the L3 detail table.
        assert "`anthropic`" in md
        assert "`openai`" in md

    def test_render_stdout_table_truncates_long_provider(self) -> None:
        """Provider column is ~11 chars wide; longer strings are
        truncated to keep the column-aligned layout."""
        long_provider_agg = AuditAggregate(
            layer="L3",
            id="x",
            total_with_runs=5,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
            provider="a" * 30,
        )
        verdicts = apply_thresholds(
            {("a" * 30, "L3", "x"): long_provider_agg},
            min_fail_rate=0.0,
            min_discrimination=0.05,
        )
        table = render_stdout_table(verdicts)
        body = table.splitlines()[2]
        # Only 11 a's appear in the leftmost column slice.
        assert body.startswith("a" * 11)
        # And there is whitespace after — the column is bounded.
        assert body[11] == " "


# --------------------------------------------------------------------------- #
# #154 US-005 — context.json sidecar reading + verbose render                  #
# --------------------------------------------------------------------------- #


def _write_context_sidecar(
    skill_dir: Path,
    *,
    schema_version: int = 1,
    harness: str = "claude-code",
    provider: str | None = "anthropic",
    model_runner: str = "claude-sonnet-4-6",
    model_grader: str | None = "claude-sonnet-4-6",
    system_prompt_source: str = "explicit",
    sandbox_mode: str | None = "workspace-write",
    reasoning_tokens: int | None = None,
    cost_usd: float | None = None,
) -> None:
    """Write a synthetic ``context.json`` sidecar under ``skill_dir``.

    Helper for #154 US-005 tests; mirrors the ``_write_grading_v3`` /
    ``_write_extraction_v3`` shape above.
    """
    payload: dict = {
        "schema_version": schema_version,
        "harness": harness,
        "provider": provider,
        "model_runner": model_runner,
        "model_grader": model_grader,
        "system_prompt_source": system_prompt_source,
        "sandbox_mode": sandbox_mode,
        "reasoning_tokens": reasoning_tokens,
        "cost_usd": cost_usd,
    }
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "context.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )


class TestMaxSchemaVersion:
    """#154 US-005 / DEC-010: ``context.json`` registered at v1 in the
    canonical ``MAX_SCHEMA_VERSION`` map."""

    def test_context_json_registered_at_v1(self) -> None:
        from clauditor.audit import MAX_SCHEMA_VERSION, _is_accepted_version

        assert MAX_SCHEMA_VERSION["context.json"] == 1
        assert _is_accepted_version("context.json", 1) is True

    def test_context_json_v999_rejected(self) -> None:
        from clauditor.audit import _is_accepted_version

        assert _is_accepted_version("context.json", 999) is False
        assert _is_accepted_version("context.json", 2) is False


class TestReadContext:
    """#154 US-005 / DEC-011: pure helper that reads + schema-validates
    ``context.json``. Returns ``IterationContext`` on success, ``None``
    on every failure mode (missing file silently; schema/validation
    errors with stderr warning)."""

    def test_returns_iteration_context_for_valid_sidecar(
        self, tmp_path: Path
    ) -> None:
        from clauditor.audit import _read_context

        skill_dir = tmp_path / "skill"
        _write_context_sidecar(
            skill_dir,
            harness="codex",
            provider="openai",
            model_runner="gpt-5.4",
            model_grader=None,
            system_prompt_source="agents_md",
            sandbox_mode="read-only",
            reasoning_tokens=42,
            cost_usd=0.0123,
        )
        ctx = _read_context(skill_dir)
        assert ctx is not None
        assert ctx.harness == "codex"
        assert ctx.provider == "openai"
        assert ctx.model_runner == "gpt-5.4"
        assert ctx.model_grader is None
        assert ctx.system_prompt_source == "agents_md"
        assert ctx.sandbox_mode == "read-only"
        assert ctx.reasoning_tokens == 42
        assert ctx.cost_usd == pytest.approx(0.0123)

    def test_returns_none_on_missing_file_silent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from clauditor.audit import _read_context

        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        # No context.json written.
        assert _read_context(skill_dir) is None
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_returns_none_with_stderr_warning_on_schema_mismatch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from clauditor.audit import _read_context

        skill_dir = tmp_path / "skill"
        _write_context_sidecar(skill_dir, schema_version=999)
        result = _read_context(skill_dir)
        assert result is None
        captured = capsys.readouterr()
        assert "context.json" in captured.err
        assert "999" in captured.err

    def test_returns_none_on_malformed_json(self, tmp_path: Path) -> None:
        from clauditor.audit import _read_context

        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "context.json").write_text("{ this is not json")
        assert _read_context(skill_dir) is None

    def test_returns_none_on_hard_validator_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """``IterationContext.from_dict`` raises ``ValueError`` for an
        unknown ``harness`` literal (per US-001 hard-validators).
        ``_read_context`` translates that to ``None`` plus a stderr
        warning rather than propagating."""
        from clauditor.audit import _read_context

        skill_dir = tmp_path / "skill"
        _write_context_sidecar(skill_dir, harness="not-a-real-harness")
        assert _read_context(skill_dir) is None
        captured = capsys.readouterr()
        assert "context.json" in captured.err
        assert "malformed payload" in captured.err

    def test_returns_none_when_top_level_is_not_dict(
        self, tmp_path: Path
    ) -> None:
        """A JSON file containing a list/scalar at the top level is
        rejected without raising — defensive read posture per
        ``stream-json-schema.md``."""
        from clauditor.audit import _read_context

        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "context.json").write_text("[1, 2, 3]")
        assert _read_context(skill_dir) is None


class TestRenderJsonContext:
    """#154 US-005 / DEC-005: ``render_json`` always emits
    ``iteration_contexts`` (no ``--verbose`` gate on JSON output)."""

    def _verdicts(self) -> list[AuditVerdict]:
        agg = AuditAggregate(
            layer="L1",
            id="a",
            total_with_runs=2,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
        )
        return [AuditVerdict(layer="L1", id="a", verdict=Verdict.KEEP, aggregate=agg)]

    def test_always_includes_iteration_contexts_key(self) -> None:
        payload = render_json(
            self._verdicts(),
            skill="s",
            iterations_analyzed=0,
            thresholds={"last": 20},
            timestamp="t",
        )
        assert "iteration_contexts" in payload
        assert payload["iteration_contexts"] == []

    def test_legacy_iteration_emits_null_context(self) -> None:
        contexts: dict[int, IterationContext | None] = {3: None}
        payload = render_json(
            self._verdicts(),
            skill="s",
            iterations_analyzed=1,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts=contexts,
        )
        assert payload["iteration_contexts"] == [
            {"iteration": 3, "context": None}
        ]

    def test_populated_context_serializes_full_field_set(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "skill"
        _write_context_sidecar(
            skill_dir,
            harness="codex",
            provider="openai",
            model_runner="gpt-5.4",
            model_grader="gpt-5.4",
            system_prompt_source="explicit",
            sandbox_mode="workspace-write",
            reasoning_tokens=100,
            cost_usd=0.5,
        )
        from clauditor.audit import _read_context

        ctx = _read_context(skill_dir)
        payload = render_json(
            self._verdicts(),
            skill="s",
            iterations_analyzed=1,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts={5: ctx},
        )
        assert payload["iteration_contexts"] == [
            {
                "iteration": 5,
                "context": {
                    "harness": "codex",
                    "provider": "openai",
                    "model_runner": "gpt-5.4",
                    "model_grader": "gpt-5.4",
                    "system_prompt_source": "explicit",
                    "sandbox_mode": "workspace-write",
                    "reasoning_tokens": 100,
                    "cost_usd": 0.5,
                },
            }
        ]

    def test_iteration_contexts_sorted_descending(
        self, tmp_path: Path
    ) -> None:
        contexts: dict[int, IterationContext | None] = {1: None, 5: None, 3: None}
        payload = render_json(
            self._verdicts(),
            skill="s",
            iterations_analyzed=3,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts=contexts,
        )
        nums = [entry["iteration"] for entry in payload["iteration_contexts"]]
        assert nums == [5, 3, 1]


class TestRenderMarkdownVerbose:
    """#154 US-005 / DEC-005: ``render_markdown`` emits a
    per-iteration ``## Per-iteration context`` section ONLY under
    ``verbose=True``."""

    def _verdicts(self) -> list[AuditVerdict]:
        agg = AuditAggregate(
            layer="L1",
            id="a",
            total_with_runs=1,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
        )
        return [AuditVerdict(layer="L1", id="a", verdict=Verdict.KEEP, aggregate=agg)]

    def _make_ctx(self) -> IterationContext:
        return IterationContext(
            harness="codex",
            provider="openai",
            model_runner="gpt-5.4",
            model_grader="gpt-5.4",
            system_prompt_source="agents_md",
            sandbox_mode="read-only",
            reasoning_tokens=42,
            cost_usd=0.01,
        )

    def test_per_iteration_block_under_verbose(self) -> None:
        markdown = render_markdown(
            self._verdicts(),
            skill="s",
            iterations_analyzed=1,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts={7: self._make_ctx()},
            verbose=True,
        )
        assert "## Per-iteration context" in markdown
        assert "### Iteration 7" in markdown
        assert "harness" in markdown
        assert "codex" in markdown
        assert "gpt-5.4" in markdown
        assert "agents_md" in markdown

    def test_no_block_without_verbose(self) -> None:
        markdown = render_markdown(
            self._verdicts(),
            skill="s",
            iterations_analyzed=1,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts={7: self._make_ctx()},
            verbose=False,
        )
        assert "Per-iteration context" not in markdown
        assert "Iteration 7" not in markdown

    def test_no_block_when_contexts_dict_is_none(self) -> None:
        markdown = render_markdown(
            self._verdicts(),
            skill="s",
            iterations_analyzed=1,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts=None,
            verbose=True,
        )
        assert "Per-iteration context" not in markdown

    def test_legacy_only_iterations_skipped_under_verbose(self) -> None:
        """Iterations with ``context = None`` (pre-#154) do NOT
        produce an ``### Iteration N`` block — verbose markdown stays
        readable."""
        markdown = render_markdown(
            self._verdicts(),
            skill="s",
            iterations_analyzed=1,
            thresholds={"last": 20},
            timestamp="t",
            iteration_contexts={3: None},
            verbose=True,
        )
        assert "Per-iteration context" not in markdown
        assert "Iteration 3" not in markdown


class TestRenderStdoutTableVerbose:
    """#154 US-005 / DEC-005: ``render_stdout_table`` emits a per-
    iteration ``Context for iteration N:`` block ONLY under
    ``verbose=True``."""

    def _verdicts(self) -> list[AuditVerdict]:
        agg = AuditAggregate(
            layer="L1",
            id="a",
            total_with_runs=1,
            with_fails=0,
            with_pass_rate=1.0,
            total_baseline_runs=0,
            baseline_fails=0,
            baseline_pass_rate=None,
        )
        return [AuditVerdict(layer="L1", id="a", verdict=Verdict.KEEP, aggregate=agg)]

    def _make_ctx(self) -> IterationContext:
        return IterationContext(
            harness="claude-code",
            provider="anthropic",
            model_runner="claude-sonnet-4-6",
            model_grader="claude-sonnet-4-6",
            system_prompt_source="explicit",
            sandbox_mode="workspace-write",
        )

    def test_per_iteration_block_under_verbose(self) -> None:
        out = render_stdout_table(
            self._verdicts(),
            iteration_contexts={3: self._make_ctx()},
            verbose=True,
        )
        assert "Context for iteration 3:" in out
        assert "harness: claude-code" in out
        assert "provider: anthropic" in out
        assert "system_prompt_source: explicit" in out

    def test_no_block_without_verbose(self) -> None:
        out = render_stdout_table(
            self._verdicts(),
            iteration_contexts={3: self._make_ctx()},
            verbose=False,
        )
        assert "Context for iteration" not in out

    def test_legacy_only_iterations_skipped_under_verbose(self) -> None:
        out = render_stdout_table(
            self._verdicts(),
            iteration_contexts={3: None, 1: None},
            verbose=True,
        )
        assert "Context for iteration" not in out


class TestAggregateUnchanged:
    """#154 DEC-011 regression guard: ``IterationRecord`` does NOT carry
    a ``context`` field. Context lives parallel to records, attached at
    render time only."""

    def test_iteration_record_has_no_context_field(self) -> None:
        import dataclasses

        names = {f.name for f in dataclasses.fields(IterationRecord)}
        assert "context" not in names
        # Sanity check that we still own the existing fields so the
        # test does not become vacuous if the dataclass is renamed.
        assert {"iteration", "layer", "id", "passed"}.issubset(names)


class TestLoadIterationsContextDict:
    """#154 US-005 / DEC-011: ``load_iterations`` returns a
    ``dict[int, IterationContext | None]`` parallel to the records."""

    def test_returns_context_for_iteration_with_sidecar(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {1: {"l1": [{"id": "a", "passed": True}]}},
        )
        skill_dir = clauditor_dir / "iteration-1" / "s"
        _write_context_sidecar(
            skill_dir,
            harness="codex",
            provider="openai",
            model_runner="gpt-5.4",
            model_grader=None,
            system_prompt_source="agents_md",
            sandbox_mode="read-only",
        )
        records, _, contexts = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert len(records) == 1
        assert 1 in contexts
        ctx = contexts[1]
        assert ctx is not None
        assert ctx.harness == "codex"

    def test_returns_none_for_iteration_without_sidecar(
        self, tmp_path: Path
    ) -> None:
        """Pre-#154 iterations (no ``context.json`` on disk) map to
        ``None`` in the contexts dict so renderers can emit a uniform
        shape."""
        clauditor_dir = _make_iteration_fixture(
            tmp_path,
            "s",
            {1: {"l1": [{"id": "a", "passed": True}]}},
        )
        _, _, contexts = load_iterations(
            "s", last=5, clauditor_dir=clauditor_dir
        )
        assert contexts == {1: None}


class TestCmdAuditVerboseFlag:
    """#154 US-005 argparse smoke test: ``--verbose`` is a valid flag
    on ``clauditor audit``."""

    def test_verbose_flag_accepted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        clauditor_dir = _make_iteration_fixture(
            project,
            "my_skill",
            {1: {"l1": [{"id": "a", "passed": True}]}},
        )
        skill_dir = clauditor_dir / "iteration-1" / "my_skill"
        _write_context_sidecar(skill_dir)
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "my_skill", "--verbose"])
        # --verbose is accepted; ``always_pass`` flagging still drives
        # the exit code.
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "Context for iteration 1:" in out

    def test_verbose_emits_per_iteration_block_in_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """JSON output emits ``iteration_contexts`` regardless of the
        ``--verbose`` flag (per DEC-005 — JSON consumers should not
        need a flag for a stable field)."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        clauditor_dir = _make_iteration_fixture(
            project,
            "my_skill",
            {1: {"l1": [{"id": "a", "passed": True}]}},
        )
        skill_dir = clauditor_dir / "iteration-1" / "my_skill"
        _write_context_sidecar(skill_dir)
        monkeypatch.chdir(project)
        rc = cli_main(["audit", "my_skill", "--json"])
        assert rc in (0, 1)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert "iteration_contexts" in payload
        assert any(
            entry["iteration"] == 1 and entry["context"] is not None
            for entry in payload["iteration_contexts"]
        )
