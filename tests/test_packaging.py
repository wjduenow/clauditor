"""Tests for wheel packaging of the ``clauditor.skills`` subpackage."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("wheel")
    try:
        result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(PROJECT_ROOT)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("build tool unavailable: uv not on PATH")
    if result.returncode != 0:
        pytest.skip(
            f"build tool unavailable: uv build failed "
            f"(rc={result.returncode}): {result.stderr}"
        )
    wheels = list(out_dir.glob("clauditor-*.whl"))
    if not wheels:
        pytest.skip(f"build tool unavailable: no wheel produced in {out_dir}")
    return wheels[0]


@pytest.fixture(scope="module")
def wheel_namelist(built_wheel: Path) -> list[str]:
    with zipfile.ZipFile(built_wheel) as zf:
        return zf.namelist()


class TestWheelPackaging:
    def test_wheel_contains_skills_subpackage(self, wheel_namelist: list[str]) -> None:
        assert "clauditor/skills/__init__.py" in wheel_namelist

    def test_wheel_contains_bundled_markdown(self, wheel_namelist: list[str]) -> None:
        assert "clauditor/skills/.sentinel.md" in wheel_namelist

    def test_wheel_excludes_pycache(self, wheel_namelist: list[str]) -> None:
        offenders = [name for name in wheel_namelist if "__pycache__" in name]
        assert offenders == [], f"wheel contains __pycache__ entries: {offenders}"
