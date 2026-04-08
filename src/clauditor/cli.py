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
        "grading_criteria": [],
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
    elif parsed.command == "init":
        return cmd_init(parsed)

    return 1


if __name__ == "__main__":
    sys.exit(main())
