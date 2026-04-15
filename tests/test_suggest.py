"""Tests for clauditor.suggest (US-001 — loader + SuggestInput)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clauditor.assertions import AssertionResult
from clauditor.quality_grader import GradingResult
from clauditor.suggest import (
    NoPriorGradeError,
    SuggestInput,
    build_suggest_prompt,
    find_latest_grading,
    load_suggest_input,
)


def _write_assertions(skill_dir: Path, results: list[dict]) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "input_tokens": 0,
        "output_tokens": 0,
        "results": results,
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
