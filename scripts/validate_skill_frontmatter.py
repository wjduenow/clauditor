#!/usr/bin/env python3
"""Validate a bundled skill's SKILL.md frontmatter against agentskills.io core.

Fallback for the optional ``skills-ref validate`` CI step (DEC-006 of
``plans/super/43-setup-slash-command.md``). The upstream ``skills-ref``
validator rejects Claude Code extension fields (``argument-hint``,
``disable-model-invocation``) that DEC-004 explicitly mandates for our
hybrid frontmatter, so CI uses this script to enforce only the
spec-mandated core invariants:

- ``name``: present, non-empty string, matches ``^[a-z0-9]+(-[a-z0-9]+)*$``,
  length 1-64 chars, equal to the parent directory name.
- ``description``: present, non-empty string, length <=1024 chars.
- Frontmatter delimiters (``---``) present at top of file.

Usage::

    python scripts/validate_skill_frontmatter.py <skill-dir>

Exits 0 on success, 1 on any violation. All violations found are reported
before exiting (the script does not bail on the first one) so CI logs show
every problem in a single pass.

Zero third-party dependencies — uses only the Python standard library so
it runs without ``uv sync`` / ``pip install`` in CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_LEN = 64
DESCRIPTION_MAX_LEN = 1024


def _extract_frontmatter(text: str) -> tuple[str | None, str | None]:
    """Split a SKILL.md body into (frontmatter_block, error).

    Returns ``(block, None)`` on success, ``(None, reason)`` on failure.
    The frontmatter is the YAML region bounded by ``---`` delimiters at
    the very start of the file. A missing opening delimiter or a missing
    closing delimiter is an error.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "missing opening frontmatter delimiter '---' on line 1"
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx]), None
    return None, "missing closing frontmatter delimiter '---'"


def _parse_top_level_string(block: str, key: str) -> str | None:
    """Extract a top-level ``key: value`` string from a YAML-ish block.

    Handles optional single/double quoting and strips trailing comments.
    Returns ``None`` if the key is absent. This is intentionally a tiny
    parser, not a full YAML engine — the bundled skill's frontmatter is a
    fixed shape (a handful of scalar strings, one nested mapping, one
    list).
    """
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.*?)\s*$")
    for raw in block.splitlines():
        # Skip nested entries (indented lines belong to a parent mapping).
        if raw.startswith((" ", "\t")):
            continue
        m = pattern.match(raw)
        if not m:
            continue
        value = m.group(1)
        # Strip surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def validate_skill(skill_dir: Path) -> list[str]:
    """Validate a skill directory and return a list of human-readable errors.

    An empty list means the skill passes. The function reports every
    violation it can detect — it does not stop at the first one — so
    callers can surface all problems in a single CI run.
    """
    errors: list[str] = []

    if not skill_dir.is_dir():
        return [f"{skill_dir}: not a directory"]

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{skill_md}: SKILL.md not found"]

    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{skill_md}: unreadable ({exc})"]

    block, fm_error = _extract_frontmatter(text)
    if fm_error is not None or block is None:
        return [f"{skill_md}: {fm_error}"]

    # name: present, non-empty, regex, length, matches parent dir.
    name = _parse_top_level_string(block, "name")
    if name is None:
        errors.append(f"{skill_md}: 'name' field missing from frontmatter")
    elif name == "":
        errors.append(f"{skill_md}: 'name' must be a non-empty string")
    else:
        if not NAME_REGEX.match(name):
            errors.append(
                f"{skill_md}: 'name'={name!r} does not match "
                f"^[a-z0-9]+(-[a-z0-9]+)*$ (lowercase a-z/0-9 + hyphens)"
            )
        if len(name) > NAME_MAX_LEN:
            errors.append(
                f"{skill_md}: 'name' length {len(name)} > {NAME_MAX_LEN} chars"
            )
        parent_name = skill_dir.name
        if name != parent_name:
            errors.append(
                f"{skill_md}: 'name'={name!r} does not match "
                f"parent directory name {parent_name!r}"
            )

    # description: present, non-empty, length <=1024.
    description = _parse_top_level_string(block, "description")
    if description is None:
        errors.append(f"{skill_md}: 'description' field missing from frontmatter")
    elif description == "":
        errors.append(f"{skill_md}: 'description' must be a non-empty string")
    elif len(description) > DESCRIPTION_MAX_LEN:
        errors.append(
            f"{skill_md}: 'description' length {len(description)} > "
            f"{DESCRIPTION_MAX_LEN} chars"
        )

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            f"usage: {Path(argv[0]).name if argv else 'validate_skill_frontmatter.py'} "
            "<skill-dir>",
            file=sys.stderr,
        )
        return 2
    skill_dir = Path(argv[1])
    errors = validate_skill(skill_dir)
    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"{skill_dir}: frontmatter OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
