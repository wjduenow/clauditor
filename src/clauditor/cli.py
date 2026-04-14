"""CLI entry point for clauditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor import history
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.paths import resolve_clauditor_dir
from clauditor.runner import SkillResult, SkillRunner
from clauditor.spec import SkillSpec
from clauditor.workspace import (
    InvalidSkillNameError,
    IterationExistsError,
    IterationWorkspace,
    allocate_iteration,
    validate_skill_name,
)


def _positive_int(value: str) -> int:
    """argparse type: accept integers >= 1."""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from e
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a skill's output against its eval spec (Layer 1 only)."""
    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        print(
            f"Create {Path(args.skill).with_suffix('.eval.json')}",
            file=sys.stderr,
        )
        return 1

    skill_result = None
    if args.output:
        # Validate against provided output file
        output = Path(args.output).read_text()
    else:
        # Run the skill to get output
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        skill_result = spec.run()
        if not skill_result.succeeded:
            print(
                f"ERROR: Skill failed to run: {skill_result.error}",
                file=sys.stderr,
            )
            return 1
        output = skill_result.output
        print(f"Skill completed in {skill_result.duration_seconds:.1f}s")

    # Run Layer 1 assertions
    results = run_assertions(output, spec.eval_spec.assertions)

    # Record history (US-005). Layer 1 only — no grader/quality/triggers.
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
            skill=spec.skill_name,
            pass_rate=results.pass_rate,
            mean_score=None,
            metrics=metrics_dict,
            command="validate",
        )
    except Exception as e:  # pragma: no cover - defensive
        print(f"WARNING: failed to append history: {e}", file=sys.stderr)

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
    run_dir: Path, output_text: str, stream_events: list[dict]
) -> None:
    """Write ``output.txt`` and ``output.jsonl`` to a ``run-K/`` subdir.

    ``stream_events`` is one JSON object per line. Empty list yields an
    empty ``output.jsonl`` (the file still exists for layout consistency).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.txt").write_text(output_text, encoding="utf-8")
    lines = [json.dumps(ev) for ev in stream_events]
    body = ("\n".join(lines) + "\n") if lines else ""
    (run_dir / "output.jsonl").write_text(body, encoding="utf-8")


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
                # list-of-strings case
                name = str(item)
            hay = f"{name}\n{desc}".lower()
            return any(n in hay for n in needles)

        filtered = [c for c in original if _match(c)]
        if not filtered:
            available = ", ".join(
                (getattr(c, "name", None) or str(c)) for c in original
            )
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
    from clauditor.quality_grader import (
        GradingReport,
        grade_quality,
    )

    n_variance = int(args.variance) if args.variance else 0
    total_runs = 1 + n_variance

    # Collect a (output_text, stream_events) tuple per run plus the
    # corresponding skill-side token / duration totals.
    run_outputs: list[tuple[str, list[dict]]] = []
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

    if not only_criterion:
        skill_dir = workspace.tmp_path
        for idx, (text, events) in enumerate(run_outputs):
            _write_run_dir(skill_dir / f"run-{idx}", text, events)

        (skill_dir / "grading.json").write_text(
            primary_report.to_json(), encoding="utf-8"
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
    numeric_form = any(v is not None for v in (skill, from_iter, to_iter))
    positional_form = args.before is not None or args.after is not None

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
            {"type": "min_length", "value": "500"},
            {"type": "has_urls", "value": "3"},
            {"type": "has_entries", "value": "3"},
            {"type": "not_contains", "value": "Error"},
        ],
        "sections": [
            {
                "name": "Results",
                "tiers": [
                    {
                        "label": "default",
                        "min_entries": 3,
                        "fields": [
                            {"name": "name", "required": True},
                            {"name": "address", "required": True},
                        ],
                    }
                ],
            }
        ],
        "grading_criteria": [
            "Are results relevant to the query?",
            "Are descriptions specific (not generic filler)?",
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
