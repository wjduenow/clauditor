"""``clauditor capture`` — run a skill via ``claude -p`` and save stdout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.runner import SkillRunner


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``capture`` subparser."""
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
    print(f"Running /{skill_name} {skill_args}...", file=sys.stderr)
    result = runner.run(skill_name, skill_args)

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
