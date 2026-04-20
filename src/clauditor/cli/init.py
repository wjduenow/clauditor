"""``clauditor init`` — generate a starter eval.json for a skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor.paths import derive_skill_name


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``init`` subparser."""
    p_init = subparsers.add_parser(
        "init", help="Generate a starter eval.json for a skill"
    )
    p_init.add_argument("skill", help="Path to skill .md file")
    p_init.add_argument(
        "--force", action="store_true", help="Overwrite existing eval.json"
    )


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

    if not skill_path.is_file():
        print(f"ERROR: skill file not found: {skill_path}", file=sys.stderr)
        return 1

    try:
        skill_md_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read {skill_path}: {exc}", file=sys.stderr)
        return 1

    skill_name, warning = derive_skill_name(skill_path, skill_md_text)
    if warning is not None:
        print(warning, file=sys.stderr)

    starter = {
        "skill_name": skill_name,
        "description": f"Eval spec for /{skill_name}",
        "test_args": "",
        "assertions": [
            {"id": "min_length_500", "type": "min_length", "value": "500"},
            {"id": "has_urls_3", "type": "has_urls", "value": "3"},
            {"id": "has_entries_3", "type": "has_entries", "value": "3"},
            {"id": "no_error", "type": "not_contains", "value": "Error"},
        ],
        "sections": [
            {
                "name": "Results",
                "tiers": [
                    {
                        "label": "default",
                        "min_entries": 3,
                        "fields": [
                            {
                                "id": "results_name",
                                "name": "name",
                                "required": True,
                            },
                            {
                                "id": "results_address",
                                "name": "address",
                                "required": True,
                            },
                        ],
                    }
                ],
            }
        ],
        "grading_criteria": [
            {
                "id": "relevant",
                "criterion": "Are results relevant to the query?",
            },
            {
                "id": "specific",
                "criterion": "Are descriptions specific (not generic filler)?",
            },
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
