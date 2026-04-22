"""Tests for ``clauditor badge`` (#77 US-004 — DEC-025 exit codes).

CLI integration tests that wire the US-001 / US-002 / US-003 helpers
together. The tests use ``tmp_path`` + ``monkeypatch.chdir`` to pin
``Path.cwd()`` per-test, and ``unittest.mock.patch`` on
``clauditor._git.get_repo_slug`` / ``get_default_branch`` to exercise
the ``--url-only`` detect/override branches without touching any real
``git`` process.

Traces to DEC-001, DEC-002, DEC-005, DEC-006, DEC-011, DEC-014,
DEC-015, DEC-016, DEC-018, DEC-021, DEC-022, DEC-023, DEC-025,
DEC-026.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.cli import main
from clauditor.quality_grader import GradingReport, GradingResult
from clauditor.schemas import GradeThresholds

# ---------------------------------------------------------------------------
# Test helpers — skill staging + iteration sidecar fixtures.
# ---------------------------------------------------------------------------


def _write_skill(tmp_path: Path, name: str = "demo") -> Path:
    """Stage a SKILL.md under ``<tmp_path>/.claude/skills/<name>/SKILL.md``.

    Uses the modern layout so ``SkillSpec.from_file`` derives the
    ``skill_name`` from the frontmatter ``name:`` field (falling back
    to the parent dir name — both produce ``name``).
    """
    skill_dir = tmp_path / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\n---\n# {name.title()}\n\nA demo skill.\n"
    )
    return skill_md


def _write_assertions_sidecar(
    iter_skill_dir: Path,
    *,
    passed: int = 3,
    total: int = 3,
) -> None:
    """Write an ``assertions.json`` under ``iter_skill_dir``.

    Uses the flat :meth:`AssertionSet.to_json` shape — the badge
    module accepts both the flat and modern ``runs`` layouts.
    """
    results = [
        AssertionResult(
            name=f"check_{i}",
            passed=i < passed,
            message="ok" if i < passed else "fail",
            kind="presence",
        )
        for i in range(total)
    ]
    aset = AssertionSet(results=results)
    (iter_skill_dir / "assertions.json").write_text(json.dumps(aset.to_json()))


def _write_grading_sidecar(
    iter_skill_dir: Path,
    *,
    passed: bool = True,
    score: float = 0.9,
) -> None:
    """Write a ``grading.json`` under ``iter_skill_dir`` via GradingReport."""
    results = [
        GradingResult(
            id="c0",
            criterion="is it good",
            passed=passed,
            score=score,
            evidence="",
            reasoning="",
        )
    ]
    report = GradingReport(
        skill_name="demo",
        results=results,
        model="test-model",
        thresholds=GradeThresholds(),
        metrics={},
        duration_seconds=1.0,
        input_tokens=100,
        output_tokens=50,
    )
    (iter_skill_dir / "grading.json").write_text(report.to_json())


def _setup_iteration(
    tmp_path: Path,
    iter_num: int,
    skill_name: str = "demo",
    *,
    write_assertions: bool = True,
    write_grading: bool = False,
    l1_all_pass: bool = True,
) -> Path:
    """Stage an iteration dir + sidecars under ``tmp_path``.

    Returns the per-skill iteration dir path. Callers write any
    additional sidecars (``variance.json``) directly using that path.
    """
    iter_skill_dir = (
        tmp_path / ".clauditor" / f"iteration-{iter_num}" / skill_name
    )
    iter_skill_dir.mkdir(parents=True)
    if write_assertions:
        _write_assertions_sidecar(
            iter_skill_dir,
            passed=3 if l1_all_pass else 2,
            total=3,
        )
    if write_grading:
        _write_grading_sidecar(iter_skill_dir)
    return iter_skill_dir


# ---------------------------------------------------------------------------
# Happy-path writes (DEC-026 pure-compute composition sanity checks).
# ---------------------------------------------------------------------------


class TestCmdBadgeHappyPath:
    """Code path 4 — iteration found, sidecars loaded, write the JSON."""

    def test_writes_badge_json_default_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Default output lands at ``.clauditor/badges/<skill>.json``."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 0

        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        assert target.exists()
        data = json.loads(target.read_text())
        # Shields.io contract keys + our nested extension.
        assert data["schemaVersion"] == 1
        assert data["label"] == "clauditor"
        assert data["color"] == "brightgreen"
        assert data["message"] == "3/3"
        assert data["clauditor"]["skill_name"] == "demo"
        assert data["clauditor"]["iteration"] == 1

    def test_writes_badge_json_custom_output(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """``--output PATH`` writes there (DEC-005 accepts absolute paths)."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        out_dir = tmp_path / "custom"
        out_dir.mkdir()
        target = out_dir / "my-badge.json"

        rc = main(["badge", str(skill_md), "--output", str(target)])
        assert rc == 0
        assert target.exists()

        default_target = tmp_path / ".clauditor" / "badges" / "demo.json"
        assert not default_target.exists()

    def test_verbose_prints_success_line(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """DEC-018: ``--verbose`` emits ``wrote {path} (iteration N)``."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 7)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--verbose"])
        assert rc == 0

        err = capsys.readouterr().err
        assert "clauditor.badge: wrote" in err
        assert "(iteration 7)" in err

    def test_happy_path_with_grading_sidecar(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Grading present → L3 fragment lands in the message."""
        skill_md = _write_skill(tmp_path)
        iter_dir = _setup_iteration(tmp_path, 1, write_grading=True)
        assert iter_dir.exists()
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 0

        data = json.loads(
            (tmp_path / ".clauditor" / "badges" / "demo.json").read_text()
        )
        assert "L3" in data["message"]


# ---------------------------------------------------------------------------
# DEC-001 — no iteration → lightgrey placeholder, exit 0.
# ---------------------------------------------------------------------------


class TestCmdBadgeNoIteration:
    def test_writes_lightgrey_placeholder_exit_0(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 0

        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        assert target.exists()
        data = json.loads(target.read_text())
        assert data["color"] == "lightgrey"
        assert data["message"] == "no data"
        assert data["clauditor"]["iteration"] is None

    def test_emits_dec021_warning(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 0

        err = capsys.readouterr().err
        assert "no iteration found for skill demo" in err
        assert "lightgrey placeholder" in err

    def test_placeholder_respects_force(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """DEC-011 exception: lightgrey placeholder does NOT clobber
        without --force (a stale real badge is better than silent
        clobber on a misfire)."""
        skill_md = _write_skill(tmp_path)
        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"pre-existing": true}\n')
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 1

        err = capsys.readouterr().err
        assert "already exists" in err
        # Pre-existing content untouched.
        assert target.read_text() == '{"pre-existing": true}\n'

    def test_placeholder_with_force_overwrites(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        skill_md = _write_skill(tmp_path)
        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"pre-existing": true}\n')
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--force"])
        assert rc == 0
        data = json.loads(target.read_text())
        assert data["color"] == "lightgrey"


# ---------------------------------------------------------------------------
# DEC-016 — explicit --from-iteration N that is missing.
# ---------------------------------------------------------------------------


class TestCmdBadgeExplicitIterationMissing:
    def test_exit_1_with_available_list(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        _setup_iteration(tmp_path, 3)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--from-iteration", "42"])
        assert rc == 1

        err = capsys.readouterr().err
        assert "iteration 42 not found for skill demo" in err
        # Available iteration numbers rendered in ascending order.
        assert "1, 3" in err

    def test_exit_1_with_no_available_message(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        # No iterations exist at all.
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--from-iteration", "1"])
        assert rc == 1

        err = capsys.readouterr().err
        assert "iteration 1 not found for skill demo" in err
        assert "none" in err

    def test_non_integer_from_iteration_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """``--from-iteration abc`` fails argparse ``_positive_int`` validation.

        argparse raises ``SystemExit(2)`` directly with its own error
        format; the SkillSpec load never happens (review pass 1, B-2
        — validate before paying the load cost).
        """
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            main(["badge", str(skill_md), "--from-iteration", "abc"])
        assert excinfo.value.code == 2

        err = capsys.readouterr().err
        # argparse's own error message references the flag and the value.
        assert "--from-iteration" in err
        assert "'abc'" in err

    def test_zero_from_iteration_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """``--from-iteration 0`` also rejected by ``_positive_int``."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            main(["badge", str(skill_md), "--from-iteration", "0"])
        assert excinfo.value.code == 2

        err = capsys.readouterr().err
        assert "--from-iteration" in err
        assert "must be >= 1" in err

    def test_url_only_with_missing_explicit_iteration_exits_1(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Precedence anchor: DEC-016 wins over ``--url-only`` (review pass
        2, N2-2). An explicit ``--from-iteration N`` that does not exist
        is an input error even in URL-only mode — the user asked for a
        specific iteration and should be told it isn't there.
        """
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(
            [
                "badge",
                str(skill_md),
                "--url-only",
                "--from-iteration",
                "9999",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "iteration 9999 not found" in err


# ---------------------------------------------------------------------------
# DEC-008 — corrupt iteration (assertions.json missing).
# ---------------------------------------------------------------------------


class TestCmdBadgeCorruptIteration:
    def test_assertions_missing_exit_1(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        # Iteration dir exists, but no assertions.json
        _setup_iteration(tmp_path, 1, write_assertions=False)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 1

        err = capsys.readouterr().err
        assert "iteration 1 for skill demo is corrupt" in err
        assert "assertions.json is missing" in err


# ---------------------------------------------------------------------------
# DEC-011 — force overwrite policy.
# ---------------------------------------------------------------------------


class TestCmdBadgeForceOverwrite:
    def test_existing_without_force_exit_1(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"preserved": true}\n')
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 1

        err = capsys.readouterr().err
        assert "already exists" in err
        assert target.read_text() == '{"preserved": true}\n'

    def test_existing_with_force_overwrites(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"preserved": true}\n')
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--force"])
        assert rc == 0
        data = json.loads(target.read_text())
        assert data["color"] == "brightgreen"


# ---------------------------------------------------------------------------
# DEC-014 — --url-only and --output mutually exclusive.
# ---------------------------------------------------------------------------


class TestCmdBadgeMutualExclusion:
    def test_url_only_and_output_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "badge",
                str(skill_md),
                "--url-only",
                "--output",
                str(tmp_path / "foo.json"),
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "--url-only and --output are mutually exclusive" in err


# ---------------------------------------------------------------------------
# DEC-022 — --output parent-dir validation.
# ---------------------------------------------------------------------------


class TestCmdBadgeOutputValidation:
    def test_missing_parent_dir_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        bad_path = tmp_path / "does" / "not" / "exist" / "badge.json"
        rc = main(["badge", str(skill_md), "--output", str(bad_path)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "parent directory does not exist" in err

    def test_absolute_path_accepted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """DEC-005: absolute paths are accepted."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        abs_target = tmp_path / "abs-badge.json"
        rc = main(["badge", str(skill_md), "--output", str(abs_target)])
        assert rc == 0
        assert abs_target.exists()


# ---------------------------------------------------------------------------
# DEC-015 / DEC-023 — --style parsing + validation.
# ---------------------------------------------------------------------------


class TestCmdBadgeStyleValidation:
    @pytest.mark.parametrize(
        "raw, expected_code",
        [
            # Missing separator → exit 2.
            ("foo", 2),
            # Empty key → exit 2.
            ("=value", 2),
            # Empty value (allowed) → exit 0.
            ("style=", 0),
            # Known key → exit 0.
            ("style=flat", 0),
            # Unknown key → exit 0 (warns to stderr).
            ("customKey=value", 0),
        ],
    )
    def test_parsing(
        self,
        tmp_path: Path,
        monkeypatch,
        raw: str,
        expected_code: int,
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--style", raw])
        assert rc == expected_code

    def test_unknown_key_warns_passes_through(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(
            ["badge", str(skill_md), "--style", "customKey=customVal"]
        )
        assert rc == 0

        err = capsys.readouterr().err
        assert "unknown --style key 'customKey'" in err

        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        data = json.loads(target.read_text())
        # Unknown key still emitted (DEC-015 — shields.io ignores it).
        assert data["customKey"] == "customVal"

    def test_multiple_style_flags_all_emitted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "badge",
                str(skill_md),
                "--style",
                "style=flat",
                "--style",
                "cacheSeconds=300",
            ]
        )
        assert rc == 0
        data = json.loads(
            (tmp_path / ".clauditor" / "badges" / "demo.json").read_text()
        )
        assert data["style"] == "flat"
        # cacheSeconds is int-coerced per review pass 3, C3-1.
        assert data["cacheSeconds"] == 300

    def test_control_char_rejects_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        # \x01 is a control character.
        rc = main(["badge", str(skill_md), "--style", "style=flat\x01bad"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "control characters" in err

    def test_overlong_value_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        big = "x" * 600
        rc = main(["badge", str(skill_md), "--style", f"style={big}"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "length 600" in err

    def test_value_with_embedded_equals(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """``--style link=https://foo.com?x=y`` — value containing ``=``.

        The ``split("=", 1)`` handling must preserve the second ``=``
        in the value verbatim (review pass 2, N2-1).
        """
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "badge",
                str(skill_md),
                "--style",
                "link=https://foo.com?x=y",
            ]
        )
        assert rc == 0
        data = json.loads(
            (tmp_path / ".clauditor" / "badges" / "demo.json").read_text()
        )
        assert data["link"] == "https://foo.com?x=y"

    def test_cache_seconds_coerced_to_int(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Review pass 3, C3-1: ``cacheSeconds`` is typed int per shields.io."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "badge",
                str(skill_md),
                "--style",
                "cacheSeconds=300",
            ]
        )
        assert rc == 0
        data = json.loads(
            (tmp_path / ".clauditor" / "badges" / "demo.json").read_text()
        )
        # Native int in the JSON, not string "300".
        assert data["cacheSeconds"] == 300
        assert isinstance(data["cacheSeconds"], int)

    def test_non_numeric_cache_seconds_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Non-integer value for an int-typed style key rejects at parse."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "badge",
                str(skill_md),
                "--style",
                "cacheSeconds=abc",
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "cacheSeconds" in err
        assert "integer" in err


# ---------------------------------------------------------------------------
# --label validation (review pass 1, B-3).
# ---------------------------------------------------------------------------


class TestCmdBadgeLabelValidation:
    """Reject --label values that break Markdown ``![alt](url)`` syntax."""

    @pytest.mark.parametrize(
        "bad_label",
        [
            "broken[label",
            "broken]label",
            "broken(label",
            "broken)label",
            "multi\nline",
            "carriage\rreturn",
        ],
    )
    def test_label_with_markdown_breaking_chars_exits_2(
        self, tmp_path: Path, monkeypatch, capsys, bad_label: str
    ) -> None:
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["badge", str(skill_md), "--label", bad_label])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--label" in err

    def test_overlong_label_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["badge", str(skill_md), "--label", "x" * 600])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--label is too long" in err

    @pytest.mark.parametrize("empty_label", ["", "   ", "\t"])
    def test_empty_label_exits_2(
        self, tmp_path: Path, monkeypatch, capsys, empty_label: str
    ) -> None:
        """Review pass 3, N3-2: empty/whitespace labels rejected.

        Accessibility-hostile ``![](url)`` output is not what users
        intended when they pass ``--label ""``.
        """
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)
        rc = main(["badge", str(skill_md), "--label", empty_label])
        assert rc == 2
        err = capsys.readouterr().err
        assert "must not be empty" in err

    def test_label_with_spaces_and_unicode_accepted(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)
        rc = main(["badge", str(skill_md), "--label", "My Skill — ✓"])
        assert rc == 0


# ---------------------------------------------------------------------------
# DEC-002 — --url-only branches (auto-detect + fallback).
# ---------------------------------------------------------------------------


class TestCmdBadgeUrlOnly:
    def test_explicit_repo_and_branch(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        # --repo and --branch are explicit, so git helpers should not
        # even be called. Patch them to raise if invoked to prove it.
        with patch(
            "clauditor.cli.badge._git.get_repo_slug",
            side_effect=AssertionError("slug should not be queried"),
        ), patch(
            "clauditor.cli.badge._git.get_default_branch",
            side_effect=AssertionError("branch should not be queried"),
        ):
            rc = main(
                [
                    "badge",
                    str(skill_md),
                    "--url-only",
                    "--repo",
                    "acme/widget",
                    "--branch",
                    "dev",
                ]
            )

        assert rc == 0
        out = capsys.readouterr().out
        assert "acme/widget/dev/.clauditor/badges/demo.json" in out

    def test_auto_detect_success(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.badge._git.get_repo_slug",
            return_value="myorg/myrepo",
        ), patch(
            "clauditor.cli.badge._git.get_default_branch",
            return_value="master",
        ):
            rc = main(["badge", str(skill_md), "--url-only"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "myorg/myrepo/master" in captured.out
        # No placeholder warning when auto-detect succeeds.
        assert "placeholder" not in captured.err

    def test_auto_detect_slug_missing_uses_placeholder(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.badge._git.get_repo_slug",
            return_value=None,
        ), patch(
            "clauditor.cli.badge._git.get_default_branch",
            return_value=None,
        ):
            rc = main(["badge", str(skill_md), "--url-only"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "USER/REPO/main" in captured.out
        assert "git auto-detect failed" in captured.err
        assert "USER/REPO/main" in captured.err

    def test_auto_detect_branch_missing_falls_back_to_main(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.badge._git.get_repo_slug",
            return_value="real/slug",
        ), patch(
            "clauditor.cli.badge._git.get_default_branch",
            return_value=None,
        ):
            rc = main(["badge", str(skill_md), "--url-only"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "real/slug/main" in captured.out
        # Slug detected — the user-actionable warning should mention
        # the branch-specific fallback, not the global placeholder.
        assert "default-branch auto-detect failed" in captured.err

    def test_url_only_does_not_write_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.badge._git.get_repo_slug",
            return_value="u/r",
        ), patch(
            "clauditor.cli.badge._git.get_default_branch",
            return_value="main",
        ):
            rc = main(["badge", str(skill_md), "--url-only"])

        assert rc == 0
        assert not (
            tmp_path / ".clauditor" / "badges" / "demo.json"
        ).exists()

    def test_url_only_without_iteration_also_works(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """DEC-001 + --url-only: no sidecar data, still prints the line."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.badge._git.get_repo_slug",
            return_value="u/r",
        ), patch(
            "clauditor.cli.badge._git.get_default_branch",
            return_value="main",
        ):
            rc = main(["badge", str(skill_md), "--url-only"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "https://img.shields.io/endpoint?" in out
        # No badge JSON was written either (--url-only short-circuits).
        assert not (
            tmp_path / ".clauditor" / "badges" / "demo.json"
        ).exists()


# ---------------------------------------------------------------------------
# Skill spec load errors (exit 2).
# ---------------------------------------------------------------------------


class TestCmdBadgeSpecLoad:
    def test_missing_skill_file_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        rc = main(["badge", str(tmp_path / "does-not-exist.md")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "skill file not found" in err

    def test_directory_instead_of_file_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        a_dir = tmp_path / "not-a-file"
        a_dir.mkdir()
        rc = main(["badge", str(a_dir)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "is not a regular file" in err

    def test_skill_load_raises_is_exit_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """``SkillSpec.from_file`` raising maps to exit 2 (DEC-025 input)."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Patch SkillSpec.from_file at the badge module's import site
        # so the except block runs.
        with patch(
            "clauditor.cli.badge.SkillSpec.from_file",
            side_effect=ValueError("bad eval json"),
        ):
            rc = main(["badge", str(skill_md)])

        assert rc == 2
        err = capsys.readouterr().err
        assert "could not load skill spec" in err
        assert "bad eval json" in err


# ---------------------------------------------------------------------------
# DEC-007 — iteration present, zero L1 assertions → lightgrey + warn.
# ---------------------------------------------------------------------------


class TestCmdBadgeZeroL1Assertions:
    def test_zero_assertions_emits_dec007_warning(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """An iteration whose assertions.json has empty results → lightgrey
        + DEC-007 stderr warning (distinct from the DEC-001 warning)."""
        skill_md = _write_skill(tmp_path)
        iter_skill_dir = (
            tmp_path / ".clauditor" / "iteration-1" / "demo"
        )
        iter_skill_dir.mkdir(parents=True)
        # Empty results → DEC-007 path.
        (iter_skill_dir / "assertions.json").write_text(
            json.dumps({"input_tokens": 0, "output_tokens": 0, "results": []})
        )
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md)])
        assert rc == 0

        err = capsys.readouterr().err
        assert "eval spec declares 0 L1 assertions" in err

        data = json.loads(
            (tmp_path / ".clauditor" / "badges" / "demo.json").read_text()
        )
        assert data["color"] == "lightgrey"
        assert data["message"] == "no data"

    def test_zero_assertions_with_url_only_does_not_mention_write(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Review pass 1, B-1: the DEC-007 "wrote lightgrey badge" warning
        must NOT fire under ``--url-only`` (which prints a Markdown line
        and does not write JSON). Otherwise stderr claims a write that
        never happened.
        """
        skill_md = _write_skill(tmp_path)
        iter_skill_dir = (
            tmp_path / ".clauditor" / "iteration-1" / "demo"
        )
        iter_skill_dir.mkdir(parents=True)
        (iter_skill_dir / "assertions.json").write_text(
            json.dumps({"input_tokens": 0, "output_tokens": 0, "results": []})
        )
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "badge",
                str(skill_md),
                "--url-only",
                "--repo",
                "u/r",
                "--branch",
                "main",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        # Markdown line printed to stdout.
        assert "img.shields.io/endpoint" in captured.out
        # No "wrote lightgrey" claim on stderr.
        assert "wrote lightgrey" not in captured.err
        assert "0 L1 assertions" not in captured.err
        # JSON file NOT created.
        assert not (
            tmp_path / ".clauditor" / "badges" / "demo.json"
        ).exists()


# ---------------------------------------------------------------------------
# Disk-write error path (exit 1).
# ---------------------------------------------------------------------------


class TestCmdBadgeDiskWriteError:
    def test_write_os_error_exit_1(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """An OSError during write maps to exit 1 (DEC-025 runtime failure)."""
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        # Patch Path.write_text at the badge module's use site. Using
        # the module's imported Path symbol keeps the patch scoped.
        from unittest.mock import MagicMock

        with patch(
            "clauditor.cli.badge.Path.write_text",
            MagicMock(side_effect=OSError("disk full")),
        ):
            rc = main(["badge", str(skill_md)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "could not write" in err
        assert "disk full" in err

    def test_atomic_write_preserves_existing_on_failure(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Review pass 2, C2-3: failed write must NOT truncate existing badge.

        The atomic tmp+rename pattern guarantees that a mid-write
        OSError (disk full, EIO) leaves the existing target untouched.
        A naive ``Path.write_text`` on the target would truncate it
        at open-time and the failure would leave an empty file.
        """
        skill_md = _write_skill(tmp_path)
        _setup_iteration(tmp_path, 1)
        monkeypatch.chdir(tmp_path)

        # Seed an existing badge file with known content.
        target = tmp_path / ".clauditor" / "badges" / "demo.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = '{"schemaVersion": 1, "message": "prior good badge"}\n'
        target.write_text(original)

        from unittest.mock import MagicMock

        # Patch Path.write_text to fail — this fires on the tmp
        # sibling, not on the target itself.
        with patch(
            "clauditor.cli.badge.Path.write_text",
            MagicMock(side_effect=OSError("disk full")),
        ):
            rc = main(["badge", str(skill_md), "--force"])

        assert rc == 1
        # Target is still the original — NOT truncated to empty.
        assert target.read_text() == original
        # And no stray .tmp file left behind.
        assert not list(target.parent.glob(".*.tmp"))


# ---------------------------------------------------------------------------
# _list_available_iterations defensive branches (exit 1 corner cases).
# ---------------------------------------------------------------------------


class TestCmdBadgeListAvailableIterations:
    def test_ignores_non_iteration_entries(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """``.clauditor/`` children that are not ``iteration-N`` dirs skipped.

        Covers the ``_list_available_iterations`` branches that skip
        unrelated entries (files + malformed iteration names) when
        building the DEC-016 "available iterations" list.
        """
        skill_md = _write_skill(tmp_path)
        # Real iteration present for the skill
        _setup_iteration(tmp_path, 2)
        # Non-dir child — should be skipped.
        (tmp_path / ".clauditor" / "not-a-dir").write_text("x")
        # Wrong prefix — should be skipped.
        (tmp_path / ".clauditor" / "runs").mkdir()
        # iteration-XX with non-integer suffix — should be skipped.
        (tmp_path / ".clauditor" / "iteration-abc").mkdir()
        monkeypatch.chdir(tmp_path)

        rc = main(["badge", str(skill_md), "--from-iteration", "99"])
        assert rc == 1

        err = capsys.readouterr().err
        # Only the valid iteration 2 should be listed.
        assert "Available iterations with this skill: 2" in err


# ---------------------------------------------------------------------------
# Dispatcher wiring smoke test.
# ---------------------------------------------------------------------------


class TestDispatcherWiring:
    def test_badge_subcommand_registered(self, capsys) -> None:
        """``clauditor --help`` lists ``badge`` as an available subcommand."""
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "badge" in out

    def test_badge_help_renders_flags(self, capsys) -> None:
        with pytest.raises(SystemExit):
            main(["badge", "--help"])
        out = capsys.readouterr().out
        for flag in (
            "--from-iteration",
            "--output",
            "--url-only",
            "--force",
            "--repo",
            "--branch",
            "--label",
            "--style",
            "--verbose",
        ):
            assert flag in out
