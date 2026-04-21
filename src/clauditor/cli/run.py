"""``clauditor run`` — run a skill and print its output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.runner import SkillRunner


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``run`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _positive_int

    p_run = subparsers.add_parser("run", help="Run a skill and print output")
    p_run.add_argument("skill", help="Skill name (e.g., find-kid-activities)")
    p_run.add_argument("--args", help="Arguments to pass to the skill")
    p_run.add_argument("--project-dir", help="Project directory (default: cwd)")
    # DEC-014: default shifts from 180 to None so the precedence chain
    # (CLI > spec > runner's 180s default) can kick in. ``_positive_int``
    # rejects <= 0 at parse time with exit 2.
    p_run.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        metavar="SECONDS",
        help=(
            "Timeout in seconds; must be > 0. Defaults to SkillRunner's "
            "180s default."
        ),
    )
    p_run.add_argument(
        "--no-api-key",
        action="store_true",
        help=(
            "Strip ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the "
            "subprocess environment to force subscription auth."
        ),
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Run a skill and print its output."""
    # Shared helper lives in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load: ``clauditor.cli``
    # imports this module to register the subparser.
    from clauditor.cli import _render_skill_error
    from clauditor.runner import _env_without_api_key

    runner = SkillRunner(
        project_dir=Path(args.project_dir) if args.project_dir else Path.cwd(),
    )
    # DEC-001, DEC-006, DEC-014: thread CLI auth/timeout flags through
    # to the runner. Defaults are both None (today's behavior; runner
    # falls back to its own ``self.timeout`` default of 180s).
    env_override = (
        _env_without_api_key()
        if getattr(args, "no_api_key", False)
        else None
    )
    result = runner.run(
        args.skill,
        args.args or "",
        env=env_override,
        timeout=getattr(args, "timeout", None),
    )

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
