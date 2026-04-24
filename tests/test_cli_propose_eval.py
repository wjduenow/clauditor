"""Tests for ``clauditor propose-eval`` (#52 US-004 / DEC-006 exit codes)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clauditor.cli import main
from clauditor.propose_eval import ProposeEvalReport


def _write_skill(tmp_path: Path, name: str = "greeter") -> Path:
    """Stage a SKILL.md at ``<tmp_path>/.claude/skills/<name>/SKILL.md``."""
    skill_dir = tmp_path / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\n---\n# {name.title()}\n\nSay hi.\n"
    )
    return skill_md


def _make_report(
    *,
    proposed_spec: dict | None = None,
    api_error: str | None = None,
    validation_errors: list[str] | None = None,
    skill_name: str = "greeter",
) -> ProposeEvalReport:
    if proposed_spec is None:
        proposed_spec = {
            "test_args": "hello",
            "assertions": [
                {
                    "id": "greets-user",
                    "type": "contains",
                    "name": "greets the user",
                    "needle": "hello",
                }
            ],
            "grading_criteria": [
                {"id": "is-friendly", "criterion": "friendly tone"}
            ],
        }
    return ProposeEvalReport(
        skill_name=skill_name,
        model="claude-sonnet-4-6",
        proposed_spec=proposed_spec,
        capture_source=None,
        api_error=api_error,
        validation_errors=list(validation_errors or []),
        duration_seconds=0.25,
        input_tokens=100,
        output_tokens=50,
    )


class TestCmdProposeEval:
    """DEC-006 exit-code table + behavior for ``clauditor propose-eval``."""

    # ------------------------------------------------------------------
    # Happy path (exit 0)
    # ------------------------------------------------------------------

    def test_happy_path_writes_eval_json(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 0
        target = skill_md.with_suffix(".eval.json")
        assert target.exists()
        data = json.loads(target.read_text())
        assert data["assertions"][0]["id"] == "greets-user"

        out = capsys.readouterr().out
        assert "Wrote" in out
        assert "1 assertions" in out
        assert "1 criteria" in out

    # ------------------------------------------------------------------
    # --dry-run: prints prompt, NO Anthropic call, no file written
    # (plan DEC-006 "pre-call" behavior; intent is cost-free preview).
    # ------------------------------------------------------------------

    def test_dry_run_prints_prompt_without_calling_anthropic(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        # propose_eval must NOT be invoked under --dry-run. If it is,
        # the AsyncMock side_effect would raise.
        fail_mock = AsyncMock(
            side_effect=AssertionError(
                "propose_eval should not be called under --dry-run"
            )
        )
        with patch("clauditor.cli.propose_eval.propose_eval", new=fail_mock):
            rc = main(["propose-eval", str(skill_md), "--dry-run"])

        assert rc == 0
        target = skill_md.with_suffix(".eval.json")
        assert not target.exists()

        out = capsys.readouterr().out
        # Prompt always contains the trusted SKILL.md fence and the
        # stable-id contract phrase the builder guarantees.
        assert "<skill_md>" in out
        assert "unique `id`" in out
        assert fail_mock.await_count == 0

    # ------------------------------------------------------------------
    # --json (exit 0, full envelope, no file written)
    # ------------------------------------------------------------------

    def test_json_flag_prints_full_envelope(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(["propose-eval", str(skill_md), "--json"])

        assert rc == 0
        target = skill_md.with_suffix(".eval.json")
        assert not target.exists()

        out = capsys.readouterr().out
        data = json.loads(out)
        # schema_version first per .claude/rules/json-schema-version.md
        assert list(data.keys())[0] == "schema_version"
        assert data["schema_version"] == 2
        assert data["skill_name"] == "greeter"
        assert data["input_tokens"] == 100

    # ------------------------------------------------------------------
    # --force overwrites existing eval.json (exit 0)
    # ------------------------------------------------------------------

    def test_force_overwrites_existing(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        target = skill_md.with_suffix(".eval.json")
        target.write_text("{}")
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(["propose-eval", str(skill_md), "--force"])

        assert rc == 0
        data = json.loads(target.read_text())
        assert "assertions" in data
        assert data["assertions"][0]["id"] == "greets-user"

    # ------------------------------------------------------------------
    # Collision without --force exits 1 (DEC-003)
    # ------------------------------------------------------------------

    def test_collision_without_force_exits_1(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        target = skill_md.with_suffix(".eval.json")
        target.write_text("{}")
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err
        assert "--force" in err
        # File was NOT overwritten.
        assert target.read_text() == "{}"

    # ------------------------------------------------------------------
    # Oversize prompt (token budget exceeded) → exit 2 per DEC-006
    # ------------------------------------------------------------------

    def test_oversize_prompt_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """Plan DEC-006: pre-call token-budget ValueError → exit 2."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Force the prompt builder to raise as if the token budget
        # was exceeded. propose_eval must NOT be invoked (pre-call).
        fail_mock = AsyncMock(
            side_effect=AssertionError(
                "propose_eval should not be called after oversize check"
            )
        )
        with patch(
            "clauditor.cli.propose_eval.build_propose_eval_prompt",
            side_effect=ValueError(
                "prompt too long for model context window: "
                "estimated 60000 tokens > 50000 limit"
            ),
        ), patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=fail_mock,
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 2
        err = capsys.readouterr().err
        assert "prompt too long" in err
        assert fail_mock.await_count == 0

    # ------------------------------------------------------------------
    # API error → exit 3
    # ------------------------------------------------------------------

    def test_api_error_exits_3(self, tmp_path: Path, monkeypatch, capsys):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        report = _make_report(
            proposed_spec={},
            api_error="anthropic API error: RuntimeError('boom')",
        )
        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 3
        err = capsys.readouterr().err
        assert "anthropic API error" in err
        # No file written on API failure.
        assert not (skill_md.with_suffix(".eval.json")).exists()

    # ------------------------------------------------------------------
    # Parse error → exit 1 (parse_propose_eval_response: prefix)
    # ------------------------------------------------------------------

    def test_parse_error_exits_1(self, tmp_path: Path, monkeypatch, capsys):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        report = _make_report(
            proposed_spec={},
            validation_errors=[
                "parse_propose_eval_response: response was not valid "
                "JSON: Expecting value"
            ],
        )
        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "parse_propose_eval_response" in err
        assert not (skill_md.with_suffix(".eval.json")).exists()

    # ------------------------------------------------------------------
    # Validation error → exit 2
    # ------------------------------------------------------------------

    def test_validation_error_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        report = _make_report(
            proposed_spec={"assertions": []},
            validation_errors=[
                "EvalSpec(skill_name='greeter'): assertions[0]: missing 'id'"
            ],
        )
        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=report),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 2
        err = capsys.readouterr().err
        assert "validation error" in err
        assert "missing 'id'" in err
        assert not (skill_md.with_suffix(".eval.json")).exists()

    # ------------------------------------------------------------------
    # --from-capture path override (with scrub)
    # ------------------------------------------------------------------

    def test_from_capture_override_scrubs_and_forwards(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        capture = tmp_path / "my-capture.txt"
        # Include a secret-looking token that `transcripts.redact`
        # should scrub before the capture lands in the prompt.
        capture.write_text(
            "Hello sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
            "jklmnopqrstuvwxyz0123456789_-ABCDEFGHIJKL-more text here\n"
        )

        captured = {}

        async def _fake(propose_input, **kwargs):
            captured["capture_text"] = propose_input.capture_text
            captured["capture_source"] = propose_input.capture_source
            return _make_report()

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=_fake,
        ):
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--from-capture",
                    str(capture),
                ]
            )

        assert rc == 0
        assert captured["capture_text"] is not None
        # The raw API key should NOT be present in the forwarded text.
        assert (
            "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
            not in captured["capture_text"]
        )
        assert captured["capture_source"] is not None
        assert "my-capture.txt" in captured["capture_source"]

    def test_from_capture_loads_sidecar_provenance(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """#117: --from-capture reads a sibling .capture.json sidecar."""
        from clauditor.capture_provenance import write_capture_provenance

        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        capture = tmp_path / "my-capture.txt"
        capture.write_text("Hello Alice!\n")
        write_capture_provenance(
            capture,
            skill_name="greeter",
            skill_args="--name Alice --formal",
        )

        captured = {}

        async def _fake(propose_input, **kwargs):
            captured["captured_skill_args"] = (
                propose_input.captured_skill_args
            )
            return _make_report()

        with patch(
            "clauditor.cli.propose_eval.propose_eval", new=_fake
        ):
            rc = main([
                "propose-eval", str(skill_md),
                "--from-capture", str(capture),
            ])

        assert rc == 0
        assert (
            captured["captured_skill_args"] == "--name Alice --formal"
        )
        # No "shape-only placeholder" warning — sidecar was found.
        err = capsys.readouterr().err
        assert "shape-only placeholder" not in err

    def test_from_capture_without_sidecar_emits_warning(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """#117: legacy capture (no sidecar) → stderr warning."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        capture = tmp_path / "legacy.txt"
        capture.write_text("Hello!\n")
        # Intentionally no write_capture_provenance — legacy shape.

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main([
                "propose-eval", str(skill_md),
                "--from-capture", str(capture),
            ])

        assert rc == 0
        err = capsys.readouterr().err
        assert "no .capture.json sidecar" in err
        assert "shape-only placeholder" in err
        assert "Edit the resulting eval spec" in err

    def test_autodiscovered_capture_without_sidecar_emits_warning(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """#117: warning fires on DEC-001 discovery when no sidecar."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Legacy capture discovered via DEC-001 primary path.
        captured_dir = tmp_path / "tests" / "eval" / "captured"
        captured_dir.mkdir(parents=True)
        (captured_dir / "greeter.txt").write_text("hi\n")

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 0
        err = capsys.readouterr().err
        assert "no .capture.json sidecar" in err

    def test_no_capture_no_warning(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """#117: no capture at all → no warning (nothing to warn about)."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 0
        err = capsys.readouterr().err
        assert "no .capture.json sidecar" not in err
        assert "shape-only placeholder" not in err

    def test_from_capture_missing_file_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """DEC-006 row: missing capture file is a pre-call input error → 2."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ) as mock_propose:
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--from-capture",
                    str(tmp_path / "does-not-exist.txt"),
                ]
            )

        assert rc == 2
        err = capsys.readouterr().err
        assert "capture file not found" in err
        mock_propose.assert_not_called()

    # ------------------------------------------------------------------
    # --from-iteration reads from iteration dir
    # ------------------------------------------------------------------

    def test_from_iteration_reads_iteration_output(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path, name="greeter")
        iter_dir = (
            tmp_path
            / ".clauditor"
            / "runs"
            / "iteration-3"
            / "greeter"
            / "run-0"
        )
        iter_dir.mkdir(parents=True)
        (iter_dir / "output.txt").write_text("Greetings from iteration 3!\n")
        monkeypatch.chdir(tmp_path)

        captured = {}

        async def _fake(propose_input, **kwargs):
            captured["capture_text"] = propose_input.capture_text
            captured["capture_source"] = propose_input.capture_source
            return _make_report()

        with patch("clauditor.cli.propose_eval.propose_eval", new=_fake):
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--from-iteration",
                    "3",
                ]
            )

        assert rc == 0
        assert captured["capture_text"] == "Greetings from iteration 3!\n"
        assert captured["capture_source"] is not None
        assert "iteration-3" in captured["capture_source"]

    def test_from_iteration_invalid_int_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """DEC-006 row: invalid --from-iteration is a pre-call error → 2."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ) as mock_propose:
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--from-iteration",
                    "not-a-number",
                ]
            )

        assert rc == 2
        err = capsys.readouterr().err
        assert "--from-iteration" in err
        mock_propose.assert_not_called()

    # ------------------------------------------------------------------
    # --verbose stderr breadcrumbs
    # ------------------------------------------------------------------

    def test_verbose_prints_capture_source_and_model(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        capture = tmp_path / "cap.txt"
        capture.write_text("some captured run output\n")
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--from-capture",
                    str(capture),
                    "--verbose",
                ]
            )

        assert rc == 0
        err = capsys.readouterr().err
        assert "capture:" in err
        assert "cap.txt" in err
        assert "model:" in err
        assert "claude-sonnet-4-6" in err
        assert "estimated prompt tokens" in err
        assert "input_tokens=" in err
        assert "output_tokens=" in err

    # ------------------------------------------------------------------
    # --model override is forwarded
    # ------------------------------------------------------------------

    def test_model_override_forwarded(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        captured_kwargs = {}

        async def _fake(propose_input, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_report()

        with patch("clauditor.cli.propose_eval.propose_eval", new=_fake):
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--model",
                    "claude-opus-4-5",
                ]
            )

        assert rc == 0
        assert captured_kwargs.get("model") == "claude-opus-4-5"

    # ------------------------------------------------------------------
    # Skill file missing / not a regular file
    # ------------------------------------------------------------------

    def test_skill_file_missing_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """DEC-006: missing SKILL.md is a pre-call input error → 2."""
        monkeypatch.chdir(tmp_path)
        rc = main(["propose-eval", str(tmp_path / "nope.md")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "skill file not found" in err

    def test_skill_path_is_directory_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """DEC-006: non-file skill path is a pre-call input error → 2."""
        skill_dir = tmp_path / "a-dir"
        skill_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        rc = main(["propose-eval", str(skill_dir)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "not a regular file" in err

    # ------------------------------------------------------------------
    # Verbose stderr when capture was auto-discovered vs absent
    # ------------------------------------------------------------------

    def test_verbose_surfaces_autodiscovered_capture(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # DEC-001 primary capture at tests/eval/captured/<skill>.txt
        skill_md = _write_skill(tmp_path, name="greeter")
        captured_dir = tmp_path / "tests" / "eval" / "captured"
        captured_dir.mkdir(parents=True)
        (captured_dir / "greeter.txt").write_text("some auto content\n")
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(
                ["propose-eval", str(skill_md), "--verbose"]
            )

        assert rc == 0
        err = capsys.readouterr().err
        assert "tests/eval/captured/greeter.txt" in err
        assert "scrubbed by loader" in err

    def test_verbose_logs_no_capture_when_none_found(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(
                ["propose-eval", str(skill_md), "--verbose"]
            )

        assert rc == 0
        err = capsys.readouterr().err
        assert "(none" in err

    # ------------------------------------------------------------------
    # Write errors
    # ------------------------------------------------------------------

    def test_write_oserror_exits_1(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        def boom(self, *_args, **_kwargs):
            raise OSError("disk full")

        with (
            patch(
                "clauditor.cli.propose_eval.propose_eval",
                new=AsyncMock(return_value=_make_report()),
            ),
            patch("pathlib.Path.write_text", new=boom),
        ):
            rc = main(["propose-eval", str(skill_md)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "could not write" in err
        assert "disk full" in err

    # ------------------------------------------------------------------
    # Error-handler branches in --from-capture override + loader
    # (codecov/patch gate — cover error-only branches that never fire
    # through the happy-path tests above).
    # ------------------------------------------------------------------

    def test_from_capture_read_oserror_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """Trigger the generic OSError branch (not FileNotFoundError)
        in _apply_from_capture_override — e.g. permission error on read."""
        skill_md = _write_skill(tmp_path)
        capture = tmp_path / "locked.txt"
        capture.write_text("x\n")
        monkeypatch.chdir(tmp_path)

        real_read_text = Path.read_text

        def _selective_read_text(self, *a, **kw):
            if self == capture:
                raise PermissionError("locked")
            return real_read_text(self, *a, **kw)

        with patch("pathlib.Path.read_text", new=_selective_read_text):
            rc = main(
                ["propose-eval", str(skill_md),
                 "--from-capture", str(capture)]
            )
        assert rc == 2
        err = capsys.readouterr().err
        assert "could not read capture file" in err

    def test_from_capture_unicode_decode_error_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """Non-UTF-8 capture file surfaces as a pre-call input error."""
        skill_md = _write_skill(tmp_path)
        capture = tmp_path / "binary.txt"
        capture.write_bytes(b"\xff\xfe\x00\x01not utf-8")
        monkeypatch.chdir(tmp_path)

        with patch(
            "clauditor.cli.propose_eval.propose_eval",
            new=AsyncMock(return_value=_make_report()),
        ):
            rc = main(
                ["propose-eval", str(skill_md),
                 "--from-capture", str(capture)]
            )
        assert rc == 2
        err = capsys.readouterr().err
        assert "not valid UTF-8" in err

    def test_from_capture_outside_project_dir_uses_absolute(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """When the capture path lives outside project_dir, the
        relative_to() call raises and capture_source falls back to the
        full string (covers the `except (ValueError, OSError)` branch)."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        skill_md = _write_skill(project_root)

        # Capture lives in a sibling dir, NOT under project_root.
        outside = tmp_path / "outside"
        outside.mkdir()
        capture = outside / "cap.txt"
        capture.write_text("content\n")

        monkeypatch.chdir(project_root)

        captured = {}

        async def _fake(pi, **kw):
            captured["capture_source"] = pi.capture_source
            return _make_report()

        with patch("clauditor.cli.propose_eval.propose_eval", new=_fake):
            rc = main(
                ["propose-eval", str(skill_md),
                 "--from-capture", str(capture)]
            )
        assert rc == 0
        # Fallback path: absolute string, not a relative path.
        assert captured["capture_source"] == str(capture)

    def test_load_unicode_decode_error_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """SKILL.md with non-UTF-8 bytes → exit 2 via
        ``_cmd_propose_eval_impl``'s UnicodeDecodeError handler."""
        skill_dir = tmp_path / ".claude" / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_bytes(b"---\nname: broken\n\xff\xfe not utf-8\n")
        monkeypatch.chdir(tmp_path)

        rc = main(["propose-eval", str(skill_md)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "could not decode input file as UTF-8" in err

    def test_load_oserror_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """OSError from loader (e.g. PermissionError on SKILL.md)
        routes to exit 2."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        real_read_text = Path.read_text

        def _selective_read_text(self, *a, **kw):
            if self == skill_md:
                raise PermissionError("locked skill.md")
            return real_read_text(self, *a, **kw)

        with patch("pathlib.Path.read_text", new=_selective_read_text):
            rc = main(["propose-eval", str(skill_md)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "could not load SKILL.md" in err

    def test_from_iteration_zero_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """--from-iteration 0 triggers the explicit `must be >= 1`
        ValueError branch inside the try block."""
        skill_md = _write_skill(tmp_path)
        monkeypatch.chdir(tmp_path)

        rc = main(
            ["propose-eval", str(skill_md), "--from-iteration", "0"]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "--from-iteration must be a positive integer" in err
        assert "must be >= 1" in err

    def test_from_iteration_missing_output_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """--from-iteration N where the computed iteration path does
        NOT exist routes through _apply_from_capture_override's
        FileNotFoundError branch and returns 2 — covers the
        `if rc is not None: return rc` branch at the --from-iteration
        call site."""
        skill_md = _write_skill(tmp_path, name="greeter")
        # No .clauditor/runs/iteration-3/... staged, so the computed
        # path won't exist.
        monkeypatch.chdir(tmp_path)

        rc = main(
            ["propose-eval", str(skill_md), "--from-iteration", "3"]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "capture file not found" in err

    # ------------------------------------------------------------------
    # --project-dir override
    # ------------------------------------------------------------------

    def test_project_dir_override_forwarded(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # Stage a skill under one tree and a capture under a DIFFERENT
        # project dir — using --project-dir should make the --from-capture
        # relative-path printing resolve against the overridden root.
        other_root = tmp_path / "other"
        other_root.mkdir()
        skill_md = _write_skill(other_root)

        capture = other_root / "cap.txt"
        capture.write_text("captured text\n")

        # Change actual cwd somewhere else to make sure --project-dir
        # wins over Path.cwd().
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        captured = {}

        async def _fake(propose_input, **kwargs):
            captured["capture_source"] = propose_input.capture_source
            return _make_report()

        with patch("clauditor.cli.propose_eval.propose_eval", new=_fake):
            rc = main(
                [
                    "propose-eval",
                    str(skill_md),
                    "--from-capture",
                    str(capture),
                    "--project-dir",
                    str(other_root),
                ]
            )

        assert rc == 0
        # capture_source should be relative to the overridden project root.
        assert captured["capture_source"] == "cap.txt"


# ---------------------------------------------------------------------------
# ``--help`` smoke test — surfaces argparse regressions cheaply.
# ---------------------------------------------------------------------------


def test_propose_eval_help_is_registered(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["propose-eval", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--from-capture" in out
    assert "--from-iteration" in out
    assert "--force" in out
    assert "--dry-run" in out
    assert "--model" in out
    assert "--json" in out
