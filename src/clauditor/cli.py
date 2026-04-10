"""CLI entry point for clauditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor.assertions import run_assertions
from clauditor.runner import SkillRunner
from clauditor.spec import SkillSpec


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a skill's output against its eval spec (Layer 1 only)."""
    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        print(
            f"Create {Path(args.skill).with_suffix('.eval.json')}",
            file=sys.stderr,
        )
        return 1

    if args.output:
        # Validate against provided output file
        output = Path(args.output).read_text()
    else:
        # Run the skill to get output
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        result = spec.run()
        if not result.succeeded:
            print(f"ERROR: Skill failed to run: {result.error}", file=sys.stderr)
            return 1
        output = result.output
        print(f"Skill completed in {result.duration_seconds:.1f}s")

    # Run Layer 1 assertions
    results = run_assertions(output, spec.eval_spec.assertions)

    if args.json:
        print(
            json.dumps(
                {
                    "skill": spec.skill_name,
                    "pass_rate": results.pass_rate,
                    "passed": results.passed,
                    "results": [
                        {
                            "name": r.name,
                            "passed": r.passed,
                            "message": r.message,
                            **({"evidence": r.evidence} if r.evidence else {}),
                        }
                        for r in results.results
                    ],
                },
                indent=2,
            )
        )
    else:
        print(results.summary())

    return 0 if results.passed else 1


def cmd_run(args: argparse.Namespace) -> int:
    """Run a skill and print its output."""
    runner = SkillRunner(
        project_dir=args.project_dir or Path.cwd(),
        timeout=args.timeout,
    )
    result = runner.run(args.skill, args.args or "")

    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)

    if result.output:
        print(result.output)

    return result.exit_code


def cmd_grade(args: argparse.Namespace) -> int:
    """Run Layer 3 quality grading against a skill's output."""
    import asyncio

    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        return 1

    if not spec.eval_spec.grading_criteria:
        print("ERROR: No grading_criteria defined in eval spec", file=sys.stderr)
        return 1

    model = args.model or spec.eval_spec.grading_model

    # --dry-run: print prompt and exit
    if args.dry_run:
        from clauditor.quality_grader import build_grading_prompt

        prompt = build_grading_prompt(spec.eval_spec)
        print(f"Model: {model}")
        print(f"Prompt:\n{prompt}")
        return 0

    # Get output
    if args.output:
        output = Path(args.output).read_text()
    else:
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        result = spec.run()
        if not result.succeeded:
            print(f"ERROR: Skill failed: {result.error}", file=sys.stderr)
            return 1
        output = result.output

    # Grade (and optionally compare / measure variance in a single event loop)
    from clauditor.quality_grader import grade_quality

    async def _run_grade():
        report = await grade_quality(
            output, spec.eval_spec, model,
            thresholds=spec.eval_spec.grade_thresholds,
        )
        ab_report = None
        if args.compare:
            from clauditor.comparator import compare_ab

            ab_report = await compare_ab(spec, model)
        variance_report = None
        if args.variance:
            from clauditor.quality_grader import measure_variance

            variance_report = await measure_variance(
                spec, args.variance, model
            )
        return report, ab_report, variance_report

    report, ab_report, variance_report = asyncio.run(_run_grade())

    if args.json:
        data: dict = {
            "skill": spec.skill_name,
            "model": model,
            "grade": {
                "passed": report.passed,
                "pass_rate": report.pass_rate,
                "mean_score": report.mean_score,
                "results": [
                    {
                        "criterion": r.criterion,
                        "passed": r.passed,
                        "score": r.score,
                        "evidence": r.evidence,
                        "reasoning": r.reasoning,
                    }
                    for r in report.results
                ],
            },
            "comparison": None,
            "variance": None,
        }
        if ab_report:
            data["comparison"] = {
                "passed": ab_report.passed,
                "regressions": len(ab_report.regressions),
                "results": [
                    {
                        "criterion": r.criterion,
                        "skill_passed": r.skill_grade.passed,
                        "baseline_passed": r.baseline_grade.passed,
                        "regression": r.regression,
                    }
                    for r in ab_report.results
                ],
            }
        if variance_report:
            data["variance"] = {
                "passed": variance_report.passed,
                "n_runs": variance_report.n_runs,
                "score_mean": variance_report.score_mean,
                "score_stddev": variance_report.score_stddev,
                "pass_rate_mean": variance_report.pass_rate_mean,
                "stability": variance_report.stability,
            }
        print(json.dumps(data, indent=2))
    else:
        print(f"Quality Grade: {spec.skill_name} ({model})")
        print(report.summary())
        if ab_report:
            print(f"\n{ab_report.summary()}")
        if variance_report:
            print(f"\n{variance_report.summary()}")

    # --diff: compare against prior results
    save_dir = Path(".clauditor")
    save_path = save_dir / f"{spec.skill_name}.grade.json"

    if args.diff:
        if save_path.exists():
            from clauditor.quality_grader import GradingReport

            prior = GradingReport.from_json(save_path.read_text())
            prior_by_name = {r.criterion: r for r in prior.results}
            current_by_name = {r.criterion: r for r in report.results}
            common = set(prior_by_name) & set(current_by_name)
            regressions = []
            print("\nDiff vs prior results:")
            print(f"  {'Criterion':<40} {'Prior':>6} {'Current':>8} {'Delta':>6}")
            print(f"  {'-'*40} {'-'*6} {'-'*8} {'-'*6}")
            for name in sorted(common):
                p_score = prior_by_name[name].score
                c_score = current_by_name[name].score
                delta = c_score - p_score
                is_regression = (
                    delta < -0.1
                    or (prior_by_name[name].passed and not current_by_name[name].passed)
                )
                marker = " REGRESSION" if is_regression else ""
                line = (
                    f"  {name:<40} {p_score:>6.2f}"
                    f" {c_score:>8.2f} {delta:>+6.2f}{marker}"
                )
                print(line)
                if is_regression:
                    regressions.append(name)
            if regressions:
                print(f"\n  {len(regressions)} regression(s) detected.")
            else:
                print("\n  No regressions detected.")
        else:
            print(
                f"\nWARNING: No prior results at {save_path}. "
                "Run with --save first to establish a baseline.",
                file=sys.stderr,
            )

    if args.save:
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_text(report.to_json())
        print(f"\nGrade report saved to {save_path}")

    # Determine exit code
    passed = report.passed
    if ab_report:
        passed = passed and ab_report.passed
    if variance_report:
        passed = passed and variance_report.passed
    return 0 if passed else 1


def cmd_triggers(args: argparse.Namespace) -> int:
    """Run trigger precision testing for a skill."""
    import asyncio

    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        return 1

    model = args.model or spec.eval_spec.grading_model

    if args.dry_run:
        from clauditor.triggers import build_trigger_prompt

        trigger_tests = spec.eval_spec.trigger_tests
        if not trigger_tests:
            print("ERROR: No trigger_tests defined in eval spec", file=sys.stderr)
            return 1
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


def cmd_extract(args: argparse.Namespace) -> int:
    """Run Layer 2 schema extraction against a skill's output."""
    import asyncio

    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        return 1

    if not spec.eval_spec.sections:
        print("ERROR: No sections defined in eval spec", file=sys.stderr)
        return 1

    model = args.model or "claude-haiku-4-5-20251001"

    # --dry-run: print prompt and exit
    if args.dry_run:
        from clauditor.grader import build_extraction_prompt

        prompt = build_extraction_prompt(spec.eval_spec)
        print(f"Model: {model}")
        print(f"Prompt:\n{prompt}")
        return 0

    # Get output
    if args.output:
        output = Path(args.output).read_text()
    else:
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        result = spec.run()
        if not result.succeeded:
            print(f"ERROR: Skill failed: {result.error}", file=sys.stderr)
            return 1
        output = result.output

    # Extract and grade
    from clauditor.grader import extract_and_grade

    results = asyncio.run(extract_and_grade(output, spec.eval_spec, model))

    if args.json:
        print(
            json.dumps(
                {
                    "skill": spec.skill_name,
                    "model": model,
                    "pass_rate": results.pass_rate,
                    "passed": results.passed,
                    "results": [
                        {
                            "name": r.name,
                            "passed": r.passed,
                            "message": r.message,
                            **({"evidence": r.evidence} if r.evidence else {}),
                        }
                        for r in results.results
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"Schema Extraction: {spec.skill_name} ({model})")
        print(results.summary())

    return 0 if results.passed else 1


def cmd_init(args: argparse.Namespace) -> int:
    """Generate a starter eval.json for a skill."""
    skill_path = Path(args.skill)
    eval_path = skill_path.with_suffix(".eval.json")

    if eval_path.exists() and not args.force:
        print(
            f"ERROR: {eval_path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    starter = {
        "skill_name": skill_path.stem,
        "description": f"Eval spec for /{skill_path.stem}",
        "test_args": "",
        "assertions": [
            {"type": "min_length", "value": "500"},
            {"type": "has_urls", "value": "3"},
            {"type": "has_entries", "value": "3"},
            {"type": "not_contains", "value": "Error"},
        ],
        "sections": [
            {
                "name": "Results",
                "min_entries": 3,
                "fields": [
                    {"name": "name", "required": True},
                    {"name": "address", "required": True},
                ],
            }
        ],
        "grading_criteria": [
            "Are results relevant to the query?",
            "Are descriptions specific (not generic filler)?",
        ],
        "grading_model": "claude-sonnet-4-6",
        "trigger_tests": {
            "should_trigger": [],
            "should_not_trigger": [],
        },
        "variance": {
            "n_runs": 3,
            "min_stability": 0.8,
        },
    }

    eval_path.write_text(json.dumps(starter, indent=2) + "\n")
    print(f"Created {eval_path}")
    print("Edit the file to define your skill's expected output structure.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clauditor",
        description="Auditor for Claude Code skills and slash commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    p_validate = subparsers.add_parser(
        "validate", help="Run Layer 1 assertions against a skill's output"
    )
    p_validate.add_argument("skill", help="Path to skill .md file")
    p_validate.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_validate.add_argument(
        "--output", help="Path to pre-captured output file (skips running the skill)"
    )
    p_validate.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )

    # run
    p_run = subparsers.add_parser("run", help="Run a skill and print output")
    p_run.add_argument("skill", help="Skill name (e.g., find-kid-activities)")
    p_run.add_argument("--args", help="Arguments to pass to the skill")
    p_run.add_argument("--project-dir", help="Project directory (default: cwd)")
    p_run.add_argument("--timeout", type=int, default=180, help="Timeout in seconds")

    # grade
    p_grade = subparsers.add_parser(
        "grade", help="Run Layer 3 quality grading against a skill's output"
    )
    p_grade.add_argument("skill", help="Path to skill .md file")
    p_grade.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_grade.add_argument(
        "--output", help="Path to pre-captured output file (skips running the skill)"
    )
    p_grade.add_argument("--model", help="Override grading model")
    p_grade.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_grade.add_argument(
        "--dry-run", action="store_true", help="Print prompt without making API calls"
    )
    p_grade.add_argument(
        "--compare", action="store_true", help="Also run A/B comparison"
    )
    p_grade.add_argument(
        "--variance",
        type=int,
        metavar="N",
        help="Run N times with variance measurement",
    )
    p_grade.add_argument(
        "--save", action="store_true", help="Save grade report to .clauditor/"
    )
    p_grade.add_argument(
        "--diff", action="store_true", help="Compare against prior grade results"
    )

    # triggers
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

    # extract
    p_extract = subparsers.add_parser(
        "extract", help="Layer 2: LLM schema extraction"
    )
    p_extract.add_argument("skill", help="Path to skill .md file")
    p_extract.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_extract.add_argument(
        "--output", help="Path to pre-captured output file (skips running the skill)"
    )
    p_extract.add_argument("--model", help="Override extraction model")
    p_extract.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_extract.add_argument(
        "--dry-run", action="store_true", help="Print prompt without making API calls"
    )

    # init
    p_init = subparsers.add_parser(
        "init", help="Generate a starter eval.json for a skill"
    )
    p_init.add_argument("skill", help="Path to skill .md file")
    p_init.add_argument(
        "--force", action="store_true", help="Overwrite existing eval.json"
    )

    parsed = parser.parse_args(argv)

    if parsed.command == "validate":
        return cmd_validate(parsed)
    elif parsed.command == "run":
        return cmd_run(parsed)
    elif parsed.command == "grade":
        return cmd_grade(parsed)
    elif parsed.command == "triggers":
        return cmd_triggers(parsed)
    elif parsed.command == "extract":
        return cmd_extract(parsed)
    elif parsed.command == "init":
        return cmd_init(parsed)

    return 1


if __name__ == "__main__":
    sys.exit(main())
