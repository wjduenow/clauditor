"""Tests for clauditor.capture_provenance (#117)."""

from __future__ import annotations

import json
from pathlib import Path

from clauditor.capture_provenance import (
    CaptureProvenance,
    read_capture_provenance,
    sidecar_path_for,
    write_capture_provenance,
)


class TestSidecarPathFor:
    def test_replaces_txt_suffix(self) -> None:
        assert sidecar_path_for(Path("greeter.txt")) == Path(
            "greeter.capture.json"
        )

    def test_replaces_versioned_suffix(self) -> None:
        # A --versioned capture writes ``greeter-2026-04-24.txt``; the
        # sidecar should live next to it with the date stamp preserved
        # in the stem.
        assert sidecar_path_for(
            Path("greeter-2026-04-24.txt")
        ) == Path("greeter-2026-04-24.capture.json")

    def test_keeps_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "find-restaurants.txt"
        assert sidecar_path_for(out) == tmp_path / "sub" / (
            "find-restaurants.capture.json"
        )


class TestWriteCaptureProvenance:
    def test_writes_json_with_schema_version_first(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "greeter.txt"
        sidecar = write_capture_provenance(
            out, skill_name="greeter", skill_args="hello world"
        )
        assert sidecar == tmp_path / "greeter.capture.json"
        raw = sidecar.read_text(encoding="utf-8")
        data = json.loads(raw)
        # schema_version must be the FIRST key on the wire per
        # .claude/rules/json-schema-version.md.
        first_key = list(data.keys())[0]
        assert first_key == "schema_version"
        assert data["schema_version"] == 1
        assert data["skill_name"] == "greeter"
        assert data["skill_args"] == "hello world"
        assert "captured_at" in data

    def test_writes_empty_args_verbatim(self, tmp_path: Path) -> None:
        # Empty args is a first-class shape — capture with no args must
        # still produce a sidecar so propose-eval knows to emit empty
        # ``test_args`` rather than falling back to shape-only.
        out = tmp_path / "greeter.txt"
        write_capture_provenance(out, skill_name="greeter", skill_args="")
        data = json.loads(
            (tmp_path / "greeter.capture.json").read_text(encoding="utf-8")
        )
        assert data["skill_args"] == ""

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "sub" / "greeter.txt"
        # Parent dir doesn't exist; write_capture_provenance should
        # create it defensively.
        sidecar = write_capture_provenance(
            out, skill_name="greeter", skill_args=""
        )
        assert sidecar.exists()

    def test_captured_at_is_iso8601_utc_with_z(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "greeter.txt"
        write_capture_provenance(out, skill_name="greeter", skill_args="")
        data = json.loads(
            (tmp_path / "greeter.capture.json").read_text(encoding="utf-8")
        )
        # Must end with 'Z' to indicate UTC — not '+00:00'.
        assert data["captured_at"].endswith("Z")
        assert "+00:00" not in data["captured_at"]


class TestReadCaptureProvenance:
    def test_returns_none_when_sidecar_missing(
        self, tmp_path: Path
    ) -> None:
        assert read_capture_provenance(tmp_path / "greeter.txt") is None

    def test_round_trip_via_writer(self, tmp_path: Path) -> None:
        out = tmp_path / "greeter.txt"
        write_capture_provenance(
            out, skill_name="greeter", skill_args="hello world"
        )
        record = read_capture_provenance(out)
        assert record is not None
        assert isinstance(record, CaptureProvenance)
        assert record.skill_name == "greeter"
        assert record.skill_args == "hello world"
        assert record.schema_version == 1

    def test_skip_and_warn_on_schema_mismatch(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "greeter.capture.json").write_text(
            json.dumps(
                {
                    "schema_version": 99,
                    "skill_name": "greeter",
                    "skill_args": "hello",
                    "captured_at": "2026-04-24T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        assert read_capture_provenance(tmp_path / "greeter.txt") is None
        err = capsys.readouterr().err
        assert "schema_version" in err
        assert "99" in err

    def test_skip_and_warn_on_malformed_json(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "greeter.capture.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        assert read_capture_provenance(tmp_path / "greeter.txt") is None
        assert "valid JSON" in capsys.readouterr().err

    def test_skip_and_warn_on_non_object_top_level(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "greeter.capture.json").write_text(
            "[1, 2, 3]", encoding="utf-8"
        )
        assert read_capture_provenance(tmp_path / "greeter.txt") is None
        assert "object" in capsys.readouterr().err

    def test_skip_and_warn_on_missing_required_field(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "greeter.capture.json").write_text(
            json.dumps(
                {"schema_version": 1, "skill_name": "greeter"}
            ),
            encoding="utf-8",
        )
        assert read_capture_provenance(tmp_path / "greeter.txt") is None
        assert "missing required" in capsys.readouterr().err

    def test_skip_and_warn_on_non_utf8_sidecar(
        self, tmp_path: Path, capsys
    ) -> None:
        # Write invalid UTF-8 bytes. read_text will raise
        # UnicodeDecodeError, which the loader tolerates with a warning.
        (tmp_path / "greeter.capture.json").write_bytes(b"\xff\xfe\x00bad")
        assert read_capture_provenance(tmp_path / "greeter.txt") is None
        assert "could not read" in capsys.readouterr().err

    def test_skip_and_warn_on_skill_name_mismatch(
        self, tmp_path: Path, capsys
    ) -> None:
        """Copilot review on PR #118: ``expected_skill_name`` kwarg guards
        against silently threading args from a *different* skill's sidecar.
        """
        out = tmp_path / "greeter.txt"
        write_capture_provenance(
            out, skill_name="find-restaurants", skill_args="--near SF"
        )
        # Caller expects ``greeter`` but the sidecar is for
        # ``find-restaurants`` → skip with a clear warning.
        record = read_capture_provenance(
            out, expected_skill_name="greeter"
        )
        assert record is None
        err = capsys.readouterr().err
        assert "find-restaurants" in err
        assert "greeter" in err

    def test_expected_skill_name_match_returns_record(
        self, tmp_path: Path, capsys
    ) -> None:
        """Copilot review on PR #118: matching ``expected_skill_name``
        returns the record (and emits no warning)."""
        out = tmp_path / "greeter.txt"
        write_capture_provenance(
            out, skill_name="greeter", skill_args="hi"
        )
        record = read_capture_provenance(
            out, expected_skill_name="greeter"
        )
        assert record is not None
        assert record.skill_name == "greeter"
        assert capsys.readouterr().err == ""

    def test_expected_skill_name_none_skips_check(
        self, tmp_path: Path, capsys
    ) -> None:
        """Default ``expected_skill_name=None`` preserves back-compat —
        the mismatch check only fires when the caller opts in."""
        out = tmp_path / "greeter.txt"
        write_capture_provenance(
            out, skill_name="whatever", skill_args="x"
        )
        # No expected_skill_name passed → record returned unconditionally.
        record = read_capture_provenance(out)
        assert record is not None
        assert record.skill_name == "whatever"

    def test_accepts_missing_captured_at(self, tmp_path: Path) -> None:
        # ``captured_at`` is informational — a record missing it should
        # still load (with captured_at coerced to "").
        (tmp_path / "greeter.capture.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "greeter",
                    "skill_args": "hi",
                }
            ),
            encoding="utf-8",
        )
        record = read_capture_provenance(tmp_path / "greeter.txt")
        assert record is not None
        assert record.captured_at == ""
