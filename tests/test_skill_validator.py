"""Tests for the fallback SKILL.md frontmatter validator.

The canonical CI validator is the upstream ``skills-ref`` package; this
fallback at ``scripts/validate_skill_frontmatter.py`` exists because
``skills-ref`` is stricter than our hybrid frontmatter (DEC-004 of
``plans/super/43-setup-slash-command.md``) and rejects the Claude Code
extension fields we legitimately ship. These tests guard the fallback's
core-spec checks: name shape, description length, parent-dir match,
frontmatter delimiter presence.

Each test spawns the script as a subprocess via ``sys.executable`` to
faithfully exercise the same entry point CI invokes, and uses
``tmp_path`` fixtures to author disposable skill dirs so the real
bundled skill is never at risk of being mutated by a failing test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "validate_skill_frontmatter.py"
)

VALID_FRONTMATTER = """\
---
name: myskill
description: A valid test skill for exercising the fallback validator.
---

# Body text
"""


def _write_skill(skill_dir: Path, name: str, body: str) -> Path:
    """Create a ``<skill_dir>/<name>/SKILL.md`` with the given body and return
    the skill directory path."""
    d = skill_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def _run(skill_dir: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the validator script as a subprocess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(skill_dir)],
        capture_output=True,
        text=True,
    )


class TestValidator:
    def test_valid_skill_exits_zero(self, tmp_path: Path) -> None:
        d = _write_skill(tmp_path, "myskill", VALID_FRONTMATTER)
        result = _run(d)
        assert result.returncode == 0, result.stderr
        assert "frontmatter OK" in result.stdout

    def test_real_bundled_skill_exits_zero(self) -> None:
        """Sanity: the script passes on the actual shipped skill."""
        bundled = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "clauditor"
            / "skills"
            / "clauditor"
        )
        result = _run(bundled)
        assert result.returncode == 0, result.stderr

    def test_missing_name_exits_one(self, tmp_path: Path) -> None:
        body = (
            "---\n"
            "description: A skill with no name field.\n"
            "---\n\n"
            "# Body\n"
        )
        d = _write_skill(tmp_path, "myskill", body)
        result = _run(d)
        assert result.returncode == 1
        assert "'name' field missing" in result.stderr

    def test_name_does_not_match_parent_dir_exits_one(
        self, tmp_path: Path
    ) -> None:
        body = (
            "---\n"
            "name: wrongname\n"
            "description: Name does not equal parent directory.\n"
            "---\n\n"
            "# Body\n"
        )
        d = _write_skill(tmp_path, "actualname", body)
        result = _run(d)
        assert result.returncode == 1
        assert "does not match parent directory name" in result.stderr

    def test_name_with_uppercase_exits_one(self, tmp_path: Path) -> None:
        body = (
            "---\n"
            "name: BadName\n"
            "description: Uppercase in name violates the spec regex.\n"
            "---\n\n"
            "# Body\n"
        )
        d = _write_skill(tmp_path, "BadName", body)
        result = _run(d)
        assert result.returncode == 1
        assert "does not match" in result.stderr

    def test_description_over_limit_exits_one(self, tmp_path: Path) -> None:
        long_desc = "x" * 1025
        body = f"---\nname: myskill\ndescription: {long_desc}\n---\n\n# Body\n"
        d = _write_skill(tmp_path, "myskill", body)
        result = _run(d)
        assert result.returncode == 1
        assert "'description' length 1025" in result.stderr

    def test_missing_description_exits_one(self, tmp_path: Path) -> None:
        body = "---\nname: myskill\n---\n\n# Body\n"
        d = _write_skill(tmp_path, "myskill", body)
        result = _run(d)
        assert result.returncode == 1
        assert "'description' field missing" in result.stderr

    def test_no_frontmatter_delimiters_exits_one(self, tmp_path: Path) -> None:
        body = "# Just a markdown file, no frontmatter at all\n"
        d = _write_skill(tmp_path, "myskill", body)
        result = _run(d)
        assert result.returncode == 1
        assert "missing opening frontmatter delimiter" in result.stderr

    def test_missing_closing_delimiter_exits_one(self, tmp_path: Path) -> None:
        body = "---\nname: myskill\ndescription: No closing delimiter.\n\n# Body\n"
        d = _write_skill(tmp_path, "myskill", body)
        result = _run(d)
        assert result.returncode == 1
        assert "missing closing frontmatter delimiter" in result.stderr

    def test_skill_md_missing_exits_one(self, tmp_path: Path) -> None:
        d = tmp_path / "myskill"
        d.mkdir()
        result = _run(d)
        assert result.returncode == 1
        assert "SKILL.md not found" in result.stderr

    def test_non_directory_target_exits_one(self, tmp_path: Path) -> None:
        target = tmp_path / "not-a-dir.txt"
        target.write_text("hello")
        result = _run(target)
        assert result.returncode == 1
        assert "not a directory" in result.stderr

    def test_no_arguments_exits_two(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "usage:" in result.stderr

    def test_empty_description_exits_one(self, tmp_path: Path) -> None:
        body = "---\nname: myskill\ndescription: \"\"\n---\n\n# Body\n"
        d = _write_skill(tmp_path, "myskill", body)
        result = _run(d)
        assert result.returncode == 1
        assert "'description' must be a non-empty string" in result.stderr

    def test_empty_name_exits_one(self, tmp_path: Path) -> None:
        # An empty-string `name` should fail the non-empty check. The parent
        # dir is named "skill" so name-mismatch might also fire — we only
        # assert on the non-empty message.
        body = '---\nname: ""\ndescription: Valid description.\n---\n\n# Body\n'
        d = tmp_path / "skill"
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        result = _run(d)
        assert result.returncode == 1
        assert "'name' must be a non-empty string" in result.stderr
