"""``clauditor propose-eval`` — propose an EvalSpec for a skill via LLM.

Thin CLI I/O layer wrapping the pure compute in
:mod:`clauditor.propose_eval` (see
``.claude/rules/pure-compute-vs-io-split.md``). This module owns the
argparse surface, capture-override loading, stderr/stdout printing,
and the DEC-006 exit-code mapping. The pure module owns prompt
construction, response parsing, spec validation, and the Anthropic
call.

Exit codes (DEC-006, mirrors ``clauditor suggest``):

- ``0`` — success: prompt printed (``--dry-run``), sidecar printed
  (``--json``), or ``<skill>/eval.json`` written.
- ``1`` — response-parse failure (``validation_errors`` starts with
  ``parse_propose_eval_response:``) OR eval.json exists without
  ``--force`` (DEC-003 collision refusal, matches ``cli/init.py``).
- ``2`` — spec-validation failure (``EvalSpec.from_dict`` rejected
  the proposed dict) OR pre-call input error (capture file missing,
  ``--from-iteration`` invalid, prompt exceeds token budget).
- ``3`` — Anthropic API failure (``api_error`` set).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from clauditor._providers import (
    AnthropicAuthMissingError,
    OpenAIAuthMissingError,
    check_provider_auth,
)
from clauditor.capture_provenance import (
    read_capture_provenance,
    sidecar_path_for,
)
from clauditor.propose_eval import (
    DEFAULT_PROPOSE_EVAL_MODEL,
    build_propose_eval_prompt,
    load_propose_eval_input,
    propose_eval,
)
from clauditor.transcripts import redact


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``propose-eval`` subparser."""
    p = subparsers.add_parser(
        "propose-eval",
        help=(
            "Propose an EvalSpec for a skill by asking Sonnet to read "
            "SKILL.md and (optionally) a captured run"
        ),
    )
    p.add_argument("skill_md", help="Path to SKILL.md file")
    p.add_argument(
        "--from-capture",
        default=None,
        metavar="PATH",
        help=(
            "Override the captured run used as proposer context "
            "(default: DEC-001 primary/fallback discovery)"
        ),
    )
    p.add_argument(
        "--from-iteration",
        default=None,
        metavar="N",
        help=(
            "Load capture from "
            ".clauditor/runs/iteration-N/<skill>/run-0/output.txt"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing eval.json at the target path",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the built prompt to stdout and exit; do not call "
            "Anthropic or write a file"
        ),
    )
    p.add_argument(
        "--model",
        default=None,
        help=(
            f"Proposer model (default: {DEFAULT_PROPOSE_EVAL_MODEL})"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit the full ProposeEvalReport JSON envelope on stdout "
            "instead of a human-readable summary"
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Log capture source, redaction count, model, and token "
            "estimates to stderr"
        ),
    )
    p.add_argument(
        "--project-dir",
        default=None,
        help="Project directory (default: cwd)",
    )
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _provider_choice, _transport_choice

    p.add_argument(
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
    p.add_argument(
        "--grading-provider",
        type=_provider_choice,
        default=None,
        choices=("anthropic", "openai", "auto"),
        help=(
            "Override the proposer provider: 'anthropic', 'openai', or "
            "'auto' (infer from --model). Four-layer precedence: this "
            "flag > CLAUDITOR_GRADING_PROVIDER env > "
            "EvalSpec.grading_provider > default 'auto'. The "
            "propose-eval command has no eval spec at the CLI seam, so "
            "only the CLI flag and env var are typically meaningful."
        ),
    )


def cmd_propose_eval(args: argparse.Namespace) -> int:
    """Entry point for ``clauditor propose-eval``.

    Sync wrapper that delegates to :func:`_cmd_propose_eval_impl` via
    ``asyncio.run``. Exit codes follow DEC-006 (see module docstring).
    """
    return asyncio.run(_cmd_propose_eval_impl(args))


def _apply_from_capture_override(
    propose_input,
    capture_path: Path,
    project_dir: Path,
    *,
    verbose: bool,
) -> int | None:
    """Override ``propose_input.capture_text`` from ``capture_path``.

    Returns an exit code (``2`` — pre-call input error per DEC-006)
    to surface to the caller if the path cannot be read; returns
    ``None`` on success. The scrub uses :func:`transcripts.redact` so
    the capture is already-scrubbed before landing in the prompt
    (DEC-008, ``.claude/rules/non-mutating-scrub.md``).
    """
    try:
        raw = capture_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(
            f"ERROR: capture file not found: {capture_path}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"ERROR: could not read capture file {capture_path}: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2
    except UnicodeDecodeError as exc:
        print(
            f"ERROR: capture file {capture_path} is not valid UTF-8: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2

    scrubbed, count = redact(raw)
    propose_input.capture_text = scrubbed
    try:
        rel = capture_path.resolve().relative_to(project_dir.resolve())
        propose_input.capture_source = str(rel)
    except (ValueError, OSError):
        propose_input.capture_source = str(capture_path)
    propose_input.capture_path = capture_path

    # #117: load the sibling ``.capture.json`` sidecar, if present, so
    # the proposer can override ``test_args`` with the args the capture
    # was produced with. Override the field on ``propose_input`` (the
    # loader already set it via DEC-001 discovery, but the caller's
    # explicit ``--from-capture`` / ``--from-iteration`` path wins).
    # Copilot review on PR #118: pass ``expected_skill_name`` so a
    # capture pointed at a *different* skill is rejected with a stderr
    # warning instead of silently threading unrelated args into
    # ``test_args``.
    provenance = read_capture_provenance(
        capture_path, expected_skill_name=propose_input.skill_name
    )
    propose_input.captured_skill_args = (
        provenance.skill_args if provenance is not None else None
    )

    if verbose:
        print(
            f"[propose-eval] capture: {propose_input.capture_source} "
            f"(redacted {count} secrets)",
            file=sys.stderr,
        )

    return None


async def _cmd_propose_eval_impl(args: argparse.Namespace) -> int:
    """Async orchestration for ``clauditor propose-eval`` (DEC-006)."""
    skill_md_path = Path(args.skill_md)
    # DEC-006: missing / non-file SKILL.md is a pre-call input error → 2.
    if not skill_md_path.exists():
        print(
            f"ERROR: skill file not found: {skill_md_path}",
            file=sys.stderr,
        )
        return 2
    if not skill_md_path.is_file():
        print(
            f"ERROR: skill path is not a regular file: {skill_md_path}",
            file=sys.stderr,
        )
        return 2

    project_dir = (
        Path(args.project_dir) if args.project_dir else Path.cwd()
    )

    # DEC-006: decode / read errors are pre-call input errors → 2.
    try:
        propose_input = load_propose_eval_input(skill_md_path, project_dir)
    except UnicodeDecodeError as exc:
        print(
            f"ERROR: could not decode input file as UTF-8: {exc}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"ERROR: could not load SKILL.md {skill_md_path}: {exc}",
            file=sys.stderr,
        )
        return 2

    # Apply capture overrides in priority order: --from-capture wins
    # over --from-iteration if both are set (explicit path beats
    # iteration lookup). The loader's DEC-001 discovery only runs
    # when neither flag is present.
    if args.from_capture is not None:
        capture_path = Path(args.from_capture)
        rc = _apply_from_capture_override(
            propose_input,
            capture_path,
            project_dir,
            verbose=args.verbose,
        )
        if rc is not None:
            return rc
    elif args.from_iteration is not None:
        try:
            iter_num = int(args.from_iteration)
            if iter_num < 1:
                raise ValueError("must be >= 1")
        except ValueError as exc:
            # Pre-call input error per DEC-006.
            print(
                f"ERROR: --from-iteration must be a positive integer, "
                f"got {args.from_iteration!r}: {exc}",
                file=sys.stderr,
            )
            return 2

        iter_capture = (
            project_dir
            / ".clauditor"
            / "runs"
            / f"iteration-{iter_num}"
            / propose_input.skill_name
            / "run-0"
            / "output.txt"
        )
        rc = _apply_from_capture_override(
            propose_input,
            iter_capture,
            project_dir,
            verbose=args.verbose,
        )
        if rc is not None:
            return rc
    elif args.verbose and propose_input.capture_source is not None:
        # The loader already discovered a capture via DEC-001; surface
        # it under verbose so users know which file was picked.
        print(
            f"[propose-eval] capture: {propose_input.capture_source} "
            f"(scrubbed by loader)",
            file=sys.stderr,
        )
    elif args.verbose:
        print(
            "[propose-eval] capture: (none — no capture file found)",
            file=sys.stderr,
        )

    # #117: surface a one-line stderr note (always — not verbose-gated)
    # when a capture was resolved but no provenance sidecar file exists
    # on disk. In that case ``test_args`` in the proposed spec is
    # shape-only and the user must edit it before running ``validate``
    # or the skill will re-run under different conditions than the
    # capture. Captures produced by a pre-#117 ``clauditor capture``
    # run are the primary trigger; the note tells users to re-capture
    # to get the sidecar.
    #
    # Copilot review on PR #118: disambiguate "sidecar truly absent"
    # from "sidecar present but rejected". In the rejected case
    # (schema mismatch, malformed JSON, skill_name mismatch)
    # ``read_capture_provenance`` already emitted its own specific
    # warning naming the reason — firing this CLI-level warning too
    # would both duplicate the message and mis-attribute the cause
    # ("no sidecar" when one is in fact on disk). Use ``capture_path``
    # (new field on ``ProposeEvalInput`` for this exact check) +
    # ``sidecar_path_for`` to discriminate.
    if (
        propose_input.capture_source is not None
        and propose_input.captured_skill_args is None
        and propose_input.capture_path is not None
        and not sidecar_path_for(propose_input.capture_path).is_file()
    ):
        print(
            "WARNING: capture has no .capture.json sidecar — proposed "
            "`test_args` will be a shape-only placeholder. Edit the "
            "resulting eval spec before running `validate`, or re-run "
            "`clauditor capture` to write the sidecar automatically.",
            file=sys.stderr,
        )

    model = args.model or DEFAULT_PROPOSE_EVAL_MODEL
    # Stamp the resolved model back onto ``args`` so the downstream
    # ``_resolve_grading_provider(args, None)`` call sees the effective
    # value (not the raw ``None`` default) when auto-inferring the
    # provider from the model prefix.
    args.model = model
    if args.verbose:
        print(f"[propose-eval] model: {model}", file=sys.stderr)

    # Build the prompt in the CLI layer so we can (a) honor --dry-run
    # without spending an API call (plan line 520-521) and (b) route
    # the token-budget ValueError to exit 2 per DEC-006 "pre-call
    # input errors" row rather than lumping it into api_error → 3.
    try:
        prompt = build_propose_eval_prompt(propose_input)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.verbose:
        est = (len(prompt) + 3) // 4
        print(
            f"[propose-eval] estimated prompt tokens: ~{est}",
            file=sys.stderr,
        )

    # --dry-run: print the prompt and exit. No Anthropic call.
    if args.dry_run:
        print(prompt, end="" if prompt.endswith("\n") else "\n")
        return 0

    # #83 DEC-002/DEC-011 + #86 DEC-008 + #145 US-009 + #146 US-005:
    # fail fast when the proposer-provider's required auth is missing.
    # ``propose-eval`` is the eval-creation step itself, so there is no
    # ``eval_spec`` at the CLI seam — the resolver is called with
    # ``eval_spec=None``. Per #146 DEC-005 ``propose-eval`` is no
    # longer hardcoded to ``"anthropic"``; the four-layer
    # ``_resolve_grading_provider`` helper handles ``--grading-provider``,
    # ``CLAUDITOR_GRADING_PROVIDER``, and falls back to default
    # "auto" with auto-inference from ``--model``. Guard lands AFTER
    # --dry-run (dry-run is a cost-free preview — no API call, no key
    # needed) and BEFORE the propose_eval orchestrator. Distinct
    # ``except`` branches per
    # ``.claude/rules/llm-cli-exit-code-taxonomy.md``.
    #
    # TODO(#146 US-006): pass ``provider`` through to ``propose_eval``
    # so the resolved value flows beyond the auth guard. Today the
    # orchestrator still hardcodes ``provider="anthropic"`` internally.
    from clauditor.cli import _resolve_grading_provider

    provider = _resolve_grading_provider(args, None)
    try:
        check_provider_auth(provider, "propose-eval")
    except AnthropicAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OpenAIAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    from clauditor.cli import _resolve_grader_transport

    report = await propose_eval(
        propose_input,
        model=model,
        spec_dir=skill_md_path.parent,
        transport=_resolve_grader_transport(args),
    )

    # DEC-006 row: Anthropic API failure → exit 3.
    if report.api_error is not None:
        print(f"ERROR: {report.api_error}", file=sys.stderr)
        return 3

    # DEC-006 row: response-parse failure → exit 1. Parse errors are
    # tagged with the `parse_propose_eval_response:` prefix by the
    # pure module so the CLI can route them distinctly from
    # spec-validation errors without a brittle substring search.
    if report.validation_errors and any(
        err.startswith("parse_propose_eval_response:")
        for err in report.validation_errors
    ):
        for msg in report.validation_errors:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    # DEC-006 row: spec-validation failure → exit 2.
    if report.validation_errors:
        print(
            f"ERROR: {len(report.validation_errors)} validation "
            f"error(s) in proposed spec:",
            file=sys.stderr,
        )
        for msg in report.validation_errors:
            print(f"  - {msg}", file=sys.stderr)
        return 2

    if args.verbose:
        print(
            f"[propose-eval] input_tokens={report.input_tokens} "
            f"output_tokens={report.output_tokens}",
            file=sys.stderr,
        )
        print(
            f"[propose-eval] duration_seconds={report.duration_seconds:.2f}",
            file=sys.stderr,
        )

    # DEC-006 row: success. Two output modes:
    # 1. --json: print the full report envelope (schema_version first).
    # 2. default: write <skill>.eval.json (respecting --force / DEC-003),
    #    print human summary.
    if args.json:
        print(report.to_json(), end="")
        return 0

    # Mirror the discovery convention used by `SkillSpec.from_file` and
    # `clauditor init`: sibling file at `<skill_stem>.eval.json`
    # (greeter.md → greeter.eval.json, SKILL.md → SKILL.eval.json).
    # Writing to a non-conventional path (e.g. eval.json without the
    # stem) would mean `validate`/`grade` can't auto-discover the
    # generated spec without an explicit --eval flag (review #53).
    target = skill_md_path.with_suffix(".eval.json")
    if target.exists() and not args.force:
        print(
            f"ERROR: {target} already exists (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    try:
        target.write_text(
            json.dumps(report.proposed_spec, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            f"ERROR: could not write {target}: {exc}",
            file=sys.stderr,
        )
        return 1

    n_assertions = len(report.proposed_spec.get("assertions", []) or [])
    n_sections = len(report.proposed_spec.get("sections", []) or [])
    n_criteria = len(report.proposed_spec.get("grading_criteria", []) or [])
    print(
        f"Wrote {target}: {n_assertions} assertions, "
        f"{n_sections} sections, {n_criteria} criteria"
    )
    return 0
