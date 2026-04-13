"""Tests for clauditor.history."""

from __future__ import annotations

from clauditor.history import SPARK_GLYPHS, append_record, read_records, sparkline


class TestAppendAndRead:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "history.jsonl"
        append_record("skill-a", 0.8, 0.75, {"foo": 1}, path=path)
        append_record("skill-b", 0.5, None, {}, path=path)
        append_record("skill-a", 0.9, 0.85, {"foo": 2}, path=path)

        all_records = read_records(path=path)
        assert len(all_records) == 3
        assert all_records[0]["skill"] == "skill-a"
        assert all_records[0]["pass_rate"] == 0.8
        assert all_records[0]["mean_score"] == 0.75
        assert all_records[0]["metrics"] == {"foo": 1}
        assert "ts" in all_records[0]

        a_records = read_records(skill="skill-a", path=path)
        assert len(a_records) == 2
        assert all(r["skill"] == "skill-a" for r in a_records)

    def test_append_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "history.jsonl"
        append_record("s", 1.0, 1.0, {}, path=path)
        assert path.exists()
        assert len(read_records(path=path)) == 1

    def test_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nope.jsonl"
        assert read_records(path=path) == []
        assert read_records(skill="x", path=path) == []

    def test_corrupt_line_skipped_with_warning(self, tmp_path, capsys):
        path = tmp_path / "history.jsonl"
        append_record("s", 0.5, 0.5, {}, path=path)
        with path.open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")
        append_record("s", 0.7, 0.7, {}, path=path)

        records = read_records(path=path)
        assert len(records) == 2
        err = capsys.readouterr().err
        assert "corrupt history line" in err


class TestSparkline:
    def test_empty(self):
        assert sparkline([]) == ""

    def test_single_value_middle_glyph(self):
        mid = SPARK_GLYPHS[len(SPARK_GLYPHS) // 2]
        assert sparkline([1.0]) == mid

    def test_three_value_range(self):
        # Glyph set is "_.-=#" (5 levels). With min=0, max=1, values
        # [0, 0.5, 1.0] normalize to indices [0, 2, 4] -> "_-#".
        assert sparkline([0.0, 0.5, 1.0]) == "_-#"

    def test_all_equal_values(self):
        mid = SPARK_GLYPHS[len(SPARK_GLYPHS) // 2]
        assert sparkline([0.5, 0.5, 0.5]) == mid * 3

    def test_ascii_only(self):
        out = sparkline([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        assert all(ord(c) < 128 for c in out)
