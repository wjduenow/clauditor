"""``clauditor trend`` — render a trend line from grade history."""

from __future__ import annotations

import argparse
import sys

from clauditor import history


def _positive_int(value: str) -> int:
    """argparse type: accept integers >= 1."""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from e
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``trend`` subparser."""
    p_trend = subparsers.add_parser(
        "trend",
        help="Print a trend line (TSV) from grade history",
    )
    p_trend.add_argument("skill_name", help="Skill name to trend")
    p_trend_group = p_trend.add_mutually_exclusive_group(required=True)
    p_trend_group.add_argument(
        "--metric",
        help=(
            "Metric to trend (pass_rate, mean_score, or a dotted path "
            "into metrics like total.total or grader.input_tokens)"
        ),
    )
    p_trend_group.add_argument(
        "--list-metrics",
        action="store_true",
        help="List every available metric path in history for the skill",
    )
    p_trend.add_argument(
        "--command",
        dest="command_filter",
        choices=["grade", "extract", "validate", "all"],
        default="grade",
        help="Filter history records by command (default: grade)",
    )
    p_trend.add_argument(
        "--last",
        type=_positive_int,
        default=20,
        help="Show last N records (default 20; must be >= 1)",
    )


def cmd_trend(args: argparse.Namespace) -> int:
    """Render a trend line (TSV) for a skill metric."""
    records = history.read_records(skill=args.skill_name)
    if not records:
        print(
            f"ERROR: no history records for skill '{args.skill_name}'. "
            "Run `clauditor grade` first.",
            file=sys.stderr,
        )
        return 1

    command_filter = args.command_filter
    if command_filter != "all":
        # v1 records (pre-#21) have no "command" key; they were all produced
        # by cmd_grade, so treat a missing key as "grade" for filter purposes.
        records = [
            rec
            for rec in records
            if rec.get("command", "grade") == command_filter
        ]
        if not records:
            print(
                f"ERROR: no history records for skill '{args.skill_name}' "
                f"with command '{command_filter}'. Try --command all to "
                "union across all recorded commands.",
                file=sys.stderr,
            )
            return 1

    last_n = args.last
    if last_n is not None and last_n > 0:
        records = records[-last_n:]

    if args.list_metrics:
        paths: set[str] = set()
        for rec in records:
            paths |= history.collect_metric_paths(rec)
        if not paths:
            print(
                f"ERROR: no metric paths available for skill "
                f"'{args.skill_name}'.",
                file=sys.stderr,
            )
            return 1
        for path in sorted(paths):
            print(path)
        return 0

    metric = args.metric
    timestamps: list[str] = []
    values: list[float] = []
    for rec in records:
        v = history.resolve_path(rec, metric)
        if v is None:
            continue
        try:
            numeric = float(v)
        except (TypeError, ValueError):
            continue
        timestamps.append(str(rec.get("ts", "")))
        values.append(numeric)

    if not values:
        print(
            f"ERROR: no records with metric '{metric}' for skill "
            f"'{args.skill_name}'.",
            file=sys.stderr,
        )
        return 1

    for ts, v in zip(timestamps, values):
        print(f"{ts}\t{v}")
    return 0
