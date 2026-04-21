"""``clauditor run`` — run a skill and print its output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.runner import SkillRunner


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``run`` subparser."""
    p_run = subparsers.add_parser("run", help="Run a skill and print output")
    p_run.add_argument("skill", help="Skill name (e.g., find-kid-activities)")
    p_run.add_argument("--args", help="Arguments to pass to the skill")
    p_run.add_argument("--project-dir", help="Project directory (default: cwd)")
    p_run.add_argument("--timeout", type=int, default=180, help="Timeout in seconds")


def cmd_run(args: argparse.Namespace) -> int:
    """Run a skill and print its output."""
    # Shared helper lives in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load: ``clauditor.cli``
    # imports this module to register the subparser.
    from clauditor.cli import _render_skill_error

    runner = SkillRunner(
        project_dir=Path(args.project_dir) if args.project_dir else Path.cwd(),
        timeout=args.timeout,
    )
    result = runner.run(args.skill, args.args or "")

    # Render error whenever the run was not clean — this includes explicit
    # error text (rate_limit / auth / api / timeout / subprocess) AND the
    # interactive-hang heuristic which sets ``error_category="interactive"``
    # + a ``warnings[0]`` tag but leaves ``result.error`` ``None`` (US-003).
    # The pre-US-005 ``if result.error:`` guard silently suppressed those.
    if not result.succeeded_cleanly:
        print(f"ERROR: {_render_skill_error(result)}", file=sys.stderr)

    if result.output:
        print(result.output)

    return result.exit_code
