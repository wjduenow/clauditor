"""Tests for US-007 — verbose `-v` transcript slice printer.

Covers the pure helper ``clauditor.cli._print_failing_transcript_slice``
in isolation (header, slice-of-5 window, fewer-than-5, redaction, 2 KB
truncation) plus the gate conditions at its call site inside
``_cmd_grade_with_workspace``: nothing printed without `-v`, nothing
printed when every assertion passes, slice printed when both conditions
hold.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.cli import (
    _TRANSCRIPT_SLICE_BLOCK_CAP_BYTES,
    _TRANSCRIPT_SLICE_TRUNC_MARKER,
    _print_failing_transcript_slice,
    main,
)

# A fake secret that matches one of the ``clauditor.transcripts`` regexes.
_FAKE_KEY = "sk-proj-" + "a" * 32


def _assistant_event(*texts: str) -> dict:
    """Build a synthetic ``assistant`` stream event with text blocks."""
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": t} for t in texts
            ],
        },
    }


class TestPrintFailingTranscriptSlice:
    def test_print_last_5_of_8(self) -> None:
        # Spread 8 text blocks across two assistant events so the helper
        # has to collect across events in order before slicing.
        events = [
            _assistant_event("b0", "b1", "b2", "b3"),
            _assistant_event("b4", "b5", "b6", "b7"),
        ]
        out = io.StringIO()
        _print_failing_transcript_slice(2, events, out)

        text = out.getvalue()
        assert "--- transcript slice (run-2, last 5 assistant blocks) ---" in text
        # First three should be dropped.
        assert "b0" not in text
        assert "b1" not in text
        assert "b2" not in text
        # Last five, in order.
        for expected in ("b3", "b4", "b5", "b6", "b7"):
            assert expected in text
        # Order preservation check: b3 appears before b7.
        assert text.index("b3") < text.index("b7")

    def test_print_fewer_than_5(self) -> None:
        events = [_assistant_event("only-a", "only-b")]
        out = io.StringIO()
        _print_failing_transcript_slice(0, events, out)

        text = out.getvalue()
        # Header still says "last 5" regardless of how many are present.
        assert "last 5 assistant blocks" in text
        assert "only-a" in text
        assert "only-b" in text

    def test_prints_nothing_but_header_when_no_text_blocks(self) -> None:
        events = [{"type": "system", "foo": "bar"}]
        out = io.StringIO()
        _print_failing_transcript_slice(1, events, out)
        text = out.getvalue()
        assert "--- transcript slice (run-1" in text

    def test_ignores_non_text_blocks(self) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "tu_1"},
                        {"type": "text", "text": "keep-me"},
                    ],
                },
            },
        ]
        out = io.StringIO()
        _print_failing_transcript_slice(0, events, out)
        text = out.getvalue()
        assert "keep-me" in text
        assert "tu_1" not in text

    def test_ignores_non_assistant_events(self) -> None:
        events = [
            {
                "type": "system",
                "message": {
                    "content": [{"type": "text", "text": "sys"}]
                },
            },
            _assistant_event("asst"),
        ]
        out = io.StringIO()
        _print_failing_transcript_slice(0, events, out)
        text = out.getvalue()
        assert "asst" in text
        assert "sys" not in text

    def test_tolerates_malformed_events(self) -> None:
        events = [
            "not a dict",
            {"type": "assistant", "message": "not a dict"},
            {"type": "assistant", "message": {"content": "not a list"}},
            {"type": "assistant", "message": {"content": [None, 42, "x"]}},
            _assistant_event("good-one"),
        ]
        out = io.StringIO()
        _print_failing_transcript_slice(0, events, out)
        assert "good-one" in out.getvalue()

    def test_redaction_applied(self) -> None:
        events = [_assistant_event(f"here is my key {_FAKE_KEY} end")]
        out = io.StringIO()
        _print_failing_transcript_slice(0, events, out)
        text = out.getvalue()
        assert _FAKE_KEY not in text
        assert "[REDACTED]" in text

    def test_truncation_2kb(self) -> None:
        big = "x" * 3072  # 3 KB of ASCII → 3 KB of bytes
        events = [_assistant_event(big)]
        out = io.StringIO()
        _print_failing_transcript_slice(0, events, out)
        text = out.getvalue()

        assert _TRANSCRIPT_SLICE_TRUNC_MARKER in text
        # The printed block itself (x-run) should be capped at the byte
        # limit. Count consecutive x's to verify.
        x_count = text.count("x")
        assert x_count == _TRANSCRIPT_SLICE_BLOCK_CAP_BYTES


class TestGradeVerboseInvocation:
    """Drive ``main(['grade', ...])`` end-to-end with mocked runner +
    grader, and assert the real call site inside
    ``_cmd_grade_with_workspace`` (cli.py ~835-840) invokes
    ``_print_failing_transcript_slice`` under the verbose + failing gate.
    """

    def _make_spec(self, eval_spec):
        from unittest.mock import MagicMock

        from clauditor.spec import SkillSpec

        spec = MagicMock(spec=SkillSpec)
        spec.skill_name = "test-skill"
        spec.eval_spec = eval_spec
        return spec

    def _make_eval_spec(self):
        from clauditor.schemas import EvalSpec

        return EvalSpec(
            skill_name="test-skill",
            description="T",
            test_args="--depth quick",
            # Assertion deliberately fails against the mocked output.
            assertions=[{"type": "contains", "value": "WILL_NOT_MATCH"}],
            sections=[],
            grading_criteria=["Is it relevant?"],
            grading_model="claude-sonnet-4-6",
            trigger_tests=None,
            variance=None,
        )

    def _skill_result(self, *, text: str):
        from clauditor.runner import SkillResult

        return SkillResult(
            output=text,
            exit_code=0,
            skill_name="test-skill",
            args="",
            duration_seconds=0.5,
            stream_events=[_assistant_event("assistant ctx line")],
        )

    def _grading_report(self):
        from clauditor.quality_grader import GradingReport, GradingResult

        return GradingReport(
            skill_name="test-skill",
            model="claude-sonnet-4-6",
            results=[
                GradingResult(
                    criterion="Is it relevant?",
                    passed=True,
                    score=1.0,
                    evidence="",
                    reasoning="",
                )
            ],
            duration_seconds=1.0,
        )

    def test_grade_verbose_invokes_slice_on_failure(
        self, tmp_path, monkeypatch
    ) -> None:
        """-v + at least one failing assertion triggers the slice printer
        from inside _cmd_grade_with_workspace."""
        from unittest.mock import AsyncMock

        monkeypatch.chdir(tmp_path)
        spec = self._make_spec(self._make_eval_spec())
        spec.run.return_value = self._skill_result(text="not the target word")
        report = self._grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
            patch(
                "clauditor.cli._print_failing_transcript_slice"
            ) as mock_slice,
        ):
            # Grade's exit code is driven by Layer 3 (grading criteria),
            # not Layer 1 assertions — so rc is 0 here even though the
            # contains-assertion fails. The invariant under test is that
            # the slice printer fires whenever -v is set AND any
            # assertion in a run failed.
            rc = main(["grade", "skill.md", "-v"])

        assert rc == 0
        assert mock_slice.call_count == 1
        # The helper is called with (run_idx, stream_events, stderr).
        args_, _ = mock_slice.call_args
        assert args_[0] == 0  # primary run index

    def test_grade_no_verbose_suppresses_slice(
        self, tmp_path, monkeypatch
    ) -> None:
        """Without -v, the slice printer is never invoked even on
        assertion failure."""
        from unittest.mock import AsyncMock

        monkeypatch.chdir(tmp_path)
        spec = self._make_spec(self._make_eval_spec())
        spec.run.return_value = self._skill_result(text="not the target word")
        report = self._grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
            patch(
                "clauditor.cli._print_failing_transcript_slice"
            ) as mock_slice,
        ):
            rc = main(["grade", "skill.md"])

        assert rc == 0
        assert mock_slice.call_count == 0

    def test_grade_verbose_all_pass_suppresses_slice(
        self, tmp_path, monkeypatch
    ) -> None:
        """-v with all-passing assertions does NOT invoke the slice
        printer — the inner `if aset.failed` gate must fail closed."""
        from unittest.mock import AsyncMock

        from clauditor.schemas import EvalSpec

        monkeypatch.chdir(tmp_path)
        # Assertion now MATCHES the mocked output.
        eval_spec = EvalSpec(
            skill_name="test-skill",
            description="T",
            test_args="--depth quick",
            assertions=[{"type": "contains", "value": "hello"}],
            sections=[],
            grading_criteria=["Is it relevant?"],
            grading_model="claude-sonnet-4-6",
            trigger_tests=None,
            variance=None,
        )
        spec = self._make_spec(eval_spec)
        spec.run.return_value = self._skill_result(text="hello world")
        report = self._grading_report()

        with (
            patch("clauditor.cli.SkillSpec.from_file", return_value=spec),
            patch(
                "clauditor.quality_grader.grade_quality",
                new_callable=AsyncMock,
                return_value=report,
            ),
            patch(
                "clauditor.cli._print_failing_transcript_slice"
            ) as mock_slice,
        ):
            rc = main(["grade", "skill.md", "-v"])

        assert rc == 0
        assert mock_slice.call_count == 0


class TestValidateVerboseInvocation:
    """Smoke test that ``cmd_validate`` invokes the slice printer when
    verbose and at least one assertion fails.
    """

    def test_cmd_validate_invokes_slice_on_failure(self, tmp_path, capsys) -> None:
        from clauditor import cli

        skill_path = tmp_path / "demo.md"
        skill_path.write_text("# demo\nhello\n")
        eval_path = tmp_path / "demo.eval.json"
        eval_path.write_text(
            '{"assertions": [{"id": "a1", "type": "contains", "value": "__nope__"}]}'
        )

        fake_skill_result = MagicMock()
        fake_skill_result.succeeded = True
        fake_skill_result.output = "produced output without the token"
        fake_skill_result.stream_events = [
            _assistant_event("A first chunk of reasoning"),
            _assistant_event("Finally, the answer."),
        ]
        fake_skill_result.duration_seconds = 0.1
        fake_skill_result.input_tokens = 0
        fake_skill_result.output_tokens = 0

        args = MagicMock()
        args.skill = str(skill_path)
        args.eval = str(eval_path)
        args.output = None
        args.json = False
        args.verbose = True
        args.no_transcript = False

        with patch.object(cli.SkillSpec, "from_file") as from_file:
            spec = MagicMock()
            spec.skill_name = "demo"
            spec.eval_spec = MagicMock()
            spec.eval_spec.test_args = ""
            spec.eval_spec.assertions = [
                MagicMock(
                    name="a1",
                    type="contains",
                    value="__nope__",
                    description=None,
                )
            ]
            spec.run.return_value = fake_skill_result
            from_file.return_value = spec

            with patch.object(cli, "run_assertions") as run_assertions:
                run_assertions.return_value = AssertionSet(
                    results=[
                        AssertionResult(
                            name="a1",
                            passed=False,
                            message="missing",
                            kind="custom",
                        )
                    ]
                )
                with patch.object(cli, "history"):
                    with patch.object(
                        cli, "_print_failing_transcript_slice"
                    ) as printer:
                        with patch.object(cli, "allocate_iteration") as alloc:
                            ws = MagicMock()
                            ws.iteration = 1
                            ws.tmp_path = tmp_path / "stage"
                            ws.tmp_path.mkdir()
                            ws.final_path = tmp_path / "final"
                            ws.finalized = False

                            def _finalize():
                                ws.finalized = True

                            ws.finalize.side_effect = _finalize
                            alloc.return_value = ws

                            rc = cli.cmd_validate(args)

                        printer.assert_called_once()
                        call_args = printer.call_args
                        assert call_args.args[0] == 0
                        assert call_args.args[1] == list(
                            fake_skill_result.stream_events
                        )

        assert rc == 1

    def test_cmd_validate_does_not_invoke_slice_when_verbose_off(
        self, tmp_path
    ) -> None:
        from clauditor import cli

        fake_skill_result = MagicMock()
        fake_skill_result.succeeded = True
        fake_skill_result.output = "out"
        fake_skill_result.stream_events = [_assistant_event("x")]
        fake_skill_result.duration_seconds = 0.0
        fake_skill_result.input_tokens = 0
        fake_skill_result.output_tokens = 0

        args = MagicMock()
        args.skill = "demo.md"
        args.eval = None
        args.output = None
        args.json = False
        args.verbose = False
        args.no_transcript = False

        with patch.object(cli.SkillSpec, "from_file") as from_file:
            spec = MagicMock()
            spec.skill_name = "demo"
            spec.eval_spec = MagicMock()
            spec.eval_spec.test_args = ""
            spec.eval_spec.assertions = []
            spec.run.return_value = fake_skill_result
            from_file.return_value = spec

            with patch.object(cli, "run_assertions") as run_assertions:
                run_assertions.return_value = AssertionSet(
                    results=[
                        AssertionResult(
                            name="a1",
                            passed=False,
                            message="missing",
                            kind="custom",
                        )
                    ]
                )
                with patch.object(cli, "history"):
                    with patch.object(
                        cli, "_print_failing_transcript_slice"
                    ) as printer:
                        with patch.object(cli, "allocate_iteration") as alloc:
                            ws = MagicMock()
                            ws.iteration = 1
                            ws.tmp_path = tmp_path / "stage2"
                            ws.tmp_path.mkdir()
                            ws.final_path = tmp_path / "final2"
                            ws.finalized = False

                            def _finalize():
                                ws.finalized = True

                            ws.finalize.side_effect = _finalize
                            alloc.return_value = ws

                            cli.cmd_validate(args)

                        printer.assert_not_called()


@pytest.mark.parametrize("run_idx", [0, 3, 12])
def test_header_includes_run_index(run_idx: int) -> None:
    out = io.StringIO()
    _print_failing_transcript_slice(run_idx, [_assistant_event("hi")], out)
    assert f"run-{run_idx}" in out.getvalue()
