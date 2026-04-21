"""Tests for ``clauditor lint`` CLI command (US-003, US-004, US-005).

Plain-text output, the ``--strict`` flag (US-004, DEC-004), and the
``--json`` flag (US-005, DEC-012).
"""

from __future__ import annotations

import json

import pytest

from clauditor.cli import main


def _write_skill(path, frontmatter: str, body: str = "# Body\n\nContent.\n") -> None:
    """Write a SKILL.md file with the given frontmatter and body."""
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")


def _make_valid_skill(tmp_path, name: str = "my-skill"):
    """Create a minimal-valid SKILL.md at ``tmp_path/<name>/SKILL.md``.

    Returns the path object. The skill conforms to the agentskills.io
    specification: modern layout, required ``name`` + ``description`` keys,
    body under 500 lines.
    """
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    _write_skill(
        skill_path,
        f'name: {name}\ndescription: A minimal valid skill for testing.\n',
    )
    return skill_path


class TestCmdLint:
    """Tests for the ``clauditor lint`` subcommand."""

    def test_valid_skill_exits_0(self, tmp_path, capsys):
        """Minimal-valid SKILL.md exits 0 with success line on stdout."""
        skill_path = _make_valid_skill(tmp_path)

        rc = main(["lint", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "Conformance check passed: " in captured.out
        assert str(skill_path.resolve()) in captured.out
        assert captured.err == ""

    def test_warning_only_no_strict_exits_0(self, tmp_path, capsys):
        """Warning-only skill (body >500 lines) without ``--strict`` exits 0 (DEC-004).

        Warnings still render to stderr, but the run is considered
        passing. A stdout success line advertises the warning count so
        the caller is not misled into thinking nothing was emitted.
        """
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        body = "# Body\n\n" + ("line content\n" * 600)
        _write_skill(
            skill_path,
            "name: my-skill\ndescription: A skill with a long body.\n",
            body=body,
        )

        rc = main(["lint", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "clauditor.conformance: AGENTSKILLS_BODY_TOO_LONG: " in captured.err
        # Stdout acknowledges the warning(s) on the success line so the
        # caller does not miss the stderr output.
        assert "Conformance check passed" in captured.out
        assert "1 warning" in captured.out

    def test_error_exits_2(self, tmp_path, capsys):
        """Skill missing ``name:`` exits 2 with error on stderr."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        _write_skill(
            skill_path,
            "description: A skill without a name field.\n",
        )

        rc = main(["lint", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        assert "clauditor.conformance: AGENTSKILLS_NAME_MISSING: " in captured.err
        assert "Conformance check failed: " in captured.out

    def test_invalid_yaml_exits_1(self, tmp_path, capsys):
        """Malformed frontmatter exits 1 (parse failure, not conformance)."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        # Frontmatter block that ``_frontmatter.parse_frontmatter`` rejects
        # structurally: missing closing ``---``.
        skill_path.write_text(
            "---\nname: my-skill\ndescription: Missing closing fence.\n"
            "# Body without closing ---\n",
            encoding="utf-8",
        )

        rc = main(["lint", str(skill_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert (
            "clauditor.conformance: AGENTSKILLS_FRONTMATTER_INVALID_YAML: "
            in captured.err
        )
        assert "Cannot lint: SKILL.md frontmatter is not valid YAML" in captured.out

    def test_path_not_a_file_exits_1(self, tmp_path, capsys):
        """Passing a directory exits 1 with stderr error."""
        some_dir = tmp_path / "not-a-file"
        some_dir.mkdir()

        rc = main(["lint", str(some_dir)])

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: " in captured.err
        assert "is not a regular file" in captured.err

    def test_path_nonexistent_exits_1(self, tmp_path, capsys):
        """Passing a nonexistent path exits 1 with stderr error."""
        missing = tmp_path / "does-not-exist.md"

        rc = main(["lint", str(missing)])

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: " in captured.err
        assert "is not a regular file" in captured.err

    def test_stderr_prefix_format(self, tmp_path, capsys):
        """Any issue renders as ``clauditor.conformance: <CODE>: <message>``.

        Uses a warning-only skill to exercise the soft-exit path under
        DEC-004 while still asserting the stderr prefix contract.
        """
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        _write_skill(
            skill_path,
            "name: my-skill\ndescription: Has an unknown key.\n"
            "some-unknown-key: whatever\n",
        )

        rc = main(["lint", str(skill_path)])

        # Unknown key is a warning; without ``--strict`` that is exit 0.
        assert rc == 0
        err_lines = capsys.readouterr().err.strip().splitlines()
        assert any(
            line.startswith("clauditor.conformance: AGENTSKILLS_")
            and ": " in line[len("clauditor.conformance: "):]
            for line in err_lines
        )

    def test_unreadable_file_exits_1(self, tmp_path, capsys, monkeypatch):
        """``OSError`` on file read surfaces as exit 1 with stderr error."""
        skill_path = _make_valid_skill(tmp_path)

        def _boom(self, *_args, **_kwargs):
            raise OSError("simulated read failure")

        from pathlib import Path

        monkeypatch.setattr(Path, "read_text", _boom)

        rc = main(["lint", str(skill_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: " in captured.err
        assert "simulated read failure" in captured.err

    def test_undecodable_file_exits_1(self, tmp_path, capsys):
        """Non-UTF-8 content surfaces as exit 1 with stderr error."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.write_bytes(b"\xff\xfe\x00\x00invalid utf-8")

        rc = main(["lint", str(skill_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: " in captured.err

    def test_resolved_path_in_success_message(self, tmp_path, capsys):
        """Success line prints the *resolved* absolute path, not the argv."""
        skill_path = _make_valid_skill(tmp_path)

        rc = main(["lint", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        # The resolved path is absolute.
        resolved = str(skill_path.resolve())
        assert resolved in captured.out

    def test_multiple_issues_count_in_summary(self, tmp_path, capsys):
        """Failure summary reports the total issue count."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        _write_skill(
            skill_path,
            # Two distinct issues: missing name (error) + unknown key (warning).
            "description: Missing name and has an unknown key.\n"
            "some-unknown-key: whatever\n",
        )

        rc = main(["lint", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        # Check the summary line has a numeric count.
        assert "Conformance check failed: " in captured.out
        assert "2 issue(s)" in captured.out


# ---------------------------------------------------------------------------
# Argparse surface — ``--strict`` (US-004, DEC-004) and ``--json``
# (US-005, DEC-012) are both registered.
# ---------------------------------------------------------------------------


class TestArgparseSurface:
    """Verify the post-US-005 argparse surface (``--strict`` + ``--json``)."""

    def test_strict_flag_accepted(self, tmp_path, capsys):
        """``--strict`` is registered and parses without error (US-004)."""
        skill_path = _make_valid_skill(tmp_path)

        # A minimal-valid skill has no issues, so ``--strict`` should not
        # change the outcome — exit 0 regardless.
        rc = main(["lint", "--strict", str(skill_path)])
        assert rc == 0

    def test_strict_flag_in_help(self, capsys):
        """``--strict`` appears in the ``lint --help`` output."""
        with pytest.raises(SystemExit) as exc:
            main(["lint", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "--strict" in captured.out

    def test_json_flag_accepted(self, tmp_path, capsys):
        """``--json`` is registered and parses without error (US-005)."""
        skill_path = _make_valid_skill(tmp_path)

        # A minimal-valid skill has no issues, so ``--json`` should not
        # change the exit code — exit 0 regardless.
        rc = main(["lint", "--json", str(skill_path)])
        assert rc == 0

    def test_json_flag_in_help(self, capsys):
        """``--json`` appears in the ``lint --help`` output."""
        with pytest.raises(SystemExit) as exc:
            main(["lint", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "--json" in captured.out


# ---------------------------------------------------------------------------
# ``--strict`` six-cell matrix (US-004, DEC-004).
#
# | strict | severity     | expected_rc |
# |--------|--------------|-------------|
# | False  | warning-only | 0           |
# | False  | error        | 2           |
# | False  | mixed        | 2           |
# | True   | warning-only | 2           |
# | True   | error        | 2           |
# | True   | mixed        | 2           |
#
# Plus: ``AGENTSKILLS_FRONTMATTER_INVALID_YAML`` keeps exit 1 under
# ``--strict`` (parse failure precedence; preserve US-003 special case).
# ---------------------------------------------------------------------------


def _write_warning_only(path):
    """Write a SKILL.md whose only conformance issue is a warning.

    Uses ``AGENTSKILLS_BODY_TOO_LONG`` (>500-line body) — the skill
    has a valid modern layout, required frontmatter keys, and a body
    that trips the warning-only line-count check.
    """
    path.parent.mkdir(exist_ok=True)
    body = "# Body\n\n" + ("line content\n" * 600)
    _write_skill(
        path,
        "name: my-skill\ndescription: A long-bodied skill for warning-only tests.\n",
        body=body,
    )


def _write_error_only(path):
    """Write a SKILL.md whose only conformance issue is an error.

    Uses ``AGENTSKILLS_NAME_MISSING`` — the frontmatter omits the
    required ``name`` key. Body stays under 500 lines to avoid
    accidentally adding a warning.
    """
    path.parent.mkdir(exist_ok=True)
    _write_skill(
        path,
        "description: Missing the required name field.\n",
    )


def _write_mixed(path):
    """Write a SKILL.md with BOTH an error and a warning.

    Error: missing ``name`` (``AGENTSKILLS_NAME_MISSING``).
    Warning: body over 500 lines (``AGENTSKILLS_BODY_TOO_LONG``).
    """
    path.parent.mkdir(exist_ok=True)
    body = "# Body\n\n" + ("line content\n" * 600)
    _write_skill(
        path,
        "description: Missing name and oversized body.\n",
        body=body,
    )


class TestStrictFlag:
    """Six-cell matrix for ``--strict`` behavior (DEC-004)."""

    @pytest.mark.parametrize(
        "strict,fixture,expected_rc",
        [
            (False, _write_warning_only, 0),
            (False, _write_error_only, 2),
            (False, _write_mixed, 2),
            (True, _write_warning_only, 2),
            (True, _write_error_only, 2),
            (True, _write_mixed, 2),
        ],
        ids=[
            "nostrict-warnings-0",
            "nostrict-errors-2",
            "nostrict-mixed-2",
            "strict-warnings-2",
            "strict-errors-2",
            "strict-mixed-2",
        ],
    )
    def test_exit_code_matrix(
        self, tmp_path, capsys, strict, fixture, expected_rc
    ):
        """Each (strict, severity) cell routes to the documented exit code."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        fixture(skill_path)

        argv = ["lint"]
        if strict:
            argv.append("--strict")
        argv.append(str(skill_path))

        rc = main(argv)
        assert rc == expected_rc, (
            f"strict={strict} fixture={fixture.__name__} "
            f"expected rc={expected_rc}, got {rc}"
        )

    def test_strict_warning_only_renders_stderr(self, tmp_path, capsys):
        """``--strict`` on warning-only still renders the stderr issue line."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_warning_only(skill_path)

        rc = main(["lint", "--strict", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        # Stderr rendering is identical to the non-strict warning path
        # (the flag only changes exit code, not output format).
        assert (
            "clauditor.conformance: AGENTSKILLS_BODY_TOO_LONG: "
            in captured.err
        )
        # Stdout shows the failure summary (not the success line).
        assert "Conformance check failed: " in captured.out
        assert "1 issue" in captured.out

    def test_nostrict_warning_only_renders_stderr(self, tmp_path, capsys):
        """Without ``--strict``, warning-only renders stderr identically (just rc=0)."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_warning_only(skill_path)

        rc = main(["lint", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        # Same stderr line regardless of strict — DEC-004 is exit-code
        # only, not rendering.
        assert (
            "clauditor.conformance: AGENTSKILLS_BODY_TOO_LONG: "
            in captured.err
        )

    def test_invalid_yaml_with_strict_exits_1(self, tmp_path, capsys):
        """``--strict`` does NOT override the INVALID_YAML → exit 1 special case.

        Malformed frontmatter is a parse failure, not a conformance
        issue. Preserve the US-003 special case verbatim.
        """
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.write_text(
            "---\nname: my-skill\ndescription: Missing closing fence.\n"
            "# Body without closing ---\n",
            encoding="utf-8",
        )

        rc = main(["lint", "--strict", str(skill_path)])

        # Exit 1, same as without ``--strict`` (parse-failure taxonomy).
        assert rc == 1
        captured = capsys.readouterr()
        assert (
            "clauditor.conformance: AGENTSKILLS_FRONTMATTER_INVALID_YAML: "
            in captured.err
        )
        assert "Cannot lint: SKILL.md frontmatter is not valid YAML" in captured.out


# ---------------------------------------------------------------------------
# ``--json`` output envelope (US-005, DEC-012).
#
# Envelope shape:
#
#   {
#     "schema_version": 1,
#     "skill_path": "<resolved-path>",
#     "passed": bool,
#     "issues": [
#       {"code": "...", "severity": "error"|"warning", "message": "..."}
#     ]
#   }
#
# ``schema_version: 1`` is the FIRST key in the payload per
# ``.claude/rules/json-schema-version.md``. When ``--json`` is set,
# stderr is empty and exit codes are identical to the non-JSON path
# (including ``--strict`` interaction and the INVALID_YAML → exit 1
# special case).
# ---------------------------------------------------------------------------


class TestJsonOutput:
    """Tests for ``clauditor lint --json`` (US-005, DEC-012)."""

    def test_json_pass_envelope(self, tmp_path, capsys):
        """Minimal-valid skill with ``--json`` emits a pass envelope, exit 0."""
        skill_path = _make_valid_skill(tmp_path)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["schema_version"] == 1
        assert payload["passed"] is True
        assert payload["issues"] == []
        assert payload["skill_path"] == str(skill_path.resolve())

    def test_json_schema_version_first_key(self, tmp_path, capsys):
        """``schema_version`` is the FIRST key in the payload (structural).

        Verified by reading the raw JSON string — not just that the key
        is present, but that it is the first one in insertion order.
        """
        skill_path = _make_valid_skill(tmp_path)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 0
        out = capsys.readouterr().out
        # Locate the first quoted key in the raw JSON string. The first
        # opening double-quote inside the object belongs to the first key.
        brace_idx = out.index("{")
        first_key_start = out.index('"', brace_idx)
        first_key_end = out.index('"', first_key_start + 1)
        first_key = out[first_key_start + 1 : first_key_end]
        assert first_key == "schema_version"

    def test_json_warning_envelope_no_strict(self, tmp_path, capsys):
        """Warning-only + ``--json`` (no strict) → passed=True, exit 0."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_warning_only(skill_path)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["passed"] is True
        assert len(payload["issues"]) >= 1
        assert all(i["severity"] == "warning" for i in payload["issues"])
        codes = [i["code"] for i in payload["issues"]]
        assert "AGENTSKILLS_BODY_TOO_LONG" in codes

    def test_json_warning_envelope_with_strict(self, tmp_path, capsys):
        """Warning-only + ``--strict --json`` → passed=False, exit 2.

        ``--strict`` promotes warnings to failures; the JSON envelope's
        ``passed`` must mirror the effective exit-code outcome.
        """
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_warning_only(skill_path)

        rc = main(["lint", "--strict", "--json", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["passed"] is False
        codes = [i["code"] for i in payload["issues"]]
        assert "AGENTSKILLS_BODY_TOO_LONG" in codes

    def test_json_error_envelope(self, tmp_path, capsys):
        """Error-severity skill + ``--json`` → passed=False, exit 2."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_error_only(skill_path)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["passed"] is False
        codes = [i["code"] for i in payload["issues"]]
        assert "AGENTSKILLS_NAME_MISSING" in codes
        assert any(i["severity"] == "error" for i in payload["issues"])

    def test_json_invalid_yaml_envelope(self, tmp_path, capsys):
        """Malformed frontmatter + ``--json`` → passed=False, exit 1.

        Preserves the INVALID_YAML → exit 1 special case from US-003.
        """
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.write_text(
            "---\nname: my-skill\ndescription: Missing closing fence.\n"
            "# Body without closing ---\n",
            encoding="utf-8",
        )

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["passed"] is False
        assert len(payload["issues"]) == 1
        assert payload["issues"][0]["code"] == "AGENTSKILLS_FRONTMATTER_INVALID_YAML"

    def test_json_path_not_a_file_envelope(self, tmp_path, capsys):
        """Directory + ``--json`` → passed=False, exit 1, synthetic PATH_* issue."""
        some_dir = tmp_path / "not-a-file"
        some_dir.mkdir()

        rc = main(["lint", "--json", str(some_dir)])

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["passed"] is False
        assert len(payload["issues"]) == 1
        issue = payload["issues"][0]
        assert issue["code"] == "PATH_NOT_A_FILE"
        assert issue["severity"] == "error"

    def test_json_silences_stderr(self, tmp_path, capsys):
        """``--json`` on a failing case emits NO stderr output."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_error_only(skill_path)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.err == ""
        # And no conformance-prefix lines leaked into stdout either.
        assert "clauditor.conformance:" not in captured.out

    def test_json_issue_fields(self, tmp_path, capsys):
        """Each JSON issue has exactly 3 keys: code, severity, message."""
        skill_path = tmp_path / "my-skill" / "SKILL.md"
        _write_mixed(skill_path)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert len(payload["issues"]) >= 1
        for issue in payload["issues"]:
            assert set(issue.keys()) == {"code", "severity", "message"}
            assert isinstance(issue["code"], str)
            assert issue["severity"] in ("error", "warning")
            assert isinstance(issue["message"], str)

    def test_json_unreadable_file_envelope(self, tmp_path, capsys, monkeypatch):
        """``OSError`` on read + ``--json`` → exit 1, PATH_UNREADABLE issue."""
        skill_path = _make_valid_skill(tmp_path)

        def _boom(self, *_args, **_kwargs):
            raise OSError("simulated read failure")

        from pathlib import Path

        monkeypatch.setattr(Path, "read_text", _boom)

        rc = main(["lint", "--json", str(skill_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["passed"] is False
        assert len(payload["issues"]) == 1
        assert payload["issues"][0]["code"] == "PATH_UNREADABLE"
        assert payload["issues"][0]["severity"] == "error"
