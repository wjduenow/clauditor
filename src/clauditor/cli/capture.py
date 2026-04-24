"""``clauditor capture`` — run a skill via ``claude -p`` and save stdout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.runner import (
    SkillRunner,
    env_with_sync_tasks,
    env_without_api_key,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``capture`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _positive_int

    p_capture = subparsers.add_parser(
        "capture",
        help="Run a skill via `claude -p` and save stdout to a captured file",
    )
    p_capture.add_argument(
        "skill",
        help="Skill name (leading slash optional, e.g. find-restaurants)",
    )
    p_capture.add_argument(
        "--out",
        default=None,
        help="Output file path (default: tests/eval/captured/<skill>.txt)",
    )
    p_capture.add_argument(
        "--versioned",
        action="store_true",
        help="Append -YYYY-MM-DD to the output file stem",
    )
    p_capture.add_argument(
        "--claude-bin",
        default=None,
        help="Path to the `claude` CLI (default: `claude` on PATH)",
    )
    p_capture.add_argument(
        "--no-api-key",
        action="store_true",
        help=(
            "Strip ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the "
            "subprocess environment to force subscription auth."
        ),
    )
    p_capture.add_argument(
        "--sync-tasks",
        action="store_true",
        help=(
            "Force Task(run_in_background=true) spawns to run "
            "synchronously by setting "
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 in the "
            "subprocess env. See "
            "docs/adr/transport-research-103.md for fidelity "
            "caveats."
        ),
    )
    p_capture.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        metavar="SECONDS",
        help=(
            "Override the runner timeout (seconds); must be > 0. "
            "Defaults to SkillRunner's 300s default."
        ),
    )
    p_capture.add_argument(
        "skill_args",
        nargs="*",
        help="Arguments to pass to the skill (put after `--`)",
    )


def cmd_capture(args: argparse.Namespace) -> int:
    """Run a skill via ``claude -p`` and write stdout to a captured file.

    DEC-001/002/010/013: default path is ``tests/eval/captured/<skill>.txt``;
    ``--out`` overrides; ``--versioned`` appends ``-YYYY-MM-DD`` to the stem
    (combines with ``--out``). Skill name accepts an optional leading ``/``.
    """
    from datetime import date

    # Shared helper lives in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load.
    from clauditor.cli import _render_skill_error

    skill_name = args.skill.lstrip("/")
    skill_args = " ".join(args.skill_args) if args.skill_args else ""

    default_out = Path("tests/eval/captured") / f"{skill_name}.txt"
    out_path = Path(args.out) if args.out else default_out

    if args.versioned:
        stamp = date.today().isoformat()
        out_path = out_path.with_name(
            f"{out_path.stem}-{stamp}{out_path.suffix}"
        )

    runner = SkillRunner(claude_bin=args.claude_bin or "claude")
    # DEC-001, DEC-006, DEC-014: thread CLI auth/timeout flags through
    # to the runner. Defaults are both None (today's behavior).
    env_override: dict[str, str] | None = (
        env_without_api_key()
        if getattr(args, "no_api_key", False)
        else None
    )
    if getattr(args, "sync_tasks", False):
        env_override = env_with_sync_tasks(env_override)
    print(f"Running /{skill_name} {skill_args}...", file=sys.stderr)
    result = runner.run(
        skill_name,
        skill_args,
        env=env_override,
        timeout=getattr(args, "timeout", None),
    )

    if not result.succeeded_cleanly:
        print(
            f"ERROR: Skill run failed (exit {result.exit_code}): "
            f"{_render_skill_error(result)}",
            file=sys.stderr,
        )
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.output, encoding="utf-8")
    print(f"Captured {len(result.output)} chars to {out_path}", file=sys.stderr)
    return 0
