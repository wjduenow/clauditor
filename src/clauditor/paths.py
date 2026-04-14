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


def resolve_clauditor_dir() -> Path:
    """Return the ``.clauditor`` directory anchored at the nearest repo root.

    Walks up from :func:`Path.cwd` looking for a directory containing any
    of ``.git`` or ``.claude``. Returns ``<that_dir>/.clauditor``. If no
    ancestor contains a marker, emits a single-line warning to stderr and
    returns ``Path.cwd() / ".clauditor"``.
    """
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        for marker in _MARKERS:
            if (candidate / marker).exists():
                return candidate / _CLAUDITOR_DIRNAME
    print(
        f"WARNING: no .git/.claude ancestor found; using {cwd / _CLAUDITOR_DIRNAME}",
        file=sys.stderr,
    )
    return cwd / _CLAUDITOR_DIRNAME
