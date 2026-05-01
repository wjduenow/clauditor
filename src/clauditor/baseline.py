"""Compute helper for the ``--baseline`` phase of ``clauditor grade``.

Aggregates Layer 1 assertions, Layer 2 extraction (when sections are
declared), and Layer 3 grading for a baseline (no-skill-prefix) run.
No file I/O, no subprocess calls, no ``print()`` — the CLI wrapper
handles all side effects.

Traces to DEC-006.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

# Import modules (not functions) so that tests patching the source
# module (e.g. ``clauditor.quality_grader.grade_quality``) are
# effective. Direct function imports would create a local binding
# that the patch cannot reach.
from clauditor import grader as _grader_mod
from clauditor import quality_grader as _qg_mod
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.grader import ExtractionReport
from clauditor.runner import SkillResult
from clauditor.schemas import EvalSpec

__all__ = [
    "BaselineReports",
    "compute_baseline",
]

_SCHEMA_VERSION = 1


@dataclass
class BaselineReports:
    """Collected baseline grading artifacts ready for persistence.

    Returned by :func:`compute_baseline`. The CLI wrapper calls
    :meth:`to_json_map` to get a ``{filename: json_str}`` mapping
    and writes each entry into the staging directory.
    """

    skill_name: str
    iteration: int
    skill_result: SkillResult
    grading_report: _qg_mod.GradingReport
    assertion_set: AssertionSet
    extraction_report: ExtractionReport | None

    def to_json_map(self) -> dict[str, str]:
        """Return ``{filename: json_string}`` for all baseline sidecars.

        ``schema_version`` is the first key in every payload per
        ``.claude/rules/json-schema-version.md``.
        """
        files: dict[str, str] = {}

        # baseline.json — run metadata
        meta = {
            "schema_version": _SCHEMA_VERSION,
            "skill": self.skill_name,
            "iteration": self.iteration,
            "output": self.skill_result.output,
            "exit_code": self.skill_result.exit_code,
            "input_tokens": self.skill_result.input_tokens,
            "output_tokens": self.skill_result.output_tokens,
            "duration_seconds": self.skill_result.duration_seconds,
        }
        files["baseline.json"] = json.dumps(meta, indent=2) + "\n"

        # baseline_assertions.json — Layer 1
        assertions_payload = {
            "schema_version": _SCHEMA_VERSION,
            "skill": self.skill_name,
            "iteration": self.iteration,
            **self.assertion_set.to_json(),
        }
        files["baseline_assertions.json"] = (
            json.dumps(assertions_payload, indent=2) + "\n"
        )

        # baseline_extraction.json — Layer 2 (only when sections declared)
        if self.extraction_report is not None:
            files["baseline_extraction.json"] = self.extraction_report.to_json()

        # baseline_grading.json — Layer 3
        files["baseline_grading.json"] = self.grading_report.to_json()

        return files


def compute_baseline(
    *,
    skill_result: SkillResult,
    eval_spec: EvalSpec,
    skill_name: str,
    iteration: int,
    model: str,
    provider: str = "anthropic",
) -> BaselineReports:
    """Compute all baseline grading layers from an already-run result.

    Takes the already-executed :class:`SkillResult` and the evaluation
    spec, runs L1 assertions synchronously, and awaits L2/L3 grading via
    ``asyncio.run``. Returns a :class:`BaselineReports` dataclass — no
    file I/O, no subprocess invocation, no stderr output.

    ``provider`` is resolved at the CLI seam per #146 US-006 and
    threaded into the L2/L3 orchestrator calls. Default
    ``"anthropic"`` preserves back-compat for direct callers.

    Parameters are keyword-only to match the codebase convention and keep
    call sites readable.
    """
    baseline_text = skill_result.output

    # Layer 1: deterministic assertions
    assertion_set = run_assertions(baseline_text, eval_spec.assertions)

    # Layer 2: LLM-graded extraction (only when sections declared)
    extraction_report: ExtractionReport | None = None
    if eval_spec.sections:
        extraction_report = asyncio.run(
            _grader_mod.extract_and_report(
                baseline_text,
                eval_spec,
                skill_name=skill_name,
                provider=provider,
            )
        )

    # Layer 3: LLM-graded quality
    grading_report = asyncio.run(
        _qg_mod.grade_quality(
            baseline_text,
            eval_spec,
            model,
            thresholds=eval_spec.grade_thresholds,
            provider=provider,
        )
    )

    return BaselineReports(
        skill_name=skill_name,
        iteration=iteration,
        skill_result=skill_result,
        grading_report=grading_report,
        assertion_set=assertion_set,
        extraction_report=extraction_report,
    )
