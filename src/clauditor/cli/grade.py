"""``clauditor grade`` — Layer 3 quality grading against a skill's output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from clauditor import history
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.benchmark import Benchmark, compute_benchmark
from clauditor.paths import resolve_clauditor_dir
from clauditor.runner import SkillResult
from clauditor.spec import SkillSpec
from clauditor.workspace import (
    InvalidSkillNameError,
    IterationExistsError,
    IterationWorkspace,
    allocate_iteration,
)

if TYPE_CHECKING:
    from clauditor.quality_grader import GradingReport, VarianceReport


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``grade`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _positive_int, _unit_float

    p_grade = subparsers.add_parser(
        "grade", help="Run Layer 3 quality grading against a skill's output"
    )
    p_grade.add_argument("skill", help="Path to skill .md file")
    p_grade.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_grade.add_argument(
        "--output", help="Path to pre-captured output file (skips running the skill)"
    )
    p_grade.add_argument("--model", help="Override grading model")
    p_grade.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_grade.add_argument(
        "--dry-run", action="store_true", help="Print prompt without making API calls"
    )
    p_grade.add_argument(
        "--variance",
        type=int,
        metavar="N",
        help="Run N times with variance measurement",
    )
    p_grade.add_argument(
        "--iteration",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Explicit iteration index (>= 1). Defaults to auto-increment "
            "(scan .clauditor/iteration-* and pick max+1)."
        ),
    )
    p_grade.add_argument(
        "--force",
        action="store_true",
        help=(
            "With --iteration N, remove the existing iteration-N/ "
            "directory before writing (clean slate)."
        ),
    )
    p_grade.add_argument(
        "--diff",
        action="store_true",
        help="Compare against the previous iteration's grading.json",
    )
    p_grade.add_argument(
        "--baseline",
        action="store_true",
        help=(
            "After grading, run the test args through Claude without the "
            "skill prefix (baseline) and capture L1/L2/L3 sidecars. "
            "Roughly doubles LLM cost; never the default. Also writes "
            "benchmark.json and prints a delta block on stdout "
            "(skipped under --output, where subprocess metrics are "
            "unavailable)."
        ),
    )
    p_grade.add_argument(
        "--min-baseline-delta",
        type=_unit_float,
        default=None,
        help=(
            "(#28) Fail with exit 1 if the with-skill vs without-skill "
            "pass_rate delta is below this threshold (0.0-1.0). Requires "
            "--baseline. Equality passes: --min-baseline-delta 0.0 is a "
            "strict no-regression gate (default: no gate). Without "
            "--baseline, the flag is an input error (exit 2)."
        ),
    )
    p_grade.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Log verbose grading details to stderr, including per-run "
            "transcript redaction counts "
            "(clauditor.transcripts: redacted N matches in run-K) and "
            "failing transcript slices when assertions fail"
        ),
    )
    p_grade.add_argument(
        "--no-transcript",
        action="store_true",
        help="Skip writing per-run stream-json transcripts",
    )
    p_grade.add_argument(
        "--only-criterion",
        action="append",
        default=None,
        metavar="SUBSTRING",
        help=(
            "Run only criteria whose name/description contains SUBSTRING"
            " (case-insensitive, repeatable)"
        ),
    )
    p_grade.add_argument(
        "--no-api-key",
        action="store_true",
        help=(
            "Strip ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the "
            "subprocess environment to force subscription auth."
        ),
    )
    p_grade.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        metavar="SECONDS",
        help=(
            "Override the runner timeout (seconds); must be > 0. "
            "Defaults to EvalSpec.timeout or 180s."
        ),
    )


def _load_and_validate_grade_args(
    args: argparse.Namespace,
) -> tuple[SkillSpec | None, int]:
    """Load the spec and validate input-only args pre-LLM.

    Returns ``(spec, 0)`` on success; ``(None, rc)`` on input error with
    ``rc in {1, 2}`` — the caller returns that exit code directly. An
    error message has already been printed to stderr.
    """
    from clauditor.cli import _load_spec_or_report

    spec = _load_spec_or_report(args.skill, args.eval)
    if spec is None:
        return None, 2

    if not spec.eval_spec:
        print(
            f"ERROR: No eval spec found for {args.skill}\n"
            f"Run 'clauditor init {args.skill}' to create one.",
            file=sys.stderr,
        )
        return None, 1

    if not spec.eval_spec.grading_criteria:
        print("ERROR: No grading_criteria defined in eval spec", file=sys.stderr)
        return None, 1

    # #28 US-004: --min-baseline-delta depends on --baseline. Knowable from
    # args alone, so validate early alongside other input-error exit-2 paths.
    if (
        getattr(args, "min_baseline_delta", None) is not None
        and getattr(args, "output", None)
    ):
        print(
            "ERROR: --min-baseline-delta is incompatible with --output "
            "(benchmark delta requires live subprocess metrics)",
            file=sys.stderr,
        )
        return None, 2
    if (
        getattr(args, "min_baseline_delta", None) is not None
        and not getattr(args, "baseline", False)
    ):
        print(
            "ERROR: --min-baseline-delta requires --baseline",
            file=sys.stderr,
        )
        return None, 2

    return spec, 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Run Layer 3 quality grading against a skill's output.

    Always writes a per-iteration workspace under
    ``.clauditor/iteration-N/<skill>/`` containing ``grading.json``,
    ``timing.json`` and one ``run-K/`` subdir per skill invocation
    (DEC-002, DEC-003, DEC-010, DEC-011, DEC-012, DEC-014).
    """
    spec, rc = _load_and_validate_grade_args(args)
    if spec is None:
        return rc

    # --only-criterion: filter criteria before LLM call (token savings).
    # Partial runs produce a subset grading.json and must not publish an
    # iteration workspace (the partial report would later be misused as a
    # baseline). Reject combinations that would either destroy existing
    # iteration state or interact confusingly with diffing.
    only = getattr(args, "only_criterion", None)
    if only:
        filter_rc = _apply_only_criterion_filter(args, spec, only)
        if filter_rc != 0:
            return filter_rc

    model = args.model or spec.eval_spec.grading_model

    # --dry-run: print prompt and exit
    if args.dry_run:
        from clauditor.quality_grader import build_grading_prompt

        prompt = build_grading_prompt(spec.eval_spec)
        print(f"Model: {model}")
        print(f"Prompt:\n{prompt}")
        return 0

    # Allocate the iteration workspace early so that a collision
    # (--iteration N already exists) fails before we make any LLM calls.
    clauditor_dir = resolve_clauditor_dir()
    try:
        workspace = allocate_iteration(
            clauditor_dir,
            spec.skill_name,
            iteration=getattr(args, "iteration", None),
            force=getattr(args, "force", False),
        )
    except IterationExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except InvalidSkillNameError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        return _cmd_grade_with_workspace(
            args=args,
            spec=spec,
            model=model,
            clauditor_dir=clauditor_dir,
            workspace=workspace,
        )
    except IterationExistsError as exc:
        # Raised by workspace.finalize() when a concurrent peer won the
        # rename race. The staging dir has already been aborted inside
        # finalize(); surface a clean error instead of a traceback.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        # workspace.finalized is set to True inside finalize(); any other
        # exit path (early return on skill failure, exception mid-write,
        # --only-criterion partial run) leaves the staging dir behind,
        # so we clean it up here. --only-criterion in particular must NOT
        # publish a partial grading.json that later runs would mistake
        # for a baseline.
        if not workspace.finalized:
            workspace.abort()


def _apply_only_criterion_filter(
    args: argparse.Namespace, spec: SkillSpec, only: list[str]
) -> int:
    """Filter ``spec.eval_spec.grading_criteria`` in-place by ``only``.

    Returns 0 on success (spec mutated), 2 on input error (message
    already printed to stderr).
    """
    conflict = []
    if getattr(args, "iteration", None) is not None:
        conflict.append("--iteration")
    if getattr(args, "force", False):
        conflict.append("--force")
    if getattr(args, "diff", False):
        conflict.append("--diff")
    if conflict:
        print(
            "ERROR: --only-criterion cannot be combined with "
            + ", ".join(conflict)
            + " (partial runs are not persisted to the iteration workspace)",
            file=sys.stderr,
        )
        return 2
    needles = [s.lower() for s in only]
    original = list(spec.eval_spec.grading_criteria)

    def _match(item: object) -> bool:
        name = getattr(item, "name", None) or ""
        desc = getattr(item, "description", None) or ""
        if not name and not desc:
            if isinstance(item, dict):
                # Canonical {id, criterion} shape from from_file
                name = str(item.get("id", ""))
                desc = str(item.get("criterion", ""))
            else:
                # list-of-strings case (in-memory fixtures)
                name = str(item)
        hay = f"{name}\n{desc}".lower()
        return any(n in hay for n in needles)

    def _label(item: object) -> str:
        if isinstance(item, dict):
            return str(item.get("id") or item.get("criterion") or item)
        return getattr(item, "name", None) or str(item)

    filtered = [c for c in original if _match(c)]
    if not filtered:
        available = ", ".join(_label(c) for c in original)
        print(
            f"No grading criteria match filter. Available: {available}",
            file=sys.stderr,
        )
        return 2
    spec.eval_spec.grading_criteria = filtered
    return 0


def _run_skill_variants(
    args: argparse.Namespace,
    spec: SkillSpec,
    workspace: IterationWorkspace,
    n_variance: int,
) -> tuple[
    list[tuple[str, list[dict]]],
    list[SkillResult | None],
    int,
    int,
    float,
    int | None,
]:
    """Run the skill N+1 times (primary + variance) or load from ``--output``.

    Returns ``(run_outputs, skill_results, skill_input_total,
    skill_output_total, skill_duration_total, error_rc)``. ``error_rc`` is
    non-``None`` when the caller should return that exit code (skill
    subprocess failure).
    """
    # Shared helper lives in ``clauditor.cli`` (package __init__). Import
    # lazily to avoid a circular import at module load.
    from clauditor.cli import _render_skill_error
    from clauditor.runner import _env_without_api_key

    # DEC-001, DEC-006, DEC-014: thread CLI auth/timeout flags through
    # to every ``spec.run`` invocation (primary + variance). Defaults
    # are both None (today's behavior).
    env_override = (
        _env_without_api_key()
        if getattr(args, "no_api_key", False)
        else None
    )
    timeout_override = getattr(args, "timeout", None)

    run_outputs: list[tuple[str, list[dict]]] = []
    # Parallel list of SkillResult objects — None entries correspond to
    # captured-text (--output) runs where no subprocess SkillResult exists.
    # Used by compute_benchmark to build the with_skill arm (#28 US-002).
    skill_results: list[SkillResult | None] = []
    skill_input_total = 0
    skill_output_total = 0
    skill_duration_total = 0.0

    # Run 0: primary. Honors --output (no subprocess) when provided.
    if args.output:
        if spec.eval_spec is not None and spec.eval_spec.input_files:
            print(
                "WARNING: --output bypasses the runner; "
                "input_files declaration is ignored.",
                file=sys.stderr,
            )
        primary_text = Path(args.output).read_text()
        run_outputs.append((primary_text, []))
        skill_results.append(None)
    else:
        print(
            f"Running /{spec.skill_name} {spec.eval_spec.test_args}..."
        )
        primary_skill_result = spec.run(
            run_dir=workspace.tmp_path / "run-0",
            timeout_override=timeout_override,
            env_override=env_override,
        )
        if not primary_skill_result.succeeded_cleanly:
            print(
                f"ERROR: Skill failed: "
                f"{_render_skill_error(primary_skill_result)}",
                file=sys.stderr,
            )
            return run_outputs, skill_results, 0, 0, 0.0, 1
        primary_text = primary_skill_result.output
        run_outputs.append(
            (primary_text, list(primary_skill_result.stream_events))
        )
        skill_results.append(primary_skill_result)
        skill_input_total += primary_skill_result.input_tokens
        skill_output_total += primary_skill_result.output_tokens
        skill_duration_total += primary_skill_result.duration_seconds

    # Variance runs: always invoke the skill subprocess (variance against
    # a fixed --output file would be meaningless).
    for variance_idx in range(n_variance):
        # In captured-output mode, skip run_dir threading so input_files
        # staging is suppressed (warning already printed above).
        variance_run_dir = (
            None
            if args.output
            else workspace.tmp_path / f"run-{variance_idx + 1}"
        )
        variance_result = spec.run(
            run_dir=variance_run_dir,
            timeout_override=timeout_override,
            env_override=env_override,
        )
        if not variance_result.succeeded_cleanly:
            print(
                f"ERROR: Variance skill run failed: "
                f"{_render_skill_error(variance_result)}",
                file=sys.stderr,
            )
            return run_outputs, skill_results, 0, 0, 0.0, 1
        run_outputs.append(
            (variance_result.output, list(variance_result.stream_events))
        )
        skill_results.append(variance_result)
        skill_input_total += variance_result.input_tokens
        skill_output_total += variance_result.output_tokens
        skill_duration_total += variance_result.duration_seconds

    return (
        run_outputs,
        skill_results,
        skill_input_total,
        skill_output_total,
        skill_duration_total,
        None,
    )


def _build_variance_report(
    *,
    reports: list[GradingReport],
    skill_name: str,
    spec: SkillSpec,
    model: str,
    n_runs: int,
    skill_input_total: int,
    skill_output_total: int,
    skill_duration_total: float,
) -> VarianceReport:
    """Aggregate per-run :class:`GradingReport`s into a ``VarianceReport``."""
    from clauditor.quality_grader import VarianceReport

    scores = [r.mean_score for r in reports]
    score_mean = sum(scores) / len(scores)
    score_stddev = (
        sum((s - score_mean) ** 2 for s in scores) / len(scores)
    ) ** 0.5
    pass_rate_mean = sum(r.pass_rate for r in reports) / len(reports)
    stability = sum(1 for r in reports if r.passed) / len(reports)
    min_stability = 0.8
    if spec.eval_spec.variance is not None:
        min_stability = spec.eval_spec.variance.min_stability
    # Token / duration fields cover *all* runs (primary + variance),
    # matching the pre-#22 measure_variance() contract — downstream
    # cost accounting reads these for the full n_runs, not just
    # non-primary. See review Pass 1 bug 4.
    return VarianceReport(
        skill_name=skill_name,
        n_runs=n_runs,
        reports=reports,
        score_mean=score_mean,
        score_stddev=score_stddev,
        pass_rate_mean=pass_rate_mean,
        stability=stability,
        min_stability=min_stability,
        model=model,
        input_tokens=sum(r.input_tokens for r in reports),
        output_tokens=sum(r.output_tokens for r in reports),
        skill_input_tokens=skill_input_total,
        skill_output_tokens=skill_output_total,
        skill_duration_seconds=skill_duration_total,
    )


def _write_workspace_sidecars(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    workspace: IterationWorkspace,
    clauditor_dir: Path,
    run_outputs: list[tuple[str, list[dict]]],
    skill_results: list[SkillResult | None],
    primary_text: str,
    primary_report: GradingReport,
    reports: list[GradingReport],
    model: str,
) -> Benchmark | None:
    """Write grading/assertions/extraction/baseline/benchmark sidecars.

    Runs during workspace staging (before ``workspace.finalize()``) per
    ``.claude/rules/sidecar-during-staging.md``. Returns the computed
    ``Benchmark`` when ``--baseline`` was passed AND every primary run
    has a real :class:`SkillResult` (i.e. not ``--output`` mode);
    otherwise returns ``None``.
    """
    skill_dir = workspace.tmp_path
    verbose = bool(getattr(args, "verbose", False))
    no_transcript = bool(getattr(args, "no_transcript", False))

    _write_run_dirs_or_scrub(
        skill_dir=skill_dir,
        run_outputs=run_outputs,
        no_transcript=no_transcript,
        verbose=verbose,
    )

    (skill_dir / "grading.json").write_text(
        primary_report.to_json(), encoding="utf-8"
    )

    _write_assertions_sidecar(
        args=args,
        spec=spec,
        workspace=workspace,
        clauditor_dir=clauditor_dir,
        run_outputs=run_outputs,
        verbose=verbose,
        no_transcript=no_transcript,
    )

    if spec.eval_spec.sections:
        _write_extraction_sidecar(skill_dir, primary_text, spec)

    if getattr(args, "baseline", False):
        return _write_baseline_and_benchmark(
            spec=spec,
            skill_dir=skill_dir,
            workspace=workspace,
            skill_results=skill_results,
            reports=reports,
            model=model,
        )

    return None


def _write_run_dirs_or_scrub(
    *,
    skill_dir: Path,
    run_outputs: list[tuple[str, list[dict]]],
    no_transcript: bool,
    verbose: bool,
) -> None:
    """Write per-run transcripts, or scrub pre-staged run dirs.

    Under ``--no-transcript``, pre-staged ``run-K/`` subtrees (e.g.
    ``inputs/`` copies from ``input_files``) are removed so the skip
    flag does not leak half-populated run dirs into the published
    iteration (see ``.claude/rules/sidecar-during-staging.md``).
    """
    from clauditor.cli import _write_run_dir

    if not no_transcript:
        for idx, (text, events) in enumerate(run_outputs):
            _write_run_dir(
                skill_dir / f"run-{idx}", text, events, verbose=verbose
            )
        return
    import shutil as _shutil

    for idx in range(len(run_outputs)):
        _shutil.rmtree(skill_dir / f"run-{idx}", ignore_errors=True)


def _write_assertions_sidecar(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    workspace: IterationWorkspace,
    clauditor_dir: Path,
    run_outputs: list[tuple[str, list[dict]]],
    verbose: bool,
    no_transcript: bool,
) -> None:
    """Run L1 assertions per run, write ``assertions.json``, emit slices.

    Threads ``transcript_path`` onto every assertion result so the
    auditor can jump from a failing row to the stream-json that
    produced it (US-004). Under ``verbose`` mode, failing-run transcript
    slices are printed to stderr (US-007).
    """
    from clauditor.cli import _print_failing_transcript_slice, _relative_to_repo

    skill_dir = workspace.tmp_path

    # US-004: Captured-text mode (--output) has no run subprocess, so no
    # transcript file exists — transcript_path stays None. US-005:
    # --no-transcript suppresses the stream-json write, so there's no
    # file to point at.
    def _assertions_with_transcript(text: str, run_idx: int) -> AssertionSet:
        result = run_assertions(text, spec.eval_spec.assertions)
        if args.output or no_transcript:
            return result
        # Path is computed against workspace.final_path (the post-
        # finalize iteration-N/<skill>/ dir), NOT the staging dir,
        # so readers of assertions.json see a stable repo-relative
        # path after the atomic rename.
        transcript_rel = _relative_to_repo(
            clauditor_dir,
            workspace.final_path / f"run-{run_idx}" / "output.jsonl",
        )
        for r in result.results:
            r.transcript_path = transcript_rel
        return result

    per_run_assertions: list[tuple[int, AssertionSet]] = [
        (idx, _assertions_with_transcript(text, idx))
        for idx, (text, _events) in enumerate(run_outputs)
    ]

    if verbose:
        for idx, aset in per_run_assertions:
            if aset.failed:
                _print_failing_transcript_slice(
                    idx, run_outputs[idx][1], sys.stderr
                )

    assertions_payload = {
        "schema_version": 1,
        "skill": spec.skill_name,
        "iteration": workspace.iteration,
        "runs": [
            {"run": idx, **aset.to_json()}
            for idx, aset in per_run_assertions
        ],
    }
    (skill_dir / "assertions.json").write_text(
        json.dumps(assertions_payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_extraction_sidecar(
    skill_dir: Path, primary_text: str, spec: SkillSpec
) -> None:
    """Run Layer 2 schema extraction and persist ``extraction.json``."""
    import asyncio

    from clauditor.grader import extract_and_report

    extraction_report = asyncio.run(
        extract_and_report(
            primary_text,
            spec.eval_spec,
            skill_name=spec.skill_name,
        )
    )
    (skill_dir / "extraction.json").write_text(
        extraction_report.to_json(), encoding="utf-8"
    )


def _write_baseline_and_benchmark(
    *,
    spec: SkillSpec,
    skill_dir: Path,
    workspace: IterationWorkspace,
    skill_results: list[SkillResult | None],
    reports: list[GradingReport],
    model: str,
) -> Benchmark | None:
    """Run the baseline phase and compute+persist the benchmark delta.

    Returns the computed :class:`Benchmark` when every primary run has
    a real :class:`SkillResult` (i.e. not ``--output`` mode); otherwise
    returns ``None`` (the baseline sidecars still land on disk).
    """
    baseline_grading, baseline_skill_result = _run_baseline_phase(
        spec=spec,
        skill_dir=skill_dir,
        iteration=workspace.iteration,
        model=model,
    )

    # #28 US-002: compute the pair-run benchmark delta and persist
    # it alongside the baseline_*.json sidecars. Skipped in
    # captured-text mode (--output) because there is no primary
    # SkillResult to supply duration / token metrics for the
    # with_skill arm.
    if not all(sr is not None for sr in skill_results):
        return None
    benchmark = compute_benchmark(
        skill_name=spec.skill_name,
        primary_reports=reports,
        baseline_report=baseline_grading,
        primary_results=[sr for sr in skill_results if sr is not None],
        baseline_result=baseline_skill_result,
    )
    (skill_dir / "benchmark.json").write_text(
        benchmark.to_json(), encoding="utf-8"
    )
    return benchmark


def _build_grade_metrics(
    *,
    reports: list[GradingReport],
    skill_input_total: int,
    skill_output_total: int,
    skill_duration_total: float,
) -> dict:
    """Aggregate skill + quality-grader tokens into a bucketed metrics dict."""
    from clauditor.metrics import TokenUsage, build_metrics

    quality_input_total = sum(r.input_tokens for r in reports)
    quality_output_total = sum(r.output_tokens for r in reports)

    return build_metrics(
        skill=TokenUsage(
            input_tokens=skill_input_total,
            output_tokens=skill_output_total,
        ),
        duration_seconds=skill_duration_total,
        quality=TokenUsage(
            input_tokens=quality_input_total,
            output_tokens=quality_output_total,
        ),
    )


def _run_baseline_phase(
    *,
    spec: SkillSpec,
    skill_dir: Path,
    iteration: int,
    model: str,
) -> tuple[GradingReport, SkillResult]:
    """Run the baseline (no skill prefix) and persist sidecars.

    Thin I/O wrapper around :func:`clauditor.baseline.compute_baseline`:
    handles subprocess invocation, input-file staging, stderr progress,
    and sidecar writes into ``skill_dir`` (the staging iteration dir).

    Returns ``(GradingReport, SkillResult)`` so the caller can feed
    them to :func:`clauditor.benchmark.compute_benchmark`.
    """
    from clauditor.baseline import compute_baseline
    from clauditor.workspace import stage_inputs

    test_args = spec.eval_spec.test_args or ""
    # FIX-15: mirror the primary run's staging so baseline finds input
    # files in the same relative layout.
    effective_cwd: Path | None = None
    if spec.eval_spec.input_files:
        baseline_run_dir = skill_dir / "baseline-run"
        sources = [Path(p) for p in spec.eval_spec.input_files]
        stage_inputs(baseline_run_dir, sources)
        effective_cwd = baseline_run_dir / "inputs"
    print(f"Running baseline (no skill prefix) {test_args}...")
    baseline_result = spec.runner.run_raw(test_args, cwd=effective_cwd)

    reports = compute_baseline(
        skill_result=baseline_result,
        eval_spec=spec.eval_spec,
        skill_name=spec.skill_name,
        iteration=iteration,
        model=model,
    )

    for filename, content in reports.to_json_map().items():
        (skill_dir / filename).write_text(content, encoding="utf-8")

    return reports.grading_report, reports.skill_result


def _write_timing_and_finalize(
    *,
    workspace: IterationWorkspace,
    spec: SkillSpec,
    metrics_dict: dict,
    total_runs: int,
) -> None:
    """Write ``timing.json`` to the staging dir and atomically finalize.

    Must run inside the staging block (before the workspace is published)
    per ``.claude/rules/sidecar-during-staging.md``. ``workspace.finalize()``
    performs the atomic rename; on failure the caller's ``except`` path
    invokes ``workspace.abort()``.
    """
    skill_dir = workspace.tmp_path
    timing_payload: dict = {
        "skill": spec.skill_name,
        "iteration": workspace.iteration,
        "n_runs": total_runs,
        "metrics": metrics_dict,
    }
    (skill_dir / "timing.json").write_text(
        json.dumps(timing_payload, indent=2) + "\n", encoding="utf-8"
    )
    workspace.finalize()


def _grade_all_runs(
    run_outputs: list[tuple[str, list[dict]]],
    spec: SkillSpec,
    model: str,
) -> list[GradingReport]:
    """Grade every run's output concurrently and return the per-run reports."""
    import asyncio

    from clauditor.quality_grader import grade_quality

    async def _grade_all() -> list[GradingReport]:
        return list(
            await asyncio.gather(
                *[
                    grade_quality(
                        text,
                        spec.eval_spec,
                        model,
                        thresholds=spec.eval_spec.grade_thresholds,
                    )
                    for text, _ev in run_outputs
                ]
            )
        )

    return asyncio.run(_grade_all())


def _cmd_grade_with_workspace(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    model: str,
    clauditor_dir: Path,
    workspace: IterationWorkspace,
) -> int:
    """Body of cmd_grade that runs inside an allocated iteration workspace."""
    n_variance = int(args.variance) if args.variance else 0
    total_runs = 1 + n_variance

    # Run the skill N+1 times (primary + --variance); grade every run.
    (
        run_outputs,
        skill_results,
        skill_input_total,
        skill_output_total,
        skill_duration_total,
        error_rc,
    ) = _run_skill_variants(args, spec, workspace, n_variance)
    if error_rc is not None:
        return error_rc

    primary_text = run_outputs[0][0]
    reports = _grade_all_runs(run_outputs, spec, model)
    primary_report = reports[0]

    variance_report: VarianceReport | None = None
    if n_variance >= 1:
        variance_report = _build_variance_report(
            reports=reports,
            skill_name=spec.skill_name,
            spec=spec,
            model=model,
            n_runs=total_runs,
            skill_input_total=skill_input_total,
            skill_output_total=skill_output_total,
            skill_duration_total=skill_duration_total,
        )

    metrics_dict = _build_grade_metrics(
        reports=reports,
        skill_input_total=skill_input_total,
        skill_output_total=skill_output_total,
        skill_duration_total=skill_duration_total,
    )
    primary_report.metrics = metrics_dict

    # Stage workspace files (DEC-012). --only-criterion runs grade a
    # filtered subset of criteria; that partial report must NOT land in
    # iteration-N/<skill>/grading.json where --diff / compare /
    # _find_prior_grading_json would later pick it up as a misleading
    # baseline. Skip the entire write+finalize step; the outer finally
    # block will abort() the staging dir. `benchmark` stays None in
    # --output mode where compute_benchmark is skipped.
    only_criterion = bool(getattr(args, "only_criterion", None))
    benchmark: Benchmark | None = None
    if not only_criterion:
        benchmark = _write_workspace_sidecars(
            args=args,
            spec=spec,
            workspace=workspace,
            clauditor_dir=clauditor_dir,
            run_outputs=run_outputs,
            skill_results=skill_results,
            primary_text=primary_text,
            primary_report=primary_report,
            reports=reports,
            model=model,
        )
        _write_timing_and_finalize(
            workspace=workspace,
            spec=spec,
            metrics_dict=metrics_dict,
            total_runs=total_runs,
        )

    _report_grade_to_user(
        args=args,
        spec=spec,
        model=model,
        clauditor_dir=clauditor_dir,
        workspace=workspace,
        primary_report=primary_report,
        variance_report=variance_report,
        benchmark=benchmark,
        metrics_dict=metrics_dict,
    )

    # Determine exit code
    passed = primary_report.passed
    if variance_report:
        passed = passed and variance_report.passed

    gate_rc = _check_min_baseline_delta_gate(args, benchmark)
    if gate_rc is not None:
        return gate_rc

    return 0 if passed else 1


def _report_grade_to_user(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    model: str,
    clauditor_dir: Path,
    workspace: IterationWorkspace,
    primary_report: GradingReport,
    variance_report: VarianceReport | None,
    benchmark: Benchmark | None,
    metrics_dict: dict,
) -> None:
    """Emit the grade report, diff/baseline delta blocks, and history row.

    Bundles the post-finalize "report to the user" phase: stdout JSON or
    human-readable grade output, ``--diff`` block, ``--baseline`` delta
    block, and the ``history.jsonl`` append (US-006). Does NOT compute
    the exit code — the caller owns that based on
    :attr:`primary_report.passed` and the ``--min-baseline-delta`` gate.
    """
    final_skill_dir = workspace.final_path if workspace.finalized else None

    _print_grade_output(
        args=args,
        spec=spec,
        model=model,
        workspace=workspace,
        final_skill_dir=final_skill_dir,
        primary_report=primary_report,
        variance_report=variance_report,
    )

    _emit_diff_and_baseline_blocks(
        args=args,
        spec=spec,
        clauditor_dir=clauditor_dir,
        workspace=workspace,
        primary_report=primary_report,
        benchmark=benchmark,
    )

    # Append a history record for trendability (US-006). Skip when
    # --only-criterion is set: partial-criterion runs would silently
    # corrupt longitudinal pass_rate/mean_score trends.
    if not getattr(args, "only_criterion", None):
        _append_grade_history_record(
            spec=spec,
            clauditor_dir=clauditor_dir,
            workspace=workspace,
            final_skill_dir=final_skill_dir,
            primary_report=primary_report,
            metrics_dict=metrics_dict,
        )


def _check_min_baseline_delta_gate(
    args: argparse.Namespace, benchmark: Benchmark | None
) -> int | None:
    """Check ``--min-baseline-delta`` against the computed benchmark.

    Returns ``1`` when the gate fails (DEC-008), ``None`` otherwise.
    Equality passes per DEC-009 (``--min-baseline-delta 0.0`` is a
    strict no-regression gate). Runs after history is recorded so the
    failing iteration is still published for inspection. The delta
    block was already printed above, so the user sees the observed
    delta before this diagnostic.
    """
    if (
        getattr(args, "min_baseline_delta", None) is None
        or benchmark is None
    ):
        return None
    threshold = args.min_baseline_delta
    observed = benchmark.run_summary.delta.pass_rate
    if observed < threshold:
        print(
            f"ERROR: baseline delta {observed:+.2f} below "
            f"threshold {threshold:.2f}",
            file=sys.stderr,
        )
        return 1
    return None


def _print_grade_output(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    model: str,
    workspace: IterationWorkspace,
    final_skill_dir: Path | None,
    primary_report: GradingReport,
    variance_report: VarianceReport | None,
) -> None:
    """Print the grade report to stdout (JSON or human-readable form).

    In ``--json`` mode, emits a single JSON document with ``grade``,
    ``variance``, and workspace metadata. Otherwise prints the
    :meth:`GradingReport.summary` block plus optional variance summary
    and a ``Workspace:`` breadcrumb line when the iteration was
    finalized.
    """
    if args.json:
        data: dict = {
            "skill": spec.skill_name,
            "model": model,
            "iteration": workspace.iteration if final_skill_dir else None,
            "workspace": str(final_skill_dir) if final_skill_dir else None,
            "grade": {
                "passed": primary_report.passed,
                "pass_rate": primary_report.pass_rate,
                "mean_score": primary_report.mean_score,
                "results": [
                    {
                        "criterion": r.criterion,
                        "passed": r.passed,
                        "score": r.score,
                        "evidence": r.evidence,
                        "reasoning": r.reasoning,
                    }
                    for r in primary_report.results
                ],
            },
            "variance": None,
        }
        if variance_report:
            data["variance"] = {
                "passed": variance_report.passed,
                "n_runs": variance_report.n_runs,
                "score_mean": variance_report.score_mean,
                "score_stddev": variance_report.score_stddev,
                "pass_rate_mean": variance_report.pass_rate_mean,
                "stability": variance_report.stability,
            }
        print(json.dumps(data, indent=2))
    else:
        print(f"Quality Grade: {spec.skill_name} ({model})")
        print(primary_report.summary())
        if variance_report:
            print(f"\n{variance_report.summary()}")
        if final_skill_dir is not None:
            print(f"\nWorkspace: {final_skill_dir}")


def _emit_diff_and_baseline_blocks(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    clauditor_dir: Path,
    workspace: IterationWorkspace,
    primary_report: GradingReport,
    benchmark: Benchmark | None,
) -> None:
    """Emit ``--diff`` (prior-iteration delta) and ``--baseline`` delta blocks.

    Output routes to stderr under ``--json`` so stdout stays parseable.
    No-op when neither flag is set.
    """
    diff_out = sys.stderr if args.json else sys.stdout
    if getattr(args, "diff", False):
        prior_path = _find_prior_grading_json(
            clauditor_dir, spec.skill_name, workspace.iteration
        )
        if prior_path is not None:
            _print_grade_diff(prior_path, primary_report, diff_out)
        else:
            print(
                "\nWARNING: No prior iteration grading.json found "
                f"for skill '{spec.skill_name}'.",
                file=sys.stderr,
            )

    # #28 US-003: plain, unconditional baseline delta block.
    # Gated on --baseline AND a computed Benchmark (skipped in --output
    # mode where primary SkillResult metrics are unavailable). DEC-010
    # — no TTY branching, no color, one format always. In --json mode
    # the block routes to stderr (same pattern as --diff) so stdout
    # stays parseable JSON for automated consumers.
    if getattr(args, "baseline", False) and benchmark is not None:
        _print_baseline_delta_block(benchmark, out=diff_out)


def _append_grade_history_record(
    *,
    spec: SkillSpec,
    clauditor_dir: Path,
    workspace: IterationWorkspace,
    final_skill_dir: Path | None,
    primary_report: GradingReport,
    metrics_dict: dict,
) -> None:
    """Append a ``command="grade"`` row to ``history.jsonl`` (US-006)."""
    from clauditor.cli import _relative_to_repo

    workspace_rel = _relative_to_repo(clauditor_dir, final_skill_dir)
    try:
        history.append_record(
            skill=spec.skill_name,
            pass_rate=primary_report.pass_rate,
            mean_score=primary_report.mean_score,
            metrics=metrics_dict,
            command="grade",
            iteration=workspace.iteration,
            workspace_path=workspace_rel,
        )
    except Exception as e:  # pragma: no cover - defensive
        print(f"WARNING: failed to append history: {e}", file=sys.stderr)


def _find_prior_grading_json(
    clauditor_dir: Path, skill: str, current_iteration: int
) -> Path | None:
    """Return the most recent prior ``grading.json`` for ``skill``, if any.

    Scans ``iteration-*`` siblings of ``iteration-<current>``, picks the
    highest index strictly less than ``current_iteration`` whose
    ``<skill>/grading.json`` exists.
    """
    if not clauditor_dir.exists():
        return None
    import re as _re

    pattern = _re.compile(r"^iteration-(\d+)$")
    candidates: list[int] = []
    for child in clauditor_dir.iterdir():
        if not child.is_dir():
            continue
        m = pattern.match(child.name)
        if m is None:
            continue
        idx = int(m.group(1))
        if idx >= current_iteration:
            continue
        if (child / skill / "grading.json").exists():
            candidates.append(idx)
    if not candidates:
        return None
    best = max(candidates)
    return clauditor_dir / f"iteration-{best}" / skill / "grading.json"


def _print_grade_diff(prior_path: Path, current_report, out) -> None:
    """Print a per-criterion diff against ``prior_path``'s GradingReport."""
    from clauditor.quality_grader import GradingReport

    prior = GradingReport.from_json(prior_path.read_text())
    prior_by_name = {r.criterion: r for r in prior.results}
    current_by_name = {r.criterion: r for r in current_report.results}
    common = set(prior_by_name) & set(current_by_name)
    regressions: list[str] = []
    print(f"\nDiff vs prior results ({prior_path}):", file=out)
    print(
        f"  {'Criterion':<40} {'Prior':>6} {'Current':>8} {'Delta':>6}",
        file=out,
    )
    print(f"  {'-'*40} {'-'*6} {'-'*8} {'-'*6}", file=out)
    for name in sorted(common):
        p_score = prior_by_name[name].score
        c_score = current_by_name[name].score
        delta = c_score - p_score
        is_regression = (
            delta < -0.1
            or (
                prior_by_name[name].passed
                and not current_by_name[name].passed
            )
        )
        marker = " REGRESSION" if is_regression else ""
        line = (
            f"  {name:<40} {p_score:>6.2f}"
            f" {c_score:>8.2f} {delta:>+6.2f}{marker}"
        )
        print(line, file=out)
        if is_regression:
            regressions.append(name)
    if regressions:
        print(f"\n  {len(regressions)} regression(s) detected.", file=out)
    else:
        print("\n  No regressions detected.", file=out)


def _print_baseline_delta_block(benchmark, out=sys.stdout) -> None:
    """Print the #28 US-003 baseline delta block to ``out``.

    DEC-010: plain unconditional output — no TTY detection, no ANSI color,
    no table / one-liner branching. One printer, one format, always.
    Signs are explicit on every delta row so the reader can scan a column
    of ``+``/``-`` without decoding column positions.
    """
    rs = benchmark.run_summary
    pr_delta = rs.delta.pass_rate
    pr_w = rs.with_skill.pass_rate.mean
    pr_wo = rs.without_skill.pass_rate.mean

    t_delta = rs.delta.time_seconds
    t_w = rs.with_skill.time_seconds.mean
    t_wo = rs.without_skill.time_seconds.mean

    tk_delta = int(round(rs.delta.tokens))
    tk_w = int(round(rs.with_skill.tokens.mean))
    tk_wo = int(round(rs.without_skill.tokens.mean))

    print("baseline delta:", file=out)
    print(
        f"  {'pass_rate':<12} {pr_delta:+.2f}  "
        f"(with_skill {pr_w:.2f}, without_skill {pr_wo:.2f})",
        file=out,
    )
    print(
        f"  {'time_seconds':<12} {t_delta:+.1f}  "
        f"(with_skill {t_w:.1f}, without_skill {t_wo:.1f})",
        file=out,
    )
    print(
        f"  {'tokens':<12} {tk_delta:+d}  "
        f"(with_skill {tk_w:d}, without_skill {tk_wo:d})",
        file=out,
    )
