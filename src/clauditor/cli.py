"""CLI entry point for clauditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor import history
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.runner import SkillRunner
from clauditor.spec import SkillSpec


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

    if args.output:
        # Validate against provided output file
        output = Path(args.output).read_text()
    else:
        # Run the skill to get output
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        result = spec.run()
        if not result.succeeded:
            print(f"ERROR: Skill failed to run: {result.error}", file=sys.stderr)
            return 1
        output = result.output
        print(f"Skill completed in {result.duration_seconds:.1f}s")

    # Run Layer 1 assertions
    results = run_assertions(output, spec.eval_spec.assertions)

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


def cmd_grade(args: argparse.Namespace) -> int:
    """Run Layer 3 quality grading against a skill's output."""
    import asyncio

    spec = SkillSpec.from_file(args.skill, eval_path=args.eval)

    if not spec.eval_spec:
        print(f"ERROR: No eval spec found for {args.skill}", file=sys.stderr)
        return 1

    if not spec.eval_spec.grading_criteria:
        print("ERROR: No grading_criteria defined in eval spec", file=sys.stderr)
        return 1

    # --only-criterion: filter criteria before LLM call (token savings)
    only = getattr(args, "only_criterion", None)
    if only:
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

    # Get output
    if args.output:
        output = Path(args.output).read_text()
    else:
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        result = spec.run()
        if not result.succeeded:
            print(f"ERROR: Skill failed: {result.error}", file=sys.stderr)
            return 1
        output = result.output

    # Grade (and optionally measure variance in a single event loop)
    from clauditor.quality_grader import grade_quality

    async def _run_grade():
        report = await grade_quality(
            output, spec.eval_spec, model,
            thresholds=spec.eval_spec.grade_thresholds,
        )
        variance_report = None
        if args.variance:
            from clauditor.quality_grader import measure_variance

            variance_report = await measure_variance(
                spec, args.variance, model
            )
        return report, variance_report

    report, variance_report = asyncio.run(_run_grade())

    if args.json:
        data: dict = {
            "skill": spec.skill_name,
            "model": model,
            "grade": {
                "passed": report.passed,
                "pass_rate": report.pass_rate,
                "mean_score": report.mean_score,
                "results": [
                    {
                        "criterion": r.criterion,
                        "passed": r.passed,
                        "score": r.score,
                        "evidence": r.evidence,
                        "reasoning": r.reasoning,
                    }
                    for r in report.results
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
        print(report.summary())
        if variance_report:
            print(f"\n{variance_report.summary()}")

    # --diff: compare against prior results
    # Use stderr for human output when --json is set to keep stdout valid JSON
    save_dir = Path(".clauditor")
    save_path = save_dir / f"{spec.skill_name}.grade.json"
    diff_out = sys.stderr if args.json else sys.stdout

    if args.diff:
        if save_path.exists():
            from clauditor.quality_grader import GradingReport

            prior = GradingReport.from_json(save_path.read_text())
            prior_by_name = {r.criterion: r for r in prior.results}
            current_by_name = {r.criterion: r for r in report.results}
            common = set(prior_by_name) & set(current_by_name)
            regressions = []
            print("\nDiff vs prior results:", file=diff_out)
            print(
                f"  {'Criterion':<40} {'Prior':>6} {'Current':>8} {'Delta':>6}",
                file=diff_out,
            )
            print(
                f"  {'-'*40} {'-'*6} {'-'*8} {'-'*6}", file=diff_out
            )
            for name in sorted(common):
                p_score = prior_by_name[name].score
                c_score = current_by_name[name].score
                delta = c_score - p_score
                is_regression = (
                    delta < -0.1
                    or (prior_by_name[name].passed and not current_by_name[name].passed)
                )
                marker = " REGRESSION" if is_regression else ""
                line = (
                    f"  {name:<40} {p_score:>6.2f}"
                    f" {c_score:>8.2f} {delta:>+6.2f}{marker}"
                )
                print(line, file=diff_out)
                if is_regression:
                    regressions.append(name)
            if regressions:
                print(
                    f"\n  {len(regressions)} regression(s) detected.", file=diff_out
                )
            else:
                print("\n  No regressions detected.", file=diff_out)
        else:
            print(
                f"\nWARNING: No prior results at {save_path}. "
                "Run with --save first to establish a baseline.",
                file=sys.stderr,
            )

    if args.save:
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_text(report.to_json())
        print(f"\nGrade report saved to {save_path}", file=diff_out)

    # Append a history record for trendability (US-006). Skip when
    # --only-criterion is set: partial-criterion runs would silently
    # corrupt longitudinal pass_rate/mean_score trends.
    if not getattr(args, "only_criterion", None):
        try:
            history.append_record(
                skill=spec.skill_name,
                pass_rate=report.pass_rate,
                mean_score=report.mean_score,
                metrics={},
            )
        except Exception as e:  # pragma: no cover - defensive
            print(f"WARNING: failed to append history: {e}", file=sys.stderr)

    # Determine exit code
    passed = report.passed
    if variance_report:
        passed = passed and variance_report.passed
    return 0 if passed else 1


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
    if suffix.endswith(".grade.json") or path.name.endswith(".grade.json"):
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
    """Return a coarse file-kind label for mismatch detection."""
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
    if args.output:
        output = Path(args.output).read_text()
    else:
        print(f"Running /{spec.skill_name} {spec.eval_spec.test_args}...")
        result = spec.run()
        if not result.succeeded:
            print(f"ERROR: Skill failed: {result.error}", file=sys.stderr)
            return 1
        output = result.output

    # Extract and grade
    from clauditor.grader import extract_and_grade

    results = asyncio.run(extract_and_grade(output, spec.eval_spec, model))

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

    last_n = args.last
    if last_n is not None and last_n > 0:
        records = records[-last_n:]

    metric = args.metric
    timestamps: list[str] = []
    values: list[float] = []
    for rec in records:
        if metric in ("pass_rate", "mean_score"):
            v = rec.get(metric)
        else:
            v = rec.get("metrics", {}).get(metric)
        if v is None:
            continue
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        timestamps.append(str(rec.get("ts", "")))
        values.append(value)

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
        "--save", action="store_true", help="Save grade report to .clauditor/"
    )
    p_grade.add_argument(
        "--diff", action="store_true", help="Compare against prior grade results"
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
    p_compare.add_argument("before", help="Baseline file (.txt or .grade.json)")
    p_compare.add_argument("after", help="Candidate file (.txt or .grade.json)")
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
    p_trend.add_argument(
        "--metric",
        required=True,
        help="Metric to trend (pass_rate, mean_score, or a metrics.<name>)",
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
