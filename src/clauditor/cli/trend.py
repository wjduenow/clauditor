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
    # #153 US-004 DEC-001: ``--<dim>`` filter and ``--cross-<dim>`` opt-in
    # are mutually exclusive per axis. Two SEPARATE mutex groups (one per
    # axis) so ``--harness X --cross-provider`` is valid (filter the
    # harness axis, allow mixed provider). A single global mutex group
    # would over-couple the axes.
    p_provider_group = p_trend.add_mutually_exclusive_group()
    p_provider_group.add_argument(
        "--provider",
        type=_provider_concrete_choice,
        default=None,
        help=(
            "Filter history records by grading provider "
            "('anthropic' or 'openai'). Required when history contains "
            "mixed providers."
        ),
    )
    p_provider_group.add_argument(
        "--cross-provider",
        dest="cross_provider",
        action="store_true",
        help=(
            "Allow averaging across mixed providers "
            "(results may not be comparable)"
        ),
    )

    p_harness_group = p_trend.add_mutually_exclusive_group()
    p_harness_group.add_argument(
        "--harness",
        type=_harness_concrete_choice,
        default=None,
        help=(
            "Filter history records by harness "
            "('claude-code' or 'codex'). Required when history contains "
            "mixed harnesses."
        ),
    )
    p_harness_group.add_argument(
        "--cross-harness",
        dest="cross_harness",
        action="store_true",
        help=(
            "Allow averaging across mixed harnesses "
            "(results may not be comparable)"
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

    # Provider + harness refusal / filter / opt-in (#153 US-004,
    # extending #147 DEC-003 / DEC-009 / DEC-011 for the provider
    # axis and #153 US-003 for the harness axis).
    #
    # Mixed-state per axis is computed from the full filtered set
    # BEFORE the ``--last`` slice so a user with mixed history cannot
    # silently slip past the refusal by narrowing the window.
    # ``_normalized_provider`` / ``_normalized_harness`` coerce
    # missing/non-string/blank values to canonical defaults so a
    # malformed v2 record (``provider: null`` or a stray int) cannot
    # raise ``TypeError`` mid-sort.
    #
    # DEC-011 multi-axis refusal: when both axes are mixed and only
    # one ``--cross-*`` flag is passed, the un-opted-in axis still
    # refuses. We collect refusals from both axes first, then if any
    # refusal landed we print them all together and exit 2. WARNINGs
    # for opted-in axes are deferred until both axes have cleared
    # their refusal check (else the user would see a WARNING line for
    # the axis they opted into immediately above the refusal for the
    # axis they did not — confusing on a CI log).
    providers_seen = sorted({_normalized_provider(rec) for rec in records})
    harnesses_seen = sorted({_normalized_harness(rec) for rec in records})

    refusal_messages: list[str] = []

    if args.provider is None and not args.cross_provider:
        if len(providers_seen) > 1:
            providers_str = ", ".join(repr(p) for p in providers_seen)
            refusal_messages.append(
                f"ERROR: Mixed providers detected in history for skill "
                f"'{args.skill_name}' ({providers_str}). Pass "
                f"--provider anthropic (or --provider openai) to filter, "
                f"or --cross-provider to allow averaging."
            )

    if args.harness is None and not args.cross_harness:
        if len(harnesses_seen) > 1:
            harnesses_str = ", ".join(repr(h) for h in harnesses_seen)
            refusal_messages.append(
                f"ERROR: Mixed harnesses detected in history for skill "
                f"'{args.skill_name}' ({harnesses_str}). Pass "
                f"--harness claude-code (or --harness codex) to filter, "
                f"or --cross-harness to allow averaging."
            )

    if refusal_messages:
        for msg in refusal_messages:
            print(msg, file=sys.stderr)
        return 2

    # Provider filter or opt-in WARNING.
    if args.provider is not None:
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
    elif args.cross_provider and len(providers_seen) > 1:
        providers_str = ", ".join(repr(p) for p in providers_seen)
        print(
            f"WARNING: averaging across providers ({providers_str}) "
            f"— results may not be comparable.",
            file=sys.stderr,
        )

    # Harness filter or opt-in WARNING.
    if args.harness is not None:
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
    elif args.cross_harness and len(harnesses_seen) > 1:
        harnesses_str = ", ".join(repr(h) for h in harnesses_seen)
        print(
            f"WARNING: averaging across harnesses ({harnesses_str}) "
            f"— results may not be comparable.",
            file=sys.stderr,
        )

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
