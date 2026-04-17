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

The YAML-subset parser lives in :mod:`clauditor._frontmatter` so the
propose-eval and init CLIs can reuse it. The shared module is pure
stdlib so this script still runs without ``uv sync`` / ``pip install``
in CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Make ``clauditor._frontmatter`` importable when this script is run
# directly from the repo root (the documented usage, and the shape CI
# invokes). An editable install (``uv sync --dev``) also works, but the
# script must not require one.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
if _SRC_ROOT.is_dir() and str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from clauditor._frontmatter import parse_frontmatter  # noqa: E402

NAME_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_LEN = 64
DESCRIPTION_MAX_LEN = 1024


def _extract_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """Return ``(frontmatter_dict, error)``.

    ``error`` is ``None`` on success and a human-readable string on
    failure. Missing opening delimiter, missing closing delimiter, and
    malformed YAML-subset lines are all reported via the error slot so
    the caller can surface them in the same way.
    """
    # Explicit opening-delimiter check to preserve the legacy error
    # message shape CI logs + tests already anchor on.
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "missing opening frontmatter delimiter '---' on line 1"

    try:
        parsed, _body = parse_frontmatter(text)
    except ValueError as exc:
        return None, str(exc)
    return parsed, None


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

    parsed, fm_error = _extract_frontmatter(text)
    if fm_error is not None or parsed is None:
        return [f"{skill_md}: {fm_error}"]

    # name: present, non-empty, regex, length, matches parent dir.
    name = parsed.get("name")
    if name is None:
        errors.append(f"{skill_md}: 'name' field missing from frontmatter")
    elif not isinstance(name, str) or name == "":
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
    description = parsed.get("description")
    if description is None:
        errors.append(f"{skill_md}: 'description' field missing from frontmatter")
    elif not isinstance(description, str) or description == "":
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
