"""Repo-root detection and skill-identity helpers for clauditor.

Provides :func:`resolve_clauditor_dir` which walks up from the current
working directory looking for a ``.git/`` or ``.claude/`` marker and
returns ``<repo_root>/.clauditor``. If no marker is found, falls back to
``Path.cwd() / ".clauditor"`` and emits a single-line warning to stderr.

Also hosts the pure skill-identity helpers :func:`derive_skill_name` and
:func:`derive_project_dir`, which classify a SKILL.md path as modern
(``<dir>/SKILL.md``) or legacy (``<name>.md``) and surface the
authoritative skill identity from frontmatter with a layout-aware
fallback. The helpers are strictly pure (no stderr writes, no disk I/O)
per ``.claude/rules/pure-compute-vs-io-split.md``; the caller owns any
warning emission or file reads.

Traces to DEC-009 (see ``plans/super/22-iteration-workspace.md``) and
DEC-001, DEC-002, DEC-003, DEC-007, DEC-008, DEC-012 (see
``plans/super/62-skill-md-layout.md``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_MARKERS = (".git", ".claude")
_CLAUDITOR_DIRNAME = ".clauditor"

# Shared skill-identifier regex. Skill names are interpolated into
# filesystem paths (e.g. `<project_dir>/tests/eval/captured/<name>.txt`);
# clamping to basename-style tokens matching Claude Code's own convention
# for skill directory names blocks path-traversal via a malicious
# frontmatter `name:` field like `../../../etc/passwd`.
SKILL_NAME_RE: str = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


def resolve_clauditor_dir() -> Path:
    """Return the ``.clauditor`` directory anchored at the nearest repo root.

    Walks up from :func:`Path.cwd` looking for a directory containing any
    of ``.git`` or ``.claude``. Returns ``<that_dir>/.clauditor``. If no
    ancestor contains a marker, emits a single-line warning to stderr and
    returns ``Path.cwd() / ".clauditor"``.

    The user's home directory is only accepted as a match via the
    ``.git`` marker, never ``.claude``. A stray ``~/.claude`` (common:
    Claude Code's user config directory) would otherwise cause any
    clauditor invocation run from a project lacking ``.git`` to write
    iterations into ``~/.clauditor`` and contaminate unrelated work.
    """
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        home = None

    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        at_home = home is not None and resolved == home
        for marker in _MARKERS:
            if at_home and marker == ".claude":
                continue
            if (candidate / marker).exists():
                return candidate / _CLAUDITOR_DIRNAME
    print(
        f"WARNING: no .git/.claude ancestor found; using {cwd / _CLAUDITOR_DIRNAME}",
        file=sys.stderr,
    )
    return cwd / _CLAUDITOR_DIRNAME


def _filesystem_name(skill_path: Path) -> str:
    """Return the layout-aware filesystem-derived name.

    - Modern layout (``<dir>/SKILL.md``) → ``skill_path.parent.name``.
    - Legacy layout (``<name>.md`` where ``<name> != "SKILL"``) →
      ``skill_path.stem``.

    The modern/legacy distinction is made by the literal filename:
    ``SKILL.md`` is modern, anything else is legacy. DEC-001.
    """
    if skill_path.name == "SKILL.md":
        return skill_path.parent.name
    return skill_path.stem


def derive_skill_name(skill_path: Path, skill_md_text: str) -> str:
    """Return the resolved ``skill_name`` — pure, no I/O.

    Authority order (DEC-001, DEC-002, DEC-008):

    1. Parse ``skill_md_text`` frontmatter via :func:`parse_frontmatter`.
       Any :class:`ValueError` from the parser is treated as "frontmatter
       absent" — a malformed YAML block is not a reason to refuse the
       skill, and legacy ``.md`` files that never declare frontmatter
       are the common case.
    2. If the parsed dict has a ``name:`` key whose value passes
       :data:`SKILL_NAME_RE`, the frontmatter value wins.
    3. If the parsed ``name:`` value fails the regex, fall back to the
       filesystem-derived name.
    4. If ``name:`` is absent (no frontmatter, or frontmatter has no
       ``name:`` key), return the filesystem-derived name.

    Per DEC-008 of ``plans/super/71-agentskills-lint.md``, this helper
    no longer constructs or returns warning strings for the invalid-
    name-fallback or frontmatter-vs-filesystem-mismatch cases. Those
    warnings are now produced by
    :func:`clauditor.conformance.check_conformance` via the
    ``AGENTSKILLS_NAME_INVALID_CHARS`` and
    ``AGENTSKILLS_NAME_PARENT_DIR_MISMATCH`` codes, wired into
    :meth:`clauditor.spec.SkillSpec.from_file` by US-006. This helper
    is strictly pure — name derivation only, no side-channel.
    """
    # Local import avoids a circular dependency at module import time —
    # ``clauditor._frontmatter`` is a leaf module with no clauditor
    # imports, so importing it here is safe and cheap.
    from clauditor._frontmatter import parse_frontmatter

    fs_name = _filesystem_name(skill_path)

    try:
        parsed, _body = parse_frontmatter(skill_md_text)
    except ValueError:
        # Malformed frontmatter → treat as absent. The caller's
        # validation layer (if any) is responsible for surfacing a
        # stricter error; identity derivation stays lenient.
        return fs_name

    if not isinstance(parsed, dict) or "name" not in parsed:
        return fs_name

    fm_name = parsed["name"]
    if not isinstance(fm_name, str) or re.fullmatch(SKILL_NAME_RE, fm_name) is None:
        return fs_name

    return fm_name


def derive_project_dir(skill_path: Path) -> Path:
    """Return the project dir the runner should launch ``claude`` in.

    Authority order (DEC-003):

    1. :func:`clauditor.setup.find_project_root` walks up from
       ``skill_path.parent`` looking for a ``.git``/``.claude`` marker
       (with the home-dir exclusion guard). If it returns a non-``None``
       value, use it.
    2. Otherwise, fall back to layout-aware ascent:

       - Modern (``skill_path.name == "SKILL.md"``): 4 levels up from
         ``skill_path`` (``parent.parent.parent.parent``). The typical
         modern layout is
         ``<project>/.claude/skills/<name>/SKILL.md`` and the 4-deep
         ascent lands at ``<project>``.
       - Legacy (any other filename): 3 levels up (``parent.parent.parent``).
         The typical legacy layout is
         ``<project>/.claude/commands/<name>.md`` and the 3-deep ascent
         lands at ``<project>``.

    Note: the fallback assumes the documented layout depth. A skill
    placed at an unusually shallow path (e.g. ``/a/SKILL.md``) would
    see the ascent saturate at the filesystem root — but such a
    placement is not valid under either layout convention, and the
    marker-walk step normally short-circuits the fallback anyway for
    any real repo.

    Pure — no I/O beyond the marker-walk inside ``find_project_root``.
    """
    # Local import avoids a circular dependency: ``clauditor.setup``
    # does not import ``clauditor.paths``, but future refactors could
    # wire that link; the local import makes the direction explicit
    # and keeps module import order resilient.
    from clauditor.setup import find_project_root

    found = find_project_root(skill_path.parent)
    if found is not None:
        return found
    if skill_path.name == "SKILL.md":
        return skill_path.parent.parent.parent.parent
    return skill_path.parent.parent.parent
