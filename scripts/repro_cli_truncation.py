#!/usr/bin/env python3
"""Reproduce CLI transport truncation for clauditor#94.

Calls :func:`clauditor.runner._invoke_claude_cli` directly with a prompt
engineered to elicit a long structured-JSON response (similar to a
grader verdict). Runs N times, records whether each response parses as
JSON, captures the final ``result`` message's ``stop_reason`` and token
counts, and dumps full ``raw_messages`` to a log dir on any truncation.

Usage::

    uv run python scripts/repro_cli_truncation.py --runs 20
    uv run python scripts/repro_cli_truncation.py --log-dir ./trunc-logs

Exits 0 regardless of truncation outcome — the goal is to gather
evidence, not gate CI.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# The investigation prompt: asks for a long, structured JSON response
# that mimics the shape of a real L3 grading response. Longer evidence
# strings + more criteria push output size toward the observed
# truncation boundary (~700-1000 tokens).
INVESTIGATION_PROMPT = """You are grading a skill output against 5 criteria. Return ONLY a JSON array with this exact shape (no prose before or after):

[
  {"criterion": "<criterion text>", "passed": true|false, "evidence": "<2-3 sentences quoting specific passages that justify the verdict>"},
  ...
]

Here are the 5 criteria. Invent plausible skill output in your head and grade against each one. Write evidence strings that are at least 200 characters each, quoting specific (imagined) passages from the output.

1. Does the output include a complete project overview with at least 3 paragraphs covering architecture, dependencies, and deployment considerations?
2. Does the output provide working code examples for every public API endpoint documented, including request/response schemas and error cases?
3. Does the output identify at least 4 distinct failure modes and explain the mitigation strategy for each, with concrete detection criteria?
4. Does the output recommend a specific versioning strategy (semver, calver, or custom) with justification based on the project's release cadence?
5. Does the output conclude with a prioritized list of follow-up tasks, each tagged with effort estimate (S/M/L) and a brief rationale?

Return ONLY the JSON array. No preamble, no markdown fences, no trailing prose."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10, help="Number of runs (default: 10)")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("./trunc-logs"),
        help="Where to write raw_messages dumps for truncated runs",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override model (e.g. 'sonnet', 'opus'). Defaults to CLI default.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-invocation timeout in seconds (default: 300)",
    )
    args = parser.parse_args()

    from clauditor.runner import _invoke_claude_cli, env_without_api_key
    import os

    args.log_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running {args.runs} CLI invocations against truncation-prone prompt...")
    print(f"Log dir for truncations: {args.log_dir}")
    print(f"Model: {args.model or '(CLI default)'}")
    print()

    results: list[dict] = []
    for i in range(args.runs):
        start = time.monotonic()
        invoke = _invoke_claude_cli(
            INVESTIGATION_PROMPT,
            cwd=None,
            env=env_without_api_key(os.environ),
            timeout=args.timeout,
            claude_bin="claude",
            model=args.model,
        )
        duration = time.monotonic() - start

        # Extract the final result message's stop_reason + usage.
        stop_reason = None
        result_msg = None
        for msg in invoke.raw_messages:
            if msg.get("type") == "result":
                result_msg = msg
                # stop_reason might live under `message` (SDK-style) or
                # be flat on the result — probe both.
                stop_reason = (
                    msg.get("stop_reason")
                    or (msg.get("message") or {}).get("stop_reason")
                )
                break

        # Try to parse the output as JSON to detect truncation.
        output = invoke.output.strip()
        # Strip markdown fences defensively (grader prompts get them too).
        if output.startswith("```"):
            lines = output.splitlines()
            output = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        parse_error: str | None = None
        parsed_ok = False
        try:
            data = json.loads(output)
            parsed_ok = isinstance(data, list) and len(data) >= 1
        except json.JSONDecodeError as exc:
            parse_error = f"{type(exc).__name__}: {exc.msg} at line {exc.lineno} col {exc.colno}"

        output_tail = output[-200:] if len(output) > 200 else output
        record = {
            "run": i,
            "duration_s": round(duration, 2),
            "exit_code": invoke.exit_code,
            "output_len": len(invoke.output),
            "input_tokens": invoke.input_tokens,
            "output_tokens": invoke.output_tokens,
            "stop_reason": stop_reason,
            "parsed_ok": parsed_ok,
            "parse_error": parse_error,
            "output_tail": output_tail,
            "error": invoke.error,
        }
        results.append(record)

        status = "OK " if parsed_ok else "FAIL"
        print(
            f"  run {i:2d}: {status} "
            f"exit={invoke.exit_code} "
            f"tokens={invoke.output_tokens:>4d} "
            f"stop={stop_reason!r:<20s} "
            f"dur={duration:.1f}s"
            + (f"  [{parse_error}]" if parse_error else "")
        )

        # On truncation, dump full raw_messages for inspection.
        if not parsed_ok:
            dump_path = args.log_dir / f"run-{i:02d}-raw.json"
            dump_path.write_text(
                json.dumps(
                    {
                        "summary": record,
                        "output_full": invoke.output,
                        "raw_messages": invoke.raw_messages,
                        "stream_events": invoke.stream_events,
                    },
                    indent=2,
                )
            )
            print(f"         -> dumped raw_messages to {dump_path}")

    # Summary.
    print()
    print("=" * 60)
    ok = sum(1 for r in results if r["parsed_ok"])
    fail = args.runs - ok
    print(f"Summary: {ok}/{args.runs} parsed OK, {fail}/{args.runs} failed")
    if fail > 0:
        fail_stops = sorted({r["stop_reason"] for r in results if not r["parsed_ok"]}, key=str)
        fail_tokens = [r["output_tokens"] for r in results if not r["parsed_ok"]]
        ok_tokens = [r["output_tokens"] for r in results if r["parsed_ok"]]
        print(f"Failed stop_reasons: {fail_stops}")
        print(f"Failed output_tokens: {fail_tokens}")
        print(f"Passed output_tokens: {ok_tokens}")

    # Always write the full summary JSON for post-hoc analysis.
    summary_path = args.log_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2))
    print(f"Full summary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
