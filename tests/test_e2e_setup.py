"""E2E round-trip: ``uv build --wheel`` → ``uv pip install`` → ``clauditor
setup`` → assertions → ``--unlink``.

This is the only test in the suite that exercises the full bundled-skill
install path: the wheel's ``stamp_skill_version`` build hook, the
``importlib.resources`` seam in ``cli/setup.py``, real ``os.symlink``
creation, and the round-trip unlink. Covers the gap between
``tests/test_packaging.py`` (inspects the wheel as a ZIP) and
``tests/test_setup.py`` (pure ``plan_setup`` logic).

Per plan ``plans/super/55-packaging-setup-e2e.md``, DEC-001..009.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

# DEC-001: every test in this file is slow (wheel build + venv + subprocess).
# DEC-006: Windows skipif — production ``cli/setup.py`` uses bare
# ``os.symlink`` which requires admin/dev-mode on Windows.
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "clauditor setup uses os.symlink; Windows requires admin or "
            "developer mode — unsupported until a non-symlink fallback lands."
        ),
    ),
]

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd``; raise with diagnostic output on failure."""
    result = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n"
            f"  cwd={cwd}\n"
            f"  returncode={result.returncode}\n"
            f"  stdout={result.stdout}\n"
            f"  stderr={result.stderr}"
        )
    return result


def _venv_python(venv_dir: Path) -> Path:
    """Venv's python executable (POSIX only; Windows is skipped)."""
    return venv_dir / "bin" / "python"


def _venv_clauditor(venv_dir: Path) -> Path:
    """Venv's ``clauditor`` script (the real user entry point)."""
    return venv_dir / "bin" / "clauditor"


def _expected_version() -> str:
    """Read ``[project].version`` from ``pyproject.toml``.

    DEC-004 #4: never hard-code the version string.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def _scratch_env(scratch_home: Path) -> dict[str, str]:
    """Strict subprocess env whitelist (DEC-007).

    ``HOME`` is redirected so the production ``find_project_root`` home-
    exclusion walk is not influenced by the developer's real home dir.
    No ``CLAUDITOR_*``, ``ANTHROPIC_*``, or ``PYTHONPATH`` is inherited.
    """
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(scratch_home),
        "USER": os.environ.get("USER", "tester"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR")
        or str(scratch_home / ".uv-cache"),
    }


@pytest.fixture(scope="session")
def e2e_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build wheel + create venv + install (DEC-002 session-scoped).

    DEC-008: hard-require ``uv`` on PATH — missing uv raises (becomes a
    test error), never a skip.
    """
    # DEC-008: uv presence check — fail hard if missing.
    probe = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "uv is required for E2E tests (DEC-008). "
            f"`uv --version` returned {probe.returncode}. "
            f"stdout={probe.stdout!r} stderr={probe.stderr!r}"
        )

    root = tmp_path_factory.mktemp("e2e-venv")
    wheel_dir = root / "wheels"
    wheel_dir.mkdir()
    venv_dir = root / "venv"

    _run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=REPO_ROOT,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel in {wheel_dir}, got {wheels}")

    _run(["uv", "venv", str(venv_dir)])
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(_venv_python(venv_dir)),
            str(wheels[0]),
        ]
    )
    return venv_dir


@pytest.fixture
def scratch_project(tmp_path: Path) -> Path:
    """Per-test fresh scratch project dir (DEC-002)."""
    project = tmp_path / "project"
    project.mkdir()
    return project


# DEC-003: parametrize ``.git`` as empty file (git-worktree style) + ``.claude``
# as empty dir. ``.claude``-as-file is NOT parametrized — production code
# rejects it.
_MARKER_PARAMS = [
    pytest.param(
        lambda project: (project / ".git").write_text(""),
        id="git-worktree-file",
    ),
    pytest.param(
        lambda project: (project / ".claude").mkdir(),
        id="claude-dir",
    ),
]


@pytest.mark.parametrize("make_marker", _MARKER_PARAMS)
def test_setup_roundtrip(
    e2e_venv: Path,
    scratch_project: Path,
    make_marker: Callable[[Path], None],
    tmp_path: Path,
) -> None:
    """Full positive-path assertion stack per DEC-004."""
    make_marker(scratch_project)
    env = _scratch_env(scratch_home=tmp_path)
    clauditor = _venv_clauditor(e2e_venv)
    symlink = scratch_project / ".claude" / "skills" / "clauditor"

    # Assertion 1: ``clauditor setup`` exits 0.
    setup = subprocess.run(
        [str(clauditor), "setup"],
        cwd=scratch_project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, (
        f"clauditor setup failed: stdout={setup.stdout!r} "
        f"stderr={setup.stderr!r}"
    )

    # Assertion 2: symlink exists (NOT ``.exists()`` — that follows the
    # link and passes on broken targets).
    assert symlink.is_symlink(), f"{symlink} is not a symlink"

    # Assertion 3: target is inside the venv + is the ``clauditor`` skill dir.
    target = symlink.resolve()
    assert target.is_relative_to(e2e_venv), (
        f"symlink target {target} is not inside venv {e2e_venv}"
    )
    assert target.name == "clauditor", (
        f"symlink target leaf name is {target.name!r}, expected 'clauditor'"
    )
    assert (target / "SKILL.md").is_file(), (
        f"SKILL.md not found under symlink target {target}"
    )

    # Assertion 4: stamped version matches pyproject, no dev placeholder.
    skill_md_text = (symlink / "SKILL.md").read_text()
    expected = _expected_version()
    assert f'clauditor-version: "{expected}"' in skill_md_text, (
        f"SKILL.md missing stamped version {expected!r}. "
        f"First 500 chars:\n{skill_md_text[:500]}"
    )
    assert "0.0.0-dev" not in skill_md_text, (
        "SKILL.md still contains '0.0.0-dev' — stamp hook did not run"
    )

    # Assertion 5: ``clauditor setup --unlink`` removes the symlink.
    unlink = subprocess.run(
        [str(clauditor), "setup", "--unlink"],
        cwd=scratch_project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert unlink.returncode == 0, (
        f"clauditor setup --unlink failed: stdout={unlink.stdout!r} "
        f"stderr={unlink.stderr!r}"
    )
    assert not symlink.is_symlink(), (
        f"{symlink} is still a symlink after --unlink"
    )
    assert not symlink.exists(), (
        f"{symlink} still exists after --unlink"
    )


def test_setup_no_marker_exits_2(
    e2e_venv: Path,
    scratch_project: Path,
    tmp_path: Path,
) -> None:
    """Negative: no ``.git``/``.claude`` marker → exit 2 (DEC-009).

    Asserts exit code only; stderr content is UI and is not part of
    the contract.
    """
    env = _scratch_env(scratch_home=tmp_path)
    clauditor = _venv_clauditor(e2e_venv)

    result = subprocess.run(
        [str(clauditor), "setup"],
        cwd=scratch_project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
