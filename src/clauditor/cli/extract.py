"""``clauditor extract`` — Layer 2 schema extraction against a skill's output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor import history
from clauditor._anthropic import AnthropicAuthMissingError, check_anthropic_auth


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``extract`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _transport_choice

    p_extract = subparsers.add_parser(
        "extract", help="Layer 2: LLM schema extraction"
    )
    p_extract.add_argument("skill", help="Path to skill .md file")
    p_extract.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_extract.add_argument(
        "--output", help="Path to pre-captured output file (skips running the skill)"
    )
    p_extract.add_argument("--model", help="Override extraction model")
    p_extract.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_extract.add_argument(
        "--dry-run", action="store_true", help="Print prompt without making API calls"
    )
    p_extract.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print raw Haiku JSON under failing assertions when available",
    )
    p_extract.add_argument(
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


def cmd_extract(args: argparse.Namespace) -> int:
    """Run Layer 2 schema extraction against a skill's output."""
    import asyncio

    # Shared helpers live in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load: ``clauditor.cli``
    # imports this module to register the subparser.
    from clauditor.cli import _load_spec_or_report, _render_skill_error

    spec = _load_spec_or_report(args.skill, args.eval)
    if spec is None:
        return 2

    if not spec.eval_spec:
        print(
            f"ERROR: No eval spec found for {args.skill}\n"
            f"Run 'clauditor init {args.skill}' to create one.",
            file=sys.stderr,
        )
        return 1

    if not spec.eval_spec.sections:
        print("ERROR: No sections defined in eval spec", file=sys.stderr)
        return 1

    model = args.model or "claude-haiku-4-5-20251001"

    # --dry-run: print prompt and exit
    if args.dry_run:
        from clauditor.grader import build_extraction_prompt

        prompt = build_extraction_prompt(spec.eval_spec)
        print(f"Model: {model}")
        print(f"Prompt:\n{prompt}")
        return 0

    # #83 DEC-002/DEC-011: fail fast if ANTHROPIC_API_KEY is missing.
    # Guard lands AFTER --dry-run (dry-run is a cost-free preview — no
    # API call, no key needed) and BEFORE extract_and_grade.
    try:
        check_anthropic_auth("extract")
    except AnthropicAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Get output
    skill_result = None
    if args.output:
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
    else:
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        skill_result = spec.run()
        if not skill_result.succeeded_cleanly:
            print(
                f"ERROR: Skill failed: {_render_skill_error(skill_result)}",
                file=sys.stderr,
            )
            return 1
        output = skill_result.output

    # Extract and grade
    from clauditor.grader import extract_and_grade

    results = asyncio.run(extract_and_grade(output, spec.eval_spec, model))

    # Record history (US-005). Extract does not compute Layer 3
    # pass_rate/mean_score, so those are None (DEC-013).
    from clauditor.metrics import TokenUsage, build_metrics

    skill_tokens = TokenUsage(
        input_tokens=getattr(skill_result, "input_tokens", 0) or 0,
        output_tokens=getattr(skill_result, "output_tokens", 0) or 0,
    )
    skill_duration = getattr(skill_result, "duration_seconds", 0.0) or 0.0
    grader_tokens = TokenUsage(
        input_tokens=getattr(results, "input_tokens", 0) or 0,
        output_tokens=getattr(results, "output_tokens", 0) or 0,
    )
    metrics_dict = build_metrics(
        skill=skill_tokens,
        duration_seconds=skill_duration,
        grader=grader_tokens,
    )
    try:
        history.append_record(
            skill=spec.skill_name,
            pass_rate=None,
            mean_score=None,
            metrics=metrics_dict,
            command="extract",
        )
    except Exception as e:  # pragma: no cover - defensive
        print(f"WARNING: failed to append history: {e}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "skill": spec.skill_name,
                    "model": model,
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
        print(f"Schema Extraction: {spec.skill_name} ({model})")
        print(results.summary())
        if getattr(args, "verbose", False):
            for r in results.results:
                if not r.passed and r.raw_data is not None:
                    print(f"\nRaw data for {r.name}:")
                    print(json.dumps(r.raw_data, indent=2))

    return 0 if results.passed else 1
