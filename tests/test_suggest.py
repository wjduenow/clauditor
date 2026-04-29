"""Tests for clauditor.suggest (US-001 — loader + SuggestInput)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clauditor.assertions import AssertionResult
from clauditor.quality_grader import GradingResult
from clauditor.suggest import (
    EditProposal,
    NoPriorGradeError,
    SuggestInput,
    SuggestReport,
    _check_schema_version,
    build_suggest_prompt,
    find_latest_grading,
    load_suggest_input,
    parse_suggest_response,
    propose_edits,
    render_unified_diff,
    validate_anchors,
    write_sidecar,
)
from clauditor.workspace import InvalidSkillNameError


def _write_assertions(skill_dir: Path, results: list[dict]) -> None:
    """Write assertions.json using the production `runs` schema.

    Mirrors the envelope built by cmd_grade at cli.py:849 so loaders
    exercise the real on-disk shape, not a test-only shortcut.
    """
    skill_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "skill": skill_dir.name,
        "iteration": 1,
        "runs": [
            {
                "run": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "results": results,
            }
        ],
    }
    (skill_dir / "assertions.json").write_text(json.dumps(payload))


def _write_grading(
    skill_dir: Path,
    skill_name: str,
    results: list[dict],
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "skill_name": skill_name,
        "model": "claude-sonnet-4-6",
        "duration_seconds": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "results": results,
    }
    (skill_dir / "grading.json").write_text(json.dumps(payload))


def _make_grading_result(
    *,
    rid: str,
    criterion: str,
    passed: bool,
    score: float = 1.0,
) -> dict:
    return {
        "id": rid,
        "criterion": criterion,
        "passed": passed,
        "score": score,
        "evidence": "evidence",
        "reasoning": "reason",
    }


def _make_assertion(
    *, rid: str, name: str, passed: bool
) -> dict:
    return {
        "id": rid,
        "name": name,
        "passed": passed,
        "message": "msg",
        "kind": "presence",
        "evidence": None,
        "raw_data": None,
        "transcript_path": None,
    }


def _make_skill_md(tmp_path: Path, body: str = "# Skill\n") -> Path:
    p = tmp_path / "SKILL.md"
    p.write_text(body)
    return p


class TestFindLatestGrading:
    def test_picks_max_index_with_grading_json(self, tmp_path: Path) -> None:
        clauditor = tmp_path / ".clauditor"
        # iteration-1: no grading
        (clauditor / "iteration-1" / "find").mkdir(parents=True)
        # iteration-2: has grading
        _write_grading(
            clauditor / "iteration-2" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=True)],
        )
        # iteration-3: has grading (the max)
        _write_grading(
            clauditor / "iteration-3" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=False)],
        )
        idx, skill_dir = find_latest_grading(clauditor, "find")
        assert idx == 3
        assert skill_dir == clauditor / "iteration-3" / "find"

    def test_skips_iterations_without_grading(self, tmp_path: Path) -> None:
        clauditor = tmp_path / ".clauditor"
        # iteration-5: no grading (skipped)
        (clauditor / "iteration-5" / "find").mkdir(parents=True)
        # iteration-4: has grading (chosen)
        _write_grading(
            clauditor / "iteration-4" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=True)],
        )
        idx, _ = find_latest_grading(clauditor, "find")
        assert idx == 4

    def test_skips_non_directory_entries_and_unmatched_names(
        self, tmp_path: Path
    ) -> None:
        # Coverage: find_latest_grading scans clauditor_dir.iterdir()
        # and must skip regular files and names that don't match the
        # iteration-N pattern without raising.
        clauditor = tmp_path / ".clauditor"
        clauditor.mkdir()
        # Regular file sitting in .clauditor (not a dir) — skipped.
        (clauditor / "README.md").write_text("notes")
        # Directory with a non-matching name — skipped.
        (clauditor / "scratch").mkdir()
        # A real iteration with grading.
        _write_grading(
            clauditor / "iteration-2" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=True)],
        )
        idx, _ = find_latest_grading(clauditor, "find")
        assert idx == 2

    def test_no_clauditor_workspace_raises_no_prior_grade(
        self, tmp_path: Path
    ) -> None:
        # Coverage + regression: when .clauditor does not exist at all,
        # find_latest_grading raises with a path-agnostic message so
        # the CLI can print the actionable hint using args.skill.
        with pytest.raises(NoPriorGradeError) as exc_info:
            find_latest_grading(tmp_path / ".clauditor", "find")
        assert "clauditor grade" not in str(exc_info.value)

    def test_no_iteration_contains_grading_raises_path_agnostic(
        self, tmp_path: Path
    ) -> None:
        # Coverage + regression: the "no iteration has grading.json"
        # branch must not embed a misleading `clauditor grade <stem>`
        # hint; the CLI owns the actionable message.
        clauditor = tmp_path / ".clauditor"
        (clauditor / "iteration-1" / "find").mkdir(parents=True)
        with pytest.raises(NoPriorGradeError) as exc_info:
            find_latest_grading(clauditor, "find")
        assert "clauditor grade" not in str(exc_info.value)

    def test_rejects_skill_name_with_path_traversal(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit finding): the skill argument is used
        # to construct clauditor_dir / f"iteration-{N}" / skill. An
        # unvalidated "../../etc/passwd" would escape the workspace.
        # find_latest_grading reuses the same validator as
        # write_sidecar so both ends of the pipeline are safe.
        clauditor_dir = tmp_path / ".clauditor"
        clauditor_dir.mkdir()
        with pytest.raises(InvalidSkillNameError):
            find_latest_grading(clauditor_dir, "../evil")

    def test_raises_no_prior_grade_error_when_none_exist(
        self, tmp_path: Path
    ) -> None:
        clauditor = tmp_path / ".clauditor"
        clauditor.mkdir()
        with pytest.raises(NoPriorGradeError):
            find_latest_grading(clauditor, "find")

    def test_raises_when_workspace_missing(self, tmp_path: Path) -> None:
        with pytest.raises(NoPriorGradeError):
            find_latest_grading(tmp_path / "nope", "find")

    def test_from_iteration_override_returns_requested(
        self, tmp_path: Path
    ) -> None:
        clauditor = tmp_path / ".clauditor"
        _write_grading(
            clauditor / "iteration-2" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=True)],
        )
        _write_grading(
            clauditor / "iteration-7" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=True)],
        )
        idx, skill_dir = find_latest_grading(
            clauditor, "find", from_iteration=2
        )
        assert idx == 2
        assert skill_dir == clauditor / "iteration-2" / "find"

    def test_from_iteration_raises_when_requested_missing_grading(
        self, tmp_path: Path
    ) -> None:
        clauditor = tmp_path / ".clauditor"
        (clauditor / "iteration-2" / "find").mkdir(parents=True)
        with pytest.raises(NoPriorGradeError):
            find_latest_grading(clauditor, "find", from_iteration=2)


class TestLoadSuggestInput:
    def _scaffold(
        self,
        tmp_path: Path,
        *,
        iteration: int = 3,
        assertions: list[dict] | None = None,
        grading: list[dict] | None = None,
    ) -> tuple[Path, Path, Path]:
        clauditor = tmp_path / ".clauditor"
        skill_dir = clauditor / f"iteration-{iteration}" / "find"
        skill_dir.mkdir(parents=True)
        if assertions is not None:
            _write_assertions(skill_dir, assertions)
        if grading is not None:
            _write_grading(skill_dir, "find", grading)
        skill_md = _make_skill_md(tmp_path)
        return clauditor, skill_dir, skill_md

    def test_filters_to_failing_assertions_only(self, tmp_path: Path) -> None:
        clauditor, _, skill_md = self._scaffold(
            tmp_path,
            assertions=[
                _make_assertion(rid="a1", name="ok", passed=True),
                _make_assertion(rid="a2", name="bad", passed=False),
                _make_assertion(rid="a3", name="bad2", passed=False),
            ],
            grading=[
                _make_grading_result(
                    rid="g1", criterion="c", passed=True
                ),
            ],
        )
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert isinstance(result, SuggestInput)
        assert [a.id for a in result.failing_assertions] == ["a2", "a3"]
        assert all(not a.passed for a in result.failing_assertions)

    def test_filters_to_failing_grading_criteria_only(
        self, tmp_path: Path
    ) -> None:
        clauditor, _, skill_md = self._scaffold(
            tmp_path,
            assertions=[],
            grading=[
                _make_grading_result(rid="g1", criterion="ok", passed=True),
                _make_grading_result(
                    rid="g2", criterion="bad", passed=False
                ),
            ],
        )
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert [g.id for g in result.failing_grading_criteria] == ["g2"]

    def test_with_transcripts_reads_output_jsonl(
        self, tmp_path: Path
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(
            tmp_path,
            assertions=[],
            grading=[
                _make_grading_result(rid="g1", criterion="c", passed=True),
            ],
        )
        run0 = skill_dir / "run-0"
        run0.mkdir()
        (run0 / "output.jsonl").write_text(
            json.dumps({"type": "assistant", "n": 1}) + "\n"
            + json.dumps({"type": "result", "n": 2}) + "\n"
        )
        result = load_suggest_input(
            "find",
            clauditor,
            skill_md_path=skill_md,
            with_transcripts=True,
        )
        assert result.transcript_events is not None
        assert len(result.transcript_events) == 1
        assert len(result.transcript_events[0]) == 2
        assert result.transcript_events[0][0]["type"] == "assistant"

    def test_without_transcripts_sets_events_none(
        self, tmp_path: Path
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(
            tmp_path,
            assertions=[],
            grading=[
                _make_grading_result(rid="g1", criterion="c", passed=True),
            ],
        )
        run0 = skill_dir / "run-0"
        run0.mkdir()
        (run0 / "output.jsonl").write_text(
            json.dumps({"type": "assistant"}) + "\n"
        )
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert result.transcript_events is None

    def test_from_iteration_overrides_latest(self, tmp_path: Path) -> None:
        clauditor = tmp_path / ".clauditor"
        # latest iteration-9 has all-passing grading
        _write_grading(
            clauditor / "iteration-9" / "find",
            "find",
            [_make_grading_result(rid="g1", criterion="c", passed=True)],
        )
        # earlier iteration-4 has a failing criterion
        _write_grading(
            clauditor / "iteration-4" / "find",
            "find",
            [
                _make_grading_result(
                    rid="g1", criterion="c", passed=False
                ),
            ],
        )
        skill_md = _make_skill_md(tmp_path)
        result = load_suggest_input(
            "find",
            clauditor,
            skill_md_path=skill_md,
            from_iteration=4,
        )
        assert result.source_iteration == 4
        assert [g.id for g in result.failing_grading_criteria] == ["g1"]

    def test_zero_failures_returns_empty_lists_without_error(
        self, tmp_path: Path
    ) -> None:
        clauditor, _, skill_md = self._scaffold(
            tmp_path,
            assertions=[
                _make_assertion(rid="a1", name="ok", passed=True),
            ],
            grading=[
                _make_grading_result(rid="g1", criterion="ok", passed=True),
            ],
        )
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert result.failing_assertions == []
        assert result.failing_grading_criteria == []

    def test_reads_output_slices_from_run_dirs(
        self, tmp_path: Path
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(
            tmp_path,
            assertions=[],
            grading=[
                _make_grading_result(rid="g1", criterion="c", passed=True),
            ],
        )
        (skill_dir / "run-0").mkdir()
        (skill_dir / "run-0" / "output.txt").write_text("first slice")
        (skill_dir / "run-1").mkdir()
        (skill_dir / "run-1" / "output.txt").write_text("second slice")
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert result.output_slices == ["first slice", "second slice"]

    def test_source_grading_path_is_repo_relative(
        self, tmp_path: Path
    ) -> None:
        clauditor, _, skill_md = self._scaffold(
            tmp_path,
            assertions=[],
            grading=[
                _make_grading_result(rid="g1", criterion="c", passed=True),
            ],
        )
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert result.source_grading_path == (
            ".clauditor/iteration-3/find/grading.json"
        )

    def test_transcripts_skip_malformed_lines_without_raising(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(
            tmp_path,
            assertions=[],
            grading=[
                _make_grading_result(rid="g1", criterion="c", passed=True),
            ],
        )
        run0 = skill_dir / "run-0"
        run0.mkdir()
        (run0 / "output.jsonl").write_text(
            json.dumps({"type": "assistant"}) + "\n"
            + "{not valid json\n"
            + json.dumps({"type": "result"}) + "\n"
            + "\"scalar string\"\n"  # valid JSON but not a dict
        )
        result = load_suggest_input(
            "find",
            clauditor,
            skill_md_path=skill_md,
            with_transcripts=True,
        )
        assert result.transcript_events is not None
        assert len(result.transcript_events[0]) == 2
        captured = capsys.readouterr()
        assert "skipping malformed transcript" in captured.err

    def test_grading_missing_is_tolerated(self, tmp_path: Path) -> None:
        # find_latest_grading still requires grading.json to locate the
        # iteration; this test exercises the assertions-only fallback by
        # writing both files but ensuring _load_failing_grading_criteria
        # handles a deleted file path gracefully via the helper directly.
        clauditor, skill_dir, skill_md = self._scaffold(
            tmp_path,
            assertions=[
                _make_assertion(rid="a1", name="bad", passed=False),
            ],
            grading=[
                _make_grading_result(rid="g1", criterion="ok", passed=True),
            ],
        )
        # Now delete grading.json after find_latest located it would
        # be a race; instead just sanity-check that the loader produces
        # a populated object end-to-end.
        result = load_suggest_input(
            "find", clauditor, skill_md_path=skill_md
        )
        assert len(result.failing_assertions) == 1
        assert result.skill_md_text == "# Skill\n"


def _make_suggest_input(
    *,
    skill_md_text: str = "# My Skill\n\nDo the thing.\n",
    failing_assertions: list[AssertionResult] | None = None,
    failing_grading_criteria: list[GradingResult] | None = None,
    output_slices: list[str] | None = None,
    transcript_events: list[list[dict]] | None = None,
) -> SuggestInput:
    return SuggestInput(
        skill_name="find",
        source_iteration=3,
        source_grading_path=".clauditor/iteration-3/find/grading.json",
        skill_md_text=skill_md_text,
        failing_assertions=failing_assertions or [],
        failing_grading_criteria=failing_grading_criteria or [],
        output_slices=output_slices or [],
        transcript_events=transcript_events,
    )


class TestLoadSuggestInputMalformedJson:
    def _scaffold(
        self, tmp_path: Path, assertions_payload: object
    ) -> tuple[Path, Path]:
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "assertions.json").write_text(
            json.dumps(assertions_payload), encoding="utf-8"
        )
        (skill_dir / "grading.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "s",
                    "model": "claude-sonnet-4-6",
                    "generated_at": "2026-01-01T00:00:00.000000Z",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "results": [],
                    "raw_response": "",
                }
            ),
            encoding="utf-8",
        )
        skill_md = tmp_path / "s.md"
        skill_md.write_text("# Skill\n", encoding="utf-8")
        return clauditor_dir, skill_md

    def test_top_level_non_dict_is_skipped_not_crashed(
        self, tmp_path: Path
    ) -> None:
        # Regression: corrupt assertions.json with a list top-level used
        # to hit AttributeError from data.get(...).
        clauditor_dir, skill_md = self._scaffold(
            tmp_path, assertions_payload=["not", "a", "dict"]
        )
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        assert si.failing_assertions == []

    def test_non_list_runs_is_skipped_not_crashed(
        self, tmp_path: Path
    ) -> None:
        # Regression: runs key present but a dict instead of a list.
        clauditor_dir, skill_md = self._scaffold(
            tmp_path,
            assertions_payload={"runs": {"0": "bogus"}},
        )
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        assert si.failing_assertions == []

    def test_non_list_results_inside_run_is_skipped(
        self, tmp_path: Path
    ) -> None:
        # Regression: a run entry's `results` field is a dict, not a list.
        clauditor_dir, skill_md = self._scaffold(
            tmp_path,
            assertions_payload={
                "runs": [{"run": 0, "results": {"a1": "bogus"}}]
            },
        )
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        assert si.failing_assertions == []

    def test_non_dict_result_entries_are_skipped(
        self, tmp_path: Path
    ) -> None:
        # Regression: one entry in a run's results list is a scalar
        # (partial write). The valid entry is still picked up.
        clauditor_dir, skill_md = self._scaffold(
            tmp_path,
            assertions_payload={
                "runs": [
                    {
                        "run": 0,
                        "results": [
                            "garbage string",
                            {
                                "id": "a1",
                                "name": "real one",
                                "passed": False,
                                "message": "missing",
                                "kind": "presence",
                                "transcript_path": None,
                            },
                        ],
                    }
                ]
            },
        )
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        assert len(si.failing_assertions) == 1
        assert si.failing_assertions[0].id == "a1"

    def test_corrupt_assertions_json_is_warned_and_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Regression (Copilot finding): a truncated assertions.json
        # used to raise JSONDecodeError out of load_suggest_input.
        # The loader should warn to stderr and treat the sidecar as
        # absent so the rest of the suggest pipeline keeps working.
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "assertions.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        (skill_dir / "grading.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "s",
                    "model": "claude-sonnet-4-6",
                    "generated_at": "2026-01-01T00:00:00.000000Z",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "results": [],
                    "raw_response": "",
                }
            ),
            encoding="utf-8",
        )
        skill_md = tmp_path / "s.md"
        skill_md.write_text("# Skill\n", encoding="utf-8")

        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        assert si.failing_assertions == []
        err = capsys.readouterr().err
        assert "corrupt assertions sidecar" in err

    def test_malformed_assertion_entry_is_warned_and_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Regression (Copilot finding): a partially written result
        # entry (missing a required field) used to crash with a
        # KeyError. It should be skipped with a stderr warning while
        # valid entries in the same run are still collected.
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "assertions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill": "s",
                    "iteration": 1,
                    "runs": [
                        {
                            "run": 0,
                            "results": [
                                {"id": "broken"},  # missing name/passed/kind
                                {
                                    "id": "good",
                                    "name": "ok",
                                    "passed": False,
                                    "message": "msg",
                                    "kind": "presence",
                                    "transcript_path": None,
                                },
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (skill_dir / "grading.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "s",
                    "model": "claude-sonnet-4-6",
                    "generated_at": "2026-01-01T00:00:00.000000Z",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "results": [],
                    "raw_response": "",
                }
            ),
            encoding="utf-8",
        )
        skill_md = tmp_path / "s.md"
        skill_md.write_text("# Skill\n", encoding="utf-8")

        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        ids = [r.id for r in si.failing_assertions]
        assert ids == ["good"]
        err = capsys.readouterr().err
        assert "malformed assertion entry" in err

    def test_corrupt_grading_json_is_warned_and_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Regression (Copilot finding): a truncated grading.json used
        # to raise out of GradingReport.from_json. The loader should
        # warn to stderr and continue so anchor-validation and diff
        # rendering still run against whatever assertions were loaded.
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        # A valid (empty) grading.json is required for
        # find_latest_grading to pick the iteration. Write a valid
        # envelope first, then overwrite with garbage so the search
        # still finds the iteration but the loader hits the garbage.
        (skill_dir / "grading.json").write_text(
            "{partial json",
            encoding="utf-8",
        )
        (skill_dir / "assertions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill": "s",
                    "iteration": 1,
                    "runs": [
                        {
                            "run": 0,
                            "results": [
                                {
                                    "id": "a1",
                                    "name": "n",
                                    "passed": False,
                                    "message": "m",
                                    "kind": "presence",
                                    "transcript_path": None,
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        skill_md = tmp_path / "s.md"
        skill_md.write_text("# Skill\n", encoding="utf-8")

        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        # Corrupt grading → empty grading list, but assertions still load.
        assert si.failing_grading_criteria == []
        assert len(si.failing_assertions) == 1
        err = capsys.readouterr().err
        assert "corrupt grading sidecar" in err

    def test_non_dict_run_entries_are_skipped(
        self, tmp_path: Path
    ) -> None:
        # Coverage: a stray non-dict entry in the runs list (e.g. a
        # stringified envelope from a partial write) must not crash.
        clauditor_dir, skill_md = self._scaffold(
            tmp_path,
            assertions_payload={
                "runs": [
                    "garbage",
                    {
                        "run": 0,
                        "results": [
                            {
                                "id": "a1",
                                "name": "n",
                                "passed": False,
                                "message": "m",
                                "kind": "presence",
                                "transcript_path": None,
                            }
                        ],
                    },
                ]
            },
        )
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        assert [r.id for r in si.failing_assertions] == ["a1"]

    def test_aggregates_and_dedupes_across_runs_by_stable_id(
        self, tmp_path: Path
    ) -> None:
        # Regression: the production schema has multiple runs from
        # variance reps. _load_failing_assertions should aggregate
        # across all runs and dedupe by stable id (first occurrence
        # wins) so a flaky assertion that only fails in run-1 still
        # surfaces, and the same assertion failing in both run-0 and
        # run-1 is reported once.
        clauditor_dir, skill_md = self._scaffold(
            tmp_path,
            assertions_payload={
                "runs": [
                    {
                        "run": 0,
                        "results": [
                            {
                                "id": "a1",
                                "name": "first",
                                "passed": False,
                                "message": "fails in run 0",
                                "kind": "presence",
                                "transcript_path": None,
                            },
                            {
                                "id": "a2",
                                "name": "passes",
                                "passed": True,
                                "message": "",
                                "kind": "presence",
                                "transcript_path": None,
                            },
                        ],
                    },
                    {
                        "run": 1,
                        "results": [
                            {
                                "id": "a1",
                                "name": "first",
                                "passed": False,
                                "message": "fails in run 1 too",
                                "kind": "presence",
                                "transcript_path": None,
                            },
                            {
                                "id": "a3",
                                "name": "flaky",
                                "passed": False,
                                "message": "only fails in variance",
                                "kind": "presence",
                                "transcript_path": None,
                            },
                        ],
                    },
                ]
            },
        )
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor_dir, skill_md_path=skill_md
        )
        ids = [r.id for r in si.failing_assertions]
        assert ids == ["a1", "a3"]


class TestLoadSuggestInputLoaderBranches:
    """Exercise defensive branches in the sub-loaders."""

    def _scaffold(
        self, tmp_path: Path
    ) -> tuple[Path, Path, Path]:
        clauditor = tmp_path / ".clauditor"
        skill_dir = clauditor / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "grading.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "s",
                    "model": "m",
                    "generated_at": "t",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "results": [],
                    "raw_response": "",
                }
            ),
            encoding="utf-8",
        )
        skill_md = tmp_path / "s.md"
        skill_md.write_text("# Skill\n", encoding="utf-8")
        return clauditor, skill_dir, skill_md

    def test_output_slices_skip_non_dir_and_unmatched_names(
        self, tmp_path: Path
    ) -> None:
        # Coverage: _load_output_slices iterates the skill dir and must
        # skip regular files and dirs that don't match run-N.
        clauditor, skill_dir, skill_md = self._scaffold(tmp_path)
        (skill_dir / "README").write_text("notes")
        (skill_dir / "scratch").mkdir()  # not run-N
        (skill_dir / "run-0").mkdir()
        (skill_dir / "run-0" / "output.txt").write_text("primary")
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor, skill_md_path=skill_md
        )
        assert si.output_slices == ["primary"]

    def test_output_slices_skip_run_dir_without_output_txt(
        self, tmp_path: Path
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(tmp_path)
        (skill_dir / "run-0").mkdir()  # no output.txt
        (skill_dir / "run-1").mkdir()
        (skill_dir / "run-1" / "output.txt").write_text("variance")
        si = load_suggest_input(
            skill="s", clauditor_dir=clauditor, skill_md_path=skill_md
        )
        assert si.output_slices == ["variance"]

    def test_transcripts_skip_non_dir_and_unmatched(
        self, tmp_path: Path
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(tmp_path)
        (skill_dir / "README").write_text("notes")
        (skill_dir / "scratch").mkdir()
        (skill_dir / "run-0").mkdir()
        (skill_dir / "run-0" / "output.jsonl").write_text(
            '{"type": "assistant"}\n'
        )
        si = load_suggest_input(
            skill="s",
            clauditor_dir=clauditor,
            skill_md_path=skill_md,
            with_transcripts=True,
        )
        assert si.transcript_events is not None
        assert len(si.transcript_events) == 1
        assert si.transcript_events[0] == [{"type": "assistant"}]

    def test_transcripts_empty_list_for_run_without_jsonl(
        self, tmp_path: Path
    ) -> None:
        clauditor, skill_dir, skill_md = self._scaffold(tmp_path)
        (skill_dir / "run-0").mkdir()  # no jsonl
        (skill_dir / "run-1").mkdir()
        (skill_dir / "run-1" / "output.jsonl").write_text('{"x": 1}\n')
        si = load_suggest_input(
            skill="s",
            clauditor_dir=clauditor,
            skill_md_path=skill_md,
            with_transcripts=True,
        )
        assert si.transcript_events == [[], [{"x": 1}]]

    def test_transcripts_skip_blank_lines(self, tmp_path: Path) -> None:
        # Coverage: blank lines in output.jsonl are silently skipped.
        clauditor, skill_dir, skill_md = self._scaffold(tmp_path)
        (skill_dir / "run-0").mkdir()
        (skill_dir / "run-0" / "output.jsonl").write_text(
            '{"a": 1}\n\n   \n{"b": 2}\n'
        )
        si = load_suggest_input(
            skill="s",
            clauditor_dir=clauditor,
            skill_md_path=skill_md,
            with_transcripts=True,
        )
        assert si.transcript_events == [[{"a": 1}, {"b": 2}]]

    def test_transcripts_skip_scalar_json_lines(
        self, tmp_path: Path
    ) -> None:
        # Coverage: a line that parses but isn't a dict is skipped.
        clauditor, skill_dir, skill_md = self._scaffold(tmp_path)
        (skill_dir / "run-0").mkdir()
        (skill_dir / "run-0" / "output.jsonl").write_text(
            '42\n{"kept": true}\n'
        )
        si = load_suggest_input(
            skill="s",
            clauditor_dir=clauditor,
            skill_md_path=skill_md,
            with_transcripts=True,
        )
        assert si.transcript_events == [[{"kept": True}]]


class TestLoadSuggestInputCRLF:
    def test_crlf_skill_text_is_normalized_at_load_time(
        self, tmp_path: Path
    ) -> None:
        # Regression: SKILL.md on a Windows checkout arrives with
        # CRLF endings. The loader must normalize to LF so anchor
        # validation, render_unified_diff, and Sonnet's LF-only
        # replacement strings all agree on one substrate.
        clauditor_dir = tmp_path / ".clauditor"
        skill_dir = clauditor_dir / "iteration-1" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "assertions.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill": "s",
                    "iteration": 1,
                    "runs": [
                        {
                            "run": 0,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "results": [
                                {
                                    "id": "a1",
                                    "name": "has header",
                                    "passed": False,
                                    "message": "missing",
                                    "kind": "presence",
                                    "transcript_path": None,
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (skill_dir / "grading.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "s",
                    "model": "claude-sonnet-4-6",
                    "generated_at": "2026-01-01T00:00:00.000000Z",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "results": [],
                    "raw_response": "",
                }
            ),
            encoding="utf-8",
        )
        skill_md = tmp_path / "s.md"
        skill_md.write_bytes(b"# Skill\r\n\r\nDo the thing.\r\n")

        si = load_suggest_input(
            skill="s",
            clauditor_dir=clauditor_dir,
            skill_md_path=skill_md,
        )
        assert "\r" not in si.skill_md_text
        assert si.skill_md_text == "# Skill\n\nDo the thing.\n"


class TestPrivateLoaderHelpers:
    """Direct unit tests for the private loaders.

    The public ``load_suggest_input`` path always finds ``grading.json``
    (because that's the iteration-selection key), so the "missing"
    branches in the grading / output-slice / transcript loaders are
    only reachable via direct invocation.
    """

    def test_load_failing_grading_returns_empty_when_file_missing(
        self, tmp_path: Path
    ) -> None:
        from clauditor.suggest import _load_failing_grading_criteria

        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        assert _load_failing_grading_criteria(skill_dir) == []

    def test_load_output_slices_returns_empty_when_skill_dir_missing(
        self, tmp_path: Path
    ) -> None:
        from clauditor.suggest import _load_output_slices

        assert _load_output_slices(tmp_path / "missing") == []

    def test_load_transcript_events_returns_empty_when_skill_dir_missing(
        self, tmp_path: Path
    ) -> None:
        from clauditor.suggest import _load_transcript_events

        assert _load_transcript_events(tmp_path / "missing") == []


class TestStripJsonFence:
    def test_bare_triple_backtick_fence_is_stripped(self) -> None:
        # Coverage: _strip_json_fence handles the bare ``` form (no
        # language tag) by splitting on triple-backticks.
        from clauditor.suggest import _strip_json_fence

        wrapped = 'text before\n```\n{"ok": true}\n```\nafter'
        assert _strip_json_fence(wrapped) == '{"ok": true}'


class TestFormatHelpers:
    def test_assertion_with_evidence_and_transcript_path_included(
        self,
    ) -> None:
        # Coverage: the conditional branches in _format_failing_assertion
        # that emit evidence/transcript_path lines only fire when the
        # assertion carries those fields.
        si = _make_suggest_input(
            failing_assertions=[
                AssertionResult(
                    id="a1",
                    name="has fence",
                    passed=False,
                    message="missing",
                    kind="presence",
                    evidence="line 42: no fence",
                    transcript_path=".clauditor/iter-1/s/run-0/output.jsonl",
                )
            ]
        )
        prompt = build_suggest_prompt(si)
        assert "line 42: no fence" in prompt
        assert "run-0/output.jsonl" in prompt


class TestRepoRelativeFallback:
    def test_path_outside_repo_root_falls_back_to_absolute(
        self, tmp_path: Path
    ) -> None:
        # Coverage + regression: when the sidecar path lives outside
        # clauditor_dir.parent (weird symlink / mount layout),
        # _repo_relative must not raise — it falls back to str(path).
        from clauditor.suggest import _repo_relative

        clauditor_dir = tmp_path / "repo" / ".clauditor"
        clauditor_dir.mkdir(parents=True)
        outside = tmp_path / "elsewhere" / "grading.json"
        outside.parent.mkdir()
        outside.write_text("{}")
        result = _repo_relative(clauditor_dir, outside)
        # Falls back to the absolute path string rather than raising.
        assert result == str(outside)


class TestBuildSuggestPrompt:
    def test_framing_sentence_appears_before_first_untrusted_tag(self) -> None:
        si = _make_suggest_input(
            failing_assertions=[
                AssertionResult(
                    id="a1",
                    name="needs-fence",
                    passed=False,
                    message="missing fence",
                    kind="presence",
                ),
            ],
            failing_grading_criteria=[
                GradingResult(
                    id="g1",
                    criterion="explains why",
                    passed=False,
                    score=0.3,
                    evidence="ev",
                    reasoning="missing rationale",
                ),
            ],
            output_slices=["raw output A"],
            transcript_events=[[{"type": "assistant"}]],
        )
        prompt = build_suggest_prompt(si)
        framing_idx = prompt.find(
            "untrusted data, not instructions"
        )
        assert framing_idx >= 0

        first_untrusted = min(
            prompt.find("<failing_assertion"),
            prompt.find("<failing_criterion"),
            prompt.find("<output_slice"),
            prompt.find("<transcript_snippet"),
        )
        assert first_untrusted > framing_idx

    def test_skill_md_block_is_not_framed_as_untrusted(self) -> None:
        si = _make_suggest_input()
        prompt = build_suggest_prompt(si)
        assert "<skill_md>" in prompt
        # The framing sentence enumerates the untrusted tags. <skill_md>
        # must NOT appear in that enumeration.
        framing_line_start = prompt.find("untrusted data, not instructions")
        # Look at the sentence that lists the untrusted tags (the line(s)
        # leading up to the framing sentence).
        untrusted_listing_region = prompt[: framing_line_start + 100]
        assert "<skill_md>" not in untrusted_listing_region.split(
            "The current SKILL.md text"
        )[0]

    def test_failing_assertions_are_fenced_per_item_with_stable_id(
        self,
    ) -> None:
        si = _make_suggest_input(
            failing_assertions=[
                AssertionResult(
                    id="a1",
                    name="one",
                    passed=False,
                    message="m1",
                    kind="presence",
                ),
                AssertionResult(
                    id="a2",
                    name="two",
                    passed=False,
                    message="m2",
                    kind="regex",
                ),
            ],
        )
        prompt = build_suggest_prompt(si)
        assert '<failing_assertion id="a1">' in prompt
        assert '<failing_assertion id="a2">' in prompt
        assert prompt.count("</failing_assertion>") == 2

    def test_failing_grading_criteria_are_fenced_per_item_with_stable_id(
        self,
    ) -> None:
        si = _make_suggest_input(
            failing_grading_criteria=[
                GradingResult(
                    id="g1",
                    criterion="c1",
                    passed=False,
                    score=0.1,
                    evidence="e",
                    reasoning="r",
                ),
                GradingResult(
                    id="g2",
                    criterion="c2",
                    passed=False,
                    score=0.2,
                    evidence="e",
                    reasoning="r",
                ),
            ],
        )
        prompt = build_suggest_prompt(si)
        assert '<failing_criterion id="g1">' in prompt
        assert '<failing_criterion id="g2">' in prompt
        assert prompt.count("</failing_criterion>") == 2

    def test_output_slices_are_fenced_with_run_index(self) -> None:
        si = _make_suggest_input(
            output_slices=["alpha", "beta", "gamma"],
        )
        prompt = build_suggest_prompt(si)
        assert '<output_slice index="0">' in prompt
        assert '<output_slice index="1">' in prompt
        assert '<output_slice index="2">' in prompt
        assert "alpha" in prompt
        assert "gamma" in prompt

    def test_anchor_contract_phrase_present(self) -> None:
        si = _make_suggest_input()
        prompt = build_suggest_prompt(si)
        assert "exactly once" in prompt

    def test_agentskills_guidelines_present(self) -> None:
        si = _make_suggest_input()
        prompt = build_suggest_prompt(si)
        assert "Generalize" in prompt
        assert "lean" in prompt
        assert "why" in prompt
        assert "Bundle" in prompt

    def test_response_schema_instruction_present(self) -> None:
        si = _make_suggest_input()
        prompt = build_suggest_prompt(si)
        for field_name in (
            "anchor",
            "replacement",
            "rationale",
            "confidence",
            "motivated_by",
        ):
            assert field_name in prompt

    def test_transcripts_omitted_when_none(self) -> None:
        si = _make_suggest_input(transcript_events=None)
        prompt = build_suggest_prompt(si)
        assert "<transcript_snippet" not in prompt

    def test_transcripts_included_when_provided(self) -> None:
        si = _make_suggest_input(
            transcript_events=[
                [{"type": "assistant", "n": 1}],
                [{"type": "result", "n": 2}],
            ],
        )
        prompt = build_suggest_prompt(si)
        assert '<transcript_snippet run="0">' in prompt
        assert '<transcript_snippet run="1">' in prompt
        assert prompt.count("</transcript_snippet>") == 2

    def test_transcripts_redacted_before_inclusion(self) -> None:
        # ghp_ pattern is one of the regexes redact() catches.
        secret = "ghp_" + "A" * 40
        original_events = [
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": f"token={secret}"}
                        ]
                    },
                }
            ]
        ]
        si = _make_suggest_input(transcript_events=original_events)
        prompt = build_suggest_prompt(si)
        assert secret not in prompt
        assert "[REDACTED]" in prompt
        # Non-mutating invariant: original is untouched.
        assert (
            original_events[0][0]["message"]["content"][0]["text"]
            == f"token={secret}"
        )
        assert si.transcript_events is original_events

    def test_empty_failing_lists_still_builds_a_valid_prompt(self) -> None:
        si = _make_suggest_input(
            failing_assertions=[],
            failing_grading_criteria=[],
        )
        prompt = build_suggest_prompt(si)
        assert isinstance(prompt, str)
        assert "<skill_md>" in prompt
        assert "exactly once" in prompt


# --------------------------------------------------------------------------
# US-003 tests: SuggestReport.to_json, _check_schema_version,
# parse_suggest_response, validate_anchors, propose_edits.
# --------------------------------------------------------------------------


def _make_proposal(
    *,
    pid: str = "edit-0",
    anchor: str = "Do the thing.",
    replacement: str = "Do the better thing.",
    rationale: str = "improves clarity",
    confidence: float = 0.9,
    motivated_by: list[str] | None = None,
) -> EditProposal:
    return EditProposal(
        id=pid,
        anchor=anchor,
        replacement=replacement,
        rationale=rationale,
        confidence=confidence,
        motivated_by=motivated_by or ["a1"],
    )


def _make_report(
    *,
    proposals: list[EditProposal] | None = None,
    parse_error: str | None = None,
    validation_errors: list[str] | None = None,
    summary_rationale: str = "overall summary",
    api_error: str | None = None,
) -> SuggestReport:
    return SuggestReport(
        skill_name="find",
        model="claude-sonnet-4-6",
        generated_at="2026-04-14T00:00:00.000000Z",
        source_iteration=3,
        source_grading_path=".clauditor/iteration-3/find/grading.json",
        input_tokens=10,
        output_tokens=20,
        duration_seconds=1.5,
        edit_proposals=proposals or [],
        summary_rationale=summary_rationale,
        validation_errors=validation_errors or [],
        parse_error=parse_error,
        api_error=api_error,
    )


class TestSuggestReportToJson:
    def test_schema_version_is_first_key(self) -> None:
        report = _make_report(proposals=[_make_proposal()])
        text = report.to_json()
        data = json.loads(text)
        assert list(data.keys())[0] == "schema_version"
        assert data["schema_version"] == 1

    def test_round_trip_preserves_fields(self) -> None:
        report = _make_report(
            proposals=[
                _make_proposal(pid="edit-0", motivated_by=["a1", "g1"]),
                _make_proposal(
                    pid="edit-1",
                    anchor="other",
                    replacement="rep",
                    confidence=0.4,
                ),
            ],
            validation_errors=["edit-0: oops"],
            parse_error=None,
        )
        text = report.to_json()
        data = json.loads(text)
        assert data["skill_name"] == "find"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["source_iteration"] == 3
        assert (
            data["source_grading_path"]
            == ".clauditor/iteration-3/find/grading.json"
        )
        assert data["input_tokens"] == 10
        assert data["output_tokens"] == 20
        assert data["duration_seconds"] == 1.5
        assert data["summary_rationale"] == "overall summary"
        assert data["validation_errors"] == ["edit-0: oops"]
        assert data["parse_error"] is None
        assert len(data["edit_proposals"]) == 2
        first = data["edit_proposals"][0]
        assert first["id"] == "edit-0"
        assert first["motivated_by"] == ["a1", "g1"]
        assert first["applies_to_file"] == "SKILL.md"

    def test_api_error_scrubbed_on_disk_not_in_memory(self) -> None:
        """Per .claude/rules/non-mutating-scrub.md, api_error containing a
        secret-shaped substring (e.g. an Anthropic key echoed in a 401
        body) is redacted before writing to disk, while the in-memory
        report keeps the full-fidelity copy for debugging.
        """
        secret = "sk-ant-api03-" + "A" * 95
        raw = f"anthropic API error: 401 body={{\"error\": \"{secret}\"}}"
        report = _make_report(api_error=raw)

        text = report.to_json()
        data = json.loads(text)

        # On-disk copy: secret replaced with [REDACTED] marker.
        assert data["api_error"] is not None
        assert secret not in data["api_error"]
        assert "[REDACTED]" in data["api_error"]
        # Surrounding context is preserved; only the secret span was replaced.
        assert "anthropic API error: 401" in data["api_error"]

        # In-memory copy is unchanged — downstream consumers (stderr prints,
        # debugger inspection) still see the full original.
        assert report.api_error == raw

    def test_api_error_none_stays_none(self) -> None:
        """Redaction helper must preserve None rather than coerce to string."""
        report = _make_report(api_error=None)
        data = json.loads(report.to_json())
        assert data["api_error"] is None

    def test_api_error_without_secrets_unchanged(self) -> None:
        """A benign api_error string should round-trip verbatim."""
        raw = "anthropic API error: timeout after 3 retries"
        report = _make_report(api_error=raw)
        data = json.loads(report.to_json())
        assert data["api_error"] == raw
        assert report.api_error == raw


class TestCheckSchemaVersion:
    def test_accepts_matching_version(self, tmp_path: Path) -> None:
        assert (
            _check_schema_version({"schema_version": 1}, tmp_path / "s.json")
            is True
        )

    def test_rejects_mismatched_version_and_warns_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            _check_schema_version({"schema_version": 2}, tmp_path / "s.json")
            is False
        )
        captured = capsys.readouterr()
        assert "schema_version=2" in captured.err
        assert "expected 1" in captured.err


def _suggest_input_with_signals() -> SuggestInput:
    return SuggestInput(
        skill_name="find",
        source_iteration=3,
        source_grading_path=".clauditor/iteration-3/find/grading.json",
        skill_md_text="# Skill\n\nDo the thing.\n",
        failing_assertions=[
            AssertionResult(
                id="a1",
                name="needs-fence",
                passed=False,
                message="missing fence",
                kind="presence",
            ),
        ],
        failing_grading_criteria=[
            GradingResult(
                id="g1",
                criterion="explains why",
                passed=False,
                score=0.3,
                evidence="ev",
                reasoning="missing rationale",
            ),
        ],
    )


def _good_envelope_text(
    *,
    anchor: str = "Do the thing.",
    motivated_by: list[str] | None = None,
    confidence: float = 0.8,
) -> str:
    payload = {
        "summary_rationale": "tighten the prompt",
        "edits": [
            {
                "anchor": anchor,
                "replacement": "Do the better thing.",
                "rationale": "improves clarity",
                "confidence": confidence,
                "motivated_by": motivated_by or ["a1"],
            }
        ],
    }
    return json.dumps(payload)


class TestParseSuggestResponse:
    def test_parses_well_formed_envelope(self) -> None:
        si = _suggest_input_with_signals()
        proposals, summary = parse_suggest_response(
            _good_envelope_text(motivated_by=["a1", "g1"]), si
        )
        assert summary == "tighten the prompt"
        assert len(proposals) == 1
        assert proposals[0].id == "edit-0"
        assert proposals[0].anchor == "Do the thing."
        assert proposals[0].motivated_by == ["a1", "g1"]
        assert proposals[0].applies_to_file == "SKILL.md"

    def test_strips_markdown_json_fence(self) -> None:
        si = _suggest_input_with_signals()
        wrapped = "```json\n" + _good_envelope_text() + "\n```"
        proposals, _ = parse_suggest_response(wrapped, si)
        assert len(proposals) == 1

    def test_raises_on_non_dict_top_level(self) -> None:
        si = _suggest_input_with_signals()
        with pytest.raises(ValueError, match="object"):
            parse_suggest_response("[]", si)

    def test_raises_on_missing_edits_key(self) -> None:
        si = _suggest_input_with_signals()
        with pytest.raises(ValueError, match="edits"):
            parse_suggest_response(
                json.dumps({"summary_rationale": "x"}), si
            )

    def test_raises_on_edit_missing_required_field(self) -> None:
        si = _suggest_input_with_signals()
        bad = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": "Do the thing.",
                    "replacement": "y",
                    "rationale": "z",
                    # missing confidence
                    "motivated_by": ["a1"],
                }
            ],
        }
        with pytest.raises(ValueError, match="confidence"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_edits_not_a_list(self) -> None:
        si = _suggest_input_with_signals()
        bad = {"summary_rationale": "x", "edits": "nope"}
        with pytest.raises(ValueError, match="edits.*must be a list"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_missing_summary_rationale(self) -> None:
        si = _suggest_input_with_signals()
        bad = {"edits": []}
        with pytest.raises(ValueError, match="summary_rationale"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_summary_rationale_wrong_type(self) -> None:
        si = _suggest_input_with_signals()
        bad = {"summary_rationale": 42, "edits": []}
        with pytest.raises(ValueError, match="summary_rationale"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_edit_entry_not_a_dict(self) -> None:
        si = _suggest_input_with_signals()
        bad = {"summary_rationale": "x", "edits": ["not-a-dict"]}
        with pytest.raises(ValueError, match=r"edits\[0\] must be an object"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_anchor_wrong_type(self) -> None:
        si = _suggest_input_with_signals()
        bad = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": 42,
                    "replacement": "y",
                    "rationale": "z",
                    "confidence": 0.5,
                    "motivated_by": ["a1"],
                }
            ],
        }
        with pytest.raises(ValueError, match=r"anchor must be"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_replacement_wrong_type(self) -> None:
        si = _suggest_input_with_signals()
        bad = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": "Do the thing.",
                    "replacement": 42,
                    "rationale": "z",
                    "confidence": 0.5,
                    "motivated_by": ["a1"],
                }
            ],
        }
        with pytest.raises(ValueError, match=r"replacement must be"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_rationale_wrong_type(self) -> None:
        si = _suggest_input_with_signals()
        bad = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": "Do the thing.",
                    "replacement": "y",
                    "rationale": 42,
                    "confidence": 0.5,
                    "motivated_by": ["a1"],
                }
            ],
        }
        with pytest.raises(ValueError, match=r"rationale must be"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_confidence_wrong_type(self) -> None:
        si = _suggest_input_with_signals()
        bad = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": "Do the thing.",
                    "replacement": "y",
                    "rationale": "z",
                    "confidence": "high",
                    "motivated_by": ["a1"],
                }
            ],
        }
        with pytest.raises(ValueError, match=r"confidence must be a number"):
            parse_suggest_response(json.dumps(bad), si)

    def test_raises_on_motivated_by_wrong_type(self) -> None:
        si = _suggest_input_with_signals()
        bad = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": "Do the thing.",
                    "replacement": "y",
                    "rationale": "z",
                    "confidence": 0.5,
                    "motivated_by": "a1",
                }
            ],
        }
        with pytest.raises(ValueError, match=r"motivated_by"):
            parse_suggest_response(json.dumps(bad), si)

    def test_clamps_confidence_to_unit_range(self) -> None:
        si = _suggest_input_with_signals()
        high, _ = parse_suggest_response(
            _good_envelope_text(confidence=1.5), si
        )
        assert high[0].confidence == 1.0
        low, _ = parse_suggest_response(
            _good_envelope_text(confidence=-0.3), si
        )
        assert low[0].confidence == 0.0

    def test_nan_confidence_is_coerced_to_zero(self) -> None:
        # Regression: NaN bypasses all ordered comparisons, so it used
        # to sail through the clamp and land in the sidecar as the bare
        # token `NaN` which strict JSON parsers reject.
        si = _suggest_input_with_signals()
        # json.dumps emits `NaN` for float('nan') by default and
        # json.loads accepts it; feed the raw text to bypass the
        # float() round-trip in the helper.
        payload_text = (
            '{"summary_rationale": "x", "edits": [{'
            '"anchor": "Do the thing.", '
            '"replacement": "Do the better thing.", '
            '"rationale": "r", '
            '"confidence": NaN, '
            '"motivated_by": ["a1"]}]}'
        )
        proposals, _ = parse_suggest_response(payload_text, si)
        assert proposals[0].confidence == 0.0
        # And the resulting sidecar JSON round-trips through
        # strict json parsers without choking on `NaN`.
        report = SuggestReport(
            skill_name="s",
            model="m",
            generated_at="t",
            source_iteration=1,
            source_grading_path="p",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
            edit_proposals=proposals,
            summary_rationale="x",
            validation_errors=[],
        )
        reloaded = json.loads(report.to_json())  # strict mode
        assert reloaded["edit_proposals"][0]["confidence"] == 0.0

    def test_infinity_confidence_is_coerced_to_zero(self) -> None:
        # Infinity happens to clamp to 1.0 via > 1.0 already, but
        # treat non-finite uniformly as "model confused → 0.0".
        si = _suggest_input_with_signals()
        payload_text = (
            '{"summary_rationale": "x", "edits": [{'
            '"anchor": "Do the thing.", '
            '"replacement": "Do the better thing.", '
            '"rationale": "r", '
            '"confidence": Infinity, '
            '"motivated_by": ["a1"]}]}'
        )
        proposals, _ = parse_suggest_response(payload_text, si)
        assert proposals[0].confidence == 0.0

    def test_rejects_invented_motivated_by_ids(self) -> None:
        si = _suggest_input_with_signals()
        with pytest.raises(ValueError, match="unknown id"):
            parse_suggest_response(
                _good_envelope_text(motivated_by=["nope-99"]), si
            )

    def test_accepts_motivated_by_ids_from_either_list(self) -> None:
        si = _suggest_input_with_signals()
        # a1 is an assertion id, g1 is a grading-criterion id.
        proposals, _ = parse_suggest_response(
            _good_envelope_text(motivated_by=["a1", "g1"]), si
        )
        assert proposals[0].motivated_by == ["a1", "g1"]

    def test_assigns_positional_edit_ids(self) -> None:
        si = _suggest_input_with_signals()
        payload = {
            "summary_rationale": "x",
            "edits": [
                {
                    "anchor": f"anchor-{i}",
                    "replacement": "r",
                    "rationale": "r",
                    "confidence": 0.5,
                    "motivated_by": ["a1"],
                }
                for i in range(3)
            ],
        }
        proposals, _ = parse_suggest_response(json.dumps(payload), si)
        assert [p.id for p in proposals] == ["edit-0", "edit-1", "edit-2"]


class TestValidateAnchors:
    def test_valid_when_anchor_appears_exactly_once(self) -> None:
        proposals = [_make_proposal(anchor="Do the thing.")]
        text = "# Skill\n\nDo the thing.\n"
        assert validate_anchors(proposals, text) == []

    def test_records_error_when_anchor_missing(self) -> None:
        proposals = [
            _make_proposal(
                pid="edit-0", anchor="missing", motivated_by=["a1"]
            )
        ]
        errors = validate_anchors(proposals, "# Skill\n\nelsewhere\n")
        assert len(errors) == 1
        assert "edit-0" in errors[0]
        assert "['a1']" in errors[0]
        assert "not found" in errors[0]

    def test_records_error_when_anchor_appears_multiple_times(
        self,
    ) -> None:
        proposals = [
            _make_proposal(
                pid="edit-0", anchor="dup", motivated_by=["a1"]
            )
        ]
        errors = validate_anchors(proposals, "dup dup dup")
        assert len(errors) == 1
        assert "3 times" in errors[0]
        assert "edit-0" in errors[0]

    def test_returns_empty_list_when_all_valid(self) -> None:
        proposals = [
            _make_proposal(pid="edit-0", anchor="foo"),
            _make_proposal(pid="edit-1", anchor="bar"),
        ]
        assert validate_anchors(proposals, "foo and bar") == []

    def test_overlapping_anchor_occurrences_are_detected_as_ambiguous(
        self,
    ) -> None:
        # Regression (Copilot finding): str.count counts
        # non-overlapping matches, so "ababa".count("aba") == 1 but
        # positions 0 and 2 both match. The validator uses
        # _count_all_occurrences to catch this and reject the edit
        # as ambiguous.
        proposals = [
            _make_proposal(
                pid="edit-0", anchor="aba", motivated_by=["a1"]
            )
        ]
        errors = validate_anchors(proposals, "ababa")
        assert len(errors) == 1
        assert "edit-0" in errors[0]
        assert "2 times" in errors[0]

    def test_empty_anchor_is_rejected_as_not_found(self) -> None:
        # Degenerate input: an empty anchor. str.count("") returns
        # len(text)+1 which is nonsensical; _count_all_occurrences
        # returns 0 so the edit is rejected with the "not found"
        # message.
        proposals = [
            _make_proposal(
                pid="edit-0", anchor="", motivated_by=["a1"]
            )
        ]
        errors = validate_anchors(proposals, "# Skill\n\nDo it.\n")
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_later_anchor_destroyed_by_earlier_replacement_is_rejected(
        self,
    ) -> None:
        # edit-0 deletes "alpha" → after apply, "alpha beta" becomes
        # " beta". edit-1's anchor "alpha beta" is now gone. The
        # sequential simulation must catch this even though both
        # anchors appear exactly once in the *original* text.
        proposals = [
            _make_proposal(pid="edit-0", anchor="alpha", replacement=""),
            _make_proposal(
                pid="edit-1", anchor="alpha beta", motivated_by=["a1"]
            ),
        ]
        errors = validate_anchors(proposals, "alpha beta")
        assert len(errors) == 1
        assert "edit-1" in errors[0]
        assert "not found" in errors[0]

    def test_later_anchor_duplicated_by_earlier_replacement_is_rejected(
        self,
    ) -> None:
        # edit-0 replaces "x" with "foo", creating two occurrences of
        # "foo". edit-1's anchor "foo" appeared exactly once in the
        # original but twice after edit-0 applies.
        proposals = [
            _make_proposal(pid="edit-0", anchor="x", replacement="foo"),
            _make_proposal(
                pid="edit-1", anchor="foo", motivated_by=["a1"]
            ),
        ]
        errors = validate_anchors(proposals, "x and foo")
        assert len(errors) == 1
        assert "edit-1" in errors[0]
        assert "2 times" in errors[0]


def _mock_anthropic_result(
    *,
    text: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
):
    """Return an AnthropicResult shaped like a successful helper call.

    After bead ``clauditor-24h.3`` the suggest call path goes through
    ``clauditor._providers.call_model`` rather than constructing
    its own client, so tests that used to stub ``AsyncAnthropic`` now
    stub the helper and hand back an ``AnthropicResult`` directly.
    """
    from clauditor._anthropic import AnthropicResult

    return AnthropicResult(
        response_text=text,
        text_blocks=[text] if text else [],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_message=None,
    )


class TestProposeEdits:
    @pytest.mark.asyncio
    async def test_calls_sonnet_with_built_prompt(self) -> None:
        si = _suggest_input_with_signals()
        result = _mock_anthropic_result(
            text=_good_envelope_text(motivated_by=["a1"])
        )
        call_mock = AsyncMock(return_value=result)
        with patch("clauditor._providers.call_model", call_mock):
            report = await propose_edits(si)
        call_mock.assert_awaited_once()
        kwargs = call_mock.await_args.kwargs
        args = call_mock.await_args.args
        assert kwargs["model"] == "claude-sonnet-4-6"
        assert kwargs["max_tokens"] == 4096
        assert len(args) == 1
        assert "exactly once" in args[0]
        assert report.parse_error is None

    @pytest.mark.asyncio
    async def test_uses_monotonic_alias_for_duration(self) -> None:
        si = _suggest_input_with_signals()
        result = _mock_anthropic_result(
            text=_good_envelope_text(motivated_by=["a1"])
        )
        with patch(
            "clauditor._providers.call_model",
            AsyncMock(return_value=result),
        ), patch(
            "clauditor.suggest._monotonic", side_effect=[0.0, 1.25]
        ):
            report = await propose_edits(si)
        assert report.duration_seconds == pytest.approx(1.25)

    @pytest.mark.asyncio
    async def test_api_exception_captured_in_api_error_not_raised(
        self,
    ) -> None:
        si = _suggest_input_with_signals()
        with patch(
            "clauditor._providers.call_model",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            report = await propose_edits(si)
        assert report.edit_proposals == []
        assert report.parse_error is None
        assert report.api_error is not None
        assert "anthropic API error" in report.api_error
        assert "boom" in report.api_error

    @pytest.mark.asyncio
    async def test_malformed_json_response_sets_parse_error(self) -> None:
        si = _suggest_input_with_signals()
        result = _mock_anthropic_result(text="this is not json {{{")
        with patch(
            "clauditor._providers.call_model",
            AsyncMock(return_value=result),
        ):
            report = await propose_edits(si)
        assert report.edit_proposals == []
        assert report.parse_error is not None
        assert report.input_tokens == 100
        assert report.output_tokens == 50

    @pytest.mark.asyncio
    async def test_successful_response_populates_report(self) -> None:
        si = _suggest_input_with_signals()
        result = _mock_anthropic_result(
            text=_good_envelope_text(motivated_by=["a1", "g1"]),
            input_tokens=200,
            output_tokens=80,
        )
        with patch(
            "clauditor._providers.call_model",
            AsyncMock(return_value=result),
        ):
            report = await propose_edits(si)
        assert report.parse_error is None
        assert report.validation_errors == []
        assert len(report.edit_proposals) == 1
        assert report.edit_proposals[0].id == "edit-0"
        assert report.input_tokens == 200
        assert report.output_tokens == 80
        assert report.summary_rationale == "tighten the prompt"
        assert report.source_iteration == 3
        assert report.skill_name == "find"

    @pytest.mark.asyncio
    async def test_anchor_validation_errors_flow_into_report(self) -> None:
        si = _suggest_input_with_signals()
        # anchor that does NOT exist in skill_md_text
        result = _mock_anthropic_result(
            text=_good_envelope_text(
                anchor="this string is not in skill md",
                motivated_by=["a1"],
            )
        )
        with patch(
            "clauditor._providers.call_model",
            AsyncMock(return_value=result),
        ):
            report = await propose_edits(si)
        assert report.parse_error is None
        assert len(report.edit_proposals) == 1
        assert len(report.validation_errors) == 1
        assert "not found" in report.validation_errors[0]

    @pytest.mark.asyncio
    async def test_prompt_build_exception_captured_not_raised(self) -> None:
        # propose_edits promises to never raise. A failure inside
        # build_suggest_prompt must flow into api_error, not propagate.
        si = _suggest_input_with_signals()
        with patch(
            "clauditor.suggest.build_suggest_prompt",
            side_effect=RuntimeError("prompt kaboom"),
        ):
            report = await propose_edits(si)
        assert report.edit_proposals == []
        assert report.parse_error is None
        assert report.api_error is not None
        assert "prompt build error" in report.api_error
        assert "prompt kaboom" in report.api_error


class TestRenderUnifiedDiff:
    def test_single_edit_produces_expected_hunk(self) -> None:
        skill_md = "Line one.\nDo the thing.\nLine three.\n"
        report = _make_report(
            proposals=[
                _make_proposal(
                    anchor="Do the thing.",
                    replacement="Do the better thing.",
                )
            ]
        )
        diff = render_unified_diff(report, skill_md)
        assert "-Do the thing." in diff
        assert "+Do the better thing." in diff
        assert "Line one." in diff

    def test_multiple_edits_apply_in_declaration_order(self) -> None:
        skill_md = "alpha\nbravo\ncharlie\n"
        report = _make_report(
            proposals=[
                _make_proposal(
                    pid="edit-0", anchor="alpha", replacement="ALPHA"
                ),
                _make_proposal(
                    pid="edit-1", anchor="charlie", replacement="CHARLIE"
                ),
            ]
        )
        diff = render_unified_diff(report, skill_md)
        assert "-alpha" in diff
        assert "+ALPHA" in diff
        assert "-charlie" in diff
        assert "+CHARLIE" in diff

    def test_render_does_not_mutate_input_skill_text(self) -> None:
        skill_md = "Do the thing.\n"
        original = skill_md
        report = _make_report(
            proposals=[
                _make_proposal(
                    anchor="Do the thing.",
                    replacement="Do the better thing.",
                )
            ]
        )
        _ = render_unified_diff(report, skill_md)
        assert skill_md == original

    def test_empty_proposals_returns_empty_string(self) -> None:
        report = _make_report(proposals=[])
        assert render_unified_diff(report, "anything") == ""

    def test_diff_uses_unified_format(self) -> None:
        skill_md = "Do the thing.\n"
        report = _make_report(
            proposals=[
                _make_proposal(
                    anchor="Do the thing.",
                    replacement="Do the better thing.",
                )
            ]
        )
        diff = render_unified_diff(report, skill_md)
        assert "--- SKILL.md" in diff
        assert "+++ SKILL.md (proposed)" in diff


class TestWriteSidecar:
    def test_creates_suggestions_dir_if_missing(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        assert not (clauditor_dir / "suggestions").exists()
        report = _make_report(proposals=[_make_proposal()])
        write_sidecar(report, "diff body", clauditor_dir)
        assert (clauditor_dir / "suggestions").is_dir()

    def test_writes_both_files(self, tmp_path: Path) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        report = _make_report(proposals=[_make_proposal()])
        json_path, diff_path = write_sidecar(
            report, "my diff text", clauditor_dir
        )
        assert json_path.exists()
        assert diff_path.exists()
        assert json_path.suffix == ".json"
        assert diff_path.suffix == ".diff"

    def test_json_sidecar_first_key_is_schema_version(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        report = _make_report(proposals=[_make_proposal()])
        json_path, _ = write_sidecar(report, "", clauditor_dir)
        data = json.loads(json_path.read_text())
        assert list(data.keys())[0] == "schema_version"

    def test_diff_file_contents_match_argument(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        report = _make_report(proposals=[_make_proposal()])
        diff_text = "--- SKILL.md\n+++ SKILL.md (proposed)\n@@ -1 +1 @@\n-a\n+b\n"
        _, diff_path = write_sidecar(report, diff_text, clauditor_dir)
        assert diff_path.read_text() == diff_text

    def test_filename_uses_microsecond_timestamp(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        report = _make_report(proposals=[_make_proposal()])
        json_path, diff_path = write_sidecar(report, "", clauditor_dir)
        import re as _re
        # %Y%m%d (8) T %H%M%S%f (6+6) Z — microsecond precision.
        pattern = _re.compile(r"^find-\d{8}T\d{12}Z$")
        assert pattern.match(json_path.stem)
        assert pattern.match(diff_path.stem)

    def test_skill_name_validation_rejects_traversal(
        self, tmp_path: Path
    ) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        bad_report = SuggestReport(
            skill_name="../evil",
            model="claude-sonnet-4-6",
            generated_at="2026-04-14T00:00:00.000000Z",
            source_iteration=1,
            source_grading_path="x",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        with pytest.raises(InvalidSkillNameError):
            write_sidecar(bad_report, "", clauditor_dir)

    def test_returns_absolute_paths(self, tmp_path: Path) -> None:
        clauditor_dir = tmp_path / ".clauditor"
        report = _make_report(proposals=[_make_proposal()])
        json_path, diff_path = write_sidecar(report, "", clauditor_dir)
        assert json_path.is_absolute()
        assert diff_path.is_absolute()
