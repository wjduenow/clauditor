"""Tests for clauditor.metrics — TokenUsage and build_metrics()."""
from __future__ import annotations

from clauditor.metrics import TokenUsage, build_metrics


class TestTokenUsage:
    def test_to_dict_shape(self):
        tu = TokenUsage(input_tokens=10, output_tokens=20)
        assert tu.to_dict() == {"input_tokens": 10, "output_tokens": 20}

    def test_total_property(self):
        tu = TokenUsage(input_tokens=10, output_tokens=20)
        assert tu.total == 30

    def test_defaults_zero(self):
        tu = TokenUsage()
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0
        assert tu.total == 0
        assert tu.to_dict() == {"input_tokens": 0, "output_tokens": 0}


class TestBuildMetrics:
    def test_skill_only(self):
        m = build_metrics(skill=TokenUsage(100, 50), duration_seconds=1.0)
        assert "skill" in m
        assert "grader" not in m
        assert "quality" not in m
        assert "triggers" not in m
        assert m["skill"] == {"input_tokens": 100, "output_tokens": 50}
        assert m["total"] == {"input_tokens": 100, "output_tokens": 50, "total": 150}
        assert m["duration_seconds"] == 1.0

    def test_all_four_buckets(self):
        m = build_metrics(
            skill=TokenUsage(100, 50),
            duration_seconds=2.5,
            grader=TokenUsage(500, 200),
            quality=TokenUsage(300, 100),
            triggers=TokenUsage(40, 10),
        )
        assert m["skill"] == {"input_tokens": 100, "output_tokens": 50}
        assert m["grader"] == {"input_tokens": 500, "output_tokens": 200}
        assert m["quality"] == {"input_tokens": 300, "output_tokens": 100}
        assert m["triggers"] == {"input_tokens": 40, "output_tokens": 10}
        # sums
        in_sum = 100 + 500 + 300 + 40
        out_sum = 50 + 200 + 100 + 10
        assert m["total"] == {
            "input_tokens": in_sum,
            "output_tokens": out_sum,
            "total": in_sum + out_sum,
        }
        assert m["duration_seconds"] == 2.5

    def test_zero_token_skill(self):
        m = build_metrics(skill=TokenUsage(0, 0), duration_seconds=0.1)
        assert m["skill"] == {"input_tokens": 0, "output_tokens": 0}
        assert m["total"]["total"] == 0
        assert m["total"]["input_tokens"] == 0
        assert m["total"]["output_tokens"] == 0

    def test_duration_flows_through(self):
        m = build_metrics(skill=TokenUsage(1, 1), duration_seconds=1.5)
        assert m["duration_seconds"] == 1.5

    def test_grader_only(self):
        """cmd_extract case: skill + grader, no quality/triggers."""
        m = build_metrics(
            skill=TokenUsage(100, 50),
            duration_seconds=2.0,
            grader=TokenUsage(500, 200),
        )
        assert set(m.keys()) == {"skill", "grader", "total", "duration_seconds"}
        assert m["total"]["total"] == 850

    def test_zero_bucket_is_present_when_explicitly_passed(self):
        """None is the absence signal; TokenUsage(0, 0) is still presence."""
        m = build_metrics(
            skill=TokenUsage(10, 5),
            duration_seconds=1.0,
            grader=TokenUsage(0, 0),
        )
        assert "grader" in m
        assert m["grader"] == {"input_tokens": 0, "output_tokens": 0}
        assert "quality" not in m
        assert "triggers" not in m
        assert m["total"]["total"] == 15
