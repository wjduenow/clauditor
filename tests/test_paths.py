"""Tests for repo-root detection in clauditor.paths."""

import importlib
import re

import clauditor.paths as _paths_mod

importlib.reload(_paths_mod)

from clauditor.paths import SKILL_NAME_RE, resolve_clauditor_dir  # noqa: E402


class TestResolveClauditorDir:
    def test_resolve_from_repo_root(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        assert resolve_clauditor_dir() == tmp_path / ".clauditor"

    def test_resolve_from_nested_subdir(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert resolve_clauditor_dir() == tmp_path / ".clauditor"

    def test_resolve_with_claude_only(self, tmp_path, monkeypatch):
        (tmp_path / ".claude").mkdir()
        nested = tmp_path / "x" / "y"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert resolve_clauditor_dir() == tmp_path / ".clauditor"

    def test_resolve_no_markers_fallback(self, tmp_path, monkeypatch, capsys):
        # tmp_path lives under /tmp which has no .git/.claude ancestors.
        sub = tmp_path / "proj"
        sub.mkdir()
        monkeypatch.chdir(sub)
        result = resolve_clauditor_dir()
        assert result == sub / ".clauditor"
        captured = capsys.readouterr()
        assert "clauditor" in (captured.err + captured.out).lower()

    def test_home_dir_claude_marker_is_ignored(
        self, tmp_path, monkeypatch, capsys
    ):
        """``~/.claude`` must not be treated as a repo-root marker.

        Pass 3 bug 6 regression guard: a user's global Claude Code config
        directory would otherwise cause every clauditor invocation from a
        project without .git to write iterations into ``~/.clauditor``.
        """
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        project = fake_home / "project" / "nested"
        project.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.chdir(project)

        result = resolve_clauditor_dir()
        # Should NOT resolve to ~/.clauditor (which would be
        # fake_home / ".clauditor"). Instead it falls back to the
        # current working directory because no valid marker was found.
        assert result != fake_home / ".clauditor"
        assert result == project / ".clauditor"


class TestSkillNameRe:
    def test_matches_known_good_identifier(self):
        assert re.fullmatch(SKILL_NAME_RE, "my-skill_123") is not None

    def test_rejects_known_bad_identifier(self):
        assert re.fullmatch(SKILL_NAME_RE, "bad;name") is None
        assert re.fullmatch(SKILL_NAME_RE, "") is None
