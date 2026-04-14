"""Tests for clauditor.history."""

from __future__ import annotations

import pytest

from clauditor.history import (
    SCHEMA_VERSION,
    SPARK_GLYPHS,
    append_record,
    collect_metric_paths,
    read_records,
    resolve_path,
    sparkline,
)


class TestResolvePath:
    def test_top_level_pass_rate(self):
        assert resolve_path({"pass_rate": 0.5, "metrics": {}}, "pass_rate") == 0.5

    def test_top_level_mean_score(self):
        assert (
            resolve_path({"mean_score": 0.75, "metrics": {}}, "mean_score")
            == 0.75
        )

    def test_nested_grader_input_tokens(self):
        rec = {"metrics": {"grader": {"input_tokens": 500}}}
        assert resolve_path(rec, "grader.input_tokens") == 500

    def test_nested_total_total(self):
        rec = {"metrics": {"total": {"total": 1300}}}
        assert resolve_path(rec, "total.total") == 1300

    def test_duration_seconds_nested(self):
        rec = {"metrics": {"duration_seconds": 2.5}}
        assert resolve_path(rec, "duration_seconds") == 2.5

    def test_missing_path_returns_none(self):
        assert resolve_path({"metrics": {}}, "nonexistent.path") is None

    def test_non_numeric_returns_none(self):
        rec = {"metrics": {"grader": {"input_tokens": "oops"}}}
        assert resolve_path(rec, "grader.input_tokens") is None

    def test_missing_intermediate_returns_none(self):
        rec = {"metrics": {"grader": {}}}
        assert resolve_path(rec, "grader.input_tokens") is None

    def test_empty_record_pass_rate_none(self):
        assert resolve_path({}, "pass_rate") is None

    def test_pass_rate_none_value(self):
        assert resolve_path({"pass_rate": None, "metrics": {}}, "pass_rate") is None

    def test_dict_leaf_returns_none(self):
        rec = {"metrics": {"grader": {"input_tokens": 5}}}
        assert resolve_path(rec, "grader") is None

    def test_non_dict_record(self):
        assert resolve_path("not a dict", "pass_rate") is None  # type: ignore[arg-type]

    def test_bool_not_numeric(self):
        rec = {"metrics": {"flag": True}}
        assert resolve_path(rec, "flag") is None


class TestCollectMetricPaths:
    def test_v1_flat_record(self):
        rec = {"pass_rate": 0.8, "mean_score": 0.7, "metrics": {}}
        assert collect_metric_paths(rec) == {"pass_rate", "mean_score"}

    def test_v1_flat_record_only_pass_rate(self):
        rec = {"pass_rate": 0.8, "mean_score": None, "metrics": {}}
        assert collect_metric_paths(rec) == {"pass_rate"}

    def test_v2_nested_record(self):
        rec = {
            "pass_rate": 0.8,
            "mean_score": 0.7,
            "metrics": {
                "skill": {"input_tokens": 100, "output_tokens": 50},
                "grader": {"input_tokens": 500, "output_tokens": 200},
                "total": {
                    "input_tokens": 900,
                    "output_tokens": 400,
                    "total": 1300,
                },
                "duration_seconds": 2.5,
            },
        }
        paths = collect_metric_paths(rec)
        assert "pass_rate" in paths
        assert "mean_score" in paths
        assert "skill.input_tokens" in paths
        assert "skill.output_tokens" in paths
        assert "grader.input_tokens" in paths
        assert "total.total" in paths
        assert "total.input_tokens" in paths
        assert "duration_seconds" in paths

    def test_empty_record(self):
        assert collect_metric_paths({}) == set()

    def test_non_dict(self):
        assert collect_metric_paths("nope") == set()  # type: ignore[arg-type]


class TestAppendAndRead:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "history.jsonl"
        append_record(
            "skill-a", 0.8, 0.75, {"foo": 1}, command="grade", path=path
        )
        append_record("skill-b", 0.5, None, {}, command="grade", path=path)
        append_record(
            "skill-a", 0.9, 0.85, {"foo": 2}, command="grade", path=path
        )

        all_records = read_records(path=path)
        assert len(all_records) == 3
        assert all_records[0]["skill"] == "skill-a"
        assert all_records[0]["pass_rate"] == 0.8
        assert all_records[0]["mean_score"] == 0.75
        assert all_records[0]["metrics"] == {"foo": 1}
        assert all_records[0]["schema_version"] == SCHEMA_VERSION
        assert all_records[0]["command"] == "grade"
        assert "ts" in all_records[0]

        a_records = read_records(skill="skill-a", path=path)
        assert len(a_records) == 2
        assert all(r["skill"] == "skill-a" for r in a_records)

    def test_append_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "history.jsonl"
        append_record("s", 1.0, 1.0, {}, command="grade", path=path)
        assert path.exists()
        assert len(read_records(path=path)) == 1

    def test_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nope.jsonl"
        assert read_records(path=path) == []
        assert read_records(skill="x", path=path) == []

    def test_corrupt_line_skipped_with_warning(self, tmp_path, capsys):
        path = tmp_path / "history.jsonl"
        append_record("s", 0.5, 0.5, {}, command="grade", path=path)
        with path.open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")
        append_record("s", 0.7, 0.7, {}, command="grade", path=path)

        records = read_records(path=path)
        assert len(records) == 2
        err = capsys.readouterr().err
        assert "corrupt history line" in err

    def test_append_record_requires_command(self, tmp_path):
        path = tmp_path / "history.jsonl"
        with pytest.raises(TypeError):
            append_record("s", 1.0, 1.0, {}, path=path)  # type: ignore[call-arg]

    def test_schema_version_v2_written(self, tmp_path):
        path = tmp_path / "history.jsonl"
        append_record("s", 1.0, 1.0, {"k": 1}, command="grade", path=path)
        records = read_records(path=path)
        assert records[0]["schema_version"] == 2
        assert records[0]["command"] == "grade"

    def test_append_record_rejects_invalid_command(self, tmp_path):
        path = tmp_path / "history.jsonl"
        with pytest.raises(ValueError, match="command must be one of"):
            append_record(
                "s", 1.0, 1.0, {}, command="bogus", path=path  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError):
            append_record(
                "s", 1.0, 1.0, {}, command="GRADE", path=path  # type: ignore[arg-type]
            )
        # Valid values still work.
        for cmd in ("grade", "extract", "validate"):
            append_record("s", None, None, {}, command=cmd, path=path)
        assert len(read_records(path=path)) == 3


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
