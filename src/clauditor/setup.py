"""Pure decision layer for ``clauditor setup`` install/unlink planning.

This module is intentionally PURE per
``.claude/rules/pure-compute-vs-io-split.md``: it only reads metadata from
``pathlib.Path`` objects (``.exists``, ``.is_symlink``, ``.is_file``,
``.is_dir``, ``.resolve``, ``.parent``) and returns a ``SetupAction`` enum
member. No ``os.symlink``, ``open``, ``write``, or ``shutil`` calls live
here — all side effects are the caller's responsibility.

The CLI (US-005) dispatches on the enum to perform the actual symlink
create/remove using atomic ``os.symlink`` + ``FileExistsError`` handling
per DEC-010.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

# Bound the project-root search so a mis-rooted cwd does not walk to the
# filesystem root and beyond. 50 levels is several orders of magnitude
# deeper than any realistic checkout.
_PROJECT_ROOT_SEARCH_LIMIT = 50


class SetupAction(Enum):
    """Discrete outcomes ``plan_setup`` can return.

    Each member names what the I/O layer should do next. The CLI maps
    each value to a message + exit code per DEC-008/DEC-009/DEC-016.
    """

    CREATE_SYMLINK = "create_symlink"
    NOOP_ALREADY_INSTALLED = "noop_already_installed"
    REPLACE_WITH_FORCE = "replace_with_force"
    REFUSE_EXISTING_FILE = "refuse_existing_file"
    REFUSE_EXISTING_DIR = "refuse_existing_dir"
    REFUSE_WRONG_SYMLINK = "refuse_wrong_symlink"
    REMOVE_SYMLINK = "remove_symlink"
    NOOP_NOTHING_TO_UNLINK = "noop_nothing_to_unlink"
    REFUSE_UNLINK_NON_SYMLINK = "refuse_unlink_non_symlink"
    REFUSE_UNLINK_WRONG_TARGET = "refuse_unlink_wrong_target"


def find_project_root(cwd: Path) -> Path | None:
    """Walk up from ``cwd`` looking for a ``.git`` or ``.claude`` marker.

    Returns the first ancestor (or ``cwd`` itself) that contains either
    marker. Returns ``None`` if no marker is found before hitting the
    filesystem root or the search bound.

    ``.git`` may be a file (inside a git worktree) or a directory (normal
    checkout); either counts. ``.claude`` must be a directory — a stray
    file named ``.claude`` next to some unrelated project would not
    indicate a project root.
    """
    current = cwd
    for _ in range(_PROJECT_ROOT_SEARCH_LIMIT):
        git_marker = current / ".git"
        claude_marker = current / ".claude"
        if git_marker.exists():
            return current
        if claude_marker.is_dir():
            return current
        parent = current.parent
        if parent == current:
            # Reached filesystem root.
            return None
        current = parent
    # Exhausted the search bound — treat as "no project root" rather
    # than walking the whole filesystem tree.
    return None  # pragma: no cover


def _is_our_symlink(dest: Path, pkg_skill_root: Path) -> bool:
    """Return True if ``dest`` is a symlink whose resolved target equals the
    installed bundled skill directory.

    The comparison uses ``Path.resolve()`` on both sides so relative
    symlinks and differently-spelled-but-equivalent absolute paths compare
    equal. Callers must verify ``dest.is_symlink()`` themselves — this
    helper assumes a symlink and is only correct under that precondition.
    """
    try:
        resolved = dest.resolve()
    except OSError:  # pragma: no cover — broken symlink or perms
        return False
    return resolved == pkg_skill_root.resolve()


def plan_setup(
    cwd: Path,
    pkg_skill_root: Path,
    *,
    force: bool,
    unlink: bool,
) -> SetupAction:
    """Decide what the I/O layer should do. PURE — no file I/O, no writes.

    Args:
        cwd: user's current working directory (search origin for project
            root detection via :func:`find_project_root`).
        pkg_skill_root: absolute path to the installed bundled skill dir,
            e.g. ``<site-packages>/clauditor/skills/clauditor/``. The
            caller is responsible for resolving this via
            ``importlib.resources``.
        force: if ``True``, a conflicting destination triggers
            :attr:`SetupAction.REPLACE_WITH_FORCE` instead of a
            ``REFUSE_*`` variant. Ignored when ``unlink=True`` — refusal
            branches in unlink mode apply regardless of ``force``.
        unlink: if ``True``, interpret the operation as a removal rather
            than an install.

    Returns:
        One of the :class:`SetupAction` members describing what the
        caller should do next.

    Raises:
        ValueError: if :func:`find_project_root` returns ``None`` — the
            CLI maps this to exit code 2 with a user-visible error.
    """
    project_root = find_project_root(cwd)
    if project_root is None:
        raise ValueError("no project root found; run from a project directory")

    dest = project_root / ".claude" / "skills" / "clauditor"

    if unlink:
        return _plan_unlink(dest, pkg_skill_root)
    return _plan_install(dest, pkg_skill_root, force=force)


def _plan_install(
    dest: Path,
    pkg_skill_root: Path,
    *,
    force: bool,
) -> SetupAction:
    """Install-mode branch of :func:`plan_setup`."""
    # Symlink check must come before ``exists()`` — a symlink with a
    # broken target reports ``exists() is False`` but ``is_symlink() is
    # True``, and we still want to classify it as a wrong-target symlink
    # so ``--force`` can clear it.
    if dest.is_symlink():
        if _is_our_symlink(dest, pkg_skill_root):
            return SetupAction.NOOP_ALREADY_INSTALLED
        return (
            SetupAction.REPLACE_WITH_FORCE
            if force
            else SetupAction.REFUSE_WRONG_SYMLINK
        )
    if not dest.exists():
        return SetupAction.CREATE_SYMLINK
    if dest.is_file():
        return (
            SetupAction.REPLACE_WITH_FORCE
            if force
            else SetupAction.REFUSE_EXISTING_FILE
        )
    # Remaining case: regular directory. ``is_dir()`` is true here; an
    # exotic entry (FIFO/socket/device) is rare enough that we do not
    # enumerate it separately — the install-mode branches above cover
    # every documented case.
    return (
        SetupAction.REPLACE_WITH_FORCE
        if force
        else SetupAction.REFUSE_EXISTING_DIR
    )


def _plan_unlink(dest: Path, pkg_skill_root: Path) -> SetupAction:
    """Unlink-mode branch of :func:`plan_setup`.

    ``force`` is intentionally absent from this signature: the design
    decision (DEC-009) is that ``--force`` has no effect in unlink mode.
    Destructive operations stay safe by default.
    """
    if dest.is_symlink():
        if _is_our_symlink(dest, pkg_skill_root):
            return SetupAction.REMOVE_SYMLINK
        return SetupAction.REFUSE_UNLINK_WRONG_TARGET
    if not dest.exists():
        return SetupAction.NOOP_NOTHING_TO_UNLINK
    # Regular file, real directory, or exotic type — refuse regardless
    # of force.
    return SetupAction.REFUSE_UNLINK_NON_SYMLINK
