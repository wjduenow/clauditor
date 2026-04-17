"""``clauditor compare`` — diff assertion results or saved grade reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.assertions import AssertionSet, run_assertions
from clauditor.paths import resolve_clauditor_dir
from clauditor.spec import SkillSpec
from clauditor.workspace import InvalidSkillNameError, validate_skill_name


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``compare`` subparser."""
    # Shared argparse type helpers live in the package __init__; import
    # lazily to avoid a circular import at module load time.
    from clauditor.cli import _positive_int

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
