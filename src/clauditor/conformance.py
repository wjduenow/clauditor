"""agentskills.io specification conformance checker for SKILL.md files.

This module is the pure compute layer of the ``clauditor lint`` command
and the ``SkillSpec.from_file`` soft-warn hook. It accepts an already-
read SKILL.md text plus a ``Path`` (used only for layout classification
and parent-directory-match checks) and returns a ``list[ConformanceIssue]``
enumerating every rule violation against the agentskills.io
`specification <https://agentskills.io/specification>`_.

Pure-compute contract per
``.claude/rules/pure-compute-vs-io-split.md``:

- No file I/O (no ``open`` / ``read_text``).
- No stderr / stdout writes (``print``, ``sys.stderr``, ``sys.stdout``).
- No network, no LLM, no subprocess.
- ``check_conformance`` never raises — malformed frontmatter surfaces
  as ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` (error) inside the
  returned list.

The CLI layer (``src/clauditor/cli/lint.py``) and the soft-warn hook
(``src/clauditor/spec.py::SkillSpec.from_file``) own the I/O: path
validation, file read, stderr emission with the
``"clauditor.conformance: <CODE>: <message>"`` prefix (DEC-014), and
exit-code routing (DEC-002, DEC-004).

Public surface:

- :class:`ConformanceIssue` — ``(code, severity, message)`` triple.
- :func:`check_conformance` — pure entry point.
- :data:`AGENTSKILLS_NAME_RE` — strict-ASCII name regex (DEC-006).
- :data:`KNOWN_CLAUDE_CODE_EXTENSION_KEYS` — allowlist of Claude Code
  frontmatter extension keys (DEC-009, DEC-013).

Traces to DEC-001, DEC-005, DEC-006, DEC-007, DEC-009, DEC-014 of
``plans/super/71-agentskills-lint.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "AGENTSKILLS_NAME_RE",
    "KNOWN_CLAUDE_CODE_EXTENSION_KEYS",
    "ConformanceIssue",
    "check_conformance",
    "format_issue_line",
]


Severity = Literal["error", "warning"]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Strict-ASCII name regex per DEC-006. Two alternatives:
#  - single char ``[a-z0-9]``;
#  - multi-char ``[a-z0-9]`` + ``([a-z0-9] | -(?!-))*`` + ``[a-z0-9]``.
# The ``-(?!-)`` negative lookahead forbids consecutive hyphens. Overall
# length 1-64 is enforced as a separate check (the regex alone does not
# bound length). Tie-breaks the agentskills.io spec's self-contradictory
# "unicode lowercase" phrase in favor of the ASCII range used in every
# published example.
AGENTSKILLS_NAME_RE: re.Pattern[str] = re.compile(
    r"^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$|^[a-z0-9]$"
)

# Allowlist per DEC-009: frontmatter keys that Claude Code understands
# as slash-command extensions but that agentskills.io does not define.
# Keys in this set are NOT reported as
# ``AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY``. Maintained against Claude
# Code's published frontmatter docs by the ``/review-agentskills-spec``
# bundled skill (DEC-013).
KNOWN_CLAUDE_CODE_EXTENSION_KEYS: frozenset[str] = frozenset(
    {
        "argument-hint",
        "disable-model-invocation",
    }
)

# Canonical agentskills.io frontmatter keys (2026-04 spec fetch).
_SPEC_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)

_NAME_MAX_LEN = 64
_DESCRIPTION_MAX_LEN = 1024
_COMPATIBILITY_MAX_LEN = 500
_BODY_MAX_LINES = 500


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConformanceIssue:
    """A single rule violation or advisory from :func:`check_conformance`.

    - ``code`` — stable identifier (``AGENTSKILLS_*``); consumers may
      branch on this string.
    - ``severity`` — ``"error"`` or ``"warning"`` per the plan's
      Discovery section. The CLI layer maps these to exit codes
      (DEC-002, DEC-004).
    - ``message`` — human-readable **single-line** explanation. Does NOT
      include the ``"clauditor.conformance: "`` prefix — callers (CLI,
      soft-warn hook) add that when rendering to stderr per DEC-014.
      Newlines are rejected at construction time so line-oriented
      consumers (`grep "clauditor.conformance:" stderr`) cannot miss
      continuation lines that would otherwise drop the prefix.
    """

    code: str
    severity: Severity
    message: str

    def __post_init__(self) -> None:
        # DEC-014 contract: one issue == one stderr line. Reject
        # multi-line messages at construction time so future rule
        # authors cannot silently break line-oriented consumers.
        if "\n" in self.message or "\r" in self.message:
            raise ValueError(
                f"ConformanceIssue.message must be single-line "
                f"(code={self.code!r}): {self.message!r}"
            )


# ---------------------------------------------------------------------------
# Render helper (DEC-014)
# ---------------------------------------------------------------------------


def format_issue_line(issue: ConformanceIssue) -> str:
    """Format one issue as a ``clauditor.conformance:``-prefixed stderr line.

    Single authoritative formatter per DEC-014. Both CLI (``cli/lint.py``)
    and the ``SkillSpec.from_file`` soft-warn hook must call this helper
    — do NOT reimplement the prefix in either caller. Changes to the
    prefix (operator-visible) must land here.
    """
    return f"clauditor.conformance: {issue.code}: {issue.message}"


# ---------------------------------------------------------------------------
# Pure entry point
# ---------------------------------------------------------------------------


def check_conformance(
    skill_md_text: str, skill_path: Path
) -> list[ConformanceIssue]:
    """Return a list of conformance issues for a SKILL.md artifact.

    ``skill_md_text`` is the already-read Markdown text (the caller
    owns file I/O). ``skill_path`` is used only for layout
    classification and parent-directory-match checks; no filesystem
    access is performed.

    Empty list means the skill conforms. See module docstring for
    purity invariants. Never raises — parse failures surface as
    ``AGENTSKILLS_FRONTMATTER_INVALID_YAML``.
    """
    # Local import keeps this module's top-level clean of clauditor
    # imports beyond the leaf ``_frontmatter`` helper. Matches the
    # pattern used by ``paths.derive_skill_name``.
    from clauditor._frontmatter import parse_frontmatter

    issues: list[ConformanceIssue] = []

    # Layout classification first — a legacy-layout warning applies
    # regardless of frontmatter validity. Ordering is load-bearing:
    # ``AGENTSKILLS_LAYOUT_LEGACY`` can coexist with a downstream
    # ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` issue. The CLI's
    # ``_compute_exit_code`` in ``cli/lint.py`` uses ``any(... == INVALID_YAML)``
    # (not ``len == 1``) to route the whole run to exit 1 when YAML is
    # malformed; do NOT re-order this append without updating that logic.
    is_modern_layout = skill_path.name == "SKILL.md"
    if not is_modern_layout:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_LAYOUT_LEGACY",
                severity="warning",
                message=(
                    f"Legacy single-file skill layout `{skill_path.name}` is "
                    f"not in the agentskills.io specification, which "
                    f"requires a `<skill-name>/SKILL.md` directory layout. "
                    f"To migrate: `mkdir {skill_path.stem}/ && mv "
                    f"{skill_path.name} {skill_path.stem}/SKILL.md`. See "
                    f"https://agentskills.io/specification#directory-structure."
                ),
            )
        )

    try:
        parsed, body = parse_frontmatter(skill_md_text)
    except ValueError as exc:
        # Sanitize: YAML parser messages may include embedded newlines
        # (caret-indicator diagnostics, multi-line context). The
        # ``ConformanceIssue.__post_init__`` single-line invariant
        # rejects raw ``\n`` / ``\r`` and would break
        # ``check_conformance``'s "never raises" contract.
        exc_str = str(exc).replace("\r", "\\r").replace("\n", "\\n")
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_FRONTMATTER_INVALID_YAML",
                severity="error",
                message=(
                    f"Frontmatter YAML is malformed and could not be "
                    f"parsed: {exc_str}"
                ),
            )
        )
        return issues

    if parsed is None:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_FRONTMATTER_MISSING",
                severity="error",
                message=(
                    "Frontmatter block is missing; the agentskills.io "
                    "specification requires a `---`-delimited YAML "
                    "frontmatter block at the top of SKILL.md."
                ),
            )
        )
        # Body line-count still applies even without frontmatter.
        _check_body(body, issues)
        return issues

    _check_name(parsed, skill_path, is_modern_layout, issues)
    _check_description(parsed, issues)
    _check_license(parsed, issues)
    _check_compatibility(parsed, issues)
    _check_metadata(parsed, issues)
    _check_allowed_tools(parsed, issues)
    _check_unknown_keys(parsed, issues)
    _check_body(body, issues)

    return issues


# ---------------------------------------------------------------------------
# Per-field checkers (module-private)
# ---------------------------------------------------------------------------


def _check_name(
    parsed: dict,
    skill_path: Path,
    is_modern_layout: bool,
    issues: list[ConformanceIssue],
) -> None:
    """Validate the ``name`` frontmatter field.

    Order: MISSING → NOT_STRING → EMPTY → TOO_LONG → hyphen-specific
    readable codes (LEADING / TRAILING / CONSECUTIVE) → INVALID_CHARS
    (fallback for anything else the regex rejects) → PARENT_DIR_MISMATCH
    (modern layout only).

    The hyphen-specific codes fire BEFORE ``INVALID_CHARS`` so authors
    get an actionable diagnostic rather than the opaque "fails regex".
    They are load-bearing per the Discovery section's stable-id list.
    """
    if "name" not in parsed:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_MISSING",
                severity="error",
                message=(
                    "Required frontmatter field `name` is missing; the "
                    "agentskills.io specification requires a `name` "
                    "identifier."
                ),
            )
        )
        return

    value = parsed["name"]
    if not isinstance(value, str):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_NOT_STRING",
                severity="error",
                message=(
                    f"Frontmatter `name` must be a string; got "
                    f"{type(value).__name__}."
                ),
            )
        )
        return

    if value == "":
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_EMPTY",
                severity="error",
                message=(
                    "Frontmatter `name` is empty; must be a non-empty "
                    "identifier."
                ),
            )
        )
        return

    if len(value) > _NAME_MAX_LEN:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_TOO_LONG",
                severity="error",
                message=(
                    f"Frontmatter `name` is {len(value)} chars; must be "
                    f"at most {_NAME_MAX_LEN}."
                ),
            )
        )
        return

    # Hyphen-specific readable diagnostics. The if/elif chain enforces
    # mutual exclusion so the author sees ONE actionable hyphen-related
    # diagnostic rather than a cascade (LEADING_HYPHEN + INVALID_CHARS
    # for the same character would be noise). The parent-dir check
    # below runs independently — a hyphen issue and a parent-dir
    # mismatch CAN co-fire (different concerns).
    if value.startswith("-"):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_LEADING_HYPHEN",
                severity="error",
                message=(
                    f"Frontmatter `name` {value!r} starts with a hyphen; "
                    f"the agentskills.io name regex requires the first "
                    f"character to be `[a-z0-9]`."
                ),
            )
        )
    elif value.endswith("-"):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_TRAILING_HYPHEN",
                severity="error",
                message=(
                    f"Frontmatter `name` {value!r} ends with a hyphen; "
                    f"the agentskills.io name regex requires the last "
                    f"character to be `[a-z0-9]`."
                ),
            )
        )
    elif "--" in value:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_CONSECUTIVE_HYPHENS",
                severity="error",
                message=(
                    f"Frontmatter `name` {value!r} contains consecutive "
                    f"hyphens (`--`); the agentskills.io name regex "
                    f"forbids them."
                ),
            )
        )
    elif AGENTSKILLS_NAME_RE.fullmatch(value) is None:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_NAME_INVALID_CHARS",
                severity="error",
                message=(
                    f"Frontmatter `name` {value!r} contains characters "
                    f"outside the agentskills.io strict-ASCII set "
                    f"`[a-z0-9-]`."
                ),
            )
        )

    # Parent-dir match — modern layout only (DEC-005 qualifier).
    if is_modern_layout:
        parent_name = skill_path.parent.name
        if value != parent_name:
            issues.append(
                ConformanceIssue(
                    code="AGENTSKILLS_NAME_PARENT_DIR_MISMATCH",
                    severity="error",
                    message=(
                        f"Frontmatter `name: {value!r}` does not match "
                        f"parent directory `{parent_name!r}`; the "
                        f"agentskills.io specification requires these "
                        f"to match."
                    ),
                )
            )


def _check_description(
    parsed: dict, issues: list[ConformanceIssue]
) -> None:
    if "description" not in parsed:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_DESCRIPTION_MISSING",
                severity="error",
                message=(
                    "Required frontmatter field `description` is missing; "
                    "the agentskills.io specification requires a "
                    "`description` string."
                ),
            )
        )
        return

    value = parsed["description"]
    if not isinstance(value, str):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_DESCRIPTION_NOT_STRING",
                severity="error",
                message=(
                    f"Frontmatter `description` must be a string; got "
                    f"{type(value).__name__}."
                ),
            )
        )
        return

    if value == "":
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_DESCRIPTION_EMPTY",
                severity="error",
                message=(
                    "Frontmatter `description` is empty; must be a "
                    "non-empty string."
                ),
            )
        )
        return

    if len(value) > _DESCRIPTION_MAX_LEN:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_DESCRIPTION_TOO_LONG",
                severity="error",
                message=(
                    f"Frontmatter `description` is {len(value)} chars; "
                    f"must be at most {_DESCRIPTION_MAX_LEN}."
                ),
            )
        )


def _check_license(parsed: dict, issues: list[ConformanceIssue]) -> None:
    if "license" not in parsed:
        return

    value = parsed["license"]
    if not isinstance(value, str):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_LICENSE_NOT_STRING",
                severity="error",
                message=(
                    f"Frontmatter `license` must be a string; got "
                    f"{type(value).__name__}."
                ),
            )
        )
        return

    if value == "":
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_LICENSE_EMPTY",
                severity="error",
                message=(
                    "Frontmatter `license` is empty; omit the key or "
                    "provide a non-empty SPDX identifier."
                ),
            )
        )


def _check_compatibility(
    parsed: dict, issues: list[ConformanceIssue]
) -> None:
    if "compatibility" not in parsed:
        return

    value = parsed["compatibility"]
    if not isinstance(value, str):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_COMPATIBILITY_NOT_STRING",
                severity="error",
                message=(
                    f"Frontmatter `compatibility` must be a string; got "
                    f"{type(value).__name__}."
                ),
            )
        )
        return

    if value == "":
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_COMPATIBILITY_EMPTY",
                severity="error",
                message=(
                    "Frontmatter `compatibility` is empty; omit the key "
                    "or provide a non-empty description."
                ),
            )
        )
        return

    if len(value) > _COMPATIBILITY_MAX_LEN:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_COMPATIBILITY_TOO_LONG",
                severity="error",
                message=(
                    f"Frontmatter `compatibility` is {len(value)} chars; "
                    f"must be at most {_COMPATIBILITY_MAX_LEN}."
                ),
            )
        )


def _check_metadata(parsed: dict, issues: list[ConformanceIssue]) -> None:
    if "metadata" not in parsed:
        return

    value = parsed["metadata"]
    if not isinstance(value, dict):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_METADATA_NOT_MAP",
                severity="error",
                message=(
                    f"Frontmatter `metadata` must be a nested mapping; "
                    f"got {type(value).__name__}."
                ),
            )
        )
        return

    for key, val in value.items():
        if not isinstance(key, str):
            issues.append(
                ConformanceIssue(
                    code="AGENTSKILLS_METADATA_KEY_NOT_STRING",
                    severity="error",
                    message=(
                        f"Frontmatter `metadata` key {key!r} must be a "
                        f"string; got {type(key).__name__}."
                    ),
                )
            )
            continue
        if not isinstance(val, str):
            issues.append(
                ConformanceIssue(
                    code="AGENTSKILLS_METADATA_VALUE_NOT_STRING",
                    severity="error",
                    message=(
                        f"Frontmatter `metadata.{key}` value must be a "
                        f"string; got {type(val).__name__}."
                    ),
                )
            )


def _check_allowed_tools(
    parsed: dict, issues: list[ConformanceIssue]
) -> None:
    if "allowed-tools" not in parsed:
        return

    value = parsed["allowed-tools"]
    if not isinstance(value, str):
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_ALLOWED_TOOLS_NOT_STRING",
                severity="error",
                message=(
                    f"Frontmatter `allowed-tools` must be a string; got "
                    f"{type(value).__name__}."
                ),
            )
        )
        # Do not cascade the experimental warning when the field is
        # malformed — the author fixes the type first.
        return

    # Per the Discovery section, this warning ALWAYS fires whenever the
    # field is present (regardless of value shape), flagging the spec's
    # current "experimental" status for this field.
    issues.append(
        ConformanceIssue(
            code="AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL",
            severity="warning",
            message=(
                "Frontmatter `allowed-tools` is currently marked "
                "experimental by the agentskills.io specification; its "
                "grammar and semantics may change before stabilization."
            ),
        )
    )


def _check_unknown_keys(
    parsed: dict, issues: list[ConformanceIssue]
) -> None:
    for key in parsed:
        if key in _SPEC_FRONTMATTER_KEYS:
            continue
        if key in KNOWN_CLAUDE_CODE_EXTENSION_KEYS:
            continue
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY",
                severity="warning",
                message=(
                    f"Unknown frontmatter key `{key}`; the agentskills.io "
                    f"specification defines only: `name`, `description`, "
                    f"`license`, `compatibility`, `metadata`, "
                    f"`allowed-tools`. If this is an extension recognized "
                    f"by a specific agent host, consider opening an issue "
                    f"to add it to the clauditor allowlist."
                ),
            )
        )


def _check_body(body: str, issues: list[ConformanceIssue]) -> None:
    if body == "":
        return
    # splitlines avoids a trailing-newline off-by-one; an empty body
    # short-circuits above.
    line_count = len(body.splitlines())
    if line_count > _BODY_MAX_LINES:
        issues.append(
            ConformanceIssue(
                code="AGENTSKILLS_BODY_TOO_LONG",
                severity="warning",
                message=(
                    f"SKILL.md body is {line_count} lines; the "
                    f"agentskills.io specification recommends keeping "
                    f"the main SKILL.md under {_BODY_MAX_LINES} lines "
                    f"(see the Progressive Disclosure section)."
                ),
            )
        )
