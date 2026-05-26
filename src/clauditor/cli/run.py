"""``clauditor run`` — run a skill and print its output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor._harnesses import construct_harness
from clauditor._harnesses._claude_code import env_without_api_key
from clauditor._providers import (
    CodexAuthMissingError,
    check_codex_auth,
)
from clauditor.runner import (
    SkillResult,
    SkillRunner,
    env_with_sync_tasks,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``run`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _harness_choice, _positive_int

    p_run = subparsers.add_parser("run", help="Run a skill and print output")
    p_run.add_argument("skill", help="Skill name (e.g., find-kid-activities)")
    p_run.add_argument("--args", help="Arguments to pass to the skill")
    p_run.add_argument("--project-dir", help="Project directory (default: cwd)")
    p_run.add_argument(
        "--json",
        action="store_true",
        help=(
            "Print the run result as a JSON object to stdout instead of "
            "the rendered output. No secrets are included."
        ),
    )
    # DEC-014: default shifts from 180 to None so the precedence chain
    # (CLI > spec > runner's 300s default) can kick in. ``_positive_int``
    # rejects <= 0 at parse time with exit 2.
    p_run.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        metavar="SECONDS",
        help=(
            "Timeout in seconds; must be > 0. Defaults to SkillRunner's "
            "300s default."
        ),
    )
    p_run.add_argument(
        "--no-api-key",
        action="store_true",
        help=(
            "Strip ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the "
            "subprocess environment to force subscription auth."
        ),
    )
    p_run.add_argument(
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
    p_run.add_argument(
        "--harness",
        type=_harness_choice,
        default=None,
        choices=("claude-code", "codex", "auto"),
        help=(
            "Override the harness selection: 'claude-code' (Anthropic "
            "Claude CLI), 'codex' (OpenAI Codex CLI), or 'auto' (prefer "
            "claude-code when available). Three-layer precedence (no "
            "spec layer): this flag > CLAUDITOR_HARNESS env > default "
            "'auto'."
        ),
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Run a skill and print its output."""
    # Shared helper lives in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load: ``clauditor.cli``
    # imports this module to register the subparser.
    from clauditor.cli import _render_skill_error, _resolve_harness

    # #151 US-005 / DEC-006 / DEC-012: resolve harness via the four-layer
    # precedence helper. ``run`` has no eval spec, so the spec layer is
    # skipped (eval_spec=None). Fail fast if the resolved harness is
    # "codex" and Codex auth is missing. ``run`` has no provider-grader
    # axis, so harness auth is the only pre-call check.
    harness_name = _resolve_harness(args, None)
    if harness_name == "codex":
        try:
            check_codex_auth("run")
        except CodexAuthMissingError as exc:
            # Template already starts with "ERROR: " — print verbatim
            # to avoid a doubled "ERROR: ERROR: " prefix (matches the
            # AnthropicAuthMissingError / OpenAIAuthMissingError
            # handlers in cli/grade.py).
            print(str(exc), file=sys.stderr)
            return 2

    project_dir = (
        Path(args.project_dir) if args.project_dir else Path.cwd()
    )
    if harness_name == "claude-code":
        runner = SkillRunner(project_dir=project_dir)
    else:
        runner = SkillRunner(
            project_dir=project_dir,
            harness=construct_harness(harness_name),
        )
    # DEC-001, DEC-006, DEC-014: thread CLI auth/timeout flags through
    # to the runner. Defaults are both None (today's behavior; runner
    # falls back to its own ``self.timeout`` default of 300s).
    #
    # ``harness_name`` is threaded into ``env_without_api_key`` so the
    # codex branch preserves ``OPENAI_API_KEY`` (Copilot review feedback
    # on PR #166): otherwise ``--no-api-key`` would launch the Codex
    # subprocess without usable auth even though :func:`check_codex_auth`
    # accepted it.
    env_override: dict[str, str] | None = (
        env_without_api_key(harness_name=harness_name)
        if getattr(args, "no_api_key", False)
        else None
    )
    if getattr(args, "sync_tasks", False):
        env_override = env_with_sync_tasks(env_override)
    result = runner.run(
        args.skill,
        args.args or "",
        env=env_override,
        timeout=getattr(args, "timeout", None),
    )

    # Render error whenever the run was not clean — this includes explicit
    # error text (rate_limit / auth / api / timeout / subprocess) AND the
    # interactive-hang heuristic which sets ``error_category="interactive"``
    # + a ``warnings[0]`` tag but leaves ``result.error`` ``None`` (US-003).
    # The pre-US-005 ``if result.error:`` guard silently suppressed those.
    if not result.succeeded_cleanly:
        print(f"ERROR: {_render_skill_error(result)}", file=sys.stderr)

    if getattr(args, "json", False):
        # Structured stdout (no schema_version — that is reserved for
        # persisted sidecars; stdout --json mirrors validate/extract).
        # Deliberately excludes secret-bearing fields (api_key_source,
        # raw_messages, stream_events, harness_metadata).
        print(json.dumps(_result_to_json(result), indent=2))
        # In --json mode the run result is DATA: the skill's own exit
        # code (which can be -1 on spawn failure, 124 on timeout, or any
        # other subprocess code) is carried inside the payload as
        # ``exit_code``/``error``/``error_category``. Return 0 so a
        # JSON-consuming wrapper (the npm ``clauditor-eval`` bridge)
        # receives the parsed object instead of mapping an arbitrary
        # child exit code to a thrown error per the CLI exit taxonomy.
        return 0
    if result.output:
        print(result.output)

    return result.exit_code


def _result_to_json(result: SkillResult) -> dict:
    """Project a :class:`SkillResult` into the ``run --json`` payload.

    Pure: no I/O, no secrets. The shape is the documented stable
    surface for ``clauditor run --json`` (no ``schema_version`` — that
    convention is reserved for persisted sidecars).
    """
    return {
        "output": result.output,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "error": result.error,
        "error_category": result.error_category,
        "warnings": result.warnings,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "harness": result.harness,
        "skill": result.skill_name,
        "args": result.args,
    }
