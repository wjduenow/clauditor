"""Tests for the pure ``plan_setup`` decision layer.

The module under test is strictly pure per
``.claude/rules/pure-compute-vs-io-split.md``; these tests build a scratch
filesystem with ``tmp_path`` to exercise every :class:`SetupAction` branch
plus the :func:`find_project_root` marker walk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clauditor.setup import SetupAction, find_project_root, plan_setup


def _make_pkg_skill_root(tmp_path: Path) -> Path:
    """Create a scratch stand-in for the installed bundled skill dir."""
    pkg = tmp_path / "site-packages" / "clauditor" / "skills" / "clauditor"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text("stub\n")
    return pkg


def _make_project(tmp_path: Path) -> Path:
    """Create a scratch project root with a ``.git`` marker and the
    ``.claude/skills/`` parent dir (but not the ``clauditor`` entry
    itself — tests populate that per-branch).
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / ".claude" / "skills").mkdir(parents=True)
    return project


def _dest(project: Path) -> Path:
    return project / ".claude" / "skills" / "clauditor"


class TestFindProjectRoot:
    def test_find_project_root_with_git_marker(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / ".git").mkdir()
        assert find_project_root(proj) == proj

    def test_find_project_root_with_claude_marker(self, tmp_path: Path) -> None:
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / ".claude").mkdir()
        # No .git marker — .claude alone suffices.
        assert find_project_root(proj) == proj

    def test_find_project_root_walks_up_multiple_levels(
        self, tmp_path: Path
    ) -> None:
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / ".git").mkdir()
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert find_project_root(deep) == proj

    def test_find_project_root_none_when_no_marker(self, tmp_path: Path) -> None:
        # tmp_path has no .git or .claude anywhere in its ancestry;
        # walk from a subdirectory and expect None.
        sub = tmp_path / "nope"
        sub.mkdir()
        assert find_project_root(sub) is None

    def test_find_project_root_git_as_file_counts(self, tmp_path: Path) -> None:
        # git worktrees use a .git FILE, not dir. Both should count.
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / ".git").write_text("gitdir: /other\n")
        assert find_project_root(proj) == proj

    def test_find_project_root_claude_file_does_not_count(
        self, tmp_path: Path
    ) -> None:
        # A stray file named .claude should NOT match — only a directory.
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / ".claude").write_text("not a marker\n")
        assert find_project_root(proj) is None


class TestPlanSetupInstall:
    def test_plan_setup_returns_create_symlink(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        # dest absent
        result = plan_setup(project, pkg, force=False, unlink=False)
        assert result is SetupAction.CREATE_SYMLINK

    def test_plan_setup_returns_noop_already_installed(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.symlink_to(pkg)
        result = plan_setup(project, pkg, force=False, unlink=False)
        assert result is SetupAction.NOOP_ALREADY_INSTALLED

    def test_plan_setup_returns_refuse_existing_file(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.touch()
        result = plan_setup(project, pkg, force=False, unlink=False)
        assert result is SetupAction.REFUSE_EXISTING_FILE

    def test_plan_setup_returns_refuse_existing_dir(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.mkdir()
        result = plan_setup(project, pkg, force=False, unlink=False)
        assert result is SetupAction.REFUSE_EXISTING_DIR

    def test_plan_setup_returns_refuse_wrong_symlink(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        # Point dest at something other than pkg.
        other = tmp_path / "elsewhere"
        other.mkdir()
        dest.symlink_to(other)
        result = plan_setup(project, pkg, force=False, unlink=False)
        assert result is SetupAction.REFUSE_WRONG_SYMLINK

    def test_plan_setup_returns_replace_with_force_for_file(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.touch()
        result = plan_setup(project, pkg, force=True, unlink=False)
        assert result is SetupAction.REPLACE_WITH_FORCE

    def test_plan_setup_returns_replace_with_force_for_dir(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.mkdir()
        result = plan_setup(project, pkg, force=True, unlink=False)
        assert result is SetupAction.REPLACE_WITH_FORCE

    def test_plan_setup_returns_replace_with_force_for_wrong_symlink(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        other = tmp_path / "elsewhere"
        other.mkdir()
        dest.symlink_to(other)
        result = plan_setup(project, pkg, force=True, unlink=False)
        assert result is SetupAction.REPLACE_WITH_FORCE

    def test_plan_setup_force_ignored_for_our_symlink(
        self, tmp_path: Path
    ) -> None:
        # force does NOT replace an already-correct symlink — no-op wins.
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.symlink_to(pkg)
        result = plan_setup(project, pkg, force=True, unlink=False)
        assert result is SetupAction.NOOP_ALREADY_INSTALLED


class TestPlanSetupUnlink:
    def test_plan_setup_returns_remove_symlink(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.symlink_to(pkg)
        result = plan_setup(project, pkg, force=False, unlink=True)
        assert result is SetupAction.REMOVE_SYMLINK

    def test_plan_setup_returns_noop_nothing_to_unlink(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        # dest absent
        result = plan_setup(project, pkg, force=False, unlink=True)
        assert result is SetupAction.NOOP_NOTHING_TO_UNLINK

    def test_plan_setup_returns_refuse_unlink_non_symlink(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.mkdir()  # real dir, not a symlink
        result = plan_setup(project, pkg, force=False, unlink=True)
        assert result is SetupAction.REFUSE_UNLINK_NON_SYMLINK

    def test_plan_setup_returns_refuse_unlink_wrong_target(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        other = tmp_path / "elsewhere"
        other.mkdir()
        dest.symlink_to(other)
        result = plan_setup(project, pkg, force=False, unlink=True)
        assert result is SetupAction.REFUSE_UNLINK_WRONG_TARGET

    def test_plan_setup_force_ignored_in_unlink_mode(
        self, tmp_path: Path
    ) -> None:
        # force=True must NOT override the refuse branch in unlink mode.
        project = _make_project(tmp_path)
        pkg = _make_pkg_skill_root(tmp_path)
        dest = _dest(project)
        dest.touch()  # regular file
        result = plan_setup(project, pkg, force=True, unlink=True)
        assert result is SetupAction.REFUSE_UNLINK_NON_SYMLINK


class TestPlanSetupErrors:
    def test_plan_setup_raises_when_no_project_root(self, tmp_path: Path) -> None:
        # tmp_path has no .git or .claude anywhere in the walk.
        sub = tmp_path / "nope"
        sub.mkdir()
        pkg = _make_pkg_skill_root(tmp_path / "sp")
        with pytest.raises(ValueError, match="no project root found"):
            plan_setup(sub, pkg, force=False, unlink=False)
