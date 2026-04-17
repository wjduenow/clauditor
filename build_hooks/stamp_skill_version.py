"""Hatchling custom build hook: stamp ``metadata.clauditor-version`` in SKILL.md.

At wheel build time, substitute the real ``[project].version`` from
``pyproject.toml`` into the bundled
``src/clauditor/skills/clauditor/SKILL.md`` YAML frontmatter. The
source-tree file is never mutated; the stamped content is emitted to a
temporary file and routed into the wheel via ``build_data["force_include"]``,
which also reserves the target path so the original source-tree file is
not double-packaged.

Traces to: DEC-005 of ``plans/super/43-setup-slash-command.md``.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.plugin import hookimpl

SKILL_MD_RELATIVE = Path("src/clauditor/skills/clauditor/SKILL.md")
WHEEL_TARGET_PATH = "src/clauditor/skills/clauditor/SKILL.md"
VERSION_LINE_PATTERN = re.compile(r'clauditor-version:\s*"[^"]*"')


def _read_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        version = data["project"]["version"]
    except KeyError as exc:
        raise RuntimeError(
            f"stamp_skill_version: could not find [project].version in "
            f"{pyproject_path}"
        ) from exc
    if not isinstance(version, str) or not version:
        raise RuntimeError(
            f"stamp_skill_version: [project].version in {pyproject_path} "
            f"must be a non-empty string (got {version!r})"
        )
    return version


def _stamp_version(skill_md_text: str, version: str) -> str:
    new_text, n = VERSION_LINE_PATTERN.subn(
        f'clauditor-version: "{version}"', skill_md_text, count=1
    )
    if n == 0:
        raise RuntimeError(
            "stamp_skill_version: did not find `clauditor-version: \"...\"` "
            "line to substitute in bundled SKILL.md"
        )
    return new_text


class StampSkillVersionHook(BuildHookInterface):
    """Substitute the real project version into the bundled SKILL.md."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        pyproject_path = root / "pyproject.toml"
        skill_md_path = root / SKILL_MD_RELATIVE

        if not skill_md_path.is_file():
            raise RuntimeError(
                f"stamp_skill_version: bundled SKILL.md not found at "
                f"{skill_md_path}"
            )

        project_version = _read_project_version(pyproject_path)
        original_text = skill_md_path.read_text(encoding="utf-8")
        stamped_text = _stamp_version(original_text, project_version)

        # Write the stamped copy to a temporary file and force-include it at
        # the intended wheel path. Hatchling reserves that target path so the
        # unstamped source-tree file is NOT also packaged. The source tree is
        # never mutated.
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix="-SKILL.md",
            prefix="clauditor-stamped-",
            delete=False,
        )
        try:
            tmp.write(stamped_text)
        finally:
            tmp.close()

        force_include = build_data.setdefault("force_include", {})
        force_include[tmp.name] = WHEEL_TARGET_PATH


@hookimpl
def hatch_register_build_hook() -> type[BuildHookInterface]:
    return StampSkillVersionHook
