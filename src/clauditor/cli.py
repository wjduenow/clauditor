"""CLI entry point for clauditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from clauditor import history

if TYPE_CHECKING:
    from clauditor.quality_grader import GradingReport
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.benchmark import compute_benchmark
from clauditor.paths import resolve_clauditor_dir
from clauditor.runner import SkillResult, SkillRunner
from clauditor.spec import SkillSpec
from clauditor.suggest import (
    NoPriorGradeError,
    load_suggest_input,
    propose_edits,
    render_unified_diff,
    write_sidecar,
)
from clauditor.workspace import (
    InvalidSkillNameError,
    IterationExistsError,
    IterationWorkspace,
    allocate_iteration,
    validate_skill_name,
)


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
    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        print(
            f"Create {Path(args.skill).with_suffix('.eval.json')}",
            file=sys.stderr,
        )
        return 1

    skill_result: SkillResult | None = None
    workspace: IterationWorkspace | None = None
    workspace_rel: str | None = None
    iteration_index: int | None = None

    if args.output:
        # Validate against a pre-captured output file. This path is
        # intentionally NOT wrapped in a workspace: there is no skill
        # subprocess to capture a transcript from, so there's nothing
        # to persist under ``run-0/``. Preserve pre-US-006 behavior.
        output = Path(args.output).read_text()
        results = run_assertions(output, spec.eval_spec.assertions)
    else:
        # Live-run path: allocate an iteration workspace, run the skill
        # into ``workspace.tmp_path / run-0``, persist sidecars, and
        # finalize atomically. On any exception, abort the staging dir.
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
            skill_result = spec.run(run_dir=workspace.tmp_path / "run-0")
            if not skill_result.succeeded:
                print(
                    f"ERROR: Skill failed to run: {skill_result.error}",
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

            assertions_payload = {
                "schema_version": 1,
                "skill": spec.skill_name,
                "iteration": workspace.iteration,
                "runs": [{"run": 0, **results.to_json()}],
            }
            (skill_dir / "assertions.json").write_text(
                json.dumps(assertions_payload, indent=2) + "\n",
                encoding="utf-8",
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
    from . import transcripts

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


def cmd_run(args: argparse.Namespace) -> int:
    """Run a skill and print its output."""
    runner = SkillRunner(
        project_dir=args.project_dir or Path.cwd(),
        timeout=args.timeout,
    )
    result = runner.run(args.skill, args.args or "")

    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)

    if result.output:
        print(result.output)

    return result.exit_code


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
    from . import transcripts

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


def cmd_grade(args: argparse.Namespace) -> int:
    """Run Layer 3 quality grading against a skill's output.

    Always writes a per-iteration workspace under
    ``.clauditor/iteration-N/<skill>/`` containing ``grading.json``,
    ``timing.json`` and one ``run-K/`` subdir per skill invocation
    (DEC-002, DEC-003, DEC-010, DEC-011, DEC-012, DEC-014).
    """
    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        return 1

    if not spec.eval_spec.grading_criteria:
        print("ERROR: No grading_criteria defined in eval spec", file=sys.stderr)
        return 1

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
        return 2
    if (
        getattr(args, "min_baseline_delta", None) is not None
        and not getattr(args, "baseline", False)
    ):
        print(
            "ERROR: --min-baseline-delta requires --baseline",
            file=sys.stderr,
        )
        return 2

    # --only-criterion: filter criteria before LLM call (token savings).
    # Partial runs produce a subset grading.json and must not publish an
    # iteration workspace (the partial report would later be misused as a
    # baseline). Reject combinations that would either destroy existing
    # iteration state or interact confusingly with diffing.
    only = getattr(args, "only_criterion", None)
    if only:
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


def _cmd_grade_with_workspace(
    *,
    args: argparse.Namespace,
    spec: SkillSpec,
    model: str,
    clauditor_dir: Path,
    workspace: IterationWorkspace,
) -> int:
    """Body of cmd_grade that runs inside an allocated iteration workspace."""
    import asyncio

    from clauditor.metrics import TokenUsage, build_metrics
    from clauditor.quality_grader import grade_quality

    n_variance = int(args.variance) if args.variance else 0
    total_runs = 1 + n_variance

    # Collect a (output_text, stream_events) tuple per run plus the
    # corresponding skill-side token / duration totals.
    run_outputs: list[tuple[str, list[dict]]] = []
    # Parallel list of SkillResult objects — None entries correspond to
    # captured-text (--output) runs where no subprocess SkillResult exists.
    # Used by compute_benchmark to build the with_skill arm (#28 US-002).
    skill_results: list[SkillResult | None] = []
    skill_input_total = 0
    skill_output_total = 0
    skill_duration_total = 0.0

    # Run 0: primary. Honors --output (no subprocess) when provided.
    primary_skill_result: SkillResult | None = None
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
        )
        if not primary_skill_result.succeeded:
            print(
                f"ERROR: Skill failed: {primary_skill_result.error}",
                file=sys.stderr,
            )
            return 1
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
        variance_result = spec.run(run_dir=variance_run_dir)
        if not variance_result.succeeded:
            print(
                f"ERROR: Variance skill run failed: {variance_result.error}",
                file=sys.stderr,
            )
            return 1
        run_outputs.append(
            (variance_result.output, list(variance_result.stream_events))
        )
        skill_results.append(variance_result)
        skill_input_total += variance_result.input_tokens
        skill_output_total += variance_result.output_tokens
        skill_duration_total += variance_result.duration_seconds

    # Grade every run's output. Primary report drives the exit code; the
    # rest contribute to variance stats and aggregated token counts.
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

    reports = asyncio.run(_grade_all())
    primary_report = reports[0]

    # Build a VarianceReport when --variance was passed so the JSON /
    # human summary continues to surface stability statistics.
    variance_report = None
    if n_variance >= 1:
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
        variance_report = VarianceReport(
            skill_name=spec.skill_name,
            n_runs=total_runs,
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

    # Aggregate quality (Layer 3 grader) tokens across every run.
    quality_input_total = sum(r.input_tokens for r in reports)
    quality_output_total = sum(r.output_tokens for r in reports)

    metrics_dict = build_metrics(
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
    primary_report.metrics = metrics_dict

    # ------------------------------------------------------------------ #
    # Write workspace files (DEC-012: stage in tmp_path, then finalize)  #
    # ------------------------------------------------------------------ #
    # --only-criterion runs grade a filtered subset of criteria. That
    # partial report must NOT land in iteration-N/<skill>/grading.json
    # where --diff / compare / _find_prior_grading_json would later pick
    # it up as a misleading baseline. Skip the entire write+finalize step;
    # the outer finally block will abort() the staging dir.
    only_criterion = bool(getattr(args, "only_criterion", None))

    # Hoisted so the stdout delta printer (US-003) can see the computed
    # Benchmark after workspace.finalize(). Stays None in --output mode
    # where compute_benchmark is skipped.
    from clauditor.benchmark import Benchmark

    benchmark: Benchmark | None = None

    if not only_criterion:
        skill_dir = workspace.tmp_path
        verbose = bool(getattr(args, "verbose", False))
        no_transcript = bool(getattr(args, "no_transcript", False))
        if not no_transcript:
            for idx, (text, events) in enumerate(run_outputs):
                _write_run_dir(
                    skill_dir / f"run-{idx}", text, events, verbose=verbose
                )
        else:
            # Scrub any `run-K/` subtrees the skill already staged
            # (e.g. `inputs/` copies from input_files), so --no-transcript
            # does not leak half-populated run dirs into the published
            # iteration. Mirrors the same fix on the validate side.
            import shutil as _shutil

            for idx in range(len(run_outputs)):
                _shutil.rmtree(skill_dir / f"run-{idx}", ignore_errors=True)

        (skill_dir / "grading.json").write_text(
            primary_report.to_json(), encoding="utf-8"
        )

        # US-004: thread transcript_path onto every assertion result so
        # the auditor can jump from a failing row to the stream-json that
        # produced it. Captured-text mode (--output) has no run subprocess,
        # so no transcript file exists — transcript_path stays None.
        def _assertions_with_transcript(
            text: str, run_idx: int
        ) -> AssertionSet:
            result = run_assertions(text, spec.eval_spec.assertions)
            if args.output:
                return result
            if no_transcript:
                # US-005: --no-transcript suppresses the stream-json write,
                # so there's no file to point at. Leave transcript_path=None.
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

        # US-007: verbose transcript slice for any run whose assertions
        # failed. Runs against in-memory stream_events (no disk read)
        # and routes to stderr so grading JSON stdout stays clean.
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

        if spec.eval_spec.sections:
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

        if getattr(args, "baseline", False):
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
            if all(sr is not None for sr in skill_results):
                benchmark = compute_benchmark(
                    skill_name=spec.skill_name,
                    primary_reports=reports,
                    baseline_report=baseline_grading,
                    primary_results=[
                        sr for sr in skill_results if sr is not None
                    ],
                    baseline_result=baseline_skill_result,
                )
                (skill_dir / "benchmark.json").write_text(
                    benchmark.to_json(), encoding="utf-8"
                )

    if not only_criterion:
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

    # ------------------------------------------------------------------ #
    # Report to the user                                                  #
    # ------------------------------------------------------------------ #
    final_skill_dir = workspace.final_path if workspace.finalized else None

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

    # --diff: compare against the previous iteration's grading.json,
    # if any. Output goes to stderr in --json mode so stdout stays
    # parseable.
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

    # Append a history record for trendability (US-006). Skip when
    # --only-criterion is set: partial-criterion runs would silently
    # corrupt longitudinal pass_rate/mean_score trends.
    if not getattr(args, "only_criterion", None):
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

    # Determine exit code
    passed = primary_report.passed
    if variance_report:
        passed = passed and variance_report.passed

    # #28 US-004: --min-baseline-delta gate (DEC-008: exit 1 for
    # gate violation; DEC-009: equality passes). Runs after history
    # is recorded so the failing iteration is still published for
    # inspection. The delta block was already printed above, so the
    # user sees the observed delta before this diagnostic.
    if (
        getattr(args, "min_baseline_delta", None) is not None
        and benchmark is not None
    ):
        threshold = args.min_baseline_delta
        observed = benchmark.run_summary.delta.pass_rate
        if observed < threshold:
            print(
                f"ERROR: baseline delta {observed:+.2f} below "
                f"threshold {threshold:.2f}",
                file=sys.stderr,
            )
            return 1

    return 0 if passed else 1


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


def _load_assertion_set(
    path: Path, spec_path: str | None, eval_path: str | None
) -> AssertionSet:
    """Load an AssertionSet from either a ``.txt`` or ``.grade.json`` file.

    For ``.txt`` files, a skill spec is required and Layer 1 assertions are
    run against the file's contents. For ``.grade.json`` files, the saved
    GradingReport is deserialized and each GradingResult is adapted into an
    AssertionResult so the two formats can be diffed uniformly.
    """
    from clauditor.assertions import AssertionResult
    from clauditor.quality_grader import GradingReport

    if path.is_dir():
        grading = path / "grading.json"
        if not grading.is_file():
            raise ValueError(f"no grading.json found in {path}")
        path = grading
    suffix = "".join(path.suffixes)
    if path.suffix == ".txt":
        if not spec_path:
            raise ValueError(
                f"--spec is required when diffing .txt files ({path})"
            )
        spec = SkillSpec.from_file(spec_path, eval_path=eval_path)
        if not spec.eval_spec:
            raise ValueError(f"No eval spec found for {spec_path}")
        output = path.read_text()
        return run_assertions(output, spec.eval_spec.assertions)
    if (
        suffix.endswith(".grade.json")
        or path.name.endswith(".grade.json")
        or path.name == "grading.json"
    ):
        report = GradingReport.from_json(path.read_text())
        results = [
            AssertionResult(
                name=r.criterion,
                passed=r.passed,
                message=r.reasoning or ("pass" if r.passed else "fail"),
                kind="custom",
                evidence=r.evidence or None,
            )
            for r in report.results
        ]
        return AssertionSet(results=results)
    raise ValueError(
        f"Unsupported file type for {path}: expected .txt or .grade.json"
    )


def _file_kind(path: Path) -> str:
    """Return a coarse file-kind label for mismatch detection.

    Directories are treated as the ``grade.json`` kind so they diff
    uniformly with saved grade reports; the eventual ``grading.json``
    lookup in :func:`_load_assertion_set` surfaces a precise
    ``no grading.json found in <path>`` error when the dir is empty.
    """
    if path.is_dir():
        return "grade.json"
    if path.name == "grading.json":
        return "grade.json"
    if path.name.endswith(".grade.json"):
        return "grade.json"
    if path.suffix == ".txt":
        return "txt"
    return path.suffix or path.name


def _print_blind_report(report, before_path: Path, after_path: Path) -> None:
    """Format a :class:`BlindReport` into the human-readable verdict block.

    DEC-011 layout: model line, two score lines, preference, position
    agreement, reasoning. Filenames are reduced to basenames so the output
    stays terse regardless of the caller's invocation style.
    """
    mapping = {"a": "BEFORE", "b": "AFTER", "tie": "TIE"}
    preference = mapping.get(report.preference, report.preference.upper())
    agreement = "yes" if report.position_agreement else "no"
    before_name = before_path.name
    after_name = after_path.name

    print(f"Blind A/B comparison (model: {report.model})")
    print(f"  {before_name}: score {report.score_a:.2f}")
    print(f"  {after_name}:  score {report.score_b:.2f}")
    print(
        f"  preference: {preference} "
        f"(confidence {report.confidence:.2f})"
    )
    print(f"  position agreement: {agreement}")
    print(f"  reasoning: {report.reasoning}")


def _run_blind_compare(
    before_path: Path, after_path: Path, spec_path: str, eval_path: str | None
) -> int:
    """Dispatch blind A/B comparison for a pair of ``.txt`` outputs.

    Delegates spec/user_prompt/rubric/model resolution to
    :func:`blind_compare_from_spec`; this wrapper handles file I/O, stderr
    reporting, and the ``_print_blind_report`` call. Both files are read as
    plain UTF-8. Returns 0 regardless of which side wins — blind compare is
    informational, not a pass/fail gate.
    """
    import asyncio

    from clauditor.quality_grader import (
        blind_compare_from_spec,
        validate_blind_compare_spec,
    )

    skill_spec = SkillSpec.from_file(spec_path, eval_path=eval_path)

    # Fail-fast on invalid specs BEFORE any progress messages or file I/O:
    # the prior shape printed "Running blind A/B judge..." even when validation
    # would immediately raise, which misled users into thinking API calls had
    # happened.
    try:
        validate_blind_compare_spec(skill_spec)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for path in (before_path, after_path):
        if not path.is_file():
            print(
                f"ERROR: {path} does not exist or is not a regular file",
                file=sys.stderr,
            )
            return 2

    try:
        output_a = before_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(
            f"ERROR: {before_path} is not valid UTF-8 — blind compare "
            "requires plain text files",
            file=sys.stderr,
        )
        return 2
    try:
        output_b = after_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(
            f"ERROR: {after_path} is not valid UTF-8 — blind compare "
            "requires plain text files",
            file=sys.stderr,
        )
        return 2

    # US-002: all spec/user_prompt/rubric/model resolution happens inside
    # blind_compare_from_spec (shared with the pytest fixture from US-003).
    # We read the spec's model for the stderr progress line; the helper
    # resolves its own effective model internally. Validation already ran
    # above (fail-fast), so the progress message now reliably means
    # "actual API calls are about to happen".
    assert skill_spec.eval_spec is not None  # validate_blind_compare_spec enforced
    print(
        f"Running blind A/B judge ({skill_spec.eval_spec.grading_model}) "
        "— 2 API calls...",
        file=sys.stderr,
    )
    report = asyncio.run(
        blind_compare_from_spec(
            skill_spec,
            output_a,
            output_b,
        )
    )
    _print_blind_report(report, before_path, after_path)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Diff assertion results between two captured runs or saved grade reports.

    Accepts two positional files of matching type (both ``.txt`` or both
    ``.grade.json``). Prints flipped assertions (regressions + improvements)
    and exits non-zero if any regressions are detected.
    """
    from clauditor.comparator import diff_assertion_sets

    skill = getattr(args, "skill", None)
    from_iter = getattr(args, "from_iter", None)
    to_iter = getattr(args, "to_iter", None)
    blind = getattr(args, "blind", False)
    numeric_form = any(v is not None for v in (skill, from_iter, to_iter))
    positional_form = args.before is not None or args.after is not None

    if blind:
        if numeric_form:
            print(
                "ERROR: --blind currently only supports file-pair form "
                "(before.txt after.txt)",
                file=sys.stderr,
            )
            return 2
        if not positional_form or args.before is None or args.after is None:
            print(
                "ERROR: --blind currently only supports file-pair form "
                "(before.txt after.txt)",
                file=sys.stderr,
            )
            return 2
        before_path = Path(args.before)
        after_path = Path(args.after)
        if (
            _file_kind(before_path) != "txt"
            or _file_kind(after_path) != "txt"
        ):
            print(
                "ERROR: --blind currently only supports file-pair form "
                "(before.txt after.txt)",
                file=sys.stderr,
            )
            return 2
        if not args.spec:
            print(
                "ERROR: --blind requires --spec to provide the user prompt "
                "context",
                file=sys.stderr,
            )
            return 2
        return _run_blind_compare(
            before_path, after_path, args.spec, args.eval
        )

    if numeric_form and positional_form:
        print(
            "ERROR: cannot combine positional paths with "
            "--skill/--from/--to",
            file=sys.stderr,
        )
        return 2

    if numeric_form:
        if skill is None or from_iter is None or to_iter is None:
            print(
                "ERROR: --skill, --from, and --to must all be provided "
                "together",
                file=sys.stderr,
            )
            return 2
        try:
            validate_skill_name(skill)
        except InvalidSkillNameError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if from_iter < 1 or to_iter < 1:
            print(
                "ERROR: --from and --to must be >= 1",
                file=sys.stderr,
            )
            return 2
        clauditor_dir = resolve_clauditor_dir()
        before_path = clauditor_dir / f"iteration-{from_iter}" / skill
        after_path = clauditor_dir / f"iteration-{to_iter}" / skill
    else:
        if args.before is None or args.after is None:
            print(
                "ERROR: compare requires two positional paths or "
                "--skill/--from/--to",
                file=sys.stderr,
            )
            return 2
        before_path = Path(args.before)
        after_path = Path(args.after)

    before_kind = _file_kind(before_path)
    after_kind = _file_kind(after_path)
    if before_kind != after_kind:
        print(
            f"ERROR: Mismatched file types: {before_path} ({before_kind}) "
            f"vs {after_path} ({after_kind})",
            file=sys.stderr,
        )
        return 2
    if before_kind not in ("txt", "grade.json"):
        print(
            f"ERROR: Unsupported file type '{before_kind}'. "
            "Expected .txt or .grade.json.",
            file=sys.stderr,
        )
        return 2

    try:
        before_set = _load_assertion_set(before_path, args.spec, args.eval)
        after_set = _load_assertion_set(after_path, args.spec, args.eval)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    flips = diff_assertion_sets(before_set, after_set)

    regressions = [f for f in flips if f.kind == "regression"]
    improvements = [f for f in flips if f.kind == "improvement"]

    if not flips:
        print("no flips: assertion results match")
        return 0

    for f in flips:
        if f.kind == "regression":
            print(f"[REGRESSION] {f.name}: pass -> fail")
        elif f.kind == "improvement":
            print(f"[IMPROVEMENT] {f.name}: fail -> pass")
        elif f.kind == "new":
            tag = "pass" if f.after_passed else "fail"
            print(f"[NEW] {f.name}: {tag}")
        elif f.kind == "removed":
            tag = "pass" if f.before_passed else "fail"
            print(f"[REMOVED] {f.name}: was {tag}")

    print(
        f"\n{len(regressions)} regression(s), {len(improvements)} improvement(s)"
    )
    return 1 if regressions else 0


def cmd_triggers(args: argparse.Namespace) -> int:
    """Run trigger precision testing for a skill."""
    import asyncio

    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        return 1

    model = args.model or spec.eval_spec.grading_model

    if args.dry_run:
        from clauditor.triggers import build_trigger_prompt

        trigger_tests = spec.eval_spec.trigger_tests
        if not trigger_tests:
            print("ERROR: No trigger_tests defined in eval spec", file=sys.stderr)
            return 1
        print(f"Model: {model}")
        queries = [
            (q, True) for q in trigger_tests.should_trigger
        ] + [(q, False) for q in trigger_tests.should_not_trigger]
        for query, expected in queries:
            label = "should_trigger" if expected else "should_not_trigger"
            prompt = build_trigger_prompt(
                spec.eval_spec.skill_name, spec.eval_spec.description, query
            )
            print(f"\n--- {label}: \"{query}\" ---")
            print(prompt)
        return 0

    from clauditor.triggers import test_triggers

    report = asyncio.run(test_triggers(spec.eval_spec, model))

    if args.json:
        data = {
            "skill": spec.skill_name,
            "model": model,
            "passed": report.passed,
            "accuracy": report.accuracy,
            "precision": report.precision,
            "recall": report.recall,
            "results": [
                {
                    "query": r.query,
                    "expected": r.expected_trigger,
                    "predicted": r.predicted_trigger,
                    "passed": r.passed,
                    "confidence": r.confidence,
                    "reasoning": r.reasoning,
                }
                for r in report.results
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        print(f"Trigger Precision: {spec.skill_name} ({model})")
        print(report.summary())

    return 0 if report.passed else 1


def cmd_extract(args: argparse.Namespace) -> int:
    """Run Layer 2 schema extraction against a skill's output."""
    import asyncio

    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
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

    # Get output
    skill_result = None
    if args.output:
        output = Path(args.output).read_text()
    else:
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        skill_result = spec.run()
        if not skill_result.succeeded:
            print(f"ERROR: Skill failed: {skill_result.error}", file=sys.stderr)
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


def cmd_capture(args: argparse.Namespace) -> int:
    """Run a skill via ``claude -p`` and write stdout to a captured file.

    DEC-001/002/010/013: default path is ``tests/eval/captured/<skill>.txt``;
    ``--out`` overrides; ``--versioned`` appends ``-YYYY-MM-DD`` to the stem
    (combines with ``--out``). Skill name accepts an optional leading ``/``.
    """
    from datetime import date

    skill_name = args.skill.lstrip("/")
    skill_args = " ".join(args.skill_args) if args.skill_args else ""

    default_out = Path("tests/eval/captured") / f"{skill_name}.txt"
    out_path = Path(args.out) if args.out else default_out

    if args.versioned:
        stamp = date.today().isoformat()
        out_path = out_path.with_name(
            f"{out_path.stem}-{stamp}{out_path.suffix}"
        )

    runner = SkillRunner(claude_bin=args.claude_bin or "claude")
    print(f"Running /{skill_name} {skill_args}...", file=sys.stderr)
    result = runner.run(skill_name, skill_args)

    if not result.succeeded:
        print(
            f"ERROR: Skill run failed (exit {result.exit_code}): {result.error}",
            file=sys.stderr,
        )
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.output, encoding="utf-8")
    print(f"Captured {len(result.output)} chars to {out_path}", file=sys.stderr)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only environment diagnostics (DEC-005/008/014).

    Always exits 0 — this is a reporting tool, not a CI gate.
    """
    import importlib.metadata
    import importlib.util
    import shutil

    checks: list[tuple[str, str, str]] = []

    py_version = sys.version_info
    if py_version >= (3, 11):
        checks.append(
            (
                "python",
                "ok",
                f"Python {py_version.major}.{py_version.minor}.{py_version.micro}",
            )
        )
    else:
        checks.append(
            (
                "python",
                "fail",
                f"Python {py_version.major}.{py_version.minor} < 3.11 (required)",
            )
        )

    if importlib.util.find_spec("anthropic") is not None:
        checks.append(("anthropic", "ok", "SDK importable"))
    else:
        checks.append(
            (
                "anthropic",
                "warn",
                "SDK not installed (required only for Layer 2/3 grading)",
            )
        )

    claude_path = shutil.which("claude")
    if claude_path:
        checks.append(("claude-cli", "ok", claude_path))
    else:
        checks.append(
            ("claude-cli", "fail", "`claude` not found on PATH")
        )

    try:
        # Version-agnostic lookup: `entry_points(group=...)` is 3.10+, but
        # `doctor` must keep working even when the Python-version check
        # itself is about to fail, so fall back to filtering manually.
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            eps = eps.select(group="pytest11")
        else:
            eps = [
                ep for ep in eps
                if getattr(ep, "group", None) == "pytest11"
            ]
        names = [ep.name for ep in eps]
        if "clauditor" in names:
            checks.append(
                ("pytest-plugin", "ok", "clauditor registered under pytest11")
            )
        else:
            checks.append(
                (
                    "pytest-plugin",
                    "fail",
                    f"clauditor not registered (found: {names})",
                )
            )
    except Exception as e:  # pragma: no cover - defensive
        checks.append(("pytest-plugin", "fail", f"entry_points lookup failed: {e}"))

    spec = importlib.util.find_spec("clauditor")
    if spec is not None and spec.origin is not None:
        origin = Path(spec.origin).resolve()
        if "site-packages" in origin.parts and not origin.is_symlink():
            checks.append(
                (
                    "editable-install",
                    "warn",
                    f"clauditor installed non-editable at {origin.parent} "
                    f"— source edits will not propagate",
                )
            )
        else:
            checks.append(("editable-install", "ok", str(origin.parent)))
    else:
        checks.append(("editable-install", "fail", "clauditor package not importable"))

    width = max(len(name) for name, _, _ in checks)
    for name, status, detail in checks:
        tag = f"[{status}]"
        print(f"{tag:<7} {name:<{width}}  {detail}")

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Generate a starter eval.json for a skill."""
    skill_path = Path(args.skill)
    eval_path = skill_path.with_suffix(".eval.json")

    if eval_path.exists() and not args.force:
        print(
            f"ERROR: {eval_path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    starter = {
        "skill_name": skill_path.stem,
        "description": f"Eval spec for /{skill_path.stem}",
        "test_args": "",
        "assertions": [
            {"id": "min_length_500", "type": "min_length", "value": "500"},
            {"id": "has_urls_3", "type": "has_urls", "value": "3"},
            {"id": "has_entries_3", "type": "has_entries", "value": "3"},
            {"id": "no_error", "type": "not_contains", "value": "Error"},
        ],
        "sections": [
            {
                "name": "Results",
                "tiers": [
                    {
                        "label": "default",
                        "min_entries": 3,
                        "fields": [
                            {
                                "id": "results_name",
                                "name": "name",
                                "required": True,
                            },
                            {
                                "id": "results_address",
                                "name": "address",
                                "required": True,
                            },
                        ],
                    }
                ],
            }
        ],
        "grading_criteria": [
            {
                "id": "relevant",
                "criterion": "Are results relevant to the query?",
            },
            {
                "id": "specific",
                "criterion": "Are descriptions specific (not generic filler)?",
            },
        ],
        "grading_model": "claude-sonnet-4-6",
        "trigger_tests": {
            "should_trigger": [],
            "should_not_trigger": [],
        },
        "variance": {
            "n_runs": 3,
            "min_stability": 0.8,
        },
    }

    eval_path.write_text(json.dumps(starter, indent=2) + "\n")
    print(f"Created {eval_path}")
    print("Edit the file to define your skill's expected output structure.")
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    """Render a trend line (TSV + ASCII sparkline) for a skill metric."""
    records = history.read_records(skill=args.skill_name)
    if not records:
        print(
            f"ERROR: no history records for skill '{args.skill_name}'. "
            "Run `clauditor grade` first.",
            file=sys.stderr,
        )
        return 1

    command_filter = args.command_filter
    if command_filter != "all":
        # v1 records (pre-#21) have no "command" key; they were all produced
        # by cmd_grade, so treat a missing key as "grade" for filter purposes.
        records = [
            rec
            for rec in records
            if rec.get("command", "grade") == command_filter
        ]
        if not records:
            print(
                f"ERROR: no history records for skill '{args.skill_name}' "
                f"with command '{command_filter}'. Try --command all to "
                "union across all recorded commands.",
                file=sys.stderr,
            )
            return 1

    last_n = args.last
    if last_n is not None and last_n > 0:
        records = records[-last_n:]

    if args.list_metrics:
        paths: set[str] = set()
        for rec in records:
            paths |= history.collect_metric_paths(rec)
        if not paths:
            print(
                f"ERROR: no metric paths available for skill "
                f"'{args.skill_name}'.",
                file=sys.stderr,
            )
            return 1
        for path in sorted(paths):
            print(path)
        return 0

    metric = args.metric
    timestamps: list[str] = []
    values: list[float] = []
    for rec in records:
        v = history.resolve_path(rec, metric)
        if v is None:
            continue
        timestamps.append(str(rec.get("ts", "")))
        values.append(float(v))

    if not values:
        print(
            f"ERROR: no records with metric '{metric}' for skill "
            f"'{args.skill_name}'.",
            file=sys.stderr,
        )
        return 1

    for ts, v in zip(timestamps, values):
        print(f"{ts}\t{v}")
    print(history.sparkline(values))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Load + aggregate + threshold-check per-assertion pass rates.

    US-006: adds threshold-based flagging (DEC-005), markdown report
    written to ``.clauditor/audit/<skill>-<ts>.md``, stdout summary
    table, ``--json`` mode, and an exit code of ``1`` whenever any
    assertion is flagged.
    """
    from datetime import UTC, datetime

    from clauditor.audit import (
        aggregate,
        apply_thresholds,
        load_iterations,
        render_json,
        render_markdown,
        render_stdout_table,
    )

    # Reject path-traversal / shell-metacharacter skill names before any
    # filesystem use — report_path joins `args.skill` into a filename.
    try:
        validate_skill_name(args.skill)
    except InvalidSkillNameError as e:
        print(f"invalid skill name: {e}", file=sys.stderr)
        return 2

    clauditor_dir = resolve_clauditor_dir()
    records, skipped = load_iterations(
        args.skill, last=args.last, clauditor_dir=clauditor_dir
    )

    if skipped:
        print(
            f"skipped {skipped} iteration dirs without assertion data",
            file=sys.stderr,
        )

    aggregates = aggregate(records)

    min_fail_rate = (
        args.min_fail_rate if args.min_fail_rate is not None else 0.0
    )
    min_discrimination = (
        args.min_discrimination
        if args.min_discrimination is not None
        else 0.05
    )

    verdicts = apply_thresholds(
        aggregates,
        min_fail_rate=min_fail_rate,
        min_discrimination=min_discrimination,
    )

    iterations_analyzed = len({r.iteration for r in records})
    # Include microseconds so concurrent audits don't collide on the
    # report filename (FIX-8).
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    thresholds = {
        "last": args.last,
        "min_fail_rate": min_fail_rate,
        "min_discrimination": min_discrimination,
    }

    try:
        if args.json:
            payload = render_json(
                verdicts,
                skill=args.skill,
                iterations_analyzed=iterations_analyzed,
                thresholds=thresholds,
                timestamp=timestamp,
            )
            print(json.dumps(payload, indent=2))
        else:
            if not aggregates:
                print(
                    f"No audit data for skill {args.skill!r} under "
                    f"{clauditor_dir}"
                )
            else:
                output_dir = (
                    args.output_dir
                    if args.output_dir is not None
                    else clauditor_dir / "audit"
                )
                try:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    report_path = (
                        output_dir / f"{args.skill}-{timestamp}.md"
                    )
                    report_path.write_text(
                        render_markdown(
                            verdicts,
                            skill=args.skill,
                            iterations_analyzed=iterations_analyzed,
                            thresholds=thresholds,
                            timestamp=timestamp,
                        ),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    # FIX-14: exit 1 is reserved for "flagged assertions";
                    # IO errors surface as exit 2 so CI can distinguish.
                    print(
                        f"clauditor audit: failed to write report under "
                        f"{output_dir}: {exc}",
                        file=sys.stderr,
                    )
                    return 2
                print(render_stdout_table(verdicts))
                print(f"\nReport written to {report_path}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"clauditor audit: error rendering report: {exc}", file=sys.stderr)
        return 2

    return 1 if any(v.is_flagged for v in verdicts) else 0


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
    - exit 2 when any proposal anchor fails validation (no sidecar).
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
    report = await propose_edits(suggest_input, model=args.model)

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clauditor",
        description="Auditor for Claude Code skills and slash commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
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
        "-v",
        "--verbose",
        action="store_true",
        help="On assertion failure, print the last 5 assistant text blocks to stderr",
    )

    # run
    p_run = subparsers.add_parser("run", help="Run a skill and print output")
    p_run.add_argument("skill", help="Skill name (e.g., find-kid-activities)")
    p_run.add_argument("--args", help="Arguments to pass to the skill")
    p_run.add_argument("--project-dir", help="Project directory (default: cwd)")
    p_run.add_argument("--timeout", type=int, default=180, help="Timeout in seconds")

    # grade
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

    # compare
    p_compare = subparsers.add_parser(
        "compare",
        help=(
            "Diff assertion results between two captured outputs "
            "(.txt) or saved grade reports (.grade.json)"
        ),
    )
    p_compare.add_argument(
        "before",
        nargs="?",
        default=None,
        help="Baseline file, iteration dir (.txt or .grade.json or dir)",
    )
    p_compare.add_argument(
        "after",
        nargs="?",
        default=None,
        help="Candidate file, iteration dir (.txt or .grade.json or dir)",
    )
    p_compare.add_argument(
        "--spec",
        default=None,
        help="Path to skill .md (required when diffing .txt files)",
    )
    p_compare.add_argument(
        "--eval",
        default=None,
        help="Path to eval.json (auto-discovered if omitted)",
    )
    p_compare.add_argument(
        "--skill",
        default=None,
        help="Skill name (used with --from/--to to resolve iteration dirs)",
    )
    p_compare.add_argument(
        "--from",
        dest="from_iter",
        default=None,
        type=_positive_int,
        help="Baseline iteration number >= 1 (requires --skill)",
    )
    p_compare.add_argument(
        "--to",
        dest="to_iter",
        default=None,
        type=_positive_int,
        help="Candidate iteration number >= 1 (requires --skill)",
    )
    p_compare.add_argument(
        "--blind",
        action="store_true",
        help=(
            "Run a blind A/B LLM judge over two .txt outputs "
            "(requires --spec). Prints a preference verdict."
        ),
    )

    # triggers
    p_triggers = subparsers.add_parser(
        "triggers", help="Run trigger precision testing for a skill"
    )
    p_triggers.add_argument("skill", help="Path to skill .md file")
    p_triggers.add_argument(
        "--eval", help="Path to eval.json (auto-discovered if omitted)"
    )
    p_triggers.add_argument("--model", help="Override grading model")
    p_triggers.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    p_triggers.add_argument(
        "--dry-run", action="store_true", help="Print sample trigger prompts"
    )

    # extract
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

    # init
    p_init = subparsers.add_parser(
        "init", help="Generate a starter eval.json for a skill"
    )
    p_init.add_argument("skill", help="Path to skill .md file")
    p_init.add_argument(
        "--force", action="store_true", help="Overwrite existing eval.json"
    )

    # capture
    p_capture = subparsers.add_parser(
        "capture",
        help="Run a skill via `claude -p` and save stdout to a captured file",
    )
    p_capture.add_argument(
        "skill",
        help="Skill name (leading slash optional, e.g. find-restaurants)",
    )
    p_capture.add_argument(
        "--out",
        default=None,
        help="Output file path (default: tests/eval/captured/<skill>.txt)",
    )
    p_capture.add_argument(
        "--versioned",
        action="store_true",
        help="Append -YYYY-MM-DD to the output file stem",
    )
    p_capture.add_argument(
        "--claude-bin",
        default=None,
        help="Path to the `claude` CLI (default: `claude` on PATH)",
    )
    p_capture.add_argument(
        "skill_args",
        nargs="*",
        help="Arguments to pass to the skill (put after `--`)",
    )

    # trend
    p_trend = subparsers.add_parser(
        "trend",
        help="Print a trend line (TSV + ASCII sparkline) from grade history",
    )
    p_trend.add_argument("skill_name", help="Skill name to trend")
    p_trend_group = p_trend.add_mutually_exclusive_group(required=True)
    p_trend_group.add_argument(
        "--metric",
        help=(
            "Metric to trend (pass_rate, mean_score, or a dotted path "
            "into metrics like total.total or grader.input_tokens)"
        ),
    )
    p_trend_group.add_argument(
        "--list-metrics",
        action="store_true",
        help="List every available metric path in history for the skill",
    )
    p_trend.add_argument(
        "--command",
        dest="command_filter",
        choices=["grade", "extract", "validate", "all"],
        default="grade",
        help="Filter history records by command (default: grade)",
    )
    p_trend.add_argument(
        "--last",
        type=_positive_int,
        default=20,
        help="Show last N records (default 20; must be >= 1)",
    )

    # audit
    p_audit = subparsers.add_parser(
        "audit",
        help=(
            "Aggregate per-assertion pass rates across the last N "
            "iteration workspaces for a skill"
        ),
        description=(
            "Aggregate per-assertion pass rates across the last N "
            "iteration workspaces for a skill. Exit codes: "
            "0 (clean), 1 (flagged assertions), 2 (error)."
        ),
    )
    p_audit.add_argument("skill", help="Skill name to audit")
    p_audit.add_argument(
        "--last",
        type=_positive_int,
        default=20,
        help="Consider the last N iteration dirs (default 20)",
    )
    p_audit.add_argument(
        "--min-fail-rate",
        type=_unit_float,
        default=None,
        help="(US-006) minimum fail rate to flag an assertion (0.0-1.0)",
    )
    p_audit.add_argument(
        "--min-discrimination",
        type=_unit_float,
        default=None,
        help="(US-006) minimum with/baseline delta to flag (0.0-1.0)",
    )
    p_audit.add_argument(
        "--json",
        action="store_true",
        help="(US-006) emit machine-readable JSON instead of a table",
    )
    p_audit.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="(US-006) directory to write audit reports",
    )

    # suggest
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

    # doctor
    subparsers.add_parser(
        "doctor",
        help=(
            "Report environment diagnostics "
            "(Python, SDK, claude CLI, plugin, install)"
        ),
    )

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

    return 1


if __name__ == "__main__":
    sys.exit(main())
