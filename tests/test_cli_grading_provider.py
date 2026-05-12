"""Tests for ``--grading-provider`` flag wiring on the 6 LLM-mediated CLI commands.

Traces to US-005 / DEC-005 / DEC-007 of
``plans/super/146-grading-provider-precedence.md``.

Each LLM-mediated CLI command (``grade``, ``extract``, ``triggers``,
``compare --blind``, ``propose-eval``, ``suggest``) gains a
``--grading-provider {anthropic,openai,auto}`` argparse flag that
threads through the four-layer ``_resolve_grading_provider`` helper to
``check_provider_auth``. Tests cover:

- The flag overrides the spec-declared ``grading_provider``.
- The ``CLAUDITOR_GRADING_PROVIDER`` env var overrides the spec.
- An invalid flag value (e.g. ``--grading-provider foo``) exits 2 from
  argparse.

Per ``.claude/rules/pytester-inprocess-coverage-hazard.md`` these tests
invoke ``main([...])`` directly with ``monkeypatch`` — no pytester
runpytest_inprocess + mock.patch combination.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor.cli import main
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.schemas import GradeThresholds

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _write_skill_md(tmp_path: Path, name: str = "greeter") -> Path:
    """Stage a modern-layout SKILL.md under ``tmp_path``."""
    skill_dir = tmp_path / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\n---\n# {name.title()}\n\nSay hi.\n"
    )
    return skill_md


def _write_eval_json(skill_md: Path, eval_data: dict) -> Path:
    """Write a sibling ``<skill_stem>.eval.json`` next to ``skill_md``."""
    eval_path = skill_md.with_suffix(".eval.json")
    eval_path.write_text(json.dumps(eval_data, indent=2))
    return eval_path


def _grade_eval_data(grading_provider: str | None = None) -> dict:
    data = {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
        "grading_model": "claude-sonnet-4-6",
    }
    if grading_provider is not None:
        data["grading_provider"] = grading_provider
    return data


def _triggers_eval_data(grading_provider: str | None = None) -> dict:
    data = {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
        "grading_model": "claude-sonnet-4-6",
        "trigger_tests": {
            "should_trigger": ["hello there"],
            "should_not_trigger": ["weather today"],
        },
    }
    if grading_provider is not None:
        data["grading_provider"] = grading_provider
    return data


def _extract_eval_data(grading_provider: str | None = None) -> dict:
    data = {
        "skill_name": "greeter",
        "description": "A greeter",
        "test_args": "hello",
        "assertions": [
            {"id": "a1", "type": "contains", "needle": "hello"}
        ],
        "sections": [
            {
                "name": "Results",
                "tiers": [
                    {
                        "label": "primary",
                        "min_entries": 1,
                        "fields": [
                            {"id": "f1", "name": "name", "required": True}
                        ],
                    }
                ],
            }
        ],
        "grading_criteria": [
            {"id": "c1", "criterion": "friendly tone"}
        ],
        "grading_model": "claude-sonnet-4-6",
    }
    if grading_provider is not None:
        data["grading_provider"] = grading_provider
    return data


def _compare_blind_eval_data(grading_provider: str | None = None) -> dict:
    data = {
        "skill_name": "greeter",
        "description": "A greeter",
        "user_prompt": "Say hi to the user.",
        "grading_criteria": [
            {"id": "g1", "criterion": "greets warmly"},
        ],
    }
    if grading_provider is not None:
        data["grading_provider"] = grading_provider
    return data


def _stage_suggest_failing_run(tmp_path: Path) -> Path:
    """Stage the minimum files ``clauditor suggest`` needs to reach the guard.

    Mirrors the helper in tests/test_cli_auth_guard.py.
    """
    (tmp_path / ".git").mkdir()
    skill_md = tmp_path / "greeter.md"
    skill_md.write_text("# Greeter\n\nSay hi.\n")

    skill_dir = tmp_path / ".clauditor" / "iteration-1" / "greeter"
    skill_dir.mkdir(parents=True)

    report = GradingReport(
        skill_name="greeter",
        model="claude-sonnet-4-6",
        results=[
            GradingResult(
                id="c1",
                criterion="friendly tone",
                passed=False,
                score=0.2,
                evidence="e",
                reasoning="r",
            )
        ],
        duration_seconds=0.0,
        thresholds=GradeThresholds(),
        metrics={},
    )
    (skill_dir / "grading.json").write_text(report.to_json())

    assertions_payload = {
        "schema_version": 1,
        "skill": "greeter",
        "iteration": 1,
        "runs": [
            {
                "run": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "results": [
                    {
                        "id": "a1",
                        "name": "contains hello",
                        "passed": False,
                        "kind": "contains",
                        "message": "no match",
                        "transcript_path": None,
                    }
                ],
            }
        ],
    }
    (skill_dir / "assertions.json").write_text(json.dumps(assertions_payload))

    return skill_md


# ---------------------------------------------------------------------------
# grade
# ---------------------------------------------------------------------------


class TestGradeGradingProviderFlag:
    """``clauditor grade --grading-provider`` four-layer precedence."""

    def test_grade_grading_provider_flag_overrides_spec(
        self, tmp_path, monkeypatch
    ):
        """``--grading-provider openai`` wins over spec ``"anthropic"``.

        ``check_provider_auth`` must receive ``"openai"``.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _grade_eval_data(grading_provider="anthropic")
        )
        output = tmp_path / "o.txt"
        output.write_text("captured output")

        guard = MagicMock(return_value=None)
        grade_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.grade.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.quality_grader.grade_quality", new=grade_mock
            ),
        ):
            try:
                main(
                    [
                        "grade",
                        str(skill_md),
                        "--output",
                        str(output),
                        "--grading-provider",
                        "openai",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_grade_env_var_overrides_spec(self, tmp_path, monkeypatch):
        """``CLAUDITOR_GRADING_PROVIDER=openai`` wins over spec ``"anthropic"``."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _grade_eval_data(grading_provider="anthropic")
        )
        output = tmp_path / "o.txt"
        output.write_text("captured output")

        guard = MagicMock(return_value=None)
        grade_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.grade.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.quality_grader.grade_quality", new=grade_mock
            ),
        ):
            try:
                main(
                    [
                        "grade",
                        str(skill_md),
                        "--output",
                        str(output),
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_grade_invalid_flag_value_exits_2(self, tmp_path, monkeypatch, capsys):
        """``--grading-provider foo`` → argparse exits 2."""
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _grade_eval_data())

        with pytest.raises(SystemExit) as excinfo:
            main(["grade", str(skill_md), "--grading-provider", "foo"])

        assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


class TestExtractGradingProviderFlag:
    """``clauditor extract --grading-provider`` four-layer precedence."""

    def test_extract_grading_provider_flag_overrides_spec(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _extract_eval_data(grading_provider="anthropic")
        )
        output = tmp_path / "o.txt"
        output.write_text("captured output")

        guard = MagicMock(return_value=None)
        # Patch extract_and_grade so the test does not attempt a real
        # API call after the auth guard returns.
        extract_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.extract.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.grader.extract_and_grade", new=extract_mock
            ),
        ):
            # Call may raise after the guard; we only care about the
            # guard's call args.
            try:
                main(
                    [
                        "extract",
                        str(skill_md),
                        "--output",
                        str(output),
                        "--grading-provider",
                        "openai",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_extract_env_var_overrides_spec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _extract_eval_data(grading_provider="anthropic")
        )
        output = tmp_path / "o.txt"
        output.write_text("captured output")

        guard = MagicMock(return_value=None)
        extract_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.extract.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.grader.extract_and_grade", new=extract_mock
            ),
        ):
            try:
                main(
                    [
                        "extract",
                        str(skill_md),
                        "--output",
                        str(output),
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_extract_invalid_flag_value_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _extract_eval_data())

        with pytest.raises(SystemExit) as excinfo:
            main(["extract", str(skill_md), "--grading-provider", "foo"])

        assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# triggers
# ---------------------------------------------------------------------------


class TestTriggersGradingProviderFlag:
    """``clauditor triggers --grading-provider`` four-layer precedence."""

    def test_triggers_grading_provider_flag_overrides_spec(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _triggers_eval_data(grading_provider="anthropic")
        )

        guard = MagicMock(return_value=None)
        triggers_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.triggers.check_provider_auth", new=guard
            ),
            patch("clauditor.triggers.test_triggers", new=triggers_mock),
        ):
            try:
                main(
                    [
                        "triggers",
                        str(skill_md),
                        "--grading-provider",
                        "openai",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_triggers_env_var_overrides_spec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _triggers_eval_data(grading_provider="anthropic")
        )

        guard = MagicMock(return_value=None)
        triggers_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.triggers.check_provider_auth", new=guard
            ),
            patch("clauditor.triggers.test_triggers", new=triggers_mock),
        ):
            try:
                main(["triggers", str(skill_md)])
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_triggers_invalid_flag_value_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(skill_md, _triggers_eval_data())

        with pytest.raises(SystemExit) as excinfo:
            main(["triggers", str(skill_md), "--grading-provider", "foo"])

        assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# compare --blind
# ---------------------------------------------------------------------------


class TestCompareBlindGradingProviderFlag:
    """``clauditor compare --blind --grading-provider`` four-layer precedence."""

    def _stage(self, tmp_path: Path, grading_provider: str | None = None):
        skill_md = _write_skill_md(tmp_path)
        _write_eval_json(
            skill_md, _compare_blind_eval_data(grading_provider=grading_provider)
        )
        before = tmp_path / "before.txt"
        after = tmp_path / "after.txt"
        before.write_text("hi there")
        after.write_text("hello friend")
        return skill_md, before, after

    def test_compare_blind_grading_provider_flag_overrides_spec(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        monkeypatch.chdir(tmp_path)
        skill_md, before, after = self._stage(
            tmp_path, grading_provider="anthropic"
        )

        guard = MagicMock(return_value=None)
        blind_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.compare.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.quality_grader.blind_compare_from_spec",
                new=blind_mock,
            ),
        ):
            try:
                main(
                    [
                        "compare",
                        str(before),
                        str(after),
                        "--spec",
                        str(skill_md),
                        "--blind",
                        "--grading-provider",
                        "openai",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_compare_blind_env_var_overrides_spec(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        monkeypatch.chdir(tmp_path)
        skill_md, before, after = self._stage(
            tmp_path, grading_provider="anthropic"
        )

        guard = MagicMock(return_value=None)
        blind_mock = AsyncMock(side_effect=AssertionError("not reached"))
        with (
            patch(
                "clauditor.cli.compare.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.quality_grader.blind_compare_from_spec",
                new=blind_mock,
            ),
        ):
            try:
                main(
                    [
                        "compare",
                        str(before),
                        str(after),
                        "--spec",
                        str(skill_md),
                        "--blind",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_compare_blind_invalid_flag_value_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        skill_md, before, after = self._stage(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "compare",
                    str(before),
                    str(after),
                    "--spec",
                    str(skill_md),
                    "--blind",
                    "--grading-provider",
                    "foo",
                ]
            )

        assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# propose-eval
# ---------------------------------------------------------------------------


class TestProposeEvalGradingProviderFlag:
    """``clauditor propose-eval --grading-provider`` four-layer precedence.

    DEC-005: ``propose-eval`` is no longer hardcoded to ``"anthropic"``.
    """

    def test_propose_eval_grading_provider_flag_routes_to_openai(
        self, tmp_path, monkeypatch
    ):
        """``--grading-provider openai`` flows to ``check_provider_auth``."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        skill_md = _write_skill_md(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        proposer_mock = AsyncMock(
            side_effect=AssertionError("not reached")
        )
        with (
            patch(
                "clauditor.cli.propose_eval.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.cli.propose_eval.propose_eval", new=proposer_mock
            ),
        ):
            try:
                main(
                    [
                        "propose-eval",
                        str(skill_md),
                        "--grading-provider",
                        "openai",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_propose_eval_env_var_routes_to_openai(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        skill_md = _write_skill_md(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        proposer_mock = AsyncMock(
            side_effect=AssertionError("not reached")
        )
        with (
            patch(
                "clauditor.cli.propose_eval.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.cli.propose_eval.propose_eval", new=proposer_mock
            ),
        ):
            try:
                main(["propose-eval", str(skill_md)])
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_propose_eval_invalid_flag_value_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        skill_md = _write_skill_md(tmp_path)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--grading-provider",
                    "foo",
                ]
            )

        assert excinfo.value.code == 2

    def test_propose_eval_openai_provider_picks_openai_default_model(
        self, tmp_path, monkeypatch
    ):
        """QG pass 1 / F1 regression guard: ``--grading-provider openai``
        without ``--model`` must NOT push DEFAULT_PROPOSE_EVAL_MODEL
        (Anthropic) into the OpenAI backend. The peek-at-explicit-provider
        logic in ``cli/propose_eval.py`` should pre-stamp
        ``args.model = gpt-5.4``.
        """
        from clauditor.propose_eval import ProposeEvalReport

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        skill_md = _write_skill_md(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        canned = ProposeEvalReport(skill_name="x", model="gpt-5.4")
        proposer_mock = AsyncMock(return_value=canned)
        with (
            patch(
                "clauditor.cli.propose_eval.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.cli.propose_eval.propose_eval", new=proposer_mock
            ),
        ):
            main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--grading-provider",
                    "openai",
                ]
            )

        assert guard.call_args[0][0] == "openai"
        assert proposer_mock.call_count == 1
        actual_model = proposer_mock.call_args.kwargs["model"]
        assert actual_model.startswith("gpt-"), (
            f"Expected OpenAI model name, got {actual_model!r} — "
            "the Anthropic default leaked into the OpenAI backend."
        )


# ---------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------


class TestSuggestGradingProviderFlag:
    """``clauditor suggest --grading-provider`` four-layer precedence.

    DEC-005: ``suggest`` is no longer hardcoded to Anthropic-only auth.
    """

    def test_suggest_grading_provider_flag_routes_to_openai(
        self, tmp_path, monkeypatch
    ):
        """``--grading-provider openai`` flows to ``check_provider_auth``."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        skill_md = _stage_suggest_failing_run(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        proposer_mock = AsyncMock(
            side_effect=AssertionError("not reached")
        )
        with (
            patch(
                "clauditor.cli.suggest.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.cli.suggest.propose_edits", new=proposer_mock
            ),
        ):
            try:
                main(
                    [
                        "suggest",
                        str(skill_md),
                        "--grading-provider",
                        "openai",
                    ]
                )
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_suggest_env_var_routes_to_openai(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDITOR_GRADING_PROVIDER", "openai")
        skill_md = _stage_suggest_failing_run(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        proposer_mock = AsyncMock(
            side_effect=AssertionError("not reached")
        )
        with (
            patch(
                "clauditor.cli.suggest.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.cli.suggest.propose_edits", new=proposer_mock
            ),
        ):
            try:
                main(["suggest", str(skill_md)])
            except AssertionError:
                pass

        assert guard.call_count == 1
        args, kwargs = guard.call_args
        assert args[0] == "openai"

    def test_suggest_invalid_flag_value_exits_2(
        self, tmp_path, monkeypatch, capsys
    ):
        skill_md = _stage_suggest_failing_run(tmp_path)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "suggest",
                    str(skill_md),
                    "--grading-provider",
                    "foo",
                ]
            )

        assert excinfo.value.code == 2

    def test_suggest_openai_provider_picks_openai_default_model(
        self, tmp_path, monkeypatch
    ):
        """QG pass 2 / F1 regression guard: ``--grading-provider openai``
        without ``--model`` must NOT push the Anthropic default into the
        OpenAI backend. The peek-at-explicit-provider logic in
        ``cli/suggest.py`` should pre-stamp ``args.model = gpt-5.4``.
        """
        from clauditor.suggest import SuggestReport

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        skill_md = _stage_suggest_failing_run(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        # Use return_value (not side_effect=AssertionError) so we can
        # observe the model kwarg the orchestrator received.
        canned = SuggestReport(
            skill_name="x",
            model="gpt-5.4",
            generated_at="",
            source_iteration=0,
            source_grading_path="",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        proposer_mock = AsyncMock(return_value=canned)
        with (
            patch(
                "clauditor.cli.suggest.check_provider_auth", new=guard
            ),
            patch(
                "clauditor.cli.suggest.propose_edits", new=proposer_mock
            ),
        ):
            main(
                [
                    "suggest",
                    str(skill_md),
                    "--grading-provider",
                    "openai",
                    "--json",
                ]
            )

        assert guard.call_args[0][0] == "openai"
        assert proposer_mock.call_count == 1
        # Critical assertion: the model passed to the OpenAI provider
        # must be an OpenAI model name, not a Claude default.
        actual_model = proposer_mock.call_args.kwargs["model"]
        assert actual_model.startswith("gpt-"), (
            f"Expected OpenAI model name, got {actual_model!r} — "
            "the Anthropic default leaked into the OpenAI backend."
        )

    def test_suggest_explicit_anthropic_pre_stamps_claude_default(
        self, tmp_path, monkeypatch
    ):
        """``suggest --grading-provider anthropic`` (no ``--model``) →
        pre-stamps ``claude-sonnet-4-6`` so the resolver auto-infers
        anthropic, and the orchestrator receives a coherent
        (provider=anthropic, model=claude-...) pair. Covers
        ``suggest.py:291-292`` (the explicit-anthropic branch of the
        peek-at-explicit-provider model defaulting block).
        """
        from clauditor.suggest import SuggestReport

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("CLAUDITOR_GRADING_PROVIDER", raising=False)
        skill_md = _stage_suggest_failing_run(tmp_path)
        monkeypatch.chdir(tmp_path)

        guard = MagicMock(return_value=None)
        canned = SuggestReport(
            skill_name="x",
            model="claude-sonnet-4-6",
            generated_at="",
            source_iteration=0,
            source_grading_path="",
            input_tokens=0,
            output_tokens=0,
            duration_seconds=0.0,
        )
        proposer_mock = AsyncMock(return_value=canned)
        with (
            patch("clauditor.cli.suggest.check_provider_auth", new=guard),
            patch("clauditor.cli.suggest.propose_edits", new=proposer_mock),
        ):
            main(
                [
                    "suggest",
                    str(skill_md),
                    "--grading-provider",
                    "anthropic",
                    "--json",
                ]
            )

        assert guard.call_args[0][0] == "anthropic"
        assert proposer_mock.call_count == 1
        actual_model = proposer_mock.call_args.kwargs["model"]
        assert actual_model.startswith("claude-")
