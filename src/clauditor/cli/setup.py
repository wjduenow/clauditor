"""``clauditor setup`` — install the bundled ``/clauditor`` skill symlink.

Side-effect layer for the setup flow. Pure decision logic lives in
:func:`clauditor.setup.plan_setup`; this module translates the
returned :class:`SetupAction` into filesystem operations, stdout/
stderr messages, and exit codes (DEC-008, DEC-009, DEC-016).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from importlib.resources import as_file, files
from pathlib import Path

from clauditor import setup as setup_module


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``setup`` subparser."""
    p_setup = subparsers.add_parser(
        "setup",
        help="Install the /clauditor slash command into .claude/skills/",
        description=(
            "Create a symlink at .claude/skills/clauditor pointing at the "
            "bundled skill shipped with the clauditor package. By default, "
            "refuses to overwrite existing files or symlinks at that path. "
            "Use --unlink to remove a previously-installed symlink."
        ),
    )
    p_setup.add_argument(
        "--unlink",
        action="store_true",
        help=(
            "Remove the /clauditor symlink instead of creating it. "
            "Only removes our own symlinks; refuses to remove files or "
            "symlinks pointing elsewhere. --force does not override this "
            "refusal in --unlink mode."
        ),
    )
    p_setup.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an existing file or symlink at "
            ".claude/skills/clauditor. Has no effect in --unlink mode "
            "(which always refuses to remove non-matching entries)."
        ),
    )
    p_setup.add_argument(
        "--project-dir",
        type=str,
        default=None,
        help=(
            "Override project-root detection; use this directory as the "
            "cwd for .claude/ resolution (default: current working dir)."
        ),
    )


def check_clauditor_skill_symlink(
    project_root: Path | None,
    pkg_skill_root: Path,
) -> tuple[str, str, str]:
    """Return a ``(check_name, status, detail)`` tuple describing the health
    of ``<project_root>/.claude/skills/clauditor`` per DEC-013.

    Six states:

    - project root missing → ``info`` (doctor keeps running outside projects)
    - dest does not exist → ``info`` ("run ``clauditor setup``")
    - dest is our symlink → ``ok`` (resolves to ``pkg_skill_root``)
    - dest is a broken symlink → ``warn`` (stale — target removed by pip
      uninstall/upgrade)
    - dest is a symlink to the wrong target → ``warn``
    - dest is a regular file or directory → ``warn`` ("unmanaged")
    """
    check_name = "clauditor-skill-symlink"

    if project_root is None:
        # Doctor has no --project-dir flag, so do not suggest one here.
        # The matching cmd_setup message retains the flag hint per DEC-011.
        return (
            check_name,
            "info",
            "no project root found; run from a project directory",
        )

    dest = project_root / ".claude" / "skills" / "clauditor"

    if dest.is_symlink():
        # Handle symlinks first: ``dest.exists()`` is False for broken
        # symlinks but ``is_symlink()`` is True — this is how we detect
        # dangling symlinks after a pip uninstall/upgrade.
        if not dest.exists():
            # repr() protects the single-line doctor output from symlink
            # targets containing newlines or control characters.
            target_display = repr(os.readlink(dest))
            return (
                check_name,
                "warn",
                (
                    f"stale symlink; 'clauditor setup --force' to fix "
                    f"(target: {target_display})"
                ),
            )
        if dest.resolve() == pkg_skill_root.resolve():
            return (check_name, "ok", f"symlink -> {dest.resolve()}")
        return (
            check_name,
            "warn",
            (
                f"symlink target doesn't match installed package "
                f"(points to {dest.resolve()})"
            ),
        )

    if not dest.exists():
        return (
            check_name,
            "info",
            "clauditor skill not installed; run 'clauditor setup'",
        )

    # Regular file or real directory (not a symlink).
    kind = "file" if dest.is_file() else "directory"
    return (check_name, "warn", f"{kind}; unmanaged by clauditor")


def _install_symlink(dest: Path, pkg_skill_root: Path) -> None:
    """Create the symlink at ``dest`` pointing to ``pkg_skill_root``.

    Ensures the parent ``.claude/skills/`` dir exists with explicit mode
    ``0o755`` per DEC-012, then calls :func:`os.symlink`. Raises
    :exc:`FileExistsError` if ``dest`` appeared between the caller's
    :func:`plan_setup` inspection and this call — the caller re-plans
    once per DEC-010.
    """
    dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.symlink(pkg_skill_root, dest)


def _remove_existing(dest: Path) -> None:
    """Remove whatever is at ``dest`` — symlink, file, directory, or exotic.

    Used only in the ``--force`` replace path. ``Path.unlink`` handles
    symlinks (even broken ones) and regular files; ``shutil.rmtree``
    handles a real directory. The ``unlink(missing_ok=True)`` fallback
    catches exotic types (FIFO, socket, device) and benign races where a
    concurrent peer removed ``dest`` between our inspection and this call;
    ``rmtree`` passes ``ignore_errors=True`` for the same race.
    """
    if dest.is_symlink() or dest.is_file():
        dest.unlink(missing_ok=True)
    elif dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)
    else:
        dest.unlink(missing_ok=True)


def _dispatch_setup_action(
    action: setup_module.SetupAction,
    dest: Path,
    pkg_skill_root: Path,
) -> int:
    """Translate a :class:`SetupAction` into I/O + exit code.

    May raise :exc:`FileExistsError` from :func:`_install_symlink` when
    ``dest`` appeared since ``plan_setup`` inspected it. The caller
    (:func:`cmd_setup`) re-plans once on that exception per DEC-010.
    """
    if action is setup_module.SetupAction.CREATE_SYMLINK:
        _install_symlink(dest, pkg_skill_root)
        print(f"Installed /clauditor: {dest} -> {pkg_skill_root}")
        return 0
    if action is setup_module.SetupAction.NOOP_ALREADY_INSTALLED:
        print("/clauditor already installed (no changes)")
        return 0
    if action is setup_module.SetupAction.REPLACE_WITH_FORCE:
        _remove_existing(dest)
        _install_symlink(dest, pkg_skill_root)
        print(f"Installed /clauditor: {dest} -> {pkg_skill_root}")
        return 0
    if action is setup_module.SetupAction.REFUSE_EXISTING_FILE:
        print(
            "ERROR: .claude/skills/clauditor exists (regular file); "
            "use --force to overwrite",
            file=sys.stderr,
        )
        return 1
    if action is setup_module.SetupAction.REFUSE_EXISTING_DIR:
        print(
            "ERROR: .claude/skills/clauditor exists (directory); "
            "use --force to overwrite",
            file=sys.stderr,
        )
        return 1
    if action is setup_module.SetupAction.REFUSE_WRONG_SYMLINK:
        print(
            "ERROR: .claude/skills/clauditor is a symlink pointing "
            "elsewhere; use --force to overwrite",
            file=sys.stderr,
        )
        return 1
    if action is setup_module.SetupAction.REMOVE_SYMLINK:
        # missing_ok handles the race where a concurrent peer removed the
        # symlink between plan_setup and here: treat "already gone" as a
        # success (the user wanted it gone; it's gone).
        dest.unlink(missing_ok=True)
        print("Removed .claude/skills/clauditor")
        return 0
    if action is setup_module.SetupAction.NOOP_NOTHING_TO_UNLINK:
        print("/clauditor not installed (nothing to unlink)")
        return 0
    if action is setup_module.SetupAction.REFUSE_UNLINK_NON_SYMLINK:
        print(
            "ERROR: .claude/skills/clauditor is not a symlink; "
            "refusing to unlink",
            file=sys.stderr,
        )
        return 1
    if action is setup_module.SetupAction.REFUSE_UNLINK_WRONG_TARGET:
        print(
            "ERROR: .claude/skills/clauditor symlink target does "
            "not match installed clauditor; refusing",
            file=sys.stderr,
        )
        return 1

    # Unreachable: every SetupAction member is handled above.
    return 1  # pragma: no cover


def cmd_setup(args: argparse.Namespace) -> int:
    """Install the bundled ``/clauditor`` skill symlink (or remove it).

    Retries the plan+dispatch once on :exc:`FileExistsError` so a
    concurrent process that created ``dest`` between our inspection
    and our :func:`os.symlink` call is handled cleanly per DEC-010
    (atomic create-or-fail, no check-then-create).
    """
    traversable = files("clauditor") / "skills" / "clauditor"
    # Reject zip/PEX-style installs up front: as_file() would extract to a
    # tmp dir that gets cleaned up when the context exits, leaving a
    # dangling symlink and making doctor comparisons always mismatch.
    if not isinstance(traversable, Path):
        print(
            "ERROR: bundled clauditor skill is not available as a stable "
            "filesystem path; `clauditor setup` requires an unpacked "
            "installation and does not support zip/PEX-style package "
            "resources",
            file=sys.stderr,
        )
        return 2

    with as_file(traversable) as pkg_skill_root_path:
        pkg_skill_root = Path(pkg_skill_root_path).resolve()

        cwd = (
            Path(args.project_dir).resolve()
            if args.project_dir
            else Path.cwd().resolve()
        )

        for attempt in range(2):
            try:
                action = setup_module.plan_setup(
                    cwd,
                    pkg_skill_root,
                    force=args.force,
                    unlink=args.unlink,
                )
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 2

            project_root = setup_module.find_project_root(cwd)
            # find_project_root cannot return None here: plan_setup already
            # raised ValueError in that case and we returned above.
            assert project_root is not None
            dest = project_root / ".claude" / "skills" / "clauditor"

            try:
                return _dispatch_setup_action(action, dest, pkg_skill_root)
            except FileExistsError:
                if attempt == 1:
                    print(
                        "ERROR: .claude/skills/clauditor changed during "
                        "setup (concurrent modification); aborting, "
                        "please retry manually",
                        file=sys.stderr,
                    )
                    return 1
                # Re-plan once: dest appeared after our inspection.
                continue

        return 1  # pragma: no cover (loop always returns)
