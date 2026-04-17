"""``clauditor triggers`` — trigger precision testing for a skill."""

from __future__ import annotations

import argparse
import json
import sys


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``triggers`` subparser."""
    p_triggers = subparsers.add_parser(
        "triggers", help="Run trigger precision testing for a skill"
    )
    p_triggers.add_argument("skill", help="Path to skill .md file")
    p_triggers.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_triggers.add_argument("--model", help="Override grading model")
    p_triggers.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_triggers.add_argument(
        "--dry-run", action="store_true", help="Print sample trigger prompts"
    )


def cmd_triggers(args: argparse.Namespace) -> int:
    """Run trigger precision testing for a skill."""
    import asyncio

    # Shared helpers live in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load: ``clauditor.cli``
    # imports this module to register the subparser.
    from clauditor.cli import _load_spec_or_report

    spec = _load_spec_or_report(args.skill, args.eval)
    if spec is None:
        return 2

    if not spec.eval_spec:
        print(
            f"ERROR: No eval spec found for {args.skill}\n"
            f"Run 'clauditor init {args.skill}' to create one.",
            file=sys.stderr,
        )
        return 1

    model = args.model or spec.eval_spec.grading_model
    if not model:
        print(
            "ERROR: No grading model specified. Set grading_model in "
            "the eval spec or pass --model.",
            file=sys.stderr,
        )
        return 2

    # Both the dry-run and non-dry-run paths need trigger_tests. Without
    # this guard, the non-dry-run path would print an empty 'Trigger
    # Precision:' block and exit 0 — indistinguishable in CI from a
    # genuine pass with zero triggers.
    trigger_tests = spec.eval_spec.trigger_tests
    if not trigger_tests:
        print("ERROR: No trigger_tests defined in eval spec", file=sys.stderr)
        return 1

    if args.dry_run:
        from clauditor.triggers import build_trigger_prompt

        print(f"Model: {model}")
        queries = [
            (q, True) for q in trigger_tests.should_trigger
        ] + [(q, False) for q in trigger_tests.should_not_trigger]
        for query, expected in queries:
            label = "should_trigger" if expected else "should_not_trigger"
            prompt = build_trigger_prompt(
                spec.eval_spec.skill_name, spec.eval_spec.description, query
            )
            print(f"\n--- {label}: \"{query}\" ---")
            print(prompt)
        return 0

    from clauditor.triggers import test_triggers

    report = asyncio.run(test_triggers(spec.eval_spec, model))

    if args.json:
        data = {
            "skill": spec.skill_name,
            "model": model,
            "passed": report.passed,
            "accuracy": report.accuracy,
            "precision": report.precision,
            "recall": report.recall,
            "results": [
                {
                    "query": r.query,
                    "expected": r.expected_trigger,
                    "predicted": r.predicted_trigger,
                    "passed": r.passed,
                    "confidence": r.confidence,
                    "reasoning": r.reasoning,
                }
                for r in report.results
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        print(f"Trigger Precision: {spec.skill_name} ({model})")
        print(report.summary())

    return 0 if report.passed else 1
