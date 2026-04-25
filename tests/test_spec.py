"""Tests for SkillSpec: from_file, run, evaluate."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import clauditor.spec as _spec_mod

importlib.reload(_spec_mod)

from clauditor.runner import SkillResult  # noqa: E402
from clauditor.spec import SkillSpec, _failed_run_result  # noqa: E402

# ── Minimal eval data for fixture ──────────────────────────────────────────

MINIMAL_EVAL = {
    "skill_name": "test-skill",
    "description": "test eval",
    "test_args": "--depth quick",
    "assertions": [{"id": "a_hello", "type": "contains", "needle": "hello"}],
}


class TestFromFile:
    """SkillSpec.from_file factory method."""

    def test_missing_skill_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Skill file not found"):
            SkillSpec.from_file(tmp_path / "nonexistent.md")

    def test_loads_skill_without_eval(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("bare-skill")
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_path == skill_path
        assert spec.skill_name == "bare-skill"
        assert spec.eval_spec is None

    def test_auto_discovers_sibling_eval(self, tmp_skill_file, mock_runner):
        skill_path, eval_path = tmp_skill_file("my-skill", eval_data=MINIMAL_EVAL)
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.eval_spec is not None
        assert spec.eval_spec.skill_name == "test-skill"

    def test_explicit_eval_path(self, tmp_skill_file, tmp_path, mock_runner):
        skill_path = tmp_skill_file("a-skill")
        # Write eval json at a non-sibling location
        custom_eval = tmp_path / "custom.eval.json"
        custom_eval.write_text(json.dumps(MINIMAL_EVAL))
        spec = SkillSpec.from_file(
            skill_path, eval_path=custom_eval, runner=mock_runner()
        )
        assert spec.eval_spec is not None
        assert spec.eval_spec.test_args == "--depth quick"

    # ── Layout-aware identity derivation (DEC-001, DEC-002, DEC-009) ──

    def test_from_file_modern_layout_matching_frontmatter(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout, frontmatter ``name:`` matches parent dir → silent."""
        skill_path = tmp_skill_file(
            "foo",
            content="---\nname: foo\n---\n# Foo\n",
            layout="modern",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_from_file_modern_layout_disagreement_silent(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout, frontmatter ``name:`` disagrees → frontmatter
        wins (DEC-002). Per DEC-008 of
        ``plans/super/71-agentskills-lint.md``, ``derive_skill_name`` no
        longer emits a stderr warning for the mismatch — the equivalent
        ``AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`` conformance code is
        routed through US-006's soft-warn hook (out of scope for this
        test)."""
        skill_path = tmp_skill_file(
            "foo",
            content="---\nname: bar\n---\n# Bar\n",
            layout="modern",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "bar"
        captured = capsys.readouterr()
        assert "frontmatter name" not in captured.err

    def test_from_file_modern_layout_missing_name_silent(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout with no frontmatter → falls back to parent dir
        name silently (DEC-001)."""
        skill_path = tmp_skill_file(
            "foo",
            content="# Foo\n\nNo frontmatter here.\n",
            layout="modern",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_from_file_legacy_layout_matching_frontmatter(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Legacy layout, frontmatter ``name:`` matches stem → name-related
        warnings stay silent.

        Per DEC-005 + DEC-003 of ``plans/super/71-agentskills-lint.md``,
        legacy single-file layouts unconditionally fire
        ``AGENTSKILLS_LAYOUT_LEGACY`` (warning severity) via the US-006
        soft-warn hook, so stderr is no longer empty — but the
        frontmatter name matches the stem, so no
        ``AGENTSKILLS_NAME_*`` warnings appear.
        """
        skill_path = tmp_skill_file(
            "foo",
            content="---\nname: foo\n---\n# Foo\n",
            layout="legacy",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert "AGENTSKILLS_LAYOUT_LEGACY" in captured.err
        assert "AGENTSKILLS_NAME_" not in captured.err

    def test_from_file_legacy_layout_missing_name_silent(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Legacy layout with no frontmatter → falls back to stem; name
        path stays silent.

        Per DEC-005 + DEC-003 of ``plans/super/71-agentskills-lint.md``,
        ``AGENTSKILLS_LAYOUT_LEGACY`` (warning) fires unconditionally,
        but ``AGENTSKILLS_NAME_MISSING`` is error-severity and the hook
        filters errors out — so no ``AGENTSKILLS_NAME_*`` warning
        appears here. This mirrors today's default legacy skill shape
        (no frontmatter).
        """
        skill_path = tmp_skill_file(
            "my-skill",
            content="# My Skill\n\nNo frontmatter here.\n",
            layout="legacy",
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "my-skill"
        captured = capsys.readouterr()
        assert "AGENTSKILLS_LAYOUT_LEGACY" in captured.err
        assert "AGENTSKILLS_NAME_" not in captured.err

    def test_init_with_nonexistent_path_uses_layout_fallback(self):
        """Direct ``SkillSpec(...)`` construction with a non-existent path
        must not call ``read_text`` and must derive ``skill_name`` from
        the layout (modern → parent dir, legacy → stem). Regression guard
        for DEC-006 — the path taken by
        ``tests/test_quality_grader.py`` when building a spec with a
        placeholder ``Path("dummy.md")``.
        """
        # Modern fallback: path is a named dir / SKILL.md.
        spec_modern = SkillSpec(skill_path=Path("/nonexistent/foo/SKILL.md"))
        assert spec_modern.skill_name == "foo"

        # Legacy fallback: path is <stem>.md.
        spec_legacy = SkillSpec(skill_path=Path("/nonexistent/bar.md"))
        assert spec_legacy.skill_name == "bar"

    # ── Conformance soft-warn hook (US-006; DEC-003, DEC-014) ──────────

    def test_from_file_emits_warning_conformance_to_stderr(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout + a long body → the hook emits
        ``AGENTSKILLS_BODY_TOO_LONG`` (warning) with the
        ``"clauditor.conformance: "`` prefix per DEC-014.
        """
        # Body with 501 lines (>500 threshold).
        long_body = "\n".join(f"line {i}" for i in range(501))
        content = (
            "---\n"
            "name: foo\n"
            "description: A skill with an overly long body.\n"
            "---\n"
            f"{long_body}\n"
        )
        skill_path = tmp_skill_file(
            "foo", content=content, layout="modern"
        )
        SkillSpec.from_file(skill_path, runner=mock_runner())
        captured = capsys.readouterr()
        assert "clauditor.conformance: AGENTSKILLS_BODY_TOO_LONG" in (
            captured.err
        )

    def test_from_file_silent_on_error_conformance(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout with only error-severity conformance issues (and
        no warnings) → hook emits nothing. Errors surface via
        ``clauditor lint``, not via ``SkillSpec.from_file`` (DEC-003).
        """
        # Empty `name:` → AGENTSKILLS_NAME_EMPTY (error). No body
        # issues, no other warnings.
        content = (
            "---\n"
            'name: ""\n'
            "description: test\n"
            "---\n"
            "# Body\n"
        )
        skill_path = tmp_skill_file(
            "foo", content=content, layout="modern"
        )
        SkillSpec.from_file(skill_path, runner=mock_runner())
        captured = capsys.readouterr()
        assert "clauditor.conformance:" not in captured.err

    def test_from_file_emits_only_warnings_when_mixed(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Modern layout that produces BOTH an error AND a warning → the
        hook emits only the warning line. DEC-003: errors are silent at
        this layer.
        """
        # Empty `name:` (error) + `allowed-tools` field present
        # (AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL warning).
        content = (
            "---\n"
            'name: ""\n'
            "description: test\n"
            "allowed-tools: Bash(ls)\n"
            "---\n"
            "# Body\n"
        )
        skill_path = tmp_skill_file(
            "foo", content=content, layout="modern"
        )
        SkillSpec.from_file(skill_path, runner=mock_runner())
        captured = capsys.readouterr()
        assert (
            "clauditor.conformance: AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL"
            in captured.err
        )
        assert "AGENTSKILLS_NAME_EMPTY" not in captured.err

    def test_from_file_does_not_raise_on_malformed_yaml(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """Malformed frontmatter → ``SkillSpec.from_file`` returns without
        raising. The conformance issue ``AGENTSKILLS_FRONTMATTER_INVALID_YAML``
        is error-severity, so the hook filters it out and stderr does
        NOT gain a ``"clauditor.conformance:"`` line from the hook.
        """
        # Frontmatter block with an opening ``---`` but no closing
        # delimiter — parse_frontmatter raises ValueError here.
        content = (
            "---\n"
            "name: foo\n"
            "description: broken\n"
            "# body (note: missing closing ---)\n"
        )
        skill_path = tmp_skill_file(
            "foo", content=content, layout="modern"
        )
        # Must not raise.
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec is not None
        captured = capsys.readouterr()
        assert "clauditor.conformance:" not in captured.err

    def test_from_file_hook_preserves_skill_name(
        self, tmp_skill_file, mock_runner, capsys
    ):
        """A fully-passing modern skill → ``spec.skill_name`` comes from
        frontmatter ``name:`` and stderr stays empty (no warnings, no
        errors).
        """
        content = (
            "---\n"
            "name: foo\n"
            "description: A well-formed skill.\n"
            "---\n"
            "# Foo\n"
        )
        skill_path = tmp_skill_file(
            "foo", content=content, layout="modern"
        )
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        assert spec.skill_name == "foo"
        captured = capsys.readouterr()
        assert captured.err == ""


class TestRun:
    """SkillSpec.run method."""

    def test_run_with_explicit_args(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("run-skill")
        runner = mock_runner(output="explicit output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run(args="--custom flag")
        runner.run.assert_called_once_with(
            "run-skill",
            "--custom flag",
            cwd=None,
            allow_hang_heuristic=True,
            timeout=None,
            env=None,
        )
        assert result.output == "explicit output"

    def test_run_uses_eval_test_args_when_no_args(self, tmp_skill_file, mock_runner):
        skill_path, _ = tmp_skill_file("run-skill", eval_data=MINIMAL_EVAL)
        runner = mock_runner(output="eval args output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        runner.run.assert_called_once_with(
            "run-skill",
            "--depth quick",
            cwd=None,
            allow_hang_heuristic=True,
            timeout=None,
            env=None,
        )

    def test_run_uses_empty_string_when_no_eval_no_args(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("run-skill")
        runner = mock_runner(output="empty args output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        runner.run.assert_called_once_with(
            "run-skill",
            "",
            cwd=None,
            allow_hang_heuristic=True,
            timeout=None,
            env=None,
        )


class TestEvaluate:
    """SkillSpec.evaluate method."""

    def test_raises_when_no_eval_spec(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("no-eval")
        spec = SkillSpec.from_file(skill_path, runner=mock_runner())
        with pytest.raises(ValueError, match="No eval spec found"):
            spec.evaluate()

    def test_happy_path_with_explicit_output(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "eval-skill",
            "assertions": [{"id": "a_hello", "type": "contains", "needle": "hello"}],
        }
        skill_path, _ = tmp_skill_file("eval-skill", eval_data=eval_data)
        runner = mock_runner()
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate(output="hello world")
        assert result.passed
        # Runner should NOT have been called since we provided output
        runner.run.assert_not_called()

    def test_evaluate_runs_skill_when_no_output(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "auto-skill",
            "assertions": [{"id": "a_mock", "type": "contains", "needle": "mock"}],
        }
        skill_path, _ = tmp_skill_file("auto-skill", eval_data=eval_data)
        runner = mock_runner(output="mock output")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert result.passed
        runner.run.assert_called_once()

    def test_evaluate_returns_error_on_failed_run(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "fail-skill",
            "assertions": [{"id": "a_any", "type": "contains", "needle": "anything"}],
        }
        skill_path, _ = tmp_skill_file("fail-skill", eval_data=eval_data)
        runner = mock_runner(output="", exit_code=1, error="boom")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert not result.passed
        assert len(result.results) == 1
        assert "failed to run" in result.results[0].message
        assert "boom" in result.results[0].message


class TestEvaluateFailureClassification:
    """US-007: ``evaluate()`` uses ``succeeded_cleanly`` (strict) so an
    apparently-successful run that actually hit an error signal still
    short-circuits to an assertion-failure, and the failure message
    reflects the right source (``error`` text vs interactive-hang
    warning vs generic fallback). Per DEC-006 / DEC-010 of
    ``plans/super/63-runner-error-surfacing.md``.
    """

    EVAL_DATA = {
        "skill_name": "classify-skill",
        "assertions": [
            {"id": "a_hello", "type": "contains", "needle": "hello"},
        ],
    }

    def test_interactive_hang_produces_assertion_failure(
        self, tmp_skill_file, mock_runner
    ):
        """A run with output + exit_code=0 + ``error_category='interactive'``
        + an ``"interactive-hang:"`` warning is ``succeeded=True`` (lenient)
        but ``succeeded_cleanly=False`` (strict). Assertion evaluation
        short-circuits and the failure message reflects the warning.
        """
        skill_path, _ = tmp_skill_file(
            "classify-skill", eval_data=self.EVAL_DATA
        )
        runner = mock_runner()
        # Replace the runner's return_value with one carrying the
        # interactive-hang signal the fixture doesn't expose directly.
        # Import the canonical string so a future rename propagates here
        # instead of leaving this test silently out of sync.
        from clauditor.runner import _INTERACTIVE_HANG_WARNING

        hang_warning = _INTERACTIVE_HANG_WARNING
        runner.run.return_value = SkillResult(
            output="What color would you like?",
            exit_code=0,
            skill_name="classify-skill",
            args="",
            error=None,
            error_category="interactive",
            warnings=[hang_warning],
        )

        spec = SkillSpec.from_file(skill_path, runner=runner)
        # Sanity-check the predicates agree with the plan.
        assert runner.run.return_value.succeeded is True
        assert runner.run.return_value.succeeded_cleanly is False

        result = spec.evaluate()
        assert not result.passed
        assert len(result.results) == 1
        msg = result.results[0].message
        assert "failed to run" in msg
        assert "interactive-hang:" in msg

    def test_429_still_fails_as_before(self, tmp_skill_file, mock_runner):
        """A ``rate_limit``-categorized run with a non-None ``error`` text
        continues to land in the assertion-failure branch with the error
        text surfaced — behavior unchanged from the pre-migration path.
        """
        skill_path, _ = tmp_skill_file(
            "classify-skill", eval_data=self.EVAL_DATA
        )
        runner = mock_runner()
        runner.run.return_value = SkillResult(
            output="",
            exit_code=1,
            skill_name="classify-skill",
            args="",
            error="API Error: 429 Too Many Requests",
            error_category="rate_limit",
        )

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert not result.passed
        assert len(result.results) == 1
        msg = result.results[0].message
        assert "failed to run" in msg
        assert "429" in msg

    def test_clean_success_still_passes(self, tmp_skill_file, mock_runner):
        """A ``succeeded_cleanly=True`` run (no error, no category, no
        interactive-hang warning) proceeds to normal assertion evaluation.
        Regression guard for the lenient-vs-strict split.
        """
        skill_path, _ = tmp_skill_file(
            "classify-skill", eval_data=self.EVAL_DATA
        )
        runner = mock_runner(output="hello, world")
        # Default fixture construction already yields a cleanly-succeeding
        # result; verify the predicates to anchor the regression.
        assert runner.run.return_value.succeeded_cleanly is True

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert result.passed

    def test_interactive_category_without_warning_uses_fallback(
        self, tmp_skill_file, mock_runner
    ):
        """Defensive branch: ``error_category='interactive'`` but no
        matching warning in ``warnings`` falls back to the
        ``'interactive hang detected'`` literal.
        """
        skill_path, _ = tmp_skill_file(
            "classify-skill", eval_data=self.EVAL_DATA
        )
        runner = mock_runner()
        runner.run.return_value = SkillResult(
            output="some output?",
            exit_code=0,
            skill_name="classify-skill",
            args="",
            error=None,
            error_category="interactive",
            warnings=[],  # no interactive-hang: prefix entry
        )

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.evaluate()
        assert not result.passed
        msg = result.results[0].message
        assert "interactive hang detected" in msg

    def test_generic_unknown_error_fallback(
        self, tmp_skill_file, mock_runner
    ):
        """Defensive branch: no error text, no interactive category, but
        ``succeeded_cleanly=False`` (e.g. ``succeeded=False`` due to
        empty output / nonzero exit). Falls back to ``'Unknown error'``.
        """
        skill_path, _ = tmp_skill_file(
            "classify-skill", eval_data=self.EVAL_DATA
        )
        runner = mock_runner()
        runner.run.return_value = SkillResult(
            output="",
            exit_code=1,
            skill_name="classify-skill",
            args="",
            error=None,
            error_category=None,
            warnings=[],
        )

        spec = SkillSpec.from_file(skill_path, runner=runner)
        assert runner.run.return_value.succeeded_cleanly is False
        result = spec.evaluate()
        assert not result.passed
        msg = result.results[0].message
        assert "Unknown error" in msg


class TestFileBasedOutput:
    """SkillSpec.run with file-based output (output_file / output_files)."""

    def test_output_file_reads_file_content(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "file-skill",
            "test_args": "",
            "assertions": [],
            "output_file": "results/output.txt",
        }
        skill_path, _ = tmp_skill_file("file-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout content")
        # runner.project_dir must point to tmp_path so we can create the file
        project_dir = skill_path.parent
        runner.project_dir = project_dir
        # Create the output file
        (project_dir / "results").mkdir()
        (project_dir / "results" / "output.txt").write_text("file content here")

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.output == "file content here"

    def test_output_file_missing_keeps_stdout(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "file-skill",
            "test_args": "",
            "assertions": [],
            "output_file": "nonexistent.txt",
        }
        skill_path, _ = tmp_skill_file("file-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout fallback")
        runner.project_dir = skill_path.parent
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.output == "stdout fallback"

    def test_output_files_glob_populates_outputs(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "glob-skill",
            "test_args": "",
            "assertions": [],
            "output_files": ["out/*.txt"],
        }
        skill_path, _ = tmp_skill_file("glob-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout content")
        project_dir = skill_path.parent
        runner.project_dir = project_dir
        # Create matching files
        (project_dir / "out").mkdir()
        (project_dir / "out" / "a.txt").write_text("alpha")
        (project_dir / "out" / "b.txt").write_text("beta")

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert len(result.outputs) == 2
        assert result.outputs["out/a.txt"] == "alpha"
        assert result.outputs["out/b.txt"] == "beta"
        # result.output should be set to the first file read
        assert result.output == "alpha"

    def test_no_output_file_fields_keeps_stdout(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "plain-skill",
            "test_args": "",
            "assertions": [],
        }
        skill_path, _ = tmp_skill_file("plain-skill", eval_data=eval_data)
        runner = mock_runner(output="just stdout")
        runner.project_dir = skill_path.parent
        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.output == "just stdout"
        assert result.outputs == {}


class TestOutputFilesResolutionWithStagedInputs:
    """When input_files is staged, output_files must glob from the staging CWD,
    not the runner's project_dir — otherwise mutated outputs are lost."""

    def test_output_files_resolves_against_staging_cwd(
        self, tmp_path, mock_runner
    ):
        skill_dir = tmp_path / ".claude" / "commands"
        skill_dir.mkdir(parents=True)
        (skill_dir / "csv-cleaner.md").write_text("# CSV cleaner\n")
        (skill_dir / "sales.csv").write_text("a,b\n1,2\n")
        (skill_dir / "csv-cleaner.eval.json").write_text(
            json.dumps(
                {
                    "skill_name": "csv-cleaner",
                    "test_args": "",
                    "assertions": [],
                    "input_files": ["sales.csv"],
                    "output_files": ["cleaned.csv"],
                }
            )
        )

        runner = mock_runner(output="stdout transcript")
        runner.project_dir = tmp_path  # repo root, NOT the staging dir

        run_dir = tmp_path / "iter-tmp" / "csv-cleaner" / "run-0"
        run_dir.mkdir(parents=True)
        cleaned_text = "header\nclean,row\n"

        # Side-effect: the "skill" writes cleaned.csv into its staging CWD.
        base_result = runner.run.return_value

        def side_effect(
            skill_name,
            args,
            *,
            cwd=None,
            allow_hang_heuristic=True,
            timeout=None,
            env=None,
        ):
            assert cwd == run_dir / "inputs"
            (cwd / "cleaned.csv").write_text(cleaned_text)
            return base_result

        runner.run.side_effect = side_effect

        spec = SkillSpec.from_file(skill_dir / "csv-cleaner.md", runner=runner)
        result = spec.run(run_dir=run_dir)

        assert "cleaned.csv" in result.outputs
        assert result.outputs["cleaned.csv"] == cleaned_text
        assert result.output == cleaned_text

    def test_output_files_without_input_files_still_uses_project_dir(
        self, tmp_skill_file, mock_runner
    ):
        # Regression guard: pre-existing output_files behavior is unchanged
        # when no input_files are declared.
        eval_data = {
            "skill_name": "glob-skill",
            "test_args": "",
            "assertions": [],
            "output_files": ["out/*.txt"],
        }
        skill_path, _ = tmp_skill_file("glob-skill", eval_data=eval_data)
        runner = mock_runner(output="stdout content")
        project_dir = skill_path.parent
        runner.project_dir = project_dir
        (project_dir / "out").mkdir()
        (project_dir / "out" / "a.txt").write_text("alpha")

        spec = SkillSpec.from_file(skill_path, runner=runner)
        result = spec.run()
        assert result.outputs["out/a.txt"] == "alpha"


class TestSkillSpecRunWithInputFiles:
    """US-003: run_dir staging hook for EvalSpec.input_files."""

    def test_spec_run_without_run_dir_uses_project_dir(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("no-rd-skill")
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        # cwd kwarg defaults to None (runner falls back to project_dir)
        assert runner.run.call_args.kwargs.get("cwd") is None

    def test_spec_run_with_empty_input_files_does_not_stage(
        self, tmp_skill_file, mock_runner, tmp_path
    ):
        eval_data = {
            "skill_name": "empty-inputs",
            "test_args": "",
            "assertions": [],
            "input_files": [],
        }
        skill_path, _ = tmp_skill_file("empty-inputs", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        with patch("clauditor.spec.stage_inputs") as mock_stage:
            spec.run(run_dir=tmp_path / "run-0")
        mock_stage.assert_not_called()
        assert runner.run.call_args.kwargs.get("cwd") is None

    def test_spec_run_with_input_files_stages_and_sets_cwd(
        self, tmp_skill_file, mock_runner, tmp_path
    ):
        # Create sibling input files next to the skill
        (tmp_path / "data1.txt").write_text("one")
        (tmp_path / "data2.txt").write_text("two")
        eval_data = {
            "skill_name": "staging-skill",
            "test_args": "",
            "assertions": [],
            "input_files": ["data1.txt", "data2.txt"],
        }
        skill_path, _ = tmp_skill_file("staging-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)

        run_dir = tmp_path / "run-0"
        run_dir.mkdir()
        spec.run(run_dir=run_dir)

        assert (run_dir / "inputs" / "data1.txt").read_text() == "one"
        assert (run_dir / "inputs" / "data2.txt").read_text() == "two"
        assert runner.run.call_args.kwargs.get("cwd") == run_dir / "inputs"

    def test_spec_run_with_input_files_emits_staged_log_line(
        self, tmp_skill_file, mock_runner, tmp_path, capsys
    ):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        eval_data = {
            "skill_name": "log-skill",
            "test_args": "",
            "assertions": [],
            "input_files": ["a.txt", "b.txt"],
        }
        skill_path, _ = tmp_skill_file("log-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)

        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        spec.run(run_dir=run_dir)

        captured = capsys.readouterr()
        assert "Staged 2 input file(s)" in captured.out


class TestFailedRunResult:
    """_failed_run_result helper."""

    def test_returns_failed_assertion_result(self):
        r = _failed_run_result("my-skill", "timeout")
        assert r.passed is False
        assert "my-skill" in r.message
        assert "timeout" in r.message
        assert r.name == "skill_execution"


class TestAllowHangHeuristicThreading:
    """DEC-005 / US-003: the ``allow_hang_heuristic`` flag threads from the
    EvalSpec through ``SkillSpec.run`` into ``SkillRunner.run(...)``.
    """

    def test_eval_spec_false_threads_to_runner(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {
            "skill_name": "off-skill",
            "test_args": "",
            "assertions": [],
            "allow_hang_heuristic": False,
        }
        skill_path, _ = tmp_skill_file("off-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert (
            runner.run.call_args.kwargs.get("allow_hang_heuristic") is False
        )

    def test_eval_spec_default_threads_true(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {
            "skill_name": "on-skill",
            "test_args": "",
            "assertions": [],
        }
        skill_path, _ = tmp_skill_file("on-skill", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert (
            runner.run.call_args.kwargs.get("allow_hang_heuristic") is True
        )

    def test_no_eval_spec_threads_true(self, tmp_skill_file, mock_runner):
        skill_path = tmp_skill_file("bare-skill")
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert (
            runner.run.call_args.kwargs.get("allow_hang_heuristic") is True
        )


class TestSyncTasksPrecedence:
    """Tier 1.5 of GitHub #103: ``SkillSpec.run`` resolves the
    effective sync-tasks mode as CLI > spec > default ``False`` per
    ``.claude/rules/spec-cli-precedence.md``. When effective,
    ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` is injected into the
    ``env`` dict threaded to ``SkillRunner.run``. Composes with an
    existing ``env_override`` — both effects apply.
    """

    _VAR = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"

    def _env_from_call(self, runner):
        return runner.run.call_args.kwargs.get("env")

    def test_default_no_cli_no_spec_leaves_env_untouched(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {"skill_name": "s", "test_args": "", "assertions": []}
        skill_path, _ = tmp_skill_file("s", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        # No sync-tasks source → env passed through as-is (None).
        assert self._env_from_call(runner) is None

    def test_cli_override_true_sets_env_var(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {"skill_name": "s", "test_args": "", "assertions": []}
        skill_path, _ = tmp_skill_file("s", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(sync_tasks_override=True)
        env = self._env_from_call(runner)
        assert env is not None
        assert env[self._VAR] == "1"

    def test_spec_true_sets_env_var_when_cli_absent(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {
            "skill_name": "s",
            "test_args": "",
            "assertions": [],
            "sync_tasks": True,
        }
        skill_path, _ = tmp_skill_file("s", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        env = self._env_from_call(runner)
        assert env is not None
        assert env[self._VAR] == "1"

    def test_cli_override_wins_over_spec(
        self, tmp_skill_file, mock_runner
    ):
        """The spec says sync_tasks=False but the CLI forces True;
        precedence says CLI wins."""
        eval_data = {
            "skill_name": "s",
            "test_args": "",
            "assertions": [],
            "sync_tasks": False,
        }
        skill_path, _ = tmp_skill_file("s", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(sync_tasks_override=True)
        env = self._env_from_call(runner)
        assert env is not None
        assert env[self._VAR] == "1"

    def test_composes_with_env_override(
        self, tmp_skill_file, mock_runner
    ):
        """When --no-api-key already built an env_override dict,
        --sync-tasks adds the var without losing the strip."""
        eval_data = {"skill_name": "s", "test_args": "", "assertions": []}
        skill_path, _ = tmp_skill_file("s", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        stripped = {"PATH": "/usr/bin"}  # no auth keys
        spec.run(
            env_override=stripped,
            sync_tasks_override=True,
        )
        env = self._env_from_call(runner)
        assert env is not None
        assert env[self._VAR] == "1"
        assert env["PATH"] == "/usr/bin"
        assert "ANTHROPIC_API_KEY" not in env

    def test_sync_tasks_override_false_does_not_set_var(
        self, tmp_skill_file, mock_runner
    ):
        """Explicit CLI False overrides spec True (defensive
        precedence direction: operator can disable forced-sync even
        when the spec author set it)."""
        eval_data = {
            "skill_name": "s",
            "test_args": "",
            "assertions": [],
            "sync_tasks": True,
        }
        skill_path, _ = tmp_skill_file("s", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(sync_tasks_override=False)
        env = self._env_from_call(runner)
        # No env mutation should have happened since effective=False.
        assert env is None


class TestTimeoutPrecedence:
    """DEC-002 / US-005: ``SkillSpec.run`` resolves the effective timeout
    as CLI > spec > default, and threads ``env_override`` through to
    ``SkillRunner.run(env=...)`` unchanged (no precedence merge per DEC-013).
    """

    def test_cli_override_wins(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "cli-wins",
            "test_args": "",
            "assertions": [],
            "timeout": 300,
        }
        skill_path, _ = tmp_skill_file("cli-wins", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(timeout_override=60)
        assert runner.run.call_args.kwargs.get("timeout") == 60

    def test_spec_wins_when_no_cli_override(
        self, tmp_skill_file, mock_runner
    ):
        eval_data = {
            "skill_name": "spec-wins",
            "test_args": "",
            "assertions": [],
            "timeout": 300,
        }
        skill_path, _ = tmp_skill_file("spec-wins", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(timeout_override=None)
        assert runner.run.call_args.kwargs.get("timeout") == 300

    def test_default_when_neither_set(self, tmp_skill_file, mock_runner):
        eval_data = {
            "skill_name": "both-none",
            "test_args": "",
            "assertions": [],
        }
        skill_path, _ = tmp_skill_file("both-none", eval_data=eval_data)
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run()
        assert runner.run.call_args.kwargs.get("timeout") is None

    def test_env_override_threaded_through(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("env-dict")
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(env_override={"FOO": "bar"})
        assert runner.run.call_args.kwargs.get("env") == {"FOO": "bar"}

    def test_env_override_none_threaded_through(
        self, tmp_skill_file, mock_runner
    ):
        skill_path = tmp_skill_file("env-none")
        runner = mock_runner(output="ok")
        spec = SkillSpec.from_file(skill_path, runner=runner)
        spec.run(env_override=None)
        assert runner.run.call_args.kwargs.get("env") is None

    def test_eval_spec_none_path(self, tmp_skill_file, mock_runner):
        # Direct-constructor path: ``eval_spec`` is None. Timeout
        # resolution must still work and default to None (runner falls
        # back to its own ``self.timeout``).
        skill_path = tmp_skill_file("no-spec")
        runner = mock_runner(output="ok")
        spec = SkillSpec(skill_path=skill_path, eval_spec=None, runner=runner)
        spec.run()
        assert runner.run.call_args.kwargs.get("timeout") is None


# Path to the checked-in example eval spec used by
# ``TestExampleEvalSpec`` below. Defined once at module scope so both
# the class and any future regression tests can reference it.
_REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_EVAL_JSON = (
    _REPO_ROOT
    / "examples"
    / ".claude"
    / "skills"
    / "find-kid-activities"
    / "SKILL.eval.json"
)


class TestExampleEvalSpec:
    """Regression: the checked-in example spec loads via ``EvalSpec.from_file``.

    Traces to DEC-001 / DEC-002 of
    ``plans/super/67-per-type-assertion-keys.md``: every assertion
    entry uses the per-type semantic key (``needle`` / ``pattern`` /
    ``length`` / ``count``) and counts/lengths are native JSON ints.
    A future migration that misses this file will surface here as a
    load-time ``ValueError`` from ``_require_assertion_keys``.
    """

    def test_example_eval_spec_loads(self):
        # Import via the normal schemas path; ``EvalSpec.from_file``
        # delegates to ``from_dict`` which runs the per-type
        # required-key + type-check validator from US-001.
        from clauditor.schemas import EvalSpec

        # Must not raise.
        spec = EvalSpec.from_file(EXAMPLE_EVAL_JSON)
        assert spec.skill_name == "find-kid-activities"
        # The load-bearing invariant is "loads without error" — avoid
        # hard-coding the exact count, which would flip red on any
        # legitimate addition/removal to the example spec for the
        # wrong reason.
        assert len(spec.assertions) >= 1

    def test_example_eval_spec_has_no_legacy_value_keys(self):
        # Substring guard: the migrated file must not contain any
        # ``"value":`` keys in assertion dicts. Checking the raw JSON
        # text is cheap and catches regressions that re-introduce the
        # legacy shape via copy-paste.
        raw = EXAMPLE_EVAL_JSON.read_text(encoding="utf-8")
        assert '"value":' not in raw, (
            "example eval spec must not contain legacy 'value' keys; "
            "use per-type semantic keys (needle/pattern/length/count)"
        )
