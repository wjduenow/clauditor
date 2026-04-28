"""``clauditor capture`` — run a skill via ``claude -p`` and save stdout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor._harnesses._claude_code import env_without_api_key
from clauditor.capture_provenance import write_capture_provenance
from clauditor.runner import (
    SkillRunner,
    env_with_sync_tasks,
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
            "subprocess env. Synchronous Tasks roughly double wall "
            "time vs the parallel default; consider --timeout 600 "
            "for non-trivial skills. See "
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

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"ERROR: could not create capture directory "
            f"{out_path.parent}: {exc}",
            file=sys.stderr,
        )
        return 1

    # Write the sidecar BEFORE the .txt so any .txt file on disk is
    # guaranteed to have a companion sidecar (review nit, #117). The
    # reverse order would leave a window where a crash between the two
    # writes publishes a .txt that looks legacy to propose-eval. The
    # sidecar records the ``skill_args`` that produced this capture so
    # ``propose-eval`` can thread them into the proposed ``test_args``
    # verbatim. Always written, even for empty args — the "no-args
    # capture" case is a first-class shape, and the sidecar's presence
    # is what tells propose-eval "you know the args, use them"
    # vs absence (legacy capture from before #117) → "unknown, fall
    # back to shape-only test_args".
    #
    # Copilot review on PR #118: both writes are guarded by try/except
    # OSError so a disk-full / permission failure surfaces as a clean
    # exit 1 instead of a raw traceback. If the ``.txt`` write fails
    # AFTER the sidecar is on disk, we unlink the orphan sidecar so
    # the next run is not confused by a stale one claiming args for a
    # capture that was never written.
    try:
        sidecar = write_capture_provenance(
            out_path, skill_name=skill_name, skill_args=skill_args
        )
    except OSError as exc:
        print(
            f"ERROR: could not write capture provenance sidecar: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        out_path.write_text(result.output, encoding="utf-8")
    except OSError as exc:
        # Unlink the orphan sidecar best-effort. ``missing_ok=True`` so
        # a sidecar-already-gone race (from e.g. concurrent cleanup) is
        # not a second failure layer.
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            # Pragmatic: if we cannot remove the sidecar, surface the
            # primary error rather than stacking a second one.
            pass
        print(
            f"ERROR: could not write capture file {out_path}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Captured {len(result.output)} chars to {out_path} "
        f"(provenance: {sidecar.name})",
        file=sys.stderr,
    )
    return 0
