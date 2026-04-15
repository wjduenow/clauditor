"""LLM-driven skill improvement proposer (`clauditor suggest`).

US-001 scope: latest-run loader and the :class:`SuggestInput` dataclass
that bundles the signals downstream stories will feed into Sonnet. The
later stories add prompt building (US-002), Sonnet call + parse + anchor
validation (US-003), unified diff + sidecar persistence (US-004), and
the CLI wiring (US-005).

This module is async-bound (US-003 will introduce
:func:`propose_edits`), so per
``.claude/rules/monotonic-time-indirection.md`` it captures
``time.monotonic`` behind a module-level alias right at import. Tests can
patch ``clauditor.suggest._monotonic`` without colliding with the asyncio
event loop's own scheduler.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from clauditor.assertions import AssertionResult
from clauditor.quality_grader import GradingReport, GradingResult

# Module-level alias so tests can patch this without clobbering the
# asyncio event loop's own time.monotonic() calls. See
# .claude/rules/monotonic-time-indirection.md for the canonical pattern.
_monotonic = time.monotonic


_ITERATION_RE = re.compile(r"^iteration-(\d+)$")


class NoPriorGradeError(RuntimeError):
    """Raised when no iteration directory contains ``<skill>/grading.json``.

    Maps to DEC-008 row 1 (exit code 1) at the CLI layer in US-005.
    """


@dataclass
class SuggestInput:
    """Bundle of signals the proposer feeds to Sonnet for one skill.

    Construction is the responsibility of :func:`load_suggest_input`; the
    CLI layer (US-005) wires user flags through to the loader and then
    hands the populated :class:`SuggestInput` to the prompt builder
    (US-002) and Sonnet call (US-003).

    All "failing" lists are pre-filtered: callers can rely on
    ``len(failing_assertions) == 0 and len(failing_grading_criteria) == 0``
    to short-circuit the Sonnet call (DEC-008 row 2).
    """

    skill_name: str
    source_iteration: int
    source_grading_path: str
    skill_md_text: str
    failing_assertions: list[AssertionResult] = field(default_factory=list)
    failing_grading_criteria: list[GradingResult] = field(default_factory=list)
    output_slices: list[str] = field(default_factory=list)
    transcript_events: list[list[dict]] | None = None


def find_latest_grading(
    clauditor_dir: Path,
    skill: str,
    from_iteration: int | None = None,
) -> tuple[int, Path]:
    """Locate the iteration whose ``<skill>/grading.json`` we should load.

    Mirrors the pattern in :func:`clauditor.cli._find_prior_grading_json`
    but with two differences specific to ``suggest``:

    * No "strictly less than current" filter — ``suggest`` is a read-only
      consumer that wants the **latest** grade run, not a prior one.
    * Optional ``from_iteration`` override pins the search to a specific
      iteration (DEC-007 ``--from-iteration``).

    Raises :class:`NoPriorGradeError` if the requested iteration is
    missing ``grading.json`` or no iteration in the workspace has one.
    """
    if from_iteration is not None:
        skill_dir = clauditor_dir / f"iteration-{from_iteration}" / skill
        if not (skill_dir / "grading.json").exists():
            raise NoPriorGradeError(
                f"iteration-{from_iteration} has no grading.json for "
                f"skill {skill!r} under {clauditor_dir}"
            )
        return from_iteration, skill_dir

    if not clauditor_dir.exists():
        raise NoPriorGradeError(
            f"no clauditor workspace at {clauditor_dir} — "
            f"run `clauditor grade {skill}` first"
        )

    candidates: list[int] = []
    for child in clauditor_dir.iterdir():
        if not child.is_dir():
            continue
        m = _ITERATION_RE.match(child.name)
        if m is None:
            continue
        idx = int(m.group(1))
        if (child / skill / "grading.json").exists():
            candidates.append(idx)

    if not candidates:
        raise NoPriorGradeError(
            f"no iteration under {clauditor_dir} contains "
            f"{skill}/grading.json — run `clauditor grade {skill}` first"
        )

    best = max(candidates)
    return best, clauditor_dir / f"iteration-{best}" / skill


def _load_failing_assertions(skill_dir: Path) -> list[AssertionResult]:
    """Read ``assertions.json`` and return only the failing entries.

    Returns an empty list if the file does not exist (a grade run with
    only L3 criteria is valid).
    """
    path = skill_dir / "assertions.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    failing: list[AssertionResult] = []
    for entry in data.get("results", []):
        if entry.get("passed"):
            continue
        failing.append(AssertionResult.from_json_dict(entry))
    return failing


def _load_failing_grading_criteria(skill_dir: Path) -> list[GradingResult]:
    """Read ``grading.json`` and return only the failing criteria.

    ``GradingResult.passed`` is the source of truth — the boolean is
    persisted alongside the score by :meth:`GradingReport.to_json` and
    reflects the per-criterion verdict the judge already rendered.
    """
    path = skill_dir / "grading.json"
    if not path.exists():
        return []
    report = GradingReport.from_json(path.read_text())
    return [r for r in report.results if not r.passed]


def _load_output_slices(skill_dir: Path) -> list[str]:
    """Read every ``run-K/output.txt`` under ``skill_dir`` in K order."""
    run_re = re.compile(r"^run-(\d+)$")
    runs: list[tuple[int, Path]] = []
    if not skill_dir.exists():
        return []
    for child in skill_dir.iterdir():
        if not child.is_dir():
            continue
        m = run_re.match(child.name)
        if m is None:
            continue
        runs.append((int(m.group(1)), child))
    runs.sort(key=lambda pair: pair[0])
    slices: list[str] = []
    for _idx, run_dir in runs:
        out_txt = run_dir / "output.txt"
        if out_txt.exists():
            slices.append(out_txt.read_text())
    return slices


def _load_transcript_events(skill_dir: Path) -> list[list[dict]]:
    """Read every ``run-K/output.jsonl`` defensively, one list per run.

    Per ``.claude/rules/stream-json-schema.md``, malformed lines are
    skipped with a stderr warning rather than aborting the load. This
    keeps a single corrupt transcript from blocking the whole suggest
    run.
    """
    run_re = re.compile(r"^run-(\d+)$")
    runs: list[tuple[int, Path]] = []
    if not skill_dir.exists():
        return []
    for child in skill_dir.iterdir():
        if not child.is_dir():
            continue
        m = run_re.match(child.name)
        if m is None:
            continue
        runs.append((int(m.group(1)), child))
    runs.sort(key=lambda pair: pair[0])

    all_events: list[list[dict]] = []
    for _idx, run_dir in runs:
        jsonl = run_dir / "output.jsonl"
        if not jsonl.exists():
            all_events.append([])
            continue
        events: list[dict] = []
        for raw_line in jsonl.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"clauditor.suggest: skipping malformed transcript "
                    f"line in {jsonl}: {exc}",
                    file=sys.stderr,
                )
                continue
            if not isinstance(msg, dict):
                continue
            events.append(msg)
        all_events.append(events)
    return all_events


def _repo_relative(clauditor_dir: Path, path: Path) -> str:
    """Render ``path`` relative to the repo root (``clauditor_dir.parent``).

    Mirrors :func:`clauditor.cli._relative_to_repo`. Falls back to the
    absolute path if the two are unrelated (defensive — should not
    happen in practice, but better than raising on weird layouts).
    """
    repo_root = clauditor_dir.parent
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def load_suggest_input(
    skill: str,
    clauditor_dir: Path,
    *,
    skill_md_path: Path,
    with_transcripts: bool = False,
    from_iteration: int | None = None,
) -> SuggestInput:
    """Compose a :class:`SuggestInput` from the latest grade run on disk.

    The CLI layer (US-005) is responsible for resolving ``skill_md_path``
    from the user-facing skill name and for short-circuiting on the
    DEC-008 row 2 "no failing signals" path. This loader returns a
    valid (possibly empty) :class:`SuggestInput` either way.
    """
    iteration, skill_dir = find_latest_grading(
        clauditor_dir, skill, from_iteration=from_iteration
    )
    grading_path = skill_dir / "grading.json"

    failing_assertions = _load_failing_assertions(skill_dir)
    failing_grading_criteria = _load_failing_grading_criteria(skill_dir)
    output_slices = _load_output_slices(skill_dir)
    transcripts = (
        _load_transcript_events(skill_dir) if with_transcripts else None
    )

    return SuggestInput(
        skill_name=skill,
        source_iteration=iteration,
        source_grading_path=_repo_relative(clauditor_dir, grading_path),
        skill_md_text=skill_md_path.read_text(),
        failing_assertions=failing_assertions,
        failing_grading_criteria=failing_grading_criteria,
        output_slices=output_slices,
        transcript_events=transcripts,
    )
