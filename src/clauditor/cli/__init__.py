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


def _transport_choice(value: str) -> str:
    """argparse type: accept one of ``"api"``, ``"cli"``, ``"auto"``.

    DEC-012 of ``plans/super/86-claude-cli-transport.md``. Shared
    across the six LLM-mediated commands (``grade``, ``extract``,
    ``propose-eval``, ``suggest``, ``triggers``, ``compare``) so
    help text + error messages stay consistent.
    """
    if value not in ("api", "cli", "auto"):
        raise argparse.ArgumentTypeError(
            f"must be one of 'api', 'cli', 'auto', got {value!r}"
        )
    return value


def _resolve_grader_transport(args: argparse.Namespace, eval_spec=None) -> str:
    """Resolve grader transport using four-layer precedence.

    CLI flag > ``CLAUDITOR_TRANSPORT`` env > ``EvalSpec.transport`` > default
    ``"auto"``. Normalizes whitespace-only env values to ``None`` so they are
    treated as unset, matching the ``spec.run`` seam in
    ``src/clauditor/spec.py``.

    ``eval_spec`` is the loaded ``EvalSpec`` (or ``None`` when the calling
    command has no eval spec — e.g. ``suggest``, ``propose-eval``).

    Raises ``SystemExit(2)`` on invalid ``CLAUDITOR_TRANSPORT`` values (e.g.
    ``CLAUDITOR_TRANSPORT=foo``). Printing the error to stderr before exit
    centralizes the routing so all six LLM-mediated commands share one
    error surface.
    """
    import os

    from clauditor._anthropic import resolve_transport

    env_transport = os.environ.get("CLAUDITOR_TRANSPORT")
    if env_transport is not None and env_transport.strip() == "":
        env_transport = None
    spec_transport = eval_spec.transport if eval_spec is not None else None
    try:
        return resolve_transport(
            getattr(args, "transport", None), env_transport, spec_transport
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def should_strip_api_key_for_skill_subprocess(
    args: argparse.Namespace,
) -> bool:
    """Return True iff operator-intent selected CLI grader transport.

    Used by ``cmd_grade`` to decide whether ``--transport cli`` should
    implicitly strip ``ANTHROPIC_API_KEY`` (and ``ANTHROPIC_AUTH_TOKEN``)
    from the skill subprocess env, so a subscription-auth-end-to-end
    run does not re-hit the 429 inside the skill subprocess.

    Returns True when **either** operator-intent layer named CLI:

    - ``args.transport == "cli"`` (explicit ``--transport cli`` flag), OR
    - ``os.environ["CLAUDITOR_TRANSPORT"] == "cli"`` (env var, exact
      match — see whitespace note below).

    Returns False otherwise, including:

    - ``args.transport`` is missing, ``None``, ``"api"``, or ``"auto"``.
    - ``CLAUDITOR_TRANSPORT`` is unset, empty, or any value other than
      exactly ``"cli"`` (whitespace-padded values like ``"  cli  "``
      are rejected downstream by :func:`_resolve_grader_transport`
      with exit 2, so treating them as "cli" here would silently
      strip the skill-subprocess key right before the grader call
      exits — worse UX than a single clear error).
    - ``EvalSpec.transport == "cli"`` — **NOT** consulted here.
      Author-intent does not know the operator's env and must not
      trigger the strip (DEC-002 of
      ``plans/super/95-subscription-auth-flag.md``).
    - ``--transport auto`` resolving to CLI at runtime — this helper
      does NOT resolve auto (DEC-002). Stripping keys on any machine
      with ``claude`` on PATH would surprise users who maintain an
      API key for production purposes.

    Pure function — reads ``os.environ`` only; no stderr, no side
    effects. Matches ``.claude/rules/pure-compute-vs-io-split.md``.
    Sibling to :func:`_resolve_grader_transport` so the transport
    resolver stays purely about transport, and this helper owns the
    coupling decision (DEC-006).

    Env-var value semantics match :func:`_resolve_grader_transport` /
    :func:`clauditor._anthropic.resolve_transport`: exact ``"cli"``
    only, no whitespace normalization. Keeping the two in lockstep
    prevents the "helper accepts ``'  cli  '`` but resolver rejects
    it" split-brain that would leak a stripped-key subprocess run
    before a SystemExit(2) from the grader path.
    """
    import os

    flag = getattr(args, "transport", None)
    if flag == "cli":
        return True
    return os.environ.get("CLAUDITOR_TRANSPORT") == "cli"


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


# DEC-003 / DEC-011: soft cap applied to ``SkillResult.error`` before
# rendering in user-facing stderr messages. Realistic provider errors
# are 100-300 bytes; the 1000-char ceiling bounds pathological payloads
# while leaving typical errors untouched.
_ERROR_TEXT_MAX_CHARS: int = 1000


# DEC-004 / DEC-011: user-facing hint strings for each
# :attr:`SkillResult.error_category` value surfaced by the runner. The
# hint is emitted as a second line after the error text so CI parsers
# can grep for the category independently of the provider message.
_CATEGORY_HINTS: dict[str, str] = {
    "rate_limit": "Hint: retry in ~60s (rate limit)",
    "auth": "Hint: check the ANTHROPIC_API_KEY environment variable",
    "interactive": (
        "Hint: ensure all parameters are in test_args; "
        "/clauditor cannot drive interactive skills"
    ),
    "background-task": (
        "Hint: skill launched Task(run_in_background=true) and exited "
        "before polling — claude -p does not poll background tasks, "
        "so output is likely truncated"
    ),
    "timeout": (
        "Hint: skill exceeded the run timeout — "
        "increase the timeout with --timeout SECONDS (e.g. --timeout 600)"
    ),
    "subprocess": (
        "Hint: the claude CLI itself errored — see stream_events"
    ),
    "api": "Hint: see the error text above",
}


def _render_skill_error(
    result: SkillResult,
    *,
    unknown_fallback: str = "Unknown error",
) -> str:
    """Render a :class:`SkillResult`'s error tail for user-facing stderr.

    Returns the *tail* only — callers keep their own ``ERROR: ...``
    prefix context (e.g. ``"Skill failed to run: "``). The tail is
    assembled in up to three parts, in order:

    1. **Base error text** — ``result.error`` (truncated to
       :data:`_ERROR_TEXT_MAX_CHARS` with a
       ``" ... (truncated; see stream_events)"`` suffix if longer).
       When ``result.error`` is ``None`` or empty and
       ``result.error_category`` is a known key in
       :data:`_CATEGORY_HINTS`, the hint itself serves as the base
       text (no duplicate hint line is appended). Otherwise the
       ``unknown_fallback`` kwarg is used.
    2. **Category hint** (DEC-004) — appended as a separate line
       (``"\\n"`` joiner) *only* when ``result.error`` is set AND
       ``result.error_category`` is a known key in
       :data:`_CATEGORY_HINTS`. Unknown categories are silently
       ignored.
    3. **Warnings trailer** (DEC-002) — when ``result.warnings`` is
       non-empty, the first non-empty line of the first warning is
       appended as ``"\\n(warning: <first-line>)"``. Only the first
       warning is rendered; the full list stays in
       ``result.warnings`` for forensics. Warnings whose lines are
       all whitespace-only are skipped entirely.

    Pure helper — no I/O, no stderr emission, no filesystem access.
    Reads only from the passed ``SkillResult`` and module-level
    constants.
    """
    error_text = result.error if result.error else None
    category = result.error_category
    has_known_category = category in _CATEGORY_HINTS

    if error_text is not None:
        if len(error_text) > _ERROR_TEXT_MAX_CHARS:
            base = (
                error_text[:_ERROR_TEXT_MAX_CHARS]
                + " ... (truncated; see stream_events)"
            )
        else:
            base = error_text
    elif has_known_category:
        base = _CATEGORY_HINTS[category]
    else:
        base = unknown_fallback

    parts: list[str] = [base]

    if error_text is not None and has_known_category:
        parts.append(_CATEGORY_HINTS[category])

    if result.warnings:
        first_warning = result.warnings[0]
        first_nonempty: str | None = None
        for line in first_warning.split("\n"):
            if line.strip():
                first_nonempty = line.strip()
                break
        if first_nonempty is not None:
            parts.append(f"(warning: {first_nonempty})")

    return "\n".join(parts)


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
from clauditor.cli import badge as badge_mod  # noqa: E402
from clauditor.cli import capture as capture_mod  # noqa: E402
from clauditor.cli import compare as compare_mod  # noqa: E402
from clauditor.cli import doctor as doctor_mod  # noqa: E402
from clauditor.cli import extract as extract_mod  # noqa: E402
from clauditor.cli import grade as grade_mod  # noqa: E402
from clauditor.cli import init as init_mod  # noqa: E402
from clauditor.cli import lint as lint_mod  # noqa: E402
from clauditor.cli import propose_eval as propose_eval_mod  # noqa: E402
from clauditor.cli import run as run_mod  # noqa: E402
from clauditor.cli import setup as setup_mod  # noqa: E402
from clauditor.cli import suggest as suggest_mod  # noqa: E402
from clauditor.cli import trend as trend_mod  # noqa: E402
from clauditor.cli import triggers as triggers_mod  # noqa: E402
from clauditor.cli import validate as validate_mod  # noqa: E402
from clauditor.cli.audit import cmd_audit  # noqa: E402,F401
from clauditor.cli.badge import cmd_badge  # noqa: E402,F401
from clauditor.cli.capture import cmd_capture  # noqa: E402,F401
from clauditor.cli.compare import cmd_compare  # noqa: E402,F401
from clauditor.cli.doctor import cmd_doctor  # noqa: E402,F401
from clauditor.cli.extract import cmd_extract  # noqa: E402,F401
from clauditor.cli.grade import (  # noqa: E402,F401
    _run_baseline_phase,
    cmd_grade,
)
from clauditor.cli.init import cmd_init  # noqa: E402,F401
from clauditor.cli.lint import cmd_lint  # noqa: E402,F401
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

    # lint
    lint_mod.add_parser(subparsers)

    # badge
    badge_mod.add_parser(subparsers)

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
    elif parsed.command == "lint":
        return cmd_lint(parsed)
    elif parsed.command == "badge":
        return cmd_badge(parsed)

    return 1


if __name__ == "__main__":
    sys.exit(main())
