"""``clauditor lint`` — check a SKILL.md file against the agentskills.io spec.

Plain-text output (US-003). The `--strict` flag (US-004) and the
`--json` flag (US-005) are added by separate beads; this module's
argparse surface intentionally does not register them.

Exit-code taxonomy (non-LLM 0/1/2 per
``.claude/rules/llm-cli-exit-code-taxonomy.md``):

- **0** — no conformance issues returned by ``check_conformance``.
- **1** — load/parse failure: path does not resolve to a regular file,
  file is unreadable (OSError / UnicodeDecodeError), OR the only issue
  is ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` (malformed frontmatter
  is a parse problem, not spec drift).
- **2** — one or more conformance issues present (any severity),
  except the YAML-parse-only case above.

Traces to DEC-002, DEC-010, DEC-011, DEC-014 of
``plans/super/71-agentskills-lint.md``. The pure-compute layer lives in
``src/clauditor/conformance.py`` per
``.claude/rules/pure-compute-vs-io-split.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauditor.conformance import ConformanceIssue, check_conformance


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``lint`` subparser."""
    p_lint = subparsers.add_parser(
        "lint",
        help="Check SKILL.md against the agentskills.io specification",
    )
    p_lint.add_argument("skill_md", help="Path to SKILL.md file")


def _render_issue(issue: ConformanceIssue) -> str:
    """Format one issue as a stderr line (DEC-014)."""
    return f"clauditor.conformance: {issue.code}: {issue.message}"


def cmd_lint(args: argparse.Namespace) -> int:
    """Lint a SKILL.md file against the agentskills.io specification.

    See module docstring for the full exit-code contract. Path resolution
    follows DEC-010 (``Path.resolve()`` + ``is_file()``; accepts absolute
    paths, follows symlinks, rejects directories).
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

    # Render every issue to stderr in order (DEC-014).
    for issue in issues:
        print(_render_issue(issue), file=sys.stderr)

    # Exit-1 case: the ONLY issue is malformed frontmatter (parse failure,
    # not conformance). Any other mix routes to exit 2.
    invalid_yaml_code = "AGENTSKILLS_FRONTMATTER_INVALID_YAML"
    if len(issues) == 1 and issues[0].code == invalid_yaml_code:
        print("Cannot lint: SKILL.md frontmatter is not valid YAML")
        return 1

    # One or more conformance issues — exit 2 with stdout failure summary.
    print(f"Conformance check failed: {len(issues)} issue(s) — see above")
    return 2
