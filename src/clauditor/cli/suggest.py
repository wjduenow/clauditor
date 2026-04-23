"""``clauditor suggest`` — propose minimal edits to SKILL.md from a grade run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor._anthropic import (
    AnthropicAuthMissingError,
    check_any_auth_available,
)
from clauditor.paths import resolve_clauditor_dir
from clauditor.suggest import (
    NoPriorGradeError,
    load_suggest_input,
    propose_edits,
    render_unified_diff,
    write_sidecar,
)
from clauditor.workspace import InvalidSkillNameError


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``suggest`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _positive_int, _transport_choice

    p_suggest = subparsers.add_parser(
        "suggest",
        help=(
            "Propose minimal edits to a skill .md based on the latest "
            "grade run's failing signals"
        ),
    )
    p_suggest.add_argument("skill", help="Path to skill .md file")
    p_suggest.add_argument(
        "--from-iteration",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Source grade run iteration number (default: latest "
            "iteration containing grading.json)"
        ),
    )
    p_suggest.add_argument(
        "--with-transcripts",
        action="store_true",
        help="Include per-run stream-json transcripts in the proposer prompt",
    )
    p_suggest.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Proposer model (default: claude-sonnet-4-6)",
    )
    p_suggest.add_argument(
        "--json",
        action="store_true",
        help="Print the sidecar JSON to stdout instead of a unified diff",
    )
    p_suggest.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log extra bundle/token details to stderr",
    )
    p_suggest.add_argument(
        "--transport",
        type=_transport_choice,
        default=None,
        choices=("api", "cli", "auto"),
        help=(
            "Override the Anthropic call transport: 'api' (HTTP SDK), "
            "'cli' (subprocess via claude binary), or 'auto' (prefer "
            "CLI when available). Four-layer precedence: this flag > "
            "CLAUDITOR_TRANSPORT env > EvalSpec.transport > default "
            "'auto'."
        ),
    )


def cmd_suggest(args: argparse.Namespace) -> int:
    """Propose minimal edits to SKILL.md based on the latest grade run.

    Sync entrypoint that delegates to :func:`_cmd_suggest_impl` via
    ``asyncio.run``. Exit codes follow DEC-008.
    """
    import asyncio

    return asyncio.run(_cmd_suggest_impl(args))


async def _cmd_suggest_impl(args: argparse.Namespace) -> int:
    """Async orchestration for ``clauditor suggest``.

    Implements the DEC-008 exit-code table:

    - exit 0 on zero failing signals (Sonnet NOT called) and on success.
    - exit 1 when no prior grading.json exists or the proposer returns
      unparseable JSON (no sidecar).
    - exit 2 when any proposal anchor fails validation (no sidecar),
      OR when no usable authentication is available — the pre-flight
      ``check_any_auth_available("suggest")`` guard raises
      ``AnthropicAuthMissingError`` before any API call per #83
      DEC-002/DEC-011 and #86 DEC-008 (no sidecar).
    - exit 3 on Anthropic API errors (no sidecar).
    """
    skill_path = Path(args.skill)
    if not skill_path.exists():
        print(f"Error: skill file not found: {skill_path}", file=sys.stderr)
        return 1
    skill_name = skill_path.stem
    clauditor_dir = resolve_clauditor_dir()

    try:
        suggest_input = load_suggest_input(
            skill=skill_name,
            clauditor_dir=clauditor_dir,
            with_transcripts=args.with_transcripts,
            from_iteration=args.from_iteration,
            skill_md_path=skill_path,
        )
    except NoPriorGradeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        # `clauditor grade` consumes the skill .md path the same way
        # `suggest` does, so echo args.skill (the path the user typed)
        # rather than the bare stem.
        print(
            f"Run 'clauditor grade {args.skill}' first.",
            file=sys.stderr,
        )
        return 1
    except InvalidSkillNameError as exc:
        # `find_latest_grading` rejects skill names containing path
        # separators, leading dots, or other unsafe characters before
        # constructing any on-disk path. Users can hit this with a
        # stem like `..my-skill` or `my.skill.md` on a file whose
        # base name confuses validate_skill_name. Surface it cleanly
        # instead of leaking a traceback.
        print(
            f"Error: invalid skill name {skill_name!r}: {exc}",
            file=sys.stderr,
        )
        return 1
    except UnicodeDecodeError as exc:
        # The decode could have come from SKILL.md, grading.json, an
        # assertions.json run entry, or a transcript file. Report the
        # exception without assuming skill_path was the offender so
        # the user sees something actionable instead of a misleading
        # "skill file could not be decoded" when the real culprit was
        # e.g. iteration-7/my-skill/run-1/output.jsonl.
        print(
            f"Error: could not decode input file as UTF-8: {exc}",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        # Catches FileNotFoundError from a TOCTOU race (e.g. grading.json
        # was deleted between find_latest_grading and the loader read)
        # plus any other disk / permission issue during signal load.
        print(
            f"Error: could not load grade-run signals for {skill_name}: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    # DEC-008 row 2: zero failing signals — do NOT call Sonnet.
    if (
        not suggest_input.failing_assertions
        and not suggest_input.failing_grading_criteria
    ):
        print(
            f"No improvement suggestions: all signals passed for "
            f"{skill_name} in iteration {suggest_input.source_iteration}.",
            file=sys.stderr,
        )
        return 0

    # #83 DEC-002/DEC-011 + #86 DEC-008: fail fast only when neither
    # ANTHROPIC_API_KEY nor the claude CLI binary is available.
    # ``suggest`` has no --dry-run; the guard lands AFTER the zero-
    # failing-signals early-exit (so the "all passed" path still works
    # without auth — it never calls Anthropic) and BEFORE the
    # propose_edits orchestrator.
    try:
        check_any_auth_available("suggest")
    except AnthropicAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.verbose:
        print(
            f"[suggest] skill={skill_name} from iteration "
            f"{suggest_input.source_iteration}",
            file=sys.stderr,
        )
        print(
            f"[suggest] failing_assertions="
            f"{len(suggest_input.failing_assertions)} "
            f"failing_criteria="
            f"{len(suggest_input.failing_grading_criteria)}",
            file=sys.stderr,
        )
        print(
            f"[suggest] with_transcripts={args.with_transcripts} "
            f"model={args.model}",
            file=sys.stderr,
        )

    # propose_edits never raises — API / prompt-build errors surface via
    # SuggestReport.api_error; response-parse errors via parse_error;
    # anchor errors via validation_errors. Distinct fields avoid the
    # brittle substring-match routing an earlier reviewer flagged.
    from clauditor.cli import _resolve_grader_transport

    report = await propose_edits(
        suggest_input,
        model=args.model,
        transport=_resolve_grader_transport(args),
    )

    # DEC-008 row 3: API / prompt-build failure.
    if report.api_error is not None:
        print(f"Error: {report.api_error}", file=sys.stderr)
        return 3

    # DEC-008 row 4: response-parse failure.
    if report.parse_error is not None:
        print(
            f"Error: Proposer returned unparseable JSON: "
            f"{report.parse_error}",
            file=sys.stderr,
        )
        return 1

    # DEC-008 row 5: anchor validation errors — no sidecar.
    if report.validation_errors:
        print(
            f"Error: {len(report.validation_errors)} edit(s) failed "
            f"anchor validation:",
            file=sys.stderr,
        )
        for msg in report.validation_errors:
            print(f"  - {msg}", file=sys.stderr)
        return 2

    # DEC-008 row 6: success — render diff, write sidecar, print.
    diff_text = render_unified_diff(report, suggest_input.skill_md_text)
    try:
        json_path, diff_path = write_sidecar(
            report, diff_text, clauditor_dir
        )
    except OSError as exc:
        # Disk full, permission denied, suggestions/ is a regular file.
        # Don't leak a bare traceback to the user.
        print(
            f"Error: could not write sidecar to {clauditor_dir}: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(
            f"[suggest] input_tokens={report.input_tokens} "
            f"output_tokens={report.output_tokens}",
            file=sys.stderr,
        )
        print(
            f"[suggest] duration_seconds={report.duration_seconds:.2f}",
            file=sys.stderr,
        )
        print(f"[suggest] sidecar: {json_path}", file=sys.stderr)
        print(f"[suggest] diff:    {diff_path}", file=sys.stderr)

    if args.json:
        print(report.to_json(), end="")
    else:
        print(diff_text, end="")

    return 0
