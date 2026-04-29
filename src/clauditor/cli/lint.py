"""``clauditor lint`` — check a SKILL.md file against the agentskills.io spec.

Plain-text output (US-003), the ``--strict`` flag (US-004, DEC-004),
and the ``--json`` flag (US-005, DEC-012).

Exit-code taxonomy (non-LLM 0/1/2 per
``.claude/rules/llm-cli-exit-code-taxonomy.md``):

- **0** — either (a) no conformance issues returned by
  ``check_conformance``, or (b) issues are all warnings AND
  ``--strict`` is NOT set (warnings-are-soft default).
- **1** — load/parse failure: path does not resolve to a regular file,
  file is unreadable (OSError / UnicodeDecodeError), OR
  ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` is present among the issues
  (malformed frontmatter is a parse problem, not spec drift; this case
  dominates sibling pre-parse warnings like
  ``AGENTSKILLS_LAYOUT_LEGACY``). Preserved verbatim under
  ``--strict`` — parse failures are never escalated by that flag.
- **2** — any error-severity issue, OR any warning when ``--strict``
  is set.

The ``--strict`` flag (DEC-004) promotes warnings to exit 2. It does
NOT change the stderr rendering of issues and does NOT override the
INVALID_YAML → exit 1 special case.

The ``--json`` flag (DEC-012) emits a single JSON envelope to stdout
instead of the human-readable stderr rendering. ``schema_version: 1``
is the FIRST key in the payload per
``.claude/rules/json-schema-version.md``. Exit codes are identical to
the human path. Path-level errors (not-a-file, unreadable) surface as
synthetic ``PATH_*`` entries in the same ``issues[]`` list so JSON
consumers have a single surface to read.

Traces to DEC-002, DEC-004, DEC-010, DEC-011, DEC-012, DEC-014 of
``plans/super/71-agentskills-lint.md``. The pure-compute layer lives in
``src/clauditor/conformance.py`` per
``.claude/rules/pure-compute-vs-io-split.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from clauditor.conformance import (
    ConformanceIssue,
    check_conformance,
    format_issue_line,
)

# Code that always routes to exit 1 regardless of ``--strict`` —
# malformed-frontmatter is a parse failure, not a conformance concern.
_INVALID_YAML_CODE = "AGENTSKILLS_FRONTMATTER_INVALID_YAML"

# Synthetic lint-side path-error codes (NOT prefixed with
# ``AGENTSKILLS_`` — these are not spec violations). Surfaced in the
# JSON envelope's ``issues[]`` list so consumers read one uniform
# structure. Human-mode callers see the existing stderr ``ERROR:``
# line instead.
_PATH_NOT_A_FILE_CODE = "PATH_NOT_A_FILE"
_PATH_UNREADABLE_CODE = "PATH_UNREADABLE"


def _sanitize_message_text(value: str) -> str:
    """Replace newline characters with visible escape sequences.

    User-controlled strings (``args.skill_md`` from argv, ``OSError.__str__``
    from a failed ``read_text``) can contain ``\n`` or ``\r`` — POSIX
    allows newlines in filenames, and plugin filesystems sometimes raise
    multi-line OSError messages. Those characters, if interpolated verbatim
    into a :class:`ConformanceIssue` message, violate the ``__post_init__``
    single-line invariant (DEC-014) and crash the ``--json`` path before
    it can emit its envelope. Replace with the literal escape sequences
    ``\\n`` / ``\\r`` so the synthetic path-error issues render on one
    line and stay grep-friendly for the human-text path.
    """
    return value.replace("\r", "\\r").replace("\n", "\\n")


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
    p_lint.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit a JSON envelope to stdout instead of human-readable "
            "text. Exit codes unchanged."
        ),
    )


def _has_error(issues: list[ConformanceIssue]) -> bool:
    """Return ``True`` if any issue has ``severity == "error"``."""
    return any(issue.severity == "error" for issue in issues)


def _compute_exit_code(
    issues: list[ConformanceIssue], *, strict: bool
) -> int:
    """Map a conformance-issues list + ``--strict`` to the exit code.

    Shared by both the human-text and JSON rendering paths so the two
    branches cannot drift. See module docstring for the full taxonomy.
    """
    if not issues:
        return 0
    # INVALID_YAML dominates: sibling pre-parse issues (e.g.
    # ``AGENTSKILLS_LAYOUT_LEGACY``, which is appended before the
    # frontmatter parse in ``check_conformance``) may accompany it, but
    # the caller cannot act on them until the YAML parses. Route the
    # whole run to exit 1 even under ``--strict`` — parse failures are
    # never escalated by that flag.
    if any(issue.code == _INVALID_YAML_CODE for issue in issues):
        return 1
    if _has_error(issues) or strict:
        return 2
    return 0


def _emit_json_envelope(
    skill_path_str: str,
    issues: list[ConformanceIssue],
    *,
    exit_code: int,
) -> None:
    """Print the ``--json`` envelope to stdout (DEC-012).

    ``schema_version: 1`` is inserted as the first key via an explicit
    ordered dict construction — Python 3.7+ preserves dict insertion
    order, so ``json.dumps`` writes keys in the order they were added.
    Verified structurally by
    ``tests/test_cli_lint.py::TestJsonOutput::test_json_schema_version_first_key``
    so this property cannot silently regress.
    """
    payload = {
        "schema_version": 1,
        "skill_path": skill_path_str,
        "passed": exit_code == 0,
        "issues": [asdict(issue) for issue in issues],
    }
    print(json.dumps(payload, indent=2))


def cmd_lint(args: argparse.Namespace) -> int:
    """Lint a SKILL.md file against the agentskills.io specification.

    See module docstring for the full exit-code contract. Path resolution
    follows DEC-010 (``Path.resolve()`` + ``is_file()``; accepts absolute
    paths, follows symlinks, rejects directories). ``--strict`` (DEC-004)
    promotes warning-only results to exit 2 without altering rendering.
    ``--json`` (DEC-012) replaces the human-text rendering with a single
    JSON envelope on stdout; exit codes and ``--strict`` interaction are
    unchanged.
    """
    # Path validation (DEC-010). Accept absolute paths; follow symlinks
    # to their real target; reject directories, sockets, FIFOs, and
    # missing paths.
    #
    # ``Path.resolve()`` raises ``ValueError`` on paths containing
    # embedded null bytes (``'\x00'``) and may raise ``TypeError`` on
    # other malformed inputs. Treat those as "not a regular file" and
    # fall through to the existing not-a-file handling so malformed
    # paths produce the same clean error flow.
    try:
        skill_path = Path(args.skill_md).resolve()
        is_file = skill_path.is_file()
    except (ValueError, TypeError):
        skill_path = Path(args.skill_md)
        is_file = False
    if not is_file:
        if args.json:
            # Synthetic PATH_NOT_A_FILE entry in the issues list so JSON
            # consumers have one uniform surface to read path errors
            # alongside conformance issues. The envelope's ``skill_path``
            # field is the RESOLVED path — consistent with the success /
            # unreadable / conformance branches so consumers can rely on
            # a normalized-path schema. The raw argv lives in the issue
            # ``message`` (where it is more informative for a human
            # reading the error than the resolved target would be).
            _emit_json_envelope(
                str(skill_path),
                [
                    ConformanceIssue(
                        code=_PATH_NOT_A_FILE_CODE,
                        severity="error",
                        message=(
                            f"{_sanitize_message_text(args.skill_md)} is "
                            f"not a regular file"
                        ),
                    )
                ],
                exit_code=1,
            )
            return 1
        print(
            f"ERROR: {_sanitize_message_text(args.skill_md)} is not a regular file",
            file=sys.stderr,
        )
        return 1

    # File read — tolerate OSError (permission, IO) and
    # UnicodeDecodeError (non-UTF-8 bytes) as exit-1 parse failures.
    try:
        skill_md_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if args.json:
            _emit_json_envelope(
                str(skill_path),
                [
                    ConformanceIssue(
                        code=_PATH_UNREADABLE_CODE,
                        severity="error",
                        message=(
                            f"cannot read "
                            f"{_sanitize_message_text(args.skill_md)}: "
                            f"{_sanitize_message_text(str(exc))}"
                        ),
                    )
                ],
                exit_code=1,
            )
            return 1
        print(
            f"ERROR: cannot read "
            f"{_sanitize_message_text(args.skill_md)}: "
            f"{_sanitize_message_text(str(exc))}",
            file=sys.stderr,
        )
        return 1

    issues = check_conformance(skill_md_text, skill_path)
    exit_code = _compute_exit_code(issues, strict=args.strict)

    # JSON path: single stdout write, empty stderr, identical exit
    # code to the human path.
    if args.json:
        _emit_json_envelope(str(skill_path), issues, exit_code=exit_code)
        return exit_code

    # Human path (US-003 + US-004): byte-identical to pre-US-005.

    # Pass — no issues at all.
    if not issues:
        print(f"Conformance check passed: {skill_path}")
        return 0

    # Render every issue to stderr in order (DEC-014). Rendering is
    # independent of ``--strict`` — the flag only influences exit code.
    for issue in issues:
        print(format_issue_line(issue), file=sys.stderr)

    # Branch on the precomputed exit code so the human path inherits
    # the taxonomy from ``_compute_exit_code`` rather than re-deriving
    # it via a parallel chain of ``_INVALID_YAML_CODE`` / ``_has_error``
    # / ``args.strict`` checks (which would silently drift if the
    # taxonomy ever changed). The three cases are exhaustive:
    #   exit_code == 1 — INVALID_YAML parse failure (DEC-004 special).
    #   exit_code == 2 — error severity OR ``--strict``-escalated warning.
    #   exit_code == 0 — warning-only without ``--strict`` (soft-pass
    #                    with a count suffix so the caller notices the
    #                    stderr output).
    if exit_code == 1:
        print("Cannot lint: SKILL.md frontmatter is not valid YAML")
        return 1
    if exit_code == 2:
        print(f"Conformance check failed: {len(issues)} issue(s) — see above")
        return 2
    warning_count = len(issues)
    suffix = "" if warning_count == 1 else "s"
    print(
        f"Conformance check passed: {skill_path} "
        f"({warning_count} warning{suffix} — see above)"
    )
    return 0
