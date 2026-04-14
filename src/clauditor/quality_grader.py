"""Layer 3: LLM-graded quality evaluation using rubric criteria.

Uses Sonnet to evaluate skill output against rubric criteria defined in the
eval spec. Each criterion is scored independently with evidence and reasoning.
"""

from __future__ import annotations

import datetime
import json
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from clauditor.schemas import EvalSpec, GradeThresholds

if TYPE_CHECKING:
    from clauditor.spec import SkillSpec


@dataclass
class GradingResult:
    """Result of a single rubric criterion evaluation."""

    criterion: str
    passed: bool
    score: float  # 0.0-1.0
    evidence: str  # Quote from output
    reasoning: str  # Why it passed/failed


@dataclass
class GradingReport:
    """Aggregated results from grading against a full rubric."""

    skill_name: str
    results: list[GradingResult]
    model: str
    duration_seconds: float = 0.0
    thresholds: GradeThresholds | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    metrics: dict | None = None

    @property
    def passed(self) -> bool:
        """Whether grading meets threshold requirements.

        Uses provided thresholds or defaults (min_pass_rate=0.7,
        min_mean_score=0.5) when no thresholds are set.
        """
        t = self.thresholds if self.thresholds is not None else GradeThresholds()
        return (
            self.pass_rate >= t.min_pass_rate
            and self.mean_score >= t.min_mean_score
        )

    @property
    def pass_rate(self) -> float:
        """Fraction of criteria that passed (0.0-1.0)."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def mean_score(self) -> float:
        """Average score across all criteria (0.0-1.0)."""
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    def to_json(self) -> str:
        """Serialize the report to a JSON string."""
        data = {
            "skill_name": self.skill_name,
            "model": self.model,
            "duration_seconds": self.duration_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "results": [
                {
                    "criterion": r.criterion,
                    "passed": r.passed,
                    "score": r.score,
                    "evidence": r.evidence,
                    "reasoning": r.reasoning,
                }
                for r in self.results
            ],
        }
        if self.thresholds is not None:
            data["thresholds"] = {
                "min_pass_rate": self.thresholds.min_pass_rate,
                "min_mean_score": self.thresholds.min_mean_score,
            }
        if self.metrics is not None:
            data["metrics"] = self.metrics
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> GradingReport:
        """Deserialize a GradingReport from a JSON string."""
        parsed = json.loads(data)
        results = [
            GradingResult(
                criterion=item.get("criterion", ""),
                passed=bool(item.get("passed", False)),
                score=float(item.get("score", 0.0)),
                evidence=item.get("evidence") or "",
                reasoning=item.get("reasoning") or "",
            )
            for item in parsed.get("results", [])
        ]
        thresholds = None
        if "thresholds" in parsed:
            t = parsed["thresholds"]
            thresholds = GradeThresholds(
                min_pass_rate=float(t.get("min_pass_rate", 0.7)),
                min_mean_score=float(t.get("min_mean_score", 0.5)),
            )
        return cls(
            skill_name=parsed.get("skill_name", ""),
            results=results,
            model=parsed.get("model", ""),
            duration_seconds=float(parsed.get("duration_seconds", 0.0)),
            thresholds=thresholds,
            input_tokens=int(parsed.get("input_tokens", 0)),
            output_tokens=int(parsed.get("output_tokens", 0)),
            metrics=parsed.get("metrics"),
        )

    def summary(self) -> str:
        """Format a human-readable summary of grading results."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        lines = [
            f"{passed}/{total} criteria passed"
            f" ({self.pass_rate:.0%}, mean score {self.mean_score:.2f})"
        ]
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  {status}: {r.criterion} ({r.score:.1f})")
        return "\n".join(lines)


def build_grading_prompt(eval_spec: EvalSpec) -> str:
    """Build a prompt that asks the LLM to grade output against rubric criteria."""
    criteria_lines = []
    for i, criterion in enumerate(eval_spec.grading_criteria, 1):
        criteria_lines.append(f"{i}. {criterion}")
    criteria_block = "\n".join(criteria_lines)

    return (
        f'You are evaluating the quality of output from a Claude Code skill'
        f' called "{eval_spec.skill_name}".\n'
        f"\n"
        f"## Grading Rubric\n"
        f"Evaluate the output against each criterion below. For each,"
        f" determine:\n"
        f"- passed: whether the output satisfies the criterion"
        f" (true/false)\n"
        f"- score: confidence level from 0.0 to 1.0\n"
        f"- evidence: quote specific text from the output supporting your"
        f" judgment\n"
        f"- reasoning: explain in 1-2 sentences\n"
        f"\n"
        f"Criteria:\n"
        f"{criteria_block}\n"
        f"\n"
        f"Respond with ONLY valid JSON array:\n"
        f'[{{"criterion": "...", "passed": true, "score": 0.0,'
        f' "evidence": "...", "reasoning": "..."}}]'
    )


def parse_grading_response(
    text: str, criteria: list[str]
) -> list[GradingResult]:
    """Parse a JSON grading response into GradingResult objects.

    Handles both raw JSON and markdown-wrapped JSON (```json...```).
    Returns an empty list on parse failure.
    """
    json_str = text
    if "```" in json_str:
        # Try ```json first, then bare ```
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
        else:
            parts = json_str.split("```")
            if len(parts) >= 3:
                json_str = parts[1]

    try:
        data = json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        return []

    if not isinstance(data, list):
        return []

    results = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score", 0.0))
        except (ValueError, TypeError):
            score = 0.0
        results.append(
            GradingResult(
                criterion=str(item.get("criterion", "")),
                passed=bool(item.get("passed", False)),
                score=score,
                evidence=str(item.get("evidence", "")),
                reasoning=str(item.get("reasoning", "")),
            )
        )

    return results


async def grade_quality(
    output: str,
    eval_spec: EvalSpec,
    model: str = "claude-sonnet-4-6",
    thresholds: GradeThresholds | None = None,
) -> GradingReport:
    """Layer 3: Grade skill output against rubric criteria using an LLM.

    Requires the 'grader' extra: pip install clauditor[grader]
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ImportError(
            "Layer 3 quality grading requires the anthropic SDK. "
            "Install with: pip install clauditor[grader]"
        )

    client = AsyncAnthropic()
    prompt = build_grading_prompt(eval_spec)

    start = time.monotonic()
    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{prompt}\n\n## Output to Evaluate\n{output}"
                ),
            },
        ],
    )
    duration = time.monotonic() - start
    input_tokens = getattr(response.usage, "input_tokens", 0)
    output_tokens = getattr(response.usage, "output_tokens", 0)

    if not response.content or not hasattr(response.content[0], "text"):
        return GradingReport(
            skill_name=eval_spec.skill_name,
            results=[
                GradingResult(
                    criterion="parse_response",
                    passed=False,
                    score=0.0,
                    evidence="",
                    reasoning="LLM response contained no text content",
                )
            ],
            model=model,
            duration_seconds=duration,
            thresholds=thresholds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    response_text = response.content[0].text
    results = parse_grading_response(
        response_text, eval_spec.grading_criteria
    )

    if not results:
        # Return a failed report on parse errors
        return GradingReport(
            skill_name=eval_spec.skill_name,
            results=[
                GradingResult(
                    criterion="parse_response",
                    passed=False,
                    score=0.0,
                    evidence=response_text[:200],
                    reasoning="Failed to parse grader response as JSON",
                )
            ],
            model=model,
            duration_seconds=duration,
            thresholds=thresholds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return GradingReport(
        skill_name=eval_spec.skill_name,
        results=results,
        model=model,
        duration_seconds=duration,
        thresholds=thresholds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@dataclass
class VarianceReport:
    """Results of running the same eval N times."""

    skill_name: str
    n_runs: int
    reports: list[GradingReport]
    score_mean: float
    score_stddev: float
    pass_rate_mean: float
    stability: float  # Fraction of runs where ALL criteria passed
    min_stability: float = 0.8
    model: str = ""
    input_tokens: int = 0  # Layer 3 grader tokens across all runs
    output_tokens: int = 0
    skill_input_tokens: int = 0  # Skill subprocess tokens across all runs
    skill_output_tokens: int = 0
    skill_duration_seconds: float = 0.0  # Sum of skill run durations

    @property
    def passed(self) -> bool:
        return self.stability >= self.min_stability

    def summary(self) -> str:
        """Format a human-readable summary of variance results."""
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Variance: {self.n_runs} runs, "
            f"mean score {self.score_mean:.2f} "
            f"(stddev {self.score_stddev:.3f}), "
            f"stability {self.stability:.0%} — {status}",
        ]
        return "\n".join(lines)


async def measure_variance(
    spec: SkillSpec,
    n_runs: int = 5,
    model: str = "claude-sonnet-4-6",
) -> VarianceReport:
    """Run skill N times, grade each, measure consistency."""
    import asyncio

    if n_runs < 1:
        raise ValueError("n_runs must be >= 1")

    if spec.eval_spec is None:
        raise ValueError(
            f"No eval spec found for {spec.skill_name}. "
            f"Cannot measure variance without an eval spec."
        )

    # 1. Run skill n_runs times sequentially (subprocess) and accumulate
    # skill-side tokens + duration for later aggregation into history.
    outputs: list[str] = []
    skill_input_total = 0
    skill_output_total = 0
    skill_duration_total = 0.0
    for _ in range(n_runs):
        result = spec.run()
        outputs.append(result.output)
        skill_input_total += result.input_tokens
        skill_output_total += result.output_tokens
        skill_duration_total += result.duration_seconds

    # 2. Grade all outputs in parallel
    reports = list(
        await asyncio.gather(
            *[
                grade_quality(output, spec.eval_spec, model)
                for output in outputs
            ]
        )
    )

    # 3. Compute statistics
    scores = [r.mean_score for r in reports]
    score_mean = sum(scores) / len(scores)
    score_stddev = math.sqrt(
        sum((s - score_mean) ** 2 for s in scores) / len(scores)
    )
    pass_rate_mean = sum(r.pass_rate for r in reports) / len(reports)
    stability = sum(1 for r in reports if r.passed) / len(reports)

    # Get min_stability from eval spec variance config if available
    min_stability = 0.8
    if spec.eval_spec.variance is not None:
        min_stability = spec.eval_spec.variance.min_stability

    return VarianceReport(
        skill_name=spec.skill_name,
        n_runs=n_runs,
        reports=reports,
        score_mean=score_mean,
        score_stddev=score_stddev,
        pass_rate_mean=pass_rate_mean,
        stability=stability,
        min_stability=min_stability,
        model=model,
        input_tokens=sum(r.input_tokens for r in reports),
        output_tokens=sum(r.output_tokens for r in reports),
        skill_input_tokens=skill_input_total,
        skill_output_tokens=skill_output_total,
        skill_duration_seconds=skill_duration_total,
    )
