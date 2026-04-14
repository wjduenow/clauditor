"""Tests for repo-root detection in clauditor.paths."""

import importlib

import clauditor.paths as _paths_mod

importlib.reload(_paths_mod)

from clauditor.paths import resolve_clauditor_dir  # noqa: E402


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
