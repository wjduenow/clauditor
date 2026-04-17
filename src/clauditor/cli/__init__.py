"""CLI entry point for clauditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clauditor import history
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.paths import resolve_clauditor_dir
from clauditor.runner import SkillResult
from clauditor.spec import SkillSpec
from clauditor.suggest import (
    NoPriorGradeError,
    load_suggest_input,
    propose_edits,
    render_unified_diff,
    write_sidecar,
)
from clauditor.workspace import InvalidSkillNameError, validate_skill_name


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


def _load_spec_or_report(
    skill_path: str, eval_path: str | None
) -> SkillSpec | None:
    """Load a :class:`SkillSpec`, printing an actionable error if missing.

    Returns the loaded spec on success. On ``FileNotFoundError`` (the
    skill ``.md`` is missing), prints an ``ERROR:`` line to stderr that
    names the path AND suggests ``clauditor init`` as the next step,
    then returns ``None``. Callers map ``None`` to exit code 2 (input
    error, per DEC-008) rather than letting the traceback escape.
    """
    try:
        return SkillSpec.from_file(skill_path, eval_path=eval_path)
    except FileNotFoundError:
        print(
            f"ERROR: Skill file not found: {skill_path}. "
            f"Run 'clauditor init {skill_path}' to create one.",
            file=sys.stderr,
        )
        return None


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
    from clauditor import transcripts

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
    from clauditor import transcripts

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


# Per-command modules. Each module exposes ``add_parser(subparsers)`` and a
# ``cmd_<name>(args) -> int`` handler. ``main()`` registers the subparsers
# and dispatches to the handlers. Re-exporting the ``cmd_<name>`` symbols
# keeps existing test imports (``from clauditor.cli import cmd_run``) working.
# These imports live here — after all shared helpers are defined — so that
# per-command modules that lazily import shared helpers from ``clauditor.cli``
# see them already defined when their ``cmd_<name>`` function runs.
from clauditor.cli import capture as capture_mod  # noqa: E402
from clauditor.cli import doctor as doctor_mod  # noqa: E402
from clauditor.cli import grade as grade_mod  # noqa: E402
from clauditor.cli import init as init_mod  # noqa: E402
from clauditor.cli import run as run_mod  # noqa: E402
from clauditor.cli import trend as trend_mod  # noqa: E402
from clauditor.cli import validate as validate_mod  # noqa: E402
from clauditor.cli.capture import cmd_capture  # noqa: E402,F401
from clauditor.cli.doctor import cmd_doctor  # noqa: E402,F401
from clauditor.cli.grade import (  # noqa: E402,F401
    _run_baseline_phase,
    cmd_grade,
)
from clauditor.cli.init import cmd_init  # noqa: E402,F401
from clauditor.cli.run import cmd_run  # noqa: E402,F401
from clauditor.cli.trend import cmd_trend  # noqa: E402,F401
from clauditor.cli.validate import cmd_validate  # noqa: E402,F401


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clauditor",
        description="Auditor for Claude Code skills and slash commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    validate_mod.add_parser(subparsers)

    # run
    run_mod.add_parser(subparsers)

    # grade
    grade_mod.add_parser(subparsers)

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
    init_mod.add_parser(subparsers)

    # capture
    capture_mod.add_parser(subparsers)

    # trend
    trend_mod.add_parser(subparsers)

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
    doctor_mod.add_parser(subparsers)

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
