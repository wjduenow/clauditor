"""Integration tests for US-003 — ``_write_run_dir`` transcript redaction.

These tests exercise the on-disk contract of
``clauditor.cli._write_run_dir``: the staged ``output.jsonl`` and
``output.txt`` must contain the scrubbed form of the stream events and
final output text, while the in-memory ``stream_events`` list passed in
by the caller must be left untouched (DEC-010). Under ``verbose=True``
the per-run redaction count is always logged to stderr, even when no
matches were found.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from clauditor.cli import _write_run_dir

# A realistic OpenAI-style key shape that matches one of the regex
# patterns in ``clauditor.transcripts._SECRET_PATTERNS``. Defined once so
# all tests agree on the literal token being scrubbed.
_FAKE_KEY = "sk-proj-" + "A" * 32


class TestWriteRunDirRedaction:
    def test_jsonl_contains_no_raw_secret(self, tmp_path: Path) -> None:
        events = [
            {"type": "assistant", "text": f"here is my key {_FAKE_KEY}"},
            {"type": "result", "text": "done"},
        ]
        _write_run_dir(tmp_path / "run-0", "final answer", events)

        jsonl = (tmp_path / "run-0" / "output.jsonl").read_text()
        assert _FAKE_KEY not in jsonl
        assert "[REDACTED]" in jsonl
        # The shape is still valid JSONL: one event per line.
        parsed = [json.loads(line) for line in jsonl.splitlines()]
        assert len(parsed) == 2
        assert parsed[1]["text"] == "done"

    def test_output_txt_is_scrubbed(self, tmp_path: Path) -> None:
        output_text = f"call me: {_FAKE_KEY}"
        _write_run_dir(tmp_path / "run-1", output_text, [])

        txt = (tmp_path / "run-1" / "output.txt").read_text()
        assert _FAKE_KEY not in txt
        assert "[REDACTED]" in txt

    def test_in_memory_stream_events_not_mutated(
        self, tmp_path: Path
    ) -> None:
        events = [{"type": "assistant", "text": f"leak: {_FAKE_KEY}"}]
        snapshot = copy.deepcopy(events)
        _write_run_dir(tmp_path / "run-0", "ok", events)
        assert events == snapshot

    def test_verbose_logs_count_when_matches_found(
        self, tmp_path: Path, capsys
    ) -> None:
        events = [{"type": "assistant", "text": f"x {_FAKE_KEY} y"}]
        _write_run_dir(
            tmp_path / "run-7", f"also {_FAKE_KEY}", events, verbose=True
        )
        err = capsys.readouterr().err
        assert "clauditor.transcripts: redacted" in err
        assert "run-7" in err
        # One match in the event + one in the output text.
        assert "redacted 2 matches" in err

    def test_verbose_logs_zero_when_clean(
        self, tmp_path: Path, capsys
    ) -> None:
        _write_run_dir(
            tmp_path / "run-0",
            "nothing secret here",
            [{"type": "result", "text": "all clean"}],
            verbose=True,
        )
        err = capsys.readouterr().err
        assert "clauditor.transcripts: redacted 0 matches in run-0" in err

    def test_quiet_mode_prints_nothing(
        self, tmp_path: Path, capsys
    ) -> None:
        _write_run_dir(
            tmp_path / "run-0",
            f"secret {_FAKE_KEY}",
            [],
            verbose=False,
        )
        err = capsys.readouterr().err
        assert err == ""
