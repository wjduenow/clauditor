"""Layer 3c: Trigger precision testing with LLM classification.

Uses an LLM to classify whether user queries would invoke a skill,
then computes precision, recall, and accuracy metrics.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from clauditor.schemas import EvalSpec


@dataclass
class TriggerResult:
    """Result of classifying a single test query."""

    query: str
    expected_trigger: bool  # True for should_trigger, False for should_not_trigger
    predicted_trigger: bool  # What the LLM classified
    passed: bool  # expected == predicted
    confidence: float  # 0.0-1.0
    reasoning: str


@dataclass
class TriggerReport:
    """Aggregated trigger precision testing results."""

    skill_name: str
    skill_description: str
    results: list[TriggerResult]
    model: str

    @property
    def passed(self) -> bool:
        """Whether all individual trigger classifications matched expectations."""
        return all(r.passed for r in self.results)

    @property
    def precision(self) -> float:
        """Of queries predicted as triggering, how many should have?"""
        predicted_positive = [r for r in self.results if r.predicted_trigger]
        if not predicted_positive:
            return 1.0
        return sum(
            1 for r in predicted_positive if r.expected_trigger
        ) / len(predicted_positive)

    @property
    def recall(self) -> float:
        """Of queries that should trigger, how many were predicted?"""
        actual_positive = [r for r in self.results if r.expected_trigger]
        if not actual_positive:
            return 1.0
        return sum(
            1 for r in actual_positive if r.predicted_trigger
        ) / len(actual_positive)

    @property
    def accuracy(self) -> float:
        """Fraction of queries classified correctly."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def summary(self) -> str:
        """Format a human-readable summary of trigger test results."""
        lines = [
            f"Trigger Report: {self.skill_name}",
            f"Model: {self.model}",
            "",
        ]

        should_trigger = [r for r in self.results if r.expected_trigger]
        should_not_trigger = [
            r for r in self.results if not r.expected_trigger
        ]

        if should_trigger:
            lines.append("Should Trigger:")
            for r in should_trigger:
                status = "PASS" if r.passed else "FAIL"
                lines.append(
                    f"  [{status}] \"{r.query}\" "
                    f"(predicted={r.predicted_trigger}, "
                    f"confidence={r.confidence:.2f})"
                )
            lines.append("")

        if should_not_trigger:
            lines.append("Should NOT Trigger:")
            for r in should_not_trigger:
                status = "PASS" if r.passed else "FAIL"
                lines.append(
                    f"  [{status}] \"{r.query}\" "
                    f"(predicted={r.predicted_trigger}, "
                    f"confidence={r.confidence:.2f})"
                )
            lines.append("")

        lines.append(
            f"Precision: {self.precision:.2f}  "
            f"Recall: {self.recall:.2f}  "
            f"Accuracy: {self.accuracy:.2f}"
        )
        lines.append(f"Overall: {'PASSED' if self.passed else 'FAILED'}")

        return "\n".join(lines)


def build_trigger_prompt(
    skill_name: str, description: str, query: str
) -> str:
    """Build a prompt for LLM-based trigger classification of a single query."""
    return (
        "You are evaluating whether a Claude Code skill would be "
        "triggered by a user query.\n"
        "\n"
        "## Skill\n"
        f"Name: {skill_name}\n"
        f"Description: {description}\n"
        "\n"
        "## Query\n"
        f'"{query}"\n'
        "\n"
        "Would this query trigger the skill above? Consider:\n"
        "- Does the query's intent match the skill's purpose?\n"
        "- Would a reasonable routing system select this skill?\n"
        "\n"
        "Respond with ONLY valid JSON:\n"
        '{"triggered": true/false, "confidence": 0.0-1.0, '
        '"reasoning": "..."}'
    )


def parse_trigger_response(text: str) -> tuple[bool, float, str]:
    """Parse an LLM trigger classification response.

    Returns (triggered, confidence, reasoning). Handles JSON that may be
    wrapped in markdown code blocks.
    """
    json_str = text.strip()

    # Strip markdown code block wrapping
    if "```" in json_str:
        parts = json_str.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned.startswith("{"):
                json_str = cleaned
                break

    try:
        data = json.loads(json_str)
        triggered = bool(data.get("triggered", False))
        confidence = float(data.get("confidence", 0.0))
        reasoning = str(data.get("reasoning", ""))
        return triggered, confidence, reasoning
    except (json.JSONDecodeError, ValueError, TypeError):
        return False, 0.0, "Failed to parse response"


async def classify_query(
    skill_name: str,
    description: str,
    query: str,
    expected: bool,
    client: object,
    model: str,
) -> TriggerResult:
    """Classify a single query using the LLM.

    Sends a prompt to the LLM and parses the response to determine
    whether the query would trigger the skill.
    """
    prompt = build_trigger_prompt(skill_name, description, query)

    response = await client.messages.create(  # type: ignore[union-attr]
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    if not response.content or not hasattr(response.content[0], "text"):
        return TriggerResult(
            query=query,
            expected_trigger=expected,
            predicted_trigger=False,
            passed=not expected,
            confidence=0.0,
            reasoning="LLM response contained no text content",
        )

    response_text = response.content[0].text
    predicted, confidence, reasoning = parse_trigger_response(response_text)

    return TriggerResult(
        query=query,
        expected_trigger=expected,
        predicted_trigger=predicted,
        passed=expected == predicted,
        confidence=confidence,
        reasoning=reasoning,
    )


async def test_triggers(
    eval_spec: EvalSpec, model: str = "claude-sonnet-4-6"
) -> TriggerReport:
    """Run trigger precision testing for all queries in an eval spec.

    Classifies all should_trigger and should_not_trigger queries in
    parallel via asyncio.gather, then returns a TriggerReport with
    precision, recall, and accuracy metrics.
    """
    if eval_spec.trigger_tests is None:
        return TriggerReport(
            skill_name=eval_spec.skill_name,
            skill_description=eval_spec.description,
            results=[],
            model=model,
        )

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ImportError(
            "Trigger testing requires the anthropic SDK. "
            "Install with: pip install clauditor[grader]"
        )

    client = AsyncAnthropic()

    queries: list[tuple[str, bool]] = []
    for q in eval_spec.trigger_tests.should_trigger:
        queries.append((q, True))
    for q in eval_spec.trigger_tests.should_not_trigger:
        queries.append((q, False))

    tasks = [
        classify_query(
            skill_name=eval_spec.skill_name,
            description=eval_spec.description,
            query=q,
            expected=expected,
            client=client,
            model=model,
        )
        for q, expected in queries
    ]

    results = await asyncio.gather(*tasks)

    return TriggerReport(
        skill_name=eval_spec.skill_name,
        skill_description=eval_spec.description,
        results=list(results),
        model=model,
    )
