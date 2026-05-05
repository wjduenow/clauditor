"""Unit tests for ``clauditor.cli.grade._write_assertions_sidecar``.

#152 US-002: assertions.json bumped from schema_version 1 → 2 with a new
top-level ``harness`` field. Tests pin:

- ``schema_version: 2`` is the first key.
- ``harness`` is the second top-level key (canonical order:
  schema_version, harness, skill, iteration, runs).
- The caller's ``harness`` value is stamped through verbatim
  (``"claude-code"``, ``"codex"``, etc.).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from clauditor.cli.grade import _grade_all_runs, _write_assertions_sidecar
from clauditor.spec import SkillSpec
from clauditor.workspace import IterationWorkspace
from tests.conftest import build_eval_spec


def _make_workspace(tmp_path: Path) -> IterationWorkspace:
    """Build a real :class:`IterationWorkspace` rooted at ``tmp_path``.

    The workspace is *not* finalized; ``tmp_path`` is the staging dir
    where ``_write_assertions_sidecar`` writes its output.
    """
    final_path = tmp_path / "final"
    tmp_skill_path = tmp_path / "tmp"
    tmp_skill_path.mkdir()
    return IterationWorkspace(
        iteration=1,
        final_path=final_path,
        tmp_path=tmp_skill_path,
    )


def _make_spec() -> MagicMock:
    """Build a MagicMock SkillSpec carrying a minimal EvalSpec."""
    spec = MagicMock(spec=SkillSpec)
    spec.skill_name = "demo"
    spec.eval_spec = build_eval_spec(
        assertions=[{"type": "contains", "needle": "hello"}],
    )
    return spec


def _make_args(*, output: bool = False) -> argparse.Namespace:
    """Minimal argparse.Namespace for ``_write_assertions_sidecar``."""
    return argparse.Namespace(output=output)


class TestWriteAssertionsSidecar:
    """#152 US-002: schema_version=2 + top-level ``harness`` field."""

    def test_emits_schema_version_2(self, tmp_path: Path) -> None:
        """Payload starts with ``{"schema_version": 2, ...}``."""
        workspace = _make_workspace(tmp_path)
        spec = _make_spec()
        args = _make_args(output=True)  # output=True skips transcript path
        run_outputs = [("hello world", [])]

        _write_assertions_sidecar(
            args=args,
            spec=spec,
            workspace=workspace,
            clauditor_dir=tmp_path / ".clauditor",
            run_outputs=run_outputs,
            verbose=False,
            no_transcript=True,
            harness="claude-code",
        )

        sidecar = workspace.tmp_path / "assertions.json"
        assert sidecar.is_file(), "assertions.json was not written"
        payload = json.loads(sidecar.read_text())
        assert payload["schema_version"] == 2

    def test_includes_harness_field(self, tmp_path: Path) -> None:
        """Top-level ``harness`` from caller value is present."""
        workspace = _make_workspace(tmp_path)
        spec = _make_spec()
        args = _make_args(output=True)
        run_outputs = [("hello world", [])]

        _write_assertions_sidecar(
            args=args,
            spec=spec,
            workspace=workspace,
            clauditor_dir=tmp_path / ".clauditor",
            run_outputs=run_outputs,
            verbose=False,
            no_transcript=True,
            harness="claude-code",
        )

        sidecar = workspace.tmp_path / "assertions.json"
        payload = json.loads(sidecar.read_text())
        assert "harness" in payload
        assert payload["harness"] == "claude-code"

    def test_harness_passed_through_unchanged(self, tmp_path: Path) -> None:
        """Caller passes ``"codex"``; payload stamps ``"codex"``."""
        workspace = _make_workspace(tmp_path)
        spec = _make_spec()
        args = _make_args(output=True)
        run_outputs = [("hello world", [])]

        _write_assertions_sidecar(
            args=args,
            spec=spec,
            workspace=workspace,
            clauditor_dir=tmp_path / ".clauditor",
            run_outputs=run_outputs,
            verbose=False,
            no_transcript=True,
            harness="codex",
        )

        sidecar = workspace.tmp_path / "assertions.json"
        payload = json.loads(sidecar.read_text())
        assert payload["harness"] == "codex"

    def test_top_level_key_order(self, tmp_path: Path) -> None:
        """Canonical key order: schema_version, harness, skill, iteration, runs."""
        workspace = _make_workspace(tmp_path)
        spec = _make_spec()
        args = _make_args(output=True)
        run_outputs = [("hello world", [])]

        _write_assertions_sidecar(
            args=args,
            spec=spec,
            workspace=workspace,
            clauditor_dir=tmp_path / ".clauditor",
            run_outputs=run_outputs,
            verbose=False,
            no_transcript=True,
            harness="claude-code",
        )

        sidecar = workspace.tmp_path / "assertions.json"
        payload = json.loads(sidecar.read_text())
        keys = list(payload.keys())
        assert keys[0] == "schema_version"
        assert keys[1] == "harness"
        # Remaining keys (order-insensitive but must exist).
        assert "skill" in keys
        assert "iteration" in keys
        assert "runs" in keys


class TestGradeAllRunsHarness:
    """#152: _grade_all_runs defaults harness to 'claude-code' when
    skill_results is None (the --output path where no SkillResult exists).
    """

    def test_defaults_harness_when_skill_results_is_none(self) -> None:
        """skill_results=None → grade_quality gets harness='claude-code'."""
        spec = MagicMock(spec=SkillSpec)
        spec.eval_spec = build_eval_spec()
        canned = MagicMock()

        with patch(
            "clauditor.quality_grader.grade_quality",
            new=AsyncMock(return_value=canned),
        ) as mock_grade:
            reports = _grade_all_runs(
                run_outputs=[("hello world", [])],
                spec=spec,
                model="claude-sonnet-4-6",
                skill_results=None,
            )

        assert len(reports) == 1
        call = mock_grade.await_args
        assert call.kwargs.get("harness") == "claude-code"
