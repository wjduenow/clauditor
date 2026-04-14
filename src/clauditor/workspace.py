"""Iteration workspace allocator for clauditor grade runs.

Allocates an ``iteration-N/`` slot under ``.clauditor/`` for a given skill.
Writes stage to ``iteration-N-tmp/<skill>/`` first; callers invoke
:meth:`IterationWorkspace.finalize` to atomically rename the staging dir
into place, or :meth:`IterationWorkspace.abort` to discard it.

Traces to DEC-001 (auto-increment + explicit override), DEC-006 (optimistic
concurrent allocation), DEC-008 (collision policy), DEC-012 (atomic
tmp+rename writes), DEC-014 (``--force`` clean-slate semantics).
See ``plans/super/22-iteration-workspace.md``.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "InvalidSkillNameError",
    "IterationExistsError",
    "IterationWorkspace",
    "allocate_iteration",
    "stage_inputs",
    "validate_skill_name",
]

_ITERATION_RE = re.compile(r"^iteration-(\d+)$")
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_AUTO_RETRIES = 100


class IterationExistsError(Exception):
    """Raised when an explicit ``iteration=N`` collides and ``force`` is False."""


class InvalidSkillNameError(ValueError):
    """Raised when a skill name contains characters unsafe for path joining."""


def validate_skill_name(skill: str) -> str:
    """Reject skill names that could escape the clauditor directory.

    Accepts alphanumerics plus ``_ . -``. Rejects empty strings, path
    separators, parent references (``..``), and any name beginning with
    a dot (to avoid collisions with hidden directories like ``.lock``
    or ``.git`` siblings under ``.clauditor/``). Returns the validated
    name.
    """
    if (
        not skill
        or skill.startswith(".")
        or not _SKILL_NAME_RE.match(skill)
    ):
        raise InvalidSkillNameError(
            f"invalid skill name for workspace path: {skill!r}"
        )
    return skill


@dataclass
class IterationWorkspace:
    """Handle for an allocated iteration slot.

    Attributes:
        iteration: The numeric iteration index.
        final_path: Target path ``<clauditor_dir>/iteration-N/<skill>/``.
            Does not exist until :meth:`finalize` is called.
        tmp_path: Staging path ``<clauditor_dir>/iteration-N-tmp/<skill>/``
            where callers should write artifacts.
    """

    iteration: int
    final_path: Path
    tmp_path: Path
    finalized: bool = False

    @property
    def _tmp_parent(self) -> Path:
        return self.tmp_path.parent

    @property
    def _final_parent(self) -> Path:
        return self.final_path.parent

    def finalize(self) -> None:
        """Atomically promote the tmp dir to its final location.

        Uses :func:`os.rename` on the ``iteration-N-tmp`` → ``iteration-N``
        parent directories (they are peers under ``clauditor_dir``, so the
        rename is atomic on POSIX).

        If a concurrent peer finalized the same iteration index between our
        allocation scan and this call, the rename raises ``OSError``
        (``ENOTEMPTY``); we clean up the staging dir and surface the race as
        an :class:`IterationExistsError` so the caller sees a consistent
        failure mode rather than a bare OSError.
        """
        try:
            os.rename(self._tmp_parent, self._final_parent)
        except OSError as exc:
            # Only a concurrent-peer race produces ENOTEMPTY / EEXIST
            # (the target directory was populated by another writer).
            # Surface permission, disk-full, and read-only-fs errors
            # unchanged so the caller sees the real failure.
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
            self.abort()
            raise IterationExistsError(
                f"iteration-{self.iteration} was finalized by a concurrent "
                f"writer; staging dir discarded"
            ) from exc
        self.finalized = True

    def abort(self) -> None:
        """Remove the staging directory. Safe if it's already gone."""
        shutil.rmtree(self._tmp_parent, ignore_errors=True)


def _scan_existing_iterations(clauditor_dir: Path) -> set[int]:
    """Return the set of iteration indices already present under ``clauditor_dir``.

    Considers only real ``iteration-N/`` directories (not ``-tmp`` siblings
    and not malformed names).
    """
    if not clauditor_dir.exists():
        return set()
    found: set[int] = set()
    for child in clauditor_dir.iterdir():
        if not child.is_dir():
            continue
        match = _ITERATION_RE.match(child.name)
        if match is not None:
            found.add(int(match.group(1)))
    return found


def allocate_iteration(
    clauditor_dir: Path,
    skill: str,
    *,
    iteration: int | None = None,
    force: bool = False,
) -> IterationWorkspace:
    """Allocate an iteration workspace slot for ``skill`` under ``clauditor_dir``.

    Args:
        clauditor_dir: The ``.clauditor`` directory (created if missing).
        skill: Skill name; becomes a subdirectory inside the iteration dir.
        iteration: Explicit iteration index. ``None`` means auto-increment.
        force: If True and an explicit ``iteration`` dir already exists,
            ``shutil.rmtree`` it before allocation.

    Returns:
        An :class:`IterationWorkspace` whose ``tmp_path`` already exists and
        is ready to be written into.

    Raises:
        IterationExistsError: ``iteration=N`` given, ``iteration-N/`` exists,
            and ``force`` is False.
        RuntimeError: Auto-increment could not find a free slot within the
            retry cap (pathological contention).
    """
    validate_skill_name(skill)
    clauditor_dir.mkdir(parents=True, exist_ok=True)

    if iteration is not None:
        return _allocate_explicit(clauditor_dir, skill, iteration, force=force)
    return _allocate_auto(clauditor_dir, skill)


def _allocate_explicit(
    clauditor_dir: Path, skill: str, iteration: int, *, force: bool
) -> IterationWorkspace:
    if iteration < 1:
        raise ValueError(
            f"iteration must be >= 1, got {iteration}"
        )
    final_parent = clauditor_dir / f"iteration-{iteration}"
    tmp_parent = clauditor_dir / f"iteration-{iteration}-tmp"

    if final_parent.exists():
        if not force:
            raise IterationExistsError(
                f"iteration-{iteration} already exists; use --force to overwrite"
            )
        shutil.rmtree(final_parent)

    # Orphan tmp dirs are stage-only junk from a crashed prior run — always
    # clear them regardless of --force so explicit --iteration N doesn't
    # crash with a bare FileExistsError.
    if tmp_parent.exists():
        shutil.rmtree(tmp_parent)

    tmp_parent.mkdir(exist_ok=False)
    tmp_path = tmp_parent / skill
    tmp_path.mkdir(parents=True, exist_ok=False)
    return IterationWorkspace(
        iteration=iteration,
        final_path=final_parent / skill,
        tmp_path=tmp_path,
    )


def _allocate_auto(clauditor_dir: Path, skill: str) -> IterationWorkspace:
    existing = _scan_existing_iterations(clauditor_dir)
    candidate = (max(existing) + 1) if existing else 1

    for _ in range(_MAX_AUTO_RETRIES):
        final_parent = clauditor_dir / f"iteration-{candidate}"
        tmp_parent = clauditor_dir / f"iteration-{candidate}-tmp"

        # Skip slots whose final dir already exists (another worker won).
        if final_parent.exists():
            candidate += 1
            continue

        try:
            tmp_parent.mkdir(exist_ok=False)
        except FileExistsError:
            # Peer racing on the same slot — try the next one.
            candidate += 1
            continue

        tmp_path = tmp_parent / skill
        tmp_path.mkdir(parents=True, exist_ok=False)
        return IterationWorkspace(
            iteration=candidate,
            final_path=final_parent / skill,
            tmp_path=tmp_path,
        )

    raise RuntimeError(
        f"allocate_iteration: exceeded {_MAX_AUTO_RETRIES} retries scanning "
        f"for a free iteration slot under {clauditor_dir}"
    )


def stage_inputs(run_dir: Path, sources: list[Path]) -> list[Path]:
    """Copy input files into ``run_dir / "inputs"`` and return destinations.

    Pure helper: trusts its inputs. Path parsing, containment checks, and
    symlink resolution are performed upstream at spec load time (US-001);
    this function does no validation of its own.

    Contract:
        - If ``sources`` is empty, returns ``[]`` and does **not** create
          the ``inputs/`` subdirectory (no-op).
        - Otherwise creates ``run_dir / "inputs"`` if needed and copies
          each source to ``<run_dir>/inputs/<source.name>`` via
          :func:`shutil.copy2` (preserving mtime).
        - Returns destination paths in the same order as ``sources``.

    Raises:
        FileNotFoundError: If any source path does not exist.
    """
    if not sources:
        return []
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    destinations: list[Path] = []
    for src in sources:
        if not src.exists():
            raise FileNotFoundError(
                f"stage_inputs: source file not found: {src}"
            )
        dest = inputs_dir / src.name
        shutil.copy2(src, dest)
        destinations.append(dest)
    return destinations
