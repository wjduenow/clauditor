"""Repo-root detection helpers for clauditor.

Provides :func:`resolve_clauditor_dir` which walks up from the current
working directory looking for a ``.git/`` or ``.claude/`` marker and
returns ``<repo_root>/.clauditor``. If no marker is found, falls back to
``Path.cwd() / ".clauditor"`` and emits a single-line warning to stderr.

Traces to DEC-009 (see ``plans/super/22-iteration-workspace.md``).
"""

from __future__ import annotations

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
