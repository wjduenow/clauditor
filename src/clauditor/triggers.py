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
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TriggerReport:
    """Aggregated trigger precision testing results."""

    skill_name: str
    skill_description: str
    results: list[TriggerResult]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

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
    model: str,
    transport: str = "auto",
    provider: str = "anthropic",
) -> TriggerResult:
    """Classify a single query using the LLM.

    Sends a prompt to the LLM via the centralized
    :func:`clauditor._providers.call_model` dispatcher (#144 US-005)
    and parses the response to determine whether the query would
    trigger the skill. Retry / rate-limit / auth-error handling lives
    inside the dispatcher's anthropic backend — this function just
    awaits the result and projects it onto :class:`TriggerResult`.
    """
    from clauditor._providers import (
        AnthropicHelperError,
        OpenAIHelperError,
        call_model,
    )

    prompt = build_trigger_prompt(skill_name, description, query)

    try:
        result = await call_model(
            prompt,
            provider=provider,
            model=model,
            transport=transport,
            max_tokens=1024,
        )
    except (AnthropicHelperError, OpenAIHelperError) as exc:
        # Graceful degradation: a single API failure (auth, 5xx
        # exhaustion, network) must not abort the entire trigger batch
        # in ``test_triggers``. ``passed=False`` always — an API error
        # is never a real test pass, even for ``should_not_trigger``
        # queries (where ``passed=not expected`` would otherwise
        # silently count the batch as green despite zero classification
        # work happening).
        return TriggerResult(
            query=query,
            expected_trigger=expected,
            predicted_trigger=False,
            passed=False,
            confidence=0.0,
            reasoning=f"API error: {exc}",
            input_tokens=0,
            output_tokens=0,
        )
    input_tokens = result.input_tokens
    output_tokens = result.output_tokens

    if not result.text_blocks:
        return TriggerResult(
            query=query,
            expected_trigger=expected,
            predicted_trigger=False,
            passed=not expected,
            confidence=0.0,
            reasoning="LLM response contained no text content",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    response_text = result.text_blocks[0]
    predicted, confidence, reasoning = parse_trigger_response(response_text)

    return TriggerResult(
        query=query,
        expected_trigger=expected,
        predicted_trigger=predicted,
        passed=expected == predicted,
        confidence=confidence,
        reasoning=reasoning,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def test_triggers(
    eval_spec: EvalSpec,
    model: str = "claude-sonnet-4-6",
    transport: str = "auto",
    *,
    provider: str = "anthropic",
) -> TriggerReport:
    """Run trigger precision testing for all queries in an eval spec.

    Classifies all should_trigger and should_not_trigger queries in
    parallel via asyncio.gather, then returns a TriggerReport with
    precision, recall, and accuracy metrics. The Anthropic SDK is
    accessed through :func:`clauditor._anthropic.call_anthropic` inside
    :func:`classify_query`; retry / error handling lives in the helper.

    ``provider`` is resolved at the CLI / fixture seam per #146 US-006
    and threaded into every per-query :func:`classify_query` call.
    Default ``"anthropic"`` preserves back-compat for direct callers
    (mainly tests); production callers always pass an explicit value.
    """
    if eval_spec.trigger_tests is None:
        return TriggerReport(
            skill_name=eval_spec.skill_name,
            skill_description=eval_spec.description,
            results=[],
            model=model,
        )

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
            model=model,
            transport=transport,
            provider=provider,
        )
        for q, expected in queries
    ]

    results = await asyncio.gather(*tasks)
    results_list = list(results)

    return TriggerReport(
        skill_name=eval_spec.skill_name,
        skill_description=eval_spec.description,
        results=results_list,
        model=model,
        input_tokens=sum(r.input_tokens for r in results_list),
        output_tokens=sum(r.output_tokens for r in results_list),
    )
