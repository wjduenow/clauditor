"""Tests for repo-root detection in clauditor.paths."""

import importlib
import re
from pathlib import Path

import clauditor.paths as _paths_mod

importlib.reload(_paths_mod)

from clauditor.paths import (  # noqa: E402
    SKILL_NAME_RE,
    derive_project_dir,
    derive_skill_name,
    resolve_clauditor_dir,
)


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

    def test_home_lookup_failure_falls_through(
        self, tmp_path, monkeypatch
    ):
        """``Path.home().resolve()`` failure degrades to ``home = None``.

        Exercises the defensive ``except (RuntimeError, OSError)`` guard
        at the top of :func:`resolve_clauditor_dir`. In containerized or
        rootless environments where ``$HOME`` is unset, ``Path.home()``
        raises ``RuntimeError``; the walk continues without the
        home-exclusion and the legitimate ``.git`` marker still wins.
        """
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "clauditor.paths.Path.home",
            lambda: (_ for _ in ()).throw(RuntimeError("no HOME")),
        )
        assert resolve_clauditor_dir() == tmp_path / ".clauditor"

    def test_candidate_resolve_failure_uses_unresolved(
        self, tmp_path, monkeypatch
    ):
        """``candidate.resolve()`` failure degrades to the raw candidate.

        Exercises the defensive ``except OSError`` guard in the marker-
        walk loop. On filesystems where ``resolve()`` fails mid-walk
        (transient I/O error, broken symlink chain), the helper falls
        back to the unresolved candidate for the home comparison and
        keeps walking.
        """
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)

        real_resolve = Path.resolve

        def flaky_resolve(self, strict=False):
            # Raise only for the candidate passed to the loop's resolve;
            # let Path.home().resolve() succeed so the home lookup lands
            # and the at_home guard still runs.
            if self == tmp_path:
                raise OSError("I/O error")
            return real_resolve(self, strict=strict)

        monkeypatch.setattr(
            "clauditor.paths.Path.resolve", flaky_resolve
        )
        assert resolve_clauditor_dir() == tmp_path / ".clauditor"


class TestSkillNameRe:
    def test_matches_known_good_identifier(self):
        assert re.fullmatch(SKILL_NAME_RE, "my-skill_123") is not None

    def test_rejects_known_bad_identifier(self):
        assert re.fullmatch(SKILL_NAME_RE, "bad;name") is None
        assert re.fullmatch(SKILL_NAME_RE, "") is None


class TestDeriveSkillName:
    """Unit tests for the pure ``derive_skill_name`` helper.

    The helper takes the skill path and the SKILL.md text as input and
    returns a ``(name, warning_or_None)`` tuple without touching disk or
    stderr. Every branch of the DEC-001/DEC-002/DEC-008 decision tree
    has a dedicated test here per US-002.
    """

    def test_frontmatter_name_matches_filesystem(self, tmp_path):
        parent = tmp_path / "foo"
        parent.mkdir()
        skill_path = parent / "SKILL.md"
        text = "---\nname: foo\n---\n\n# Body\n"
        assert derive_skill_name(skill_path, text) == ("foo", None)

    def test_frontmatter_name_overrides_filesystem_with_warning(self, tmp_path):
        parent = tmp_path / "foo"
        parent.mkdir()
        skill_path = parent / "SKILL.md"
        text = "---\nname: bar\n---\n\n# Body\n"
        name, warning = derive_skill_name(skill_path, text)
        assert name == "bar"
        assert warning is not None
        assert (
            "frontmatter name 'bar' overrides filesystem name 'foo' "
            "— using 'bar'"
        ) in warning
        assert warning.startswith("clauditor.spec:")

    def test_missing_frontmatter_falls_back_modern(self, tmp_path):
        parent = tmp_path / "foo"
        parent.mkdir()
        skill_path = parent / "SKILL.md"
        text = "# Body without any frontmatter\n"
        assert derive_skill_name(skill_path, text) == ("foo", None)

    def test_missing_name_field_falls_back_legacy(self, tmp_path):
        skill_path = tmp_path / "my-skill.md"
        text = "---\ndescription: a skill\n---\n\n# Body\n"
        assert derive_skill_name(skill_path, text) == ("my-skill", None)

    def test_invalid_regex_falls_back_with_warning(self, tmp_path):
        parent = tmp_path / "foo"
        parent.mkdir()
        skill_path = parent / "SKILL.md"
        text = "---\nname: bad;value\n---\n"
        name, warning = derive_skill_name(skill_path, text)
        assert name == "foo"
        assert warning is not None
        assert "not a valid skill identifier" in warning
        assert warning.startswith("clauditor.spec:")
        assert "'bad;value'" in warning
        assert "'foo'" in warning

    def test_malformed_frontmatter_treated_as_absent(self, tmp_path):
        parent = tmp_path / "foo"
        parent.mkdir()
        skill_path = parent / "SKILL.md"
        # Opening '---' with no closing delimiter → parse_frontmatter
        # raises ValueError; derive_skill_name treats as absent.
        text = "---\nname: foo\n\n(no closing delimiter)\n"
        assert derive_skill_name(skill_path, text) == ("foo", None)

    def test_legacy_without_frontmatter(self, tmp_path):
        skill_path = tmp_path / "my-skill.md"
        text = "# Plain legacy skill file with no frontmatter block\n"
        assert derive_skill_name(skill_path, text) == ("my-skill", None)


class TestDeriveProjectDir:
    """Unit tests for the pure ``derive_project_dir`` helper.

    The helper tries marker-walk first (``find_project_root``) and falls
    back to layout-aware ascent when no marker is found. Per US-002 we
    cover both the marker-found and marker-missing paths for both
    layouts.
    """

    def test_project_dir_via_find_project_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        skills_dir = tmp_path / ".claude" / "skills" / "foo"
        skills_dir.mkdir(parents=True)
        skill_path = skills_dir / "SKILL.md"
        skill_path.write_text("---\nname: foo\n---\n")
        assert derive_project_dir(skill_path) == tmp_path

    def test_project_dir_fallback_modern_ascent(self, monkeypatch):
        # Non-existent path; force find_project_root to return None so
        # the layout-aware fallback is exercised deterministically
        # regardless of `.git`/`.claude` markers that may exist at `/`
        # or other ancestors on the host.
        monkeypatch.setattr(
            "clauditor.setup.find_project_root", lambda _p: None
        )
        skill_path = Path("/a/b/c/d/e/SKILL.md")
        assert derive_project_dir(skill_path) == Path("/a/b")

    def test_project_dir_fallback_legacy_ascent(self, monkeypatch):
        monkeypatch.setattr(
            "clauditor.setup.find_project_root", lambda _p: None
        )
        skill_path = Path("/a/b/c/d/foo.md")
        assert derive_project_dir(skill_path) == Path("/a/b")

    def test_project_dir_fallback_modern_when_no_marker(
        self, tmp_path, monkeypatch
    ):
        # tmp_path may have unexpected ancestors with `.git`/`.claude`
        # (some CI sandboxes do). Force the marker-walk to return None
        # so the 4-deep fallback for the modern SKILL.md layout is
        # exercised deterministically.
        monkeypatch.setattr(
            "clauditor.setup.find_project_root", lambda _p: None
        )
        skills_dir = tmp_path / "a" / "b" / "c" / "d" / "foo"
        skills_dir.mkdir(parents=True)
        skill_path = skills_dir / "SKILL.md"
        skill_path.write_text("---\nname: foo\n---\n")
        # parent.parent.parent.parent = tmp_path / "a" / "b"
        assert derive_project_dir(skill_path) == tmp_path / "a" / "b"
