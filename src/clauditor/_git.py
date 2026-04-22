"""Internal git metadata helpers for ``clauditor badge --url-only``.

This module is private (underscore-prefixed) and exists specifically
to support the badge CLI's repo auto-detection path. Two pure
helpers wrap :func:`subprocess.run` calls to ``git``:

- :func:`get_repo_slug` — parses ``git remote get-url origin`` into a
  ``"USER/REPO"`` string. Handles HTTPS, SSH, and custom git hosts
  (github.com, gitlab.com including nested groups, bitbucket.org,
  self-hosted installations).
- :func:`get_default_branch` — parses ``git symbolic-ref
  refs/remotes/origin/HEAD`` into the default branch name.

Both helpers return ``None`` instead of raising under any documented
error condition (git not installed, not a git repository, no origin
remote, timeout, parse failure on unknown URL shape). The CLI
translates ``None`` into the ``USER/REPO/main`` placeholder fallback
per DEC-002 of ``plans/super/77-clauditor-badge.md``.

Neither helper raises. Callers can treat a ``None`` return as "git
metadata unavailable; fall through to placeholders".
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

__all__ = ["get_default_branch", "get_repo_slug"]

# Protect against hung ``git`` invocations. Ten seconds is generous
# for what should be cheap local metadata lookups but tight enough
# that a wedged subprocess cannot stall the badge command.
_GIT_TIMEOUT_SECONDS = 10

# SSH-style URL: [ssh://]git@host[:port]:path/to/repo[.git]  (scp-like)
_SSH_URL_RE = re.compile(r"^[^@]+@[^:]+:(?P<slug>.+?)(?:\.git)?/?$")

# HTTPS/HTTP/SSH URL: scheme://[user@]host[:port]/path/to/repo[.git]
_URL_RE = re.compile(
    r"^(?:https?|ssh|git)://[^/]+/(?P<slug>.+?)(?:\.git)?/?$"
)


def _parse_remote_url(url: str) -> str | None:
    """Return the ``USER/REPO`` slug for a remote URL, or ``None``.

    Supports:

    - ``https://host/path/to/repo[.git]`` (HTTPS, any host, nested
      group paths like gitlab's ``group/sub/repo``).
    - ``ssh://[user@]host/path/to/repo[.git]`` (explicit SSH scheme).
    - ``git@host:path/to/repo[.git]`` (scp-like SSH).
    - Trailing slash after the path is stripped before matching.

    Returns ``None`` for:

    - Unrecognized URL shapes.
    - Single-path-component slugs like ``USER`` alone (review pass 3,
      C3-2 — a valid GitHub/GitLab slug has at least one ``/`` between
      owner and repo).
    """
    url = url.strip()
    if not url:
        return None

    for pattern in (_URL_RE, _SSH_URL_RE):
        match = pattern.match(url)
        if match is not None:
            slug = match.group("slug").strip("/")
            # A valid slug carries at least one ``/`` (owner/repo or
            # group/.../repo). Single-component slugs produce broken
            # shields.io URLs.
            if slug and "/" in slug:
                return slug

    return None


def _parse_symbolic_ref(output: str) -> str | None:
    """Return the branch name from ``git symbolic-ref`` output.

    Input shape: ``refs/remotes/origin/<branch>\\n``. Returns the
    trailing component, or ``None`` when the shape does not match.
    """
    line = output.strip()
    prefix = "refs/remotes/origin/"
    if not line.startswith(prefix):
        return None
    branch = line[len(prefix) :]
    if not branch:
        return None
    return branch


def get_repo_slug(cwd: Path) -> str | None:
    """Return ``"USER/REPO"`` from the origin remote, or ``None``.

    Invokes ``git remote get-url origin`` in ``cwd`` and parses the
    output. Returns ``None`` when:

    - ``git`` is not installed (``FileNotFoundError``),
    - the invocation fails (non-zero exit — not a repo, no origin),
    - the invocation times out,
    - any other ``subprocess.SubprocessError`` fires,
    - the URL shape is unrecognized.

    Never raises under any of the above conditions.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return None
    except subprocess.SubprocessError:
        return None

    if result.returncode != 0:
        return None

    return _parse_remote_url(result.stdout)


def get_default_branch(cwd: Path) -> str | None:
    """Return the default branch name, or ``None``.

    Invokes ``git symbolic-ref refs/remotes/origin/HEAD`` in ``cwd``
    and parses the output. Returns ``None`` under the same error set
    as :func:`get_repo_slug` (git missing, non-zero exit, timeout,
    generic subprocess error, unexpected output shape).

    Never raises under any of the above conditions.
    """
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return None
    except subprocess.SubprocessError:
        return None

    if result.returncode != 0:
        return None

    return _parse_symbolic_ref(result.stdout)
