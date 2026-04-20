"""CLI entry point for clauditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor import history
from clauditor.runner import SkillResult
from clauditor.spec import SkillSpec


def _unit_float(value: str) -> float:
    """argparse type: finite float in [0.0, 1.0]. Used by audit thresholds."""
    import math

    try:
        x = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a float"
        ) from e
    if not math.isfinite(x) or x < 0.0 or x > 1.0:
        raise argparse.ArgumentTypeError(
            f"must be a finite float in [0.0, 1.0], got {value!r}"
        )
    return x


def _positive_int(value: str) -> int:
    """argparse type: accept integers >= 1."""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from e
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def _append_validate_history(
    skill_name: str,
    *,
    pass_rate: float,
    skill_result: SkillResult | None,
    iteration: int | None,
    workspace_path: str | None,
) -> None:
    """Append a ``command="validate"`` row to ``history.jsonl``.

    Called on both the success and skill-failure paths of ``cmd_validate``
    so failed live validates stay visible to trend/audit tooling. On the
    failure path ``iteration`` / ``workspace_path`` are ``None`` (the
    workspace was aborted) and ``pass_rate`` is ``0.0``.
    """
    from clauditor.metrics import TokenUsage, build_metrics

    skill_tokens = TokenUsage(
        input_tokens=getattr(skill_result, "input_tokens", 0) or 0,
        output_tokens=getattr(skill_result, "output_tokens", 0) or 0,
    )
    skill_duration = getattr(skill_result, "duration_seconds", 0.0) or 0.0
    metrics_dict = build_metrics(
        skill=skill_tokens,
        duration_seconds=skill_duration,
    )
    try:
        history.append_record(
            skill=skill_name,
            pass_rate=pass_rate,
            mean_score=None,
            metrics=metrics_dict,
            command="validate",
            iteration=iteration,
            workspace_path=workspace_path,
        )
    except Exception as e:  # pragma: no cover - defensive
        print(f"WARNING: failed to append history: {e}", file=sys.stderr)


def _load_spec_or_report(
    skill_path: str, eval_path: str | None
) -> SkillSpec | None:
    """Load a :class:`SkillSpec`, printing an actionable error if unreadable.

    Returns the loaded spec on success. On ``FileNotFoundError`` (the
    skill ``.md`` is missing), prints an ``ERROR:`` line to stderr that
    names the path AND suggests ``clauditor init`` as the next step,
    then returns ``None``. On other unreadable-file errors — any
    ``OSError`` subclass (``PermissionError``, ``IsADirectoryError``,
    etc.) and ``UnicodeDecodeError`` (for example, a non-UTF-8 skill
    file, which ``SkillSpec.from_file`` surfaces via
    ``read_text(encoding="utf-8")``) — prints ``ERROR: cannot read
    {path}: {exc}`` to stderr and returns ``None``. Callers map
    ``None`` to exit code 2 (input error, per DEC-008 / DEC-010)
    rather than letting the traceback escape.

    Note the ``except`` order: ``FileNotFoundError`` is a subclass of
    ``OSError``, so its branch must come first to preserve the
    byte-identical "suggest init" message for the missing-file case.
    """
    try:
        return SkillSpec.from_file(skill_path, eval_path=eval_path)
    except FileNotFoundError:
        print(
            f"ERROR: Skill file not found: {skill_path}. "
            f"Run 'clauditor init {skill_path}' to create one.",
            file=sys.stderr,
        )
        return None
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"ERROR: cannot read {skill_path}: {exc}",
            file=sys.stderr,
        )
        return None


_TRANSCRIPT_SLICE_BLOCK_CAP_BYTES = 2048
_TRANSCRIPT_SLICE_TRUNC_MARKER = "... [truncated]"


def _print_failing_transcript_slice(
    run_idx: int,
    stream_events: list[dict],
    out,
) -> None:
    """Print the last 5 ``assistant`` text blocks from ``stream_events``.

    Pure helper — no filesystem or argparse access, so it is cheap to test
    in isolation. The caller is responsible for gating invocation on
    ``args.verbose`` and a non-empty ``AssertionSet.failed()`` for the run.

    Each ``assistant`` event's ``message.content`` list is walked for
    ``type == "text"`` blocks (in event order); the last 5 across all
    events are kept. Each text is passed through
    :func:`clauditor.transcripts.redact` before printing so that in-memory
    ``stream_events`` stays untouched (DEC-010) while the printed output
    is still scrubbed. Individual text blocks are capped at 2 KB by byte
    count on the UTF-8 encoding (per-block, post-redaction); overflow is
    truncated with a trailing ``... [truncated]`` marker.
    """
    from clauditor import transcripts

    text_blocks: list[str] = []
    for event in stream_events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        if not isinstance(message, dict):
            continue
        content = message.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text_val = block.get("text")
            if isinstance(text_val, str):
                text_blocks.append(text_val)

    slice_blocks = text_blocks[-5:]
    scrubbed_slice, redaction_count = transcripts.redact(slice_blocks)

    def _cap(text: str) -> str:
        encoded = text.encode("utf-8")
        if len(encoded) <= _TRANSCRIPT_SLICE_BLOCK_CAP_BYTES:
            return text
        # Decode the first N bytes tolerating split codepoints at the edge.
        truncated = encoded[:_TRANSCRIPT_SLICE_BLOCK_CAP_BYTES].decode(
            "utf-8", errors="ignore"
        )
        return truncated + _TRANSCRIPT_SLICE_TRUNC_MARKER

    header = (
        f"--- transcript slice (run-{run_idx}, last 5 assistant blocks) ---"
    )
    print(header, file=out)
    for i, text in enumerate(scrubbed_slice):
        if i > 0:
            print("", file=out)
        print(_cap(text), file=out)
    if redaction_count > 0:
        # Match the audit-breadcrumb that `_write_run_dir` prints under
        # verbose mode, so users can see the slice printer also scrubbed.
        print(f"[{redaction_count} redactions applied]", file=out)


def _write_run_dir(
    run_dir: Path,
    output_text: str,
    stream_events: list[dict],
    *,
    verbose: bool = False,
) -> None:
    """Write ``output.txt`` and ``output.jsonl`` to a ``run-K/`` subdir.

    ``stream_events`` is one JSON object per line. Empty list yields an
    empty ``output.jsonl`` (the file still exists for layout consistency).

    Both the stream events and the output text are passed through
    :func:`clauditor.transcripts.redact` before being serialized to disk
    so that secrets captured in skill execution traces never land in the
    iteration workspace (DEC-003, DEC-007, DEC-010). The in-memory
    ``stream_events`` list is not mutated — ``redact()`` returns a fresh
    copy. Under ``verbose=True``, the total redaction count for this run
    is always logged to stderr (including when the count is zero) so
    that users can audit what the scrubber matched.
    """
    from clauditor import transcripts

    run_dir.mkdir(parents=True, exist_ok=True)

    scrubbed_events, events_count = transcripts.redact(stream_events)
    scrubbed_text_wrap, text_count = transcripts.redact({"output": output_text})
    scrubbed_text = scrubbed_text_wrap["output"]
    total = events_count + text_count

    (run_dir / "output.txt").write_text(scrubbed_text, encoding="utf-8")
    lines = [json.dumps(ev) for ev in scrubbed_events]
    body = ("\n".join(lines) + "\n") if lines else ""
    (run_dir / "output.jsonl").write_text(body, encoding="utf-8")

    if verbose:
        print(
            f"clauditor.transcripts: redacted {total} matches in {run_dir.name}",
            file=sys.stderr,
        )


def _relative_to_repo(clauditor_dir: Path, final_skill_dir: Path) -> str:
    """Return ``final_skill_dir`` as a path relative to the repo root.

    The repo root is ``clauditor_dir.parent`` (DEC-009). Falls back to the
    absolute path if the directories are not related (defensive — should
    not happen in practice).
    """
    repo_root = clauditor_dir.parent
    try:
        return str(final_skill_dir.relative_to(repo_root))
    except ValueError:  # pragma: no cover - defensive
        return str(final_skill_dir)


# Per-command modules. Each module exposes ``add_parser(subparsers)`` and a
# ``cmd_<name>(args) -> int`` handler. ``main()`` registers the subparsers
# and dispatches to the handlers. Re-exporting the ``cmd_<name>`` symbols
# keeps existing test imports (``from clauditor.cli import cmd_run``) working.
# These imports live here — after all shared helpers are defined — so that
# per-command modules that lazily import shared helpers from ``clauditor.cli``
# see them already defined when their ``cmd_<name>`` function runs.
from clauditor.cli import audit as audit_mod  # noqa: E402
from clauditor.cli import capture as capture_mod  # noqa: E402
from clauditor.cli import compare as compare_mod  # noqa: E402
from clauditor.cli import doctor as doctor_mod  # noqa: E402
from clauditor.cli import extract as extract_mod  # noqa: E402
from clauditor.cli import grade as grade_mod  # noqa: E402
from clauditor.cli import init as init_mod  # noqa: E402
from clauditor.cli import propose_eval as propose_eval_mod  # noqa: E402
from clauditor.cli import run as run_mod  # noqa: E402
from clauditor.cli import setup as setup_mod  # noqa: E402
from clauditor.cli import suggest as suggest_mod  # noqa: E402
from clauditor.cli import trend as trend_mod  # noqa: E402
from clauditor.cli import triggers as triggers_mod  # noqa: E402
from clauditor.cli import validate as validate_mod  # noqa: E402
from clauditor.cli.audit import cmd_audit  # noqa: E402,F401
from clauditor.cli.capture import cmd_capture  # noqa: E402,F401
from clauditor.cli.compare import cmd_compare  # noqa: E402,F401
from clauditor.cli.doctor import cmd_doctor  # noqa: E402,F401
from clauditor.cli.extract import cmd_extract  # noqa: E402,F401
from clauditor.cli.grade import (  # noqa: E402,F401
    _run_baseline_phase,
    cmd_grade,
)
from clauditor.cli.init import cmd_init  # noqa: E402,F401
from clauditor.cli.propose_eval import cmd_propose_eval  # noqa: E402,F401
from clauditor.cli.run import cmd_run  # noqa: E402,F401
from clauditor.cli.setup import cmd_setup  # noqa: E402,F401
from clauditor.cli.suggest import cmd_suggest  # noqa: E402,F401
from clauditor.cli.trend import cmd_trend  # noqa: E402,F401
from clauditor.cli.triggers import cmd_triggers  # noqa: E402,F401
from clauditor.cli.validate import cmd_validate  # noqa: E402,F401


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clauditor",
        description="Auditor for Claude Code skills and slash commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    validate_mod.add_parser(subparsers)

    # run
    run_mod.add_parser(subparsers)

    # grade
    grade_mod.add_parser(subparsers)

    # compare
    compare_mod.add_parser(subparsers)

    # triggers
    triggers_mod.add_parser(subparsers)

    # extract
    extract_mod.add_parser(subparsers)

    # init
    init_mod.add_parser(subparsers)

    # setup
    setup_mod.add_parser(subparsers)

    # capture
    capture_mod.add_parser(subparsers)

    # trend
    trend_mod.add_parser(subparsers)

    # audit
    audit_mod.add_parser(subparsers)

    # suggest
    suggest_mod.add_parser(subparsers)

    # propose-eval
    propose_eval_mod.add_parser(subparsers)

    # doctor
    doctor_mod.add_parser(subparsers)

    # Split argv on a literal `--` *only* when the capture subcommand is in
    # play, so other subcommands (validate/grade/...) keep argparse's native
    # `--` handling instead of having their trailing args silently stripped.
    if argv is None:
        argv = sys.argv[1:]
    trailing: list[str] = []
    if argv and argv[0] == "capture" and "--" in argv:
        sep = argv.index("--")
        main_argv = argv[:sep]
        trailing = argv[sep + 1:]
    else:
        main_argv = argv
    parsed = parser.parse_args(main_argv)
    if trailing and getattr(parsed, "command", None) == "capture":
        parsed.skill_args = (parsed.skill_args or []) + trailing

    if parsed.command == "validate":
        return cmd_validate(parsed)
    elif parsed.command == "run":
        return cmd_run(parsed)
    elif parsed.command == "grade":
        return cmd_grade(parsed)
    elif parsed.command == "compare":
        return cmd_compare(parsed)
    elif parsed.command == "triggers":
        return cmd_triggers(parsed)
    elif parsed.command == "extract":
        return cmd_extract(parsed)
    elif parsed.command == "init":
        return cmd_init(parsed)
    elif parsed.command == "setup":
        return cmd_setup(parsed)
    elif parsed.command == "capture":
        return cmd_capture(parsed)
    elif parsed.command == "doctor":
        return cmd_doctor(parsed)
    elif parsed.command == "trend":
        return cmd_trend(parsed)
    elif parsed.command == "audit":
        return cmd_audit(parsed)
    elif parsed.command == "suggest":
        return cmd_suggest(parsed)
    elif parsed.command == "propose-eval":
        return cmd_propose_eval(parsed)

    return 1


if __name__ == "__main__":
    sys.exit(main())
