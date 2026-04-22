"""Tests for :mod:`clauditor._git`.

The module wraps ``git remote get-url origin`` and ``git symbolic-ref
refs/remotes/origin/HEAD`` with pure helpers that never raise under
documented error conditions. Tests patch ``subprocess.run`` at the
module boundary so no real ``git`` process is invoked.

Traces to DEC-002 and DEC-017 of ``plans/super/77-clauditor-badge.md``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from clauditor._git import get_default_branch, get_repo_slug


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Build a ``CompletedProcess`` for patched ``subprocess.run``."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class TestGetRepoSlug:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://github.com/USER/REPO.git", "USER/REPO"),
            ("https://github.com/USER/REPO", "USER/REPO"),
            ("git@github.com:USER/REPO.git", "USER/REPO"),
            ("git@github.com:USER/REPO", "USER/REPO"),
            ("https://gitlab.com/group/sub/REPO.git", "group/sub/REPO"),
            ("https://gitlab.com/group/sub/REPO", "group/sub/REPO"),
            ("git@gitlab.com:group/sub/REPO.git", "group/sub/REPO"),
            ("https://bitbucket.org/USER/REPO.git", "USER/REPO"),
            # Review pass 3, C3-2: trailing slash survives .git strip.
            ("https://github.com/USER/REPO.git/", "USER/REPO"),
            ("https://github.com/USER/REPO/", "USER/REPO"),
            # Explicit ssh:// scheme — docstring says SSH broadly.
            ("ssh://git@github.com/USER/REPO.git", "USER/REPO"),
            ("ssh://git@github.com/USER/REPO", "USER/REPO"),
            ("git://github.com/USER/REPO", "USER/REPO"),
        ],
    )
    def test_parses_url_shapes(self, url: str, expected: str, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout=f"{url}\n"),
        ):
            assert get_repo_slug(tmp_path) == expected

    @pytest.mark.parametrize(
        "url",
        [
            # Review pass 3, C3-2: single-component slugs are rejected.
            "https://github.com/USER",
            "https://github.com/USER/",
            "git@github.com:USER",
        ],
    )
    def test_rejects_single_component_slugs(
        self, url: str, tmp_path: Path
    ) -> None:
        """A slug without a ``/`` cannot form a valid raw-content URL."""
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout=f"{url}\n"),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_when_git_not_installed(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run", side_effect=FileNotFoundError()
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_when_not_a_repo(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=128,
                stdout="",
                stderr="fatal: not a git repository\n",
            ),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_when_no_origin_remote(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: No such remote 'origin'\n",
            ),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_on_empty_output(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="\n"),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_on_unknown_url_shape(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="some-weird-protocol://whatever\n"),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_returns_none_on_generic_subprocess_error(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            assert get_repo_slug(tmp_path) is None

    def test_passes_cwd_as_string(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="https://github.com/USER/REPO\n"),
        ) as mock_run:
            get_repo_slug(tmp_path)
            assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)

    def test_never_raises_on_any_documented_error(self, tmp_path: Path) -> None:
        """Smoke test: every documented error path returns ``None`` cleanly."""
        for side in (
            FileNotFoundError(),
            subprocess.TimeoutExpired(cmd="git", timeout=10),
            subprocess.SubprocessError("boom"),
        ):
            with patch("clauditor._git.subprocess.run", side_effect=side):
                # Would raise if the helper is not defensive enough.
                assert get_repo_slug(tmp_path) is None


class TestGetDefaultBranch:
    @pytest.mark.parametrize(
        "output, expected",
        [
            ("refs/remotes/origin/main\n", "main"),
            ("refs/remotes/origin/master\n", "master"),
            ("refs/remotes/origin/dev\n", "dev"),
            ("refs/remotes/origin/release-1.2\n", "release-1.2"),
        ],
    )
    def test_parses_symbolic_ref_output(
        self, output: str, expected: str, tmp_path: Path
    ) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout=output),
        ):
            assert get_default_branch(tmp_path) == expected

    def test_returns_none_when_git_not_installed(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run", side_effect=FileNotFoundError()
        ):
            assert get_default_branch(tmp_path) is None

    def test_returns_none_when_no_origin_head(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="fatal: ref refs/remotes/origin/HEAD is not a symbolic ref\n",
            ),
        ):
            assert get_default_branch(tmp_path) is None

    def test_returns_none_on_unexpected_output(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="unexpected\n"),
        ):
            assert get_default_branch(tmp_path) is None

    def test_returns_none_on_empty_output(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="\n"),
        ):
            assert get_default_branch(tmp_path) is None

    def test_returns_none_on_prefix_only_output(self, tmp_path: Path) -> None:
        """Output matches the prefix exactly with no trailing branch."""
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="refs/remotes/origin/\n"),
        ):
            assert get_default_branch(tmp_path) is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            assert get_default_branch(tmp_path) is None

    def test_returns_none_on_generic_subprocess_error(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            assert get_default_branch(tmp_path) is None

    def test_passes_cwd_as_string(self, tmp_path: Path) -> None:
        with patch(
            "clauditor._git.subprocess.run",
            return_value=_completed(stdout="refs/remotes/origin/main\n"),
        ) as mock_run:
            get_default_branch(tmp_path)
            assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)

    def test_never_raises_on_any_documented_error(self, tmp_path: Path) -> None:
        """Smoke test: every documented error path returns ``None`` cleanly."""
        for side in (
            FileNotFoundError(),
            subprocess.TimeoutExpired(cmd="git", timeout=10),
            subprocess.SubprocessError("boom"),
        ):
            with patch("clauditor._git.subprocess.run", side_effect=side):
                assert get_default_branch(tmp_path) is None
