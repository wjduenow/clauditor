#!/usr/bin/env python3
"""Micro-benchmark for the ``claude -p`` CLI transport spawn overhead.

Measures the wall-clock cost of the CLI transport branch in
:func:`clauditor._anthropic.call_anthropic` relative to the SDK
(HTTP) branch for a trivial prompt. The goal is to quantify the
per-call overhead added by spawning a ``claude`` subprocess + reading
its stream-json output, so operators can decide when the CLI path's
subscription-auth convenience is worth the extra latency.

Per DEC-016 of ``plans/super/86-claude-cli-transport.md``:

- Single machine, otherwise the numbers have no baseline.
- At least 10 runs per transport (``--runs 10`` minimum).
- Report mean, p95, and stddev.
- Distinguish cold (first spawn after idle) vs warm (subsequent
  spawns while the OS page cache is hot) for the CLI branch.
- The first SDK call in a process also pays a one-time TLS /
  httpx-client construction cost; we report SDK cold vs warm
  symmetrically for fair comparison.

Not intended to run in CI — a live ``claude`` binary + valid
``ANTHROPIC_API_KEY`` (and subscription) are required. Run
manually on a dedicated machine; paste the output into
``docs/transport-architecture.md#spawn-overhead-benchmark``.

Usage::

    uv run python scripts/bench_cli_transport.py
    uv run python scripts/bench_cli_transport.py --runs 20
    uv run python scripts/bench_cli_transport.py --transport cli  # CLI only
    uv run python scripts/bench_cli_transport.py --prompt "2+2="

Exits 0 on clean completion; 1 on any measurement failure (missing
binary, missing API key, ``AnthropicHelperError`` / ``ClaudeCLIError``
surfaced from a run). Transport failures are fatal: a mid-benchmark
auth error produces garbage statistics, so we bail rather than
silently dropping samples.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Make ``clauditor._anthropic`` importable when this script is run
# directly from the repo root — mirrors the scripts/ convention used
# by ``validate_skill_frontmatter.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))


_DEFAULT_PROMPT = (
    "Reply with the single word 'ok' and nothing else. "
    "No explanation, no punctuation."
)
_DEFAULT_MODEL = "claude-haiku-4-5"
_DEFAULT_RUNS = 10


@dataclass
class _Sample:
    """One successful call's timing + token accounting."""

    duration: float
    input_tokens: int
    output_tokens: int


@dataclass
class _Stats:
    """Aggregated timing across a run group."""

    n: int
    mean: float
    median: float
    p95: float
    stddev: float
    total_tokens: int


def _compute_stats(samples: list[_Sample]) -> _Stats:
    """Aggregate a list of samples into mean / median / p95 / stddev."""
    durations = [s.duration for s in samples]
    n = len(durations)
    if n == 0:
        return _Stats(0, 0.0, 0.0, 0.0, 0.0, 0)
    mean = statistics.fmean(durations)
    median = statistics.median(durations)
    sorted_durations = sorted(durations)
    # p95: nearest-rank convention (no interpolation) — with n=10 this
    # lands on sample index 9 (the largest); with n=20 on index 18.
    p95_index = max(0, min(n - 1, int(round(0.95 * n)) - 1))
    p95 = sorted_durations[p95_index]
    stddev = statistics.pstdev(durations) if n > 1 else 0.0
    total_tokens = sum(s.input_tokens + s.output_tokens for s in samples)
    return _Stats(n, mean, median, p95, stddev, total_tokens)


async def _run_once(
    prompt: str, *, model: str, transport: str
) -> _Sample:
    """Issue one ``call_anthropic`` invocation and return a :class:`_Sample`.

    Raises any exception verbatim so the caller can decide whether to
    abort the benchmark. We deliberately do NOT retry — a retry would
    hide transient issues that affect the distribution.
    """
    from clauditor._anthropic import call_anthropic

    start = time.monotonic()
    result = await call_anthropic(prompt, model=model, transport=transport)
    duration = time.monotonic() - start
    return _Sample(
        duration=duration,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


async def _run_group(
    prompt: str,
    *,
    model: str,
    transport: str,
    runs: int,
) -> tuple[_Sample, list[_Sample]]:
    """Run ``runs + 1`` invocations; split into (cold, warm-runs-list).

    The first invocation is treated as the "cold" sample (pays
    one-time costs: CLI binary page-in for the CLI transport, TLS +
    httpx-client construction for the SDK transport). Subsequent
    invocations are "warm" samples for the distribution.
    """
    cold = await _run_once(prompt, model=model, transport=transport)
    warm: list[_Sample] = []
    for _ in range(runs):
        warm.append(await _run_once(prompt, model=model, transport=transport))
    return cold, warm


def _format_stats(label: str, stats: _Stats) -> str:
    """Render a one-line summary of a stats block."""
    return (
        f"{label:<12} n={stats.n:>3}  "
        f"mean={stats.mean:6.3f}s  "
        f"median={stats.median:6.3f}s  "
        f"p95={stats.p95:6.3f}s  "
        f"stddev={stats.stddev:6.3f}s  "
        f"tokens={stats.total_tokens}"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the CLI vs SDK transport for "
            "clauditor._anthropic.call_anthropic. "
            "See docs/transport-architecture.md#spawn-overhead-benchmark."
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=_DEFAULT_RUNS,
        help=(
            f"Warm-run count per transport (default {_DEFAULT_RUNS}). "
            "One additional cold-run call happens before the warm batch."
        ),
    )
    parser.add_argument(
        "--model",
        default=_DEFAULT_MODEL,
        help=f"Model name (default {_DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--prompt",
        default=_DEFAULT_PROMPT,
        help="Prompt body. Keep it tiny — we're measuring overhead.",
    )
    parser.add_argument(
        "--transport",
        choices=("api", "cli", "both"),
        default="both",
        help="Which transport to benchmark (default both).",
    )
    return parser.parse_args(argv)


def _preflight(transport: str) -> int:
    """Verify prerequisites; print a clear error and return an exit code.

    Returns 0 when everything needed for the requested ``transport``
    mode is available; non-zero otherwise.
    """
    if transport in ("api", "both"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "ERROR: ANTHROPIC_API_KEY is not set; cannot benchmark the "
                "SDK transport. Pass --transport cli to skip.",
                file=sys.stderr,
            )
            return 2
    if transport in ("cli", "both"):
        if shutil.which("claude") is None:
            print(
                "ERROR: `claude` is not on PATH; cannot benchmark the CLI "
                "transport. Pass --transport api to skip.",
                file=sys.stderr,
            )
            return 2
    return 0


async def _amain(args: argparse.Namespace) -> int:
    """Async main. Orchestrates the run groups and prints results."""
    print(
        f"# bench_cli_transport.py — model={args.model} "
        f"runs={args.runs} prompt_len={len(args.prompt)}"
    )
    print("# cold = first call in this process (pays one-time costs)")
    print("# warm = subsequent calls (steady-state overhead)")
    print()

    if args.transport in ("api", "both"):
        try:
            api_cold, api_warm = await _run_group(
                args.prompt,
                model=args.model,
                transport="api",
                runs=args.runs,
            )
        except Exception as exc:  # noqa: BLE001 — surface anything
            print(
                f"ERROR: SDK transport benchmark failed: {exc!r}",
                file=sys.stderr,
            )
            return 1
        api_cold_stats = _compute_stats([api_cold])
        api_warm_stats = _compute_stats(api_warm)
        print(_format_stats("api cold", api_cold_stats))
        print(_format_stats("api warm", api_warm_stats))
        print()

    if args.transport in ("cli", "both"):
        try:
            cli_cold, cli_warm = await _run_group(
                args.prompt,
                model=args.model,
                transport="cli",
                runs=args.runs,
            )
        except Exception as exc:  # noqa: BLE001 — surface anything
            print(
                f"ERROR: CLI transport benchmark failed: {exc!r}",
                file=sys.stderr,
            )
            return 1
        cli_cold_stats = _compute_stats([cli_cold])
        cli_warm_stats = _compute_stats(cli_warm)
        print(_format_stats("cli cold", cli_cold_stats))
        print(_format_stats("cli warm", cli_warm_stats))
        print()

    if args.transport == "both":
        # Overhead of CLI relative to SDK on the warm path — the
        # steady-state delta an operator should expect when routing
        # many calls through the CLI transport.
        overhead_mean = cli_warm_stats.mean - api_warm_stats.mean
        overhead_p95 = cli_warm_stats.p95 - api_warm_stats.p95
        print(
            f"# CLI warm overhead vs SDK warm: "
            f"mean_delta={overhead_mean:+.3f}s  "
            f"p95_delta={overhead_p95:+.3f}s"
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Parses args, preflights, and dispatches to ``_amain``."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    pre = _preflight(args.transport)
    if pre != 0:
        return pre
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("bench_cli_transport: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
