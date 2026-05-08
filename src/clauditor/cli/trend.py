"""``clauditor trend`` — render a trend line from grade history."""

from __future__ import annotations

import argparse
import sys

from clauditor import history


def _normalized_provider(rec: dict) -> str:
    """Coerce a history record's ``provider`` to a safe string.

    Returns ``"anthropic"`` for missing keys, non-string values, and
    blank/whitespace-only strings. ``read_records`` already backfills
    missing keys for legacy v1 lines, but this helper additionally
    handles the "raw record contains ``null`` or a non-string" case so
    ``sorted({_normalized_provider(rec) for rec in records})`` cannot
    raise ``TypeError`` on a malformed mixed-history file.
    """
    provider = rec.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider
    return "anthropic"


def _normalized_harness(rec: dict) -> str:
    """Coerce a history record's ``harness`` to a safe string.

    Returns ``"claude-code"`` for missing keys, non-string values, and
    blank/whitespace-only strings. ``read_records`` already backfills
    missing keys for legacy v1/v2 lines, but this helper additionally
    handles the "raw record contains ``null`` or a non-string" case so
    ``sorted({_normalized_harness(rec) for rec in records})`` cannot
    raise ``TypeError`` on a malformed mixed-history file. Mirror of
    :func:`_normalized_provider` for the harness axis (#153 US-003).
    """
    harness = rec.get("harness")
    if isinstance(harness, str) and harness.strip():
        return harness
    return "claude-code"


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
    # Lazy import to mirror the rest of cli/*.py and side-step a circular
    # import hazard: ``clauditor.cli.__init__`` imports ``cmd_trend`` from
    # this module during CLI registration, so a top-level
    # ``from clauditor.cli import _provider_concrete_choice`` resolves
    # ``clauditor.cli`` mid-initialization.
    from clauditor.cli import _harness_concrete_choice, _provider_concrete_choice

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
        "--provider",
        type=_provider_concrete_choice,
        default=None,
        help=(
            "Filter history records by grading provider "
            "('anthropic' or 'openai'). Required when history contains "
            "mixed providers."
        ),
    )
    p_trend.add_argument(
        "--harness",
        type=_harness_concrete_choice,
        default=None,
        help=(
            "Filter history records by harness "
            "('claude-code' or 'codex'). Required when history contains "
            "mixed harnesses."
        ),
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

    # Provider refusal / filter (DEC-003, DEC-009, DEC-011 of #147).
    # Computed from the full filtered set BEFORE the --last slice so a
    # user with mixed history cannot silently slip past the refusal by
    # narrowing the window. ``_normalized_provider`` coerces
    # missing/non-string/blank values to ``"anthropic"`` so a
    # malformed v2 record (``provider: null`` or a stray int) cannot
    # raise ``TypeError`` mid-sort.
    providers_seen = sorted({_normalized_provider(rec) for rec in records})
    if args.provider is None:
        if len(providers_seen) > 1:
            providers_str = ", ".join(repr(p) for p in providers_seen)
            print(
                f"ERROR: Mixed providers detected in history for skill "
                f"'{args.skill_name}' ({providers_str}). Pass "
                f"--provider anthropic or --provider openai to filter.",
                file=sys.stderr,
            )
            return 2
    else:
        records = [
            rec
            for rec in records
            if _normalized_provider(rec) == args.provider
        ]
        if not records:
            print(
                f"ERROR: no records for provider '{args.provider}' "
                f"for skill '{args.skill_name}'.",
                file=sys.stderr,
            )
            return 1

    # Harness refusal / filter (#153 US-003 — mirror of the provider
    # check above for the harness axis). Computed from the full
    # filtered set BEFORE the --last slice so a user with mixed
    # history cannot silently slip past the refusal by narrowing the
    # window. ``_normalized_harness`` coerces missing/non-string/blank
    # values to ``"claude-code"`` so a malformed v2 record
    # (``harness: null`` or a stray int) cannot raise ``TypeError``
    # mid-sort. The refusal message names ONLY the ``--harness X``
    # filter — US-004 will retrofit a ``--cross-harness`` opt-in
    # suffix once that flag exists (DEC-008 of #153 plan).
    harnesses_seen = sorted({_normalized_harness(rec) for rec in records})
    if args.harness is None:
        if len(harnesses_seen) > 1:
            harnesses_str = ", ".join(repr(h) for h in harnesses_seen)
            print(
                f"ERROR: Mixed harnesses detected in history for skill "
                f"'{args.skill_name}' ({harnesses_str}). Pass "
                f"--harness claude-code (or --harness codex) to filter.",
                file=sys.stderr,
            )
            return 2
    else:
        records = [
            rec
            for rec in records
            if _normalized_harness(rec) == args.harness
        ]
        if not records:
            print(
                f"ERROR: no records for harness '{args.harness}' "
                f"for skill '{args.skill_name}'.",
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
