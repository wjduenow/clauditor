"""A/B baseline comparator — runs skill vs raw Claude and detects regressions.

Compares skill output against a raw-Claude baseline using the same grading
rubric, flagging regressions where the baseline passes but the skill fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clauditor.assertions import AssertionSet
from clauditor.quality_grader import GradingReport, GradingResult, grade_quality
from clauditor.spec import SkillSpec

FlipKind = Literal["regression", "improvement", "new", "removed"]


@dataclass
class Flip:
    """A single assertion that differs between two AssertionSets."""

    name: str
    before_passed: bool
    after_passed: bool
    kind: FlipKind


def diff_assertion_sets(
    before: AssertionSet, after: AssertionSet
) -> list[Flip]:
    """Diff two AssertionSets, returning a list of Flips sorted by name.

    - ``regression``: was passing, now failing
    - ``improvement``: was failing, now passing
    - ``new``: assertion only present in ``after``
    - ``removed``: assertion only present in ``before``

    Assertions present in both with the same ``passed`` value are omitted.
    """
    before_by_name = {r.name: r for r in before.results}
    after_by_name = {r.name: r for r in after.results}
    all_names = set(before_by_name) | set(after_by_name)

    flips: list[Flip] = []
    for name in all_names:
        b = before_by_name.get(name)
        a = after_by_name.get(name)
        if b is None and a is not None:
            flips.append(
                Flip(
                    name=name,
                    before_passed=False,
                    after_passed=a.passed,
                    kind="new",
                )
            )
        elif a is None and b is not None:
            flips.append(
                Flip(
                    name=name,
                    before_passed=b.passed,
                    after_passed=False,
                    kind="removed",
                )
            )
        elif b is not None and a is not None:
            if b.passed == a.passed:
                continue
            kind: FlipKind = (
                "regression" if b.passed and not a.passed else "improvement"
            )
            flips.append(
                Flip(
                    name=name,
                    before_passed=b.passed,
                    after_passed=a.passed,
                    kind=kind,
                )
            )
    flips.sort(key=lambda f: f.name)
    return flips


@dataclass
class ABResult:
    """Comparison of a single criterion between skill and baseline."""

    criterion: str
    skill_grade: GradingResult
    baseline_grade: GradingResult
    regression: bool  # True if baseline passed but skill failed


@dataclass
class ABReport:
    """Full A/B comparison report."""

    skill_name: str
    skill_report: GradingReport
    baseline_report: GradingReport
    results: list[ABResult]
    model: str

    @property
    def passed(self) -> bool:
        """No regressions detected."""
        return not any(r.regression for r in self.results)

    @property
    def regressions(self) -> list[ABResult]:
        """All criteria where the skill regressed relative to baseline."""
        return [r for r in self.results if r.regression]

    def summary(self) -> str:
        """Format a human-readable summary table."""
        reg_count = len(self.regressions)
        status = "PASS" if self.passed else f"FAIL ({reg_count} regression(s))"
        lines = [f"A/B Report: {self.skill_name} — {status}"]
        lines.append(f"{'Criterion':<40} {'Skill':<8} {'Baseline':<8} {'Status'}")
        lines.append("-" * 70)
        for r in self.results:
            skill_str = "PASS" if r.skill_grade.passed else "FAIL"
            base_str = "PASS" if r.baseline_grade.passed else "FAIL"
            row_status = "REGRESSION" if r.regression else "ok"
            lines.append(
                f"{r.criterion:<40} {skill_str:<8} {base_str:<8} {row_status}"
            )
        return "\n".join(lines)


async def compare_ab(
    spec: SkillSpec, model: str = "claude-sonnet-4-6"
) -> ABReport:
    """Run skill vs baseline, grade both, compare for regressions.

    1. Run skill via spec.run()
    2. Run raw baseline via spec.runner.run_raw(test_args)
    3. Grade both outputs against the same rubric
    4. Zip results per-criterion and flag regressions
    """
    if not spec.eval_spec:
        raise ValueError(
            f"No eval spec found for {spec.skill_name}. "
            "A/B comparison requires grading_criteria in the eval spec."
        )

    test_args = spec.eval_spec.test_args
    if not test_args or not test_args.strip():
        raise ValueError(
            "A/B comparison requires non-empty test_args in the eval spec "
            "to serve as the baseline prompt."
        )

    # Run both
    skill_result = spec.run()
    baseline_result = spec.runner.run_raw(test_args)

    # Grade both against the same rubric
    skill_report = await grade_quality(
        skill_result.output, spec.eval_spec, model=model
    )
    baseline_report = await grade_quality(
        baseline_result.output, spec.eval_spec, model=model
    )

    # Zip results per-criterion by index (not string match — LLM may paraphrase)
    results: list[ABResult] = []
    for i, skill_grade in enumerate(skill_report.results):
        if i < len(baseline_report.results):
            baseline_grade = baseline_report.results[i]
        else:
            baseline_grade = GradingResult(
                criterion=skill_grade.criterion,
                passed=False,
                score=0.0,
                evidence="",
                reasoning="Criterion not evaluated in baseline",
            )
        regression = baseline_grade.passed and not skill_grade.passed
        results.append(
            ABResult(
                criterion=skill_grade.criterion,
                skill_grade=skill_grade,
                baseline_grade=baseline_grade,
                regression=regression,
            )
        )

    return ABReport(
        skill_name=spec.skill_name,
        skill_report=skill_report,
        baseline_report=baseline_report,
        results=results,
        model=model,
    )
