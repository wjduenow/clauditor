"""Tests for wheel packaging of the ``clauditor.skills`` subpackage."""

from __future__ import annotations

import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _project_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


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
        pytest.fail(
            f"uv build failed (rc={result.returncode}):\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    wheels = list(out_dir.glob("clauditor_eval-*.whl"))
    if not wheels:
        pytest.fail(f"uv build succeeded but no wheel produced in {out_dir}")
    return wheels[0]


@pytest.fixture(scope="module")
def wheel_namelist(built_wheel: Path) -> list[str]:
    with zipfile.ZipFile(built_wheel) as zf:
        return zf.namelist()


class TestWheelPackaging:
    def test_wheel_contains_skills_subpackage(self, wheel_namelist: list[str]) -> None:
        assert "clauditor/skills/__init__.py" in wheel_namelist

    def test_wheel_contains_bundled_skill_md(self, wheel_namelist: list[str]) -> None:
        assert "clauditor/skills/clauditor/SKILL.md" in wheel_namelist

    def test_wheel_contains_bundled_eval_json(
        self, wheel_namelist: list[str]
    ) -> None:
        assert (
            "clauditor/skills/clauditor/assets/clauditor.eval.json"
            in wheel_namelist
        )

    def test_wheel_excludes_pycache(self, wheel_namelist: list[str]) -> None:
        offenders = [name for name in wheel_namelist if "__pycache__" in name]
        assert offenders == [], f"wheel contains __pycache__ entries: {offenders}"

    def test_wheel_skill_md_has_stamped_version(self, built_wheel: Path) -> None:
        version = _project_version()
        with zipfile.ZipFile(built_wheel) as zf:
            skill_md = zf.read(
                "clauditor/skills/clauditor/SKILL.md"
            ).decode("utf-8")
        expected_line = f'clauditor-version: "{version}"'
        assert expected_line in skill_md, (
            f"expected {expected_line!r} in wheel SKILL.md, got:\n{skill_md[:400]}"
        )
        # Defense-in-depth: the unstamped placeholder must NOT survive.
        assert 'clauditor-version: "0.0.0-dev"' not in skill_md

    def test_source_skill_md_remains_dev_placeholder(self) -> None:
        src_skill = PROJECT_ROOT / "src/clauditor/skills/clauditor/SKILL.md"
        assert 'clauditor-version: "0.0.0-dev"' in src_skill.read_text(
            encoding="utf-8"
        )
