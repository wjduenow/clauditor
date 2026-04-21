"""``clauditor lint`` — check a SKILL.md file against the agentskills.io spec.

Plain-text output (US-003) plus the ``--strict`` flag (US-004, DEC-004).
The ``--json`` flag (US-005) is deferred to a separate bead; this
module's argparse surface does not register it yet.

Exit-code taxonomy (non-LLM 0/1/2 per
``.claude/rules/llm-cli-exit-code-taxonomy.md``):

- **0** — either (a) no conformance issues returned by
  ``check_conformance``, or (b) issues are all warnings AND
  ``--strict`` is NOT set (warnings-are-soft default).
- **1** — load/parse failure: path does not resolve to a regular file,
  file is unreadable (OSError / UnicodeDecodeError), OR the only issue
  is ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` (malformed frontmatter
  is a parse problem, not spec drift). Preserved verbatim under
  ``--strict`` — parse failures are never escalated by that flag.
- **2** — any error-severity issue, OR any warning when ``--strict``
  is set.

The ``--strict`` flag (DEC-004) promotes warnings to exit 2. It does
NOT change the stderr rendering of issues and does NOT override the
INVALID_YAML → exit 1 special case.

Traces to DEC-002, DEC-004, DEC-010, DEC-011, DEC-014 of
``plans/super/71-agentskills-lint.md``. The pure-compute layer lives in
``src/clauditor/conformance.py`` per
``.claude/rules/pure-compute-vs-io-split.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.conformance import ConformanceIssue, check_conformance

# Code that always routes to exit 1 regardless of ``--strict`` —
# malformed-frontmatter is a parse failure, not a conformance concern.
_INVALID_YAML_CODE = "AGENTSKILLS_FRONTMATTER_INVALID_YAML"


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``lint`` subparser."""
    p_lint = subparsers.add_parser(
        "lint",
        help="Check SKILL.md against the agentskills.io specification",
    )
    p_lint.add_argument("skill_md", help="Path to SKILL.md file")
    p_lint.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat warnings as failures (exit 2). Errors always exit 2 "
            "regardless."
        ),
    )


def _render_issue(issue: ConformanceIssue) -> str:
    """Format one issue as a stderr line (DEC-014)."""
    return f"clauditor.conformance: {issue.code}: {issue.message}"


def _has_error(issues: list[ConformanceIssue]) -> bool:
    """Return ``True`` if any issue has ``severity == "error"``."""
    return any(issue.severity == "error" for issue in issues)


def cmd_lint(args: argparse.Namespace) -> int:
    """Lint a SKILL.md file against the agentskills.io specification.

    See module docstring for the full exit-code contract. Path resolution
    follows DEC-010 (``Path.resolve()`` + ``is_file()``; accepts absolute
    paths, follows symlinks, rejects directories). ``--strict`` (DEC-004)
    promotes warning-only results to exit 2 without altering rendering.
    """
    # Path validation (DEC-010). Accept absolute paths; follow symlinks
    # to their real target; reject directories, sockets, FIFOs, and
    # missing paths.
    skill_path = Path(args.skill_md).resolve()
    if not skill_path.is_file():
        print(
            f"ERROR: {args.skill_md} is not a regular file",
            file=sys.stderr,
        )
        return 1

    # File read — tolerate OSError (permission, IO) and
    # UnicodeDecodeError (non-UTF-8 bytes) as exit-1 parse failures.
    try:
        skill_md_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"ERROR: cannot read {args.skill_md}: {exc}",
            file=sys.stderr,
        )
        return 1

    issues = check_conformance(skill_md_text, skill_path)

    # Pass — no issues at all.
    if not issues:
        print(f"Conformance check passed: {skill_path}")
        return 0

    # Render every issue to stderr in order (DEC-014). Rendering is
    # independent of ``--strict`` — the flag only influences exit code.
    for issue in issues:
        print(_render_issue(issue), file=sys.stderr)

    # Exit-1 case: the ONLY issue is malformed frontmatter (parse failure,
    # not conformance). Preserve verbatim under ``--strict`` — parse
    # failures are never escalated by that flag.
    if len(issues) == 1 and issues[0].code == _INVALID_YAML_CODE:
        print("Cannot lint: SKILL.md frontmatter is not valid YAML")
        return 1

    # Hard-fail when any error-severity issue is present, OR when
    # ``--strict`` escalates warning-only results.
    if _has_error(issues) or args.strict:
        print(f"Conformance check failed: {len(issues)} issue(s) — see above")
        return 2

    # Warning-only result without ``--strict`` (DEC-004): render the
    # stderr lines above, but return exit 0 with a success line that
    # advertises the warning count so the caller is not misled into
    # thinking stderr output was a hard failure.
    warning_count = len(issues)
    suffix = "" if warning_count == 1 else "s"
    print(
        f"Conformance check passed: {skill_path} "
        f"({warning_count} warning{suffix} — see above)"
    )
    return 0
