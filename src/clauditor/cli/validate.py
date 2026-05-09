"""``clauditor validate`` — run Layer 1 assertions against a skill's output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor._harnesses._claude_code import env_without_api_key
from clauditor._providers import (
    CodexAuthMissingError,
    check_codex_auth,
)
from clauditor.assertions import run_assertions
from clauditor.context import IterationContext
from clauditor.paths import resolve_clauditor_dir
from clauditor.runner import SkillResult
from clauditor.workspace import (
    InvalidSkillNameError,
    IterationWorkspace,
    allocate_iteration,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``validate`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _harness_choice, _positive_int

    p_validate = subparsers.add_parser(
        "validate", help="Run Layer 1 assertions against a skill's output"
    )
    p_validate.add_argument("skill", help="Path to skill .md file")
    p_validate.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_validate.add_argument(
        "--output", help="Path to pre-captured output file (skips running the skill)"
    )
    p_validate.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_validate.add_argument(
        "--no-transcript",
        action="store_true",
        help="Skip writing per-run stream-json transcripts",
    )
    p_validate.add_argument(
        "--no-api-key",
        action="store_true",
        help=(
            "Strip ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the "
            "subprocess environment to force subscription auth."
        ),
    )
    p_validate.add_argument(
        "--sync-tasks",
        action="store_true",
        help=(
            "Force Task(run_in_background=true) spawns to run "
            "synchronously by setting "
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 in the "
            "subprocess env. Overrides the skill's async behavior "
            "for evaluation only. Synchronous Tasks roughly double "
            "wall time vs the parallel default; consider "
            "--timeout 600 for non-trivial skills. See "
            "docs/adr/transport-research-103.md for the fidelity "
            "caveats."
        ),
    )
    p_validate.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        metavar="SECONDS",
        help=(
            "Override the runner timeout (seconds); must be > 0. "
            "Defaults to EvalSpec.timeout or 300s."
        ),
    )
    p_validate.add_argument(
        "--harness",
        type=_harness_choice,
        default=None,
        choices=("claude-code", "codex", "auto"),
        help=(
            "Override the harness selection: 'claude-code' (Anthropic "
            "Claude CLI), 'codex' (OpenAI Codex CLI), or 'auto' (prefer "
            "claude-code when available). Four-layer precedence: this "
            "flag > CLAUDITOR_HARNESS env > EvalSpec.harness > default "
            "'auto'."
        ),
    )
    p_validate.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="On assertion failure, print the last 5 assistant text blocks to stderr",
    )


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a skill's output against its eval spec (Layer 1 only).

    Live runs (no ``--output``) publish a per-iteration workspace under
    ``.clauditor/iteration-N/<skill>/`` containing ``run-0/output.jsonl``,
    ``run-0/output.txt`` and ``assertions.json`` (with ``transcript_path``
    wired onto every assertion result). No ``grading.json`` or
    ``timing.json`` is written — validate has no Layer 3. Shares the
    iteration counter with ``clauditor grade``. ``--no-transcript``
    suppresses the ``run-0/`` stream-json write and leaves
    ``transcript_path`` unset on assertion rows (US-006).
    """
    # Shared helpers live in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load: ``clauditor.cli``
    # imports this module to register the subparser.
    from clauditor.cli import (
        _append_validate_history,
        _load_spec_or_report,
        _print_failing_transcript_slice,
        _relative_to_repo,
        _render_skill_error,
        _resolve_harness,
        _write_run_dir,
    )

    spec = _load_spec_or_report(args.skill, args.eval)
    if spec is None:
        return 2

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        print(
            f"Create {Path(args.skill).with_suffix('.eval.json')}\n"
            f"Run 'clauditor init {args.skill}' to create one.",
            file=sys.stderr,
        )
        return 1

    skill_result: SkillResult | None = None
    workspace: IterationWorkspace | None = None
    workspace_rel: str | None = None
    iteration_index: int | None = None
    # ``harness_name`` is resolved only on the live-run path. The
    # ``--output`` branch reads a captured file and never invokes a
    # subprocess, so the four-layer resolver (which raises if neither
    # ``claude`` nor ``codex`` is on PATH under the default ``"auto"``)
    # would be a hostile UX regression for the captured-output workflow.
    harness_name: str | None = None

    if args.output:
        # Validate against a pre-captured output file. This path is
        # intentionally NOT wrapped in a workspace: there is no skill
        # subprocess to capture a transcript from, so there's nothing
        # to persist under ``run-0/``. Preserve pre-US-006 behavior.
        try:
            output = Path(args.output).read_text(encoding="utf-8")
        except FileNotFoundError:
            print(
                f"ERROR: Output file not found: {args.output}",
                file=sys.stderr,
            )
            return 2
        except (PermissionError, UnicodeDecodeError, OSError) as exc:
            print(
                f"ERROR: Failed to read output file {args.output}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2
        results = run_assertions(output, spec.eval_spec.assertions)
    else:
        # Live-run path: allocate an iteration workspace, run the skill
        # into ``workspace.tmp_path / run-0``, persist sidecars, and
        # finalize atomically. On any exception, abort the staging dir.
        #
        # #151 US-005 / DEC-006 / DEC-012: resolve harness via the
        # four-layer precedence helper (CLI flag > CLAUDITOR_HARNESS env
        # > EvalSpec.harness > default "auto"), then fail fast if the
        # resolved harness is "codex" and Codex auth is missing. Guard
        # lands BEFORE allocate_iteration so we do not leave an
        # abandoned iteration-N-tmp/ staging dir behind when the guard
        # fires. ``validate`` has no provider-grader axis, so harness
        # auth is the only pre-call check.
        harness_name = _resolve_harness(args, spec.eval_spec)
        if harness_name == "codex":
            try:
                check_codex_auth("validate")
            except CodexAuthMissingError as exc:
                # Template already starts with "ERROR: " — print verbatim
                # to avoid a doubled "ERROR: ERROR: " prefix (matches the
                # AnthropicAuthMissingError / OpenAIAuthMissingError
                # handlers in cli/grade.py).
                print(str(exc), file=sys.stderr)
                return 2

        clauditor_dir = resolve_clauditor_dir()
        try:
            workspace = allocate_iteration(clauditor_dir, spec.skill_name)
        except InvalidSkillNameError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        try:
            print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
            # DEC-001, DEC-006, DEC-014: thread CLI auth/timeout flags
            # through to the spec. ``--no-api-key`` strips both auth env
            # vars via ``env_without_api_key``; ``--timeout`` wins over
            # spec/default per DEC-002. Both default to None (today's
            # behavior).
            #
            # ``harness_name`` is threaded into ``env_without_api_key``
            # so the codex branch preserves ``OPENAI_API_KEY`` (Copilot
            # review feedback on PR #166): otherwise ``--no-api-key``
            # would launch the Codex subprocess without usable auth
            # even though :func:`check_codex_auth` accepted it.
            env_override = (
                env_without_api_key(harness_name=harness_name)
                if getattr(args, "no_api_key", False)
                else None
            )
            skill_result = spec.run(
                run_dir=workspace.tmp_path / "run-0",
                timeout_override=getattr(args, "timeout", None),
                env_override=env_override,
                sync_tasks_override=(
                    True if getattr(args, "sync_tasks", False) else None
                ),
                harness_name_override=harness_name,
            )
            if not skill_result.succeeded_cleanly:
                print(
                    f"ERROR: Skill failed to run: "
                    f"{_render_skill_error(skill_result)}",
                    file=sys.stderr,
                )
                workspace.abort()
                # Still record history so failed live-validates remain
                # visible in trend/audit tooling. No iteration is
                # published, so iteration/workspace fields stay None.
                _append_validate_history(
                    spec.skill_name,
                    pass_rate=0.0,
                    skill_result=skill_result,
                    iteration=None,
                    workspace_path=None,
                    harness_name=harness_name,
                )
                return 1
            output = skill_result.output
            print(f"Skill completed in {skill_result.duration_seconds:.1f}s")

            results = run_assertions(output, spec.eval_spec.assertions)

            skill_dir = workspace.tmp_path
            verbose = bool(getattr(args, "verbose", False))
            no_transcript = bool(getattr(args, "no_transcript", False))

            if verbose and results.failed:
                _print_failing_transcript_slice(
                    0, list(skill_result.stream_events), sys.stderr
                )

            if not no_transcript:
                _write_run_dir(
                    skill_dir / "run-0",
                    output,
                    list(skill_result.stream_events),
                    verbose=verbose,
                )
                transcript_rel = _relative_to_repo(
                    clauditor_dir,
                    workspace.final_path / "run-0" / "output.jsonl",
                )
                for r in results.results:
                    r.transcript_path = transcript_rel
            else:
                # Scrub any `run-0/` subtree the skill already wrote
                # during staging (e.g. `inputs/` copies), so --no-transcript
                # does not leak a half-populated run-0 dir into the
                # published iteration.
                import shutil

                shutil.rmtree(skill_dir / "run-0", ignore_errors=True)

            # #152 US-002 / B1: assertions.json carries the resolved
            # harness identity from ``skill_result.harness`` (US-001's
            # runtime field). This branch is the live-run path
            # (``args.output`` is False), so ``skill_result`` is
            # always set by the ``spec.run`` call above; ``--output``
            # mode reaches a separate branch that does NOT write a
            # workspace sidecar.
            assertions_payload = {
                "schema_version": 2,
                "harness": skill_result.harness,
                "skill": spec.skill_name,
                "iteration": workspace.iteration,
                "runs": [{"run": 0, **results.to_json()}],
            }
            (skill_dir / "assertions.json").write_text(
                json.dumps(assertions_payload, indent=2) + "\n",
                encoding="utf-8",
            )

            # #154 US-004 / DEC-007 / DEC-008: write the per-iteration
            # comparability sidecar inside the staging block. Validate
            # has no Layer 3 grading, so ``provider`` and ``model_grader``
            # stay ``None``; ``cost_usd`` and ``reasoning_tokens`` are
            # ``None`` per DEC-001 / DEC-002 (placeholders for #169 /
            # #170). ``model``/``system_prompt_source`` are unguarded
            # subscripts per the harness contract; ``sandbox_mode`` IS
            # optional (Codex-only).
            context = IterationContext(
                harness=harness_name,
                provider=None,
                model_runner=skill_result.harness_metadata["model"],
                model_grader=None,
                system_prompt_source=skill_result.harness_metadata[
                    "system_prompt_source"
                ],
                sandbox_mode=skill_result.harness_metadata.get("sandbox_mode"),
                cost_usd=None,
                reasoning_tokens=None,
            )
            (skill_dir / "context.json").write_text(
                context.to_json(), encoding="utf-8"
            )

            workspace.finalize()
            iteration_index = workspace.iteration
            workspace_rel = _relative_to_repo(
                clauditor_dir, workspace.final_path
            )
        except Exception:
            if workspace is not None and not workspace.finalized:
                workspace.abort()
            raise

    # Record history (US-005). Layer 1 only — no grader/quality/triggers.
    _append_validate_history(
        spec.skill_name,
        pass_rate=results.pass_rate,
        skill_result=skill_result,
        iteration=iteration_index,
        workspace_path=workspace_rel,
        harness_name=harness_name,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "skill": spec.skill_name,
                    "pass_rate": results.pass_rate,
                    "passed": results.passed,
                    "results": [
                        {
                            "name": r.name,
                            "passed": r.passed,
                            "message": r.message,
                            **({"evidence": r.evidence} if r.evidence else {}),
                            **(
                                {"raw_data": r.raw_data}
                                if r.raw_data is not None
                                else {}
                            ),
                        }
                        for r in results.results
                    ],
                },
                indent=2,
            )
        )
    else:
        print(results.summary())

    return 0 if results.passed else 1
