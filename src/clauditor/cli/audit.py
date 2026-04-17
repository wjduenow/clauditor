"""``clauditor audit`` — aggregate per-assertion pass rates across iterations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor.paths import resolve_clauditor_dir
from clauditor.workspace import InvalidSkillNameError, validate_skill_name


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``audit`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _positive_int, _unit_float

    p_audit = subparsers.add_parser(
        "audit",
        help=(
            "Aggregate per-assertion pass rates across the last N "
            "iteration workspaces for a skill"
        ),
        description=(
            "Aggregate per-assertion pass rates across the last N "
            "iteration workspaces for a skill. Exit codes: "
            "0 (clean), 1 (flagged assertions), 2 (error)."
        ),
    )
    p_audit.add_argument("skill", help="Skill name to audit")
    p_audit.add_argument(
        "--last",
        type=_positive_int,
        default=20,
        help="Consider the last N iteration dirs (default 20)",
    )
    p_audit.add_argument(
        "--min-fail-rate",
        type=_unit_float,
        default=None,
        help="(US-006) minimum fail rate to flag an assertion (0.0-1.0)",
    )
    p_audit.add_argument(
        "--min-discrimination",
        type=_unit_float,
        default=None,
        help="(US-006) minimum with/baseline delta to flag (0.0-1.0)",
    )
    p_audit.add_argument(
        "--json",
        action="store_true",
        help="(US-006) emit machine-readable JSON instead of a table",
    )
    p_audit.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="(US-006) directory to write audit reports",
    )


def cmd_audit(args: argparse.Namespace) -> int:
    """Load + aggregate + threshold-check per-assertion pass rates.

    US-006: adds threshold-based flagging (DEC-005), markdown report
    written to ``.clauditor/audit/<skill>-<ts>.md``, stdout summary
    table, ``--json`` mode, and an exit code of ``1`` whenever any
    assertion is flagged.
    """
    from datetime import UTC, datetime

    from clauditor.audit import (
        aggregate,
        apply_thresholds,
        load_iterations,
        render_json,
        render_markdown,
        render_stdout_table,
    )

    # Reject path-traversal / shell-metacharacter skill names before any
    # filesystem use — report_path joins `args.skill` into a filename.
    try:
        validate_skill_name(args.skill)
    except InvalidSkillNameError as e:
        print(f"invalid skill name: {e}", file=sys.stderr)
        return 2

    clauditor_dir = resolve_clauditor_dir()
    records, skipped = load_iterations(
        args.skill, last=args.last, clauditor_dir=clauditor_dir
    )

    if skipped:
        print(
            f"skipped {skipped} iteration dirs without assertion data",
            file=sys.stderr,
        )

    aggregates = aggregate(records)

    min_fail_rate = (
        args.min_fail_rate if args.min_fail_rate is not None else 0.0
    )
    min_discrimination = (
        args.min_discrimination
        if args.min_discrimination is not None
        else 0.05
    )

    verdicts = apply_thresholds(
        aggregates,
        min_fail_rate=min_fail_rate,
        min_discrimination=min_discrimination,
    )

    iterations_analyzed = len({r.iteration for r in records})
    # Include microseconds so concurrent audits don't collide on the
    # report filename (FIX-8).
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    thresholds = {
        "last": args.last,
        "min_fail_rate": min_fail_rate,
        "min_discrimination": min_discrimination,
    }

    try:
        if args.json:
            payload = render_json(
                verdicts,
                skill=args.skill,
                iterations_analyzed=iterations_analyzed,
                thresholds=thresholds,
                timestamp=timestamp,
            )
            print(json.dumps(payload, indent=2))
        else:
            if not aggregates:
                print(
                    f"No audit data for skill {args.skill!r} under "
                    f"{clauditor_dir}"
                )
            else:
                output_dir = (
                    args.output_dir
                    if args.output_dir is not None
                    else clauditor_dir / "audit"
                )
                try:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    report_path = (
                        output_dir / f"{args.skill}-{timestamp}.md"
                    )
                    report_path.write_text(
                        render_markdown(
                            verdicts,
                            skill=args.skill,
                            iterations_analyzed=iterations_analyzed,
                            thresholds=thresholds,
                            timestamp=timestamp,
                        ),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    # FIX-14: exit 1 is reserved for "flagged assertions";
                    # IO errors surface as exit 2 so CI can distinguish.
                    print(
                        f"clauditor audit: failed to write report under "
                        f"{output_dir}: {exc}",
                        file=sys.stderr,
                    )
                    return 2
                print(render_stdout_table(verdicts))
                print(f"\nReport written to {report_path}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"clauditor audit: error rendering report: {exc}", file=sys.stderr)
        return 2

    return 1 if any(v.is_flagged for v in verdicts) else 0
