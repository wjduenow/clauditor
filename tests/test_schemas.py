"""Tests for eval spec loading and schema definitions."""

import importlib
import json
import tempfile

import pytest

import clauditor.schemas as _schemas_mod

importlib.reload(_schemas_mod)

from clauditor.schemas import (  # noqa: E402
    EvalSpec,
    FieldRequirement,
    GradeThresholds,
    SectionRequirement,
    TierRequirement,
    TriggerTests,
    VarianceConfig,
)

SAMPLE_EVAL = {
    "skill_name": "find-kid-activities",
    "description": "Eval for /find-kid-activities",
    "test_args": '"Cupertino, CA" --dates today --cost Free --depth quick',
    "assertions": [
        {"type": "contains", "value": "Venues"},
        {"type": "has_entries", "value": "3"},
    ],
    "sections": [
        {
            "name": "Venues",
            "min_entries": 3,
            "fields": [
                {"name": "name", "required": True},
                {"name": "address", "required": True},
                {"name": "hours", "required": True},
                {"name": "website", "required": True},
                {"name": "phone", "required": False},
            ],
        },
        {
            "name": "Events",
            "min_entries": 0,
            "fields": [
                {"name": "name", "required": True},
                {"name": "date", "required": True},
                {"name": "event_url", "required": True},
            ],
        },
    ],
    "grading_criteria": ["Are venues within the specified distance?"],
}


class TestEvalSpecFromFile:
    def test_loads_valid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(SAMPLE_EVAL, f)
            f.flush()

            spec = EvalSpec.from_file(f.name)

        assert spec.skill_name == "find-kid-activities"
        assert len(spec.assertions) == 2
        assert len(spec.sections) == 2
        assert spec.sections[0].name == "Venues"
        # Legacy fields normalized to single default tier
        assert len(spec.sections[0].tiers) == 1
        tier = spec.sections[0].tiers[0]
        assert tier.label == "default"
        assert tier.min_entries == 3
        assert len(tier.fields) == 5
        assert tier.fields[0].name == "name"
        assert tier.fields[0].required is True
        assert tier.fields[4].required is False

    def test_missing_optional_fields(self):
        minimal = {"skill_name": "test"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(minimal, f)
            f.flush()

            spec = EvalSpec.from_file(f.name)

        assert spec.skill_name == "test"
        assert spec.assertions == []
        assert spec.sections == []
        assert spec.test_args == ""

    def test_roundtrip(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(SAMPLE_EVAL, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        d = spec.to_dict()
        assert d["skill_name"] == "find-kid-activities"
        assert len(d["sections"]) == 2
        assert d["sections"][0]["tiers"][0]["fields"][0]["name"] == "name"


class TestFieldRequirement:
    def test_defaults(self):
        f = FieldRequirement(name="test")
        assert f.required is True
        assert f.format is None

    def test_with_registry_format(self):
        f = FieldRequirement(name="phone", format="phone_us")
        assert f.format == "phone_us"

    def test_with_inline_regex_format(self):
        """DEC-007: format accepts an inline regex when no registry match."""
        f = FieldRequirement(name="phone", format=r"\(\d{3}\)\s\d{3}-\d{4}")
        assert f.format == r"\(\d{3}\)\s\d{3}-\d{4}"

    def test_invalid_format_raises_value_error(self):
        """DEC-011: bad format (not registry, not compilable regex) fails loud."""
        with pytest.raises(ValueError, match="nor a valid regex"):
            FieldRequirement(name="phone", format="[invalid")

    def test_empty_string_format_raises(self):
        """Empty string is neither a registry key nor meaningful regex."""
        with pytest.raises(ValueError, match="may not be an empty string"):
            FieldRequirement(name="phone", format="")


class TestFormatFieldRoundTrip:
    def test_format_preserved_in_roundtrip(self):
        data = {
            "skill_name": "test",
            "sections": [
                {
                    "name": "Items",
                    "min_entries": 1,
                    "fields": [
                        {
                            "name": "phone",
                            "required": True,
                            "format": "phone_us",
                        },
                        {
                            "name": "email",
                            "required": False,
                            "format": "email",
                        },
                    ],
                }
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        tier = spec.sections[0].tiers[0]
        assert tier.fields[0].format == "phone_us"
        assert tier.fields[1].format == "email"

        d = spec.to_dict()
        fields_out = d["sections"][0]["tiers"][0]["fields"]
        assert fields_out[0]["format"] == "phone_us"
        assert fields_out[1]["format"] == "email"

    def test_format_absent_backward_compat(self):
        data = {
            "skill_name": "test",
            "sections": [
                {
                    "name": "Items",
                    "min_entries": 1,
                    "fields": [
                        {"name": "name", "required": True},
                    ],
                }
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        tier = spec.sections[0].tiers[0]
        assert tier.fields[0].format is None

        d = spec.to_dict()
        fields_out = d["sections"][0]["tiers"][0]["fields"]
        assert "format" not in fields_out[0]

    def test_format_in_tiered_sections(self):
        data = {
            "skill_name": "test",
            "sections": [
                {
                    "name": "Items",
                    "tiers": [
                        {
                            "label": "main",
                            "min_entries": 1,
                            "fields": [
                                {
                                    "name": "url",
                                    "required": True,
                                    "format": "url",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        assert spec.sections[0].tiers[0].fields[0].format == "url"


class TestSectionRequirement:
    def test_defaults(self):
        s = SectionRequirement(name="Results")
        assert s.tiers == []


class TestTriggerTests:
    def test_defaults(self):
        t = TriggerTests()
        assert t.should_trigger == []
        assert t.should_not_trigger == []

    def test_with_values(self):
        t = TriggerTests(
            should_trigger=["query1"], should_not_trigger=["query2"]
        )
        assert t.should_trigger == ["query1"]
        assert t.should_not_trigger == ["query2"]


class TestVarianceConfig:
    def test_defaults(self):
        v = VarianceConfig()
        assert v.n_runs == 5
        assert v.min_stability == 0.8

    def test_custom_values(self):
        v = VarianceConfig(n_runs=10, min_stability=0.9)
        assert v.n_runs == 10
        assert v.min_stability == 0.9


class TestEvalSpecNewFields:
    def test_defaults_when_missing(self):
        minimal = {"skill_name": "test"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(minimal, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        assert spec.grading_model == "claude-sonnet-4-6"
        assert spec.trigger_tests is None
        assert spec.variance is None

    def test_parses_new_fields(self):
        data = {
            "skill_name": "test",
            "grading_model": "claude-opus-4-6",
            "trigger_tests": {
                "should_trigger": ["q1", "q2"],
                "should_not_trigger": ["q3"],
            },
            "variance": {"n_runs": 10, "min_stability": 0.9},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        assert spec.grading_model == "claude-opus-4-6"
        assert spec.trigger_tests is not None
        assert spec.trigger_tests.should_trigger == ["q1", "q2"]
        assert spec.trigger_tests.should_not_trigger == ["q3"]
        assert spec.variance is not None
        assert spec.variance.n_runs == 10
        assert spec.variance.min_stability == 0.9

    def test_roundtrip_with_new_fields(self):
        data = {
            "skill_name": "test",
            "grading_model": "claude-opus-4-6",
            "trigger_tests": {
                "should_trigger": ["q1"],
                "should_not_trigger": ["q2"],
            },
            "variance": {"n_runs": 3, "min_stability": 0.7},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".eval.json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            spec = EvalSpec.from_file(f.name)

        d = spec.to_dict()
        assert d["grading_model"] == "claude-opus-4-6"
        assert d["trigger_tests"]["should_trigger"] == ["q1"]
        assert d["trigger_tests"]["should_not_trigger"] == ["q2"]
        assert d["variance"]["n_runs"] == 3
        assert d["variance"]["min_stability"] == 0.7

    def test_to_dict_omits_none_fields(self):
        spec = EvalSpec(skill_name="test")
        d = spec.to_dict()
        assert "trigger_tests" not in d
        assert "variance" not in d
        assert d["grading_model"] == "claude-sonnet-4-6"


# --- New test classes for from_file, to_dict, and edge cases ---

FULL_EVAL_DATA = {
    "skill_name": "full-skill",
    "description": "A fully populated eval spec",
    "test_args": "--location NYC --depth full",
    "assertions": [
        {"type": "contains", "value": "Results"},
        {"type": "min_length", "value": "200"},
    ],
    "sections": [
        {
            "name": "Places",
            "min_entries": 2,
            "fields": [
                {"name": "name", "required": True},
                {"name": "address", "required": True},
                {"name": "zip", "required": False, "format": r"^\d{5}$"},
            ],
        },
    ],
    "grading_criteria": ["Are results relevant?", "Is formatting correct?"],
    "grading_model": "claude-opus-4-6",
    "trigger_tests": {
        "should_trigger": ["find places in NYC", "places near me"],
        "should_not_trigger": ["tell me a joke"],
    },
    "variance": {"n_runs": 8, "min_stability": 0.85},
}


def _write_json(tmp_path, data, name="eval.json"):
    """Helper to write JSON data to a temp file and return its path."""
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


class TestFromFile:
    """Tests for EvalSpec.from_file() covering full data, minimal, errors."""

    def test_from_file_full(self, tmp_path):
        """Load a complete eval.json with every field populated."""
        path = _write_json(tmp_path, FULL_EVAL_DATA)
        spec = EvalSpec.from_file(path)

        assert spec.skill_name == "full-skill"
        assert spec.description == "A fully populated eval spec"
        assert spec.test_args == "--location NYC --depth full"
        assert len(spec.assertions) == 2
        assert len(spec.sections) == 1
        assert spec.sections[0].name == "Places"
        tier = spec.sections[0].tiers[0]
        assert tier.label == "default"
        assert tier.min_entries == 2
        assert len(tier.fields) == 3
        assert tier.fields[2].format == r"^\d{5}$"
        assert tier.fields[2].required is False
        assert spec.grading_criteria == [
            "Are results relevant?",
            "Is formatting correct?",
        ]
        assert spec.grading_model == "claude-opus-4-6"
        assert spec.trigger_tests is not None
        assert spec.variance is not None

    def test_from_file_minimal(self, tmp_path):
        """Load with only skill_name; everything else uses defaults."""
        path = _write_json(tmp_path, {"skill_name": "bare"})
        spec = EvalSpec.from_file(path)

        assert spec.skill_name == "bare"
        assert spec.description == ""
        assert spec.test_args == ""
        assert spec.assertions == []
        assert spec.sections == []
        assert spec.grading_criteria == []
        assert spec.grading_model == "claude-sonnet-4-6"
        assert spec.trigger_tests is None
        assert spec.variance is None

    def test_from_file_empty_dict_defaults_skill_name_to_stem(self, tmp_path):
        """When skill_name is missing, defaults to the file stem."""
        path = _write_json(tmp_path, {}, name="my-skill.eval.json")
        spec = EvalSpec.from_file(path)

        assert spec.skill_name == "my-skill.eval"

    def test_from_file_missing(self, tmp_path):
        """Raises FileNotFoundError for a nonexistent file."""
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            EvalSpec.from_file(missing)

    def test_from_file_malformed_json(self, tmp_path):
        """Raises json.JSONDecodeError for invalid JSON content."""
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json!!!")
        with pytest.raises(json.JSONDecodeError):
            EvalSpec.from_file(bad)

    def test_from_file_field_format_none_when_absent(self, tmp_path):
        """Fields without a format key have format=None."""
        data = {
            "skill_name": "test",
            "sections": [
                {"name": "S", "fields": [{"name": "f1", "required": True}]}
            ],
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        assert spec.sections[0].tiers[0].fields[0].format is None

    def test_from_file_legacy_pattern_key_rejected(self, tmp_path):
        """DEC-006: legacy 'pattern' key is rejected with a migration message."""
        data = {
            "skill_name": "test",
            "sections": [
                {
                    "name": "S",
                    "fields": [
                        {"name": "f1", "required": True, "pattern": r"\d+"},
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(ValueError, match="no longer supported"):
            EvalSpec.from_file(path)


class TestToDict:
    """Tests for EvalSpec.to_dict() serialization and round-trip."""

    def test_to_dict_roundtrip(self, tmp_path):
        """from_file -> to_dict produces data equivalent to the input."""
        path = _write_json(tmp_path, FULL_EVAL_DATA)
        spec = EvalSpec.from_file(path)
        d = spec.to_dict()

        assert d["skill_name"] == FULL_EVAL_DATA["skill_name"]
        assert d["description"] == FULL_EVAL_DATA["description"]
        assert d["test_args"] == FULL_EVAL_DATA["test_args"]
        assert d["assertions"] == FULL_EVAL_DATA["assertions"]
        assert d["grading_criteria"] == FULL_EVAL_DATA["grading_criteria"]
        assert d["grading_model"] == FULL_EVAL_DATA["grading_model"]
        # Sections structure matches (legacy normalized to tiers)
        assert len(d["sections"]) == 1
        assert d["sections"][0]["name"] == "Places"
        tier = d["sections"][0]["tiers"][0]
        assert tier["label"] == "default"
        assert tier["min_entries"] == 2
        assert len(tier["fields"]) == 3
        # Format included only where present (pattern field removed)
        assert tier["fields"][2]["format"] == r"^\d{5}$"
        assert "format" not in tier["fields"][0]
        assert "pattern" not in tier["fields"][2]
        # Trigger tests
        assert d["trigger_tests"] == FULL_EVAL_DATA["trigger_tests"]
        # Variance
        assert d["variance"] == FULL_EVAL_DATA["variance"]

    def test_to_dict_reload_produces_equal_spec(self, tmp_path):
        """to_dict output can be written and re-loaded to get an equal spec."""
        path1 = _write_json(tmp_path, FULL_EVAL_DATA, name="first.json")
        spec1 = EvalSpec.from_file(path1)
        d = spec1.to_dict()
        path2 = _write_json(tmp_path, d, name="second.json")
        spec2 = EvalSpec.from_file(path2)

        assert spec1.skill_name == spec2.skill_name
        assert spec1.description == spec2.description
        assert spec1.test_args == spec2.test_args
        assert spec1.assertions == spec2.assertions
        assert spec1.grading_model == spec2.grading_model
        assert spec1.grading_criteria == spec2.grading_criteria
        assert spec1.trigger_tests.should_trigger == spec2.trigger_tests.should_trigger
        assert (
            spec1.trigger_tests.should_not_trigger
            == spec2.trigger_tests.should_not_trigger
        )
        assert spec1.variance.n_runs == spec2.variance.n_runs
        assert spec1.variance.min_stability == spec2.variance.min_stability

    def test_to_dict_omits_trigger_tests_when_none(self):
        """trigger_tests key absent when field is None."""
        spec = EvalSpec(skill_name="x")
        d = spec.to_dict()
        assert "trigger_tests" not in d

    def test_to_dict_omits_variance_when_none(self):
        """variance key absent when field is None."""
        spec = EvalSpec(skill_name="x")
        d = spec.to_dict()
        assert "variance" not in d


class TestOptionalFields:
    """Tests for trigger_tests and variance loading from files."""

    def test_trigger_tests_loaded(self, tmp_path):
        """trigger_tests section is correctly parsed into TriggerTests."""
        data = {
            "skill_name": "t",
            "trigger_tests": {
                "should_trigger": ["a", "b"],
                "should_not_trigger": ["c"],
            },
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert isinstance(spec.trigger_tests, TriggerTests)
        assert spec.trigger_tests.should_trigger == ["a", "b"]
        assert spec.trigger_tests.should_not_trigger == ["c"]

    def test_variance_config_loaded(self, tmp_path):
        """variance section is correctly parsed into VarianceConfig."""
        data = {
            "skill_name": "v",
            "variance": {"n_runs": 12, "min_stability": 0.95},
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert isinstance(spec.variance, VarianceConfig)
        assert spec.variance.n_runs == 12
        assert spec.variance.min_stability == 0.95

    def test_no_trigger_tests(self, tmp_path):
        """trigger_tests is None when key absent from JSON."""
        path = _write_json(tmp_path, {"skill_name": "no-tt"})
        spec = EvalSpec.from_file(path)
        assert spec.trigger_tests is None

    def test_no_variance(self, tmp_path):
        """variance is None when key absent from JSON."""
        path = _write_json(tmp_path, {"skill_name": "no-var"})
        spec = EvalSpec.from_file(path)
        assert spec.variance is None

    def test_trigger_tests_defaults_empty_lists(self, tmp_path):
        """trigger_tests with empty dict gets default empty lists."""
        data = {"skill_name": "t", "trigger_tests": {}}
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert spec.trigger_tests is not None
        assert spec.trigger_tests.should_trigger == []
        assert spec.trigger_tests.should_not_trigger == []

    def test_variance_defaults(self, tmp_path):
        """variance with empty dict gets default values."""
        data = {"skill_name": "v", "variance": {}}
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert spec.variance is not None
        assert spec.variance.n_runs == 5
        assert spec.variance.min_stability == 0.8


class TestEvalSpecOutputFields:
    """Tests for output_file and output_files fields on EvalSpec."""

    def test_load_with_output_file(self, tmp_path):
        """output_file is loaded from JSON when present."""
        data = {"skill_name": "s", "output_file": "report.md"}
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        assert spec.output_file == "report.md"

    def test_load_with_output_files(self, tmp_path):
        """output_files list is loaded from JSON when present."""
        data = {"skill_name": "s", "output_files": ["out/*.md", "summary.txt"]}
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        assert spec.output_files == ["out/*.md", "summary.txt"]

    def test_to_dict_roundtrip(self, tmp_path):
        """from_file -> to_dict preserves both output fields."""
        data = {
            "skill_name": "s",
            "output_file": "result.json",
            "output_files": ["a.txt", "b.txt"],
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        d = spec.to_dict()
        assert d["output_file"] == "result.json"
        assert d["output_files"] == ["a.txt", "b.txt"]

    def test_defaults_when_missing(self, tmp_path):
        """output_file defaults to None, output_files to [] when absent."""
        path = _write_json(tmp_path, {"skill_name": "bare"})
        spec = EvalSpec.from_file(path)
        assert spec.output_file is None
        assert spec.output_files == []

    def test_backward_compat_old_eval_json(self, tmp_path):
        """Old eval.json without output fields loads without error."""
        old_data = {
            "skill_name": "legacy",
            "description": "An old spec",
            "assertions": [{"type": "contains", "value": "hello"}],
        }
        path = _write_json(tmp_path, old_data)
        spec = EvalSpec.from_file(path)
        assert spec.skill_name == "legacy"
        assert spec.output_file is None
        assert spec.output_files == []

    def test_to_dict_omits_defaults(self):
        """to_dict omits output_file and output_files when at defaults."""
        spec = EvalSpec(skill_name="x")
        d = spec.to_dict()
        assert "output_file" not in d
        assert "output_files" not in d


class TestGradeThresholds:
    """Tests for GradeThresholds loading, defaults, and round-trip."""

    def test_defaults(self):
        gt = GradeThresholds()
        assert gt.min_pass_rate == 0.7
        assert gt.min_mean_score == 0.5

    def test_from_file_with_grade_thresholds(self, tmp_path):
        """grade_thresholds section is correctly parsed from JSON."""
        data = {
            "skill_name": "gt-test",
            "grade_thresholds": {
                "min_pass_rate": 0.9,
                "min_mean_score": 0.8,
            },
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert spec.grade_thresholds is not None
        assert spec.grade_thresholds.min_pass_rate == 0.9
        assert spec.grade_thresholds.min_mean_score == 0.8

    def test_from_file_without_grade_thresholds(self, tmp_path):
        """grade_thresholds is None when absent from JSON."""
        path = _write_json(tmp_path, {"skill_name": "no-gt"})
        spec = EvalSpec.from_file(path)
        assert spec.grade_thresholds is None

    def test_roundtrip(self, tmp_path):
        """from_file -> to_dict -> from_file preserves grade_thresholds."""
        data = {
            "skill_name": "rt",
            "grade_thresholds": {
                "min_pass_rate": 0.85,
                "min_mean_score": 0.6,
            },
        }
        path1 = _write_json(tmp_path, data, name="first.json")
        spec1 = EvalSpec.from_file(path1)
        d = spec1.to_dict()
        assert d["grade_thresholds"]["min_pass_rate"] == 0.85
        assert d["grade_thresholds"]["min_mean_score"] == 0.6

        path2 = _write_json(tmp_path, d, name="second.json")
        spec2 = EvalSpec.from_file(path2)
        assert spec2.grade_thresholds.min_pass_rate == 0.85
        assert spec2.grade_thresholds.min_mean_score == 0.6

    def test_to_dict_omits_when_none(self):
        """grade_thresholds key absent from to_dict when field is None."""
        spec = EvalSpec(skill_name="x")
        d = spec.to_dict()
        assert "grade_thresholds" not in d

    def test_from_file_empty_dict_uses_defaults(self, tmp_path):
        """grade_thresholds with empty dict gets default values."""
        data = {"skill_name": "gt", "grade_thresholds": {}}
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert spec.grade_thresholds is not None
        assert spec.grade_thresholds.min_pass_rate == 0.7
        assert spec.grade_thresholds.min_mean_score == 0.5


class TestTierRequirement:
    """Tests for TierRequirement dataclass."""

    def test_defaults(self):
        t = TierRequirement(label="basic")
        assert t.label == "basic"
        assert t.description == ""
        assert t.min_entries == 0
        assert t.fields == []

    def test_with_all_fields(self):
        t = TierRequirement(
            label="premium",
            description="High quality entries",
            min_entries=5,
            fields=[FieldRequirement(name="url")],
        )
        assert t.label == "premium"
        assert t.description == "High quality entries"
        assert t.min_entries == 5
        assert len(t.fields) == 1

    def test_max_entries_default_none(self):
        t = TierRequirement(label="basic")
        assert t.max_entries is None

    def test_max_entries_roundtrip(self, tmp_path):
        """max_entries parses from JSON and serializes back via to_dict."""
        data = {
            "skill_name": "max-entries-skill",
            "sections": [
                {
                    "name": "Venues",
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 1,
                            "max_entries": 3,
                            "fields": [{"name": "name", "required": True}],
                        }
                    ],
                }
            ],
        }
        path = tmp_path / "spec.eval.json"
        path.write_text(json.dumps(data))
        spec = EvalSpec.from_file(path)
        assert spec.sections[0].tiers[0].max_entries == 3

        out = spec.to_dict()
        tier_out = out["sections"][0]["tiers"][0]
        assert tier_out["max_entries"] == 3

    def test_max_entries_none_omitted_from_dict(self, tmp_path):
        """When max_entries is None, it is omitted from serialized output."""
        data = {
            "skill_name": "default-skill",
            "sections": [
                {
                    "name": "Venues",
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 1,
                            "fields": [{"name": "name", "required": True}],
                        }
                    ],
                }
            ],
        }
        path = tmp_path / "spec.eval.json"
        path.write_text(json.dumps(data))
        spec = EvalSpec.from_file(path)
        assert spec.sections[0].tiers[0].max_entries is None

        out = spec.to_dict()
        tier_out = out["sections"][0]["tiers"][0]
        assert "max_entries" not in tier_out


TIERED_SECTION_DATA = {
    "skill_name": "tiered-skill",
    "sections": [
        {
            "name": "Venues",
            "tiers": [
                {
                    "label": "basic",
                    "description": "Minimum viable info",
                    "min_entries": 3,
                    "fields": [
                        {"name": "name", "required": True},
                        {"name": "address", "required": True},
                    ],
                },
                {
                    "label": "detailed",
                    "description": "Rich venue data",
                    "min_entries": 1,
                    "fields": [
                        {"name": "name", "required": True},
                        {"name": "address", "required": True},
                        {"name": "hours", "required": True},
                        {"name": "website", "required": False},
                    ],
                },
            ],
        }
    ],
}


class TestTieredSections:
    """Tests for tiered section loading, normalization, and roundtrip."""

    def test_load_tiered_json(self, tmp_path):
        """Load JSON with explicit tiers array."""
        path = _write_json(tmp_path, TIERED_SECTION_DATA)
        spec = EvalSpec.from_file(path)

        assert len(spec.sections) == 1
        section = spec.sections[0]
        assert section.name == "Venues"
        assert len(section.tiers) == 2

        assert section.tiers[0].label == "basic"
        assert section.tiers[0].description == "Minimum viable info"
        assert section.tiers[0].min_entries == 3
        assert len(section.tiers[0].fields) == 2

        assert section.tiers[1].label == "detailed"
        assert section.tiers[1].description == "Rich venue data"
        assert section.tiers[1].min_entries == 1
        assert len(section.tiers[1].fields) == 4

    def test_legacy_fields_normalized_to_default_tier(self, tmp_path):
        """Legacy fields-style JSON is normalized to a single default tier."""
        legacy_data = {
            "skill_name": "legacy",
            "sections": [
                {
                    "name": "Results",
                    "min_entries": 5,
                    "fields": [
                        {"name": "title", "required": True},
                        {"name": "url", "required": False},
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, legacy_data)
        spec = EvalSpec.from_file(path)

        section = spec.sections[0]
        assert len(section.tiers) == 1
        tier = section.tiers[0]
        assert tier.label == "default"
        assert tier.description == ""
        assert tier.min_entries == 5
        assert len(tier.fields) == 2
        assert tier.fields[0].name == "title"
        assert tier.fields[1].required is False

    def test_tiered_roundtrip(self, tmp_path):
        """from_file -> to_dict -> from_file preserves tiered structure."""
        path1 = _write_json(tmp_path, TIERED_SECTION_DATA, name="first.json")
        spec1 = EvalSpec.from_file(path1)
        d = spec1.to_dict()

        # Verify dict structure
        assert len(d["sections"]) == 1
        assert len(d["sections"][0]["tiers"]) == 2
        assert d["sections"][0]["tiers"][0]["label"] == "basic"
        assert d["sections"][0]["tiers"][1]["label"] == "detailed"

        # Reload and verify equality
        path2 = _write_json(tmp_path, d, name="second.json")
        spec2 = EvalSpec.from_file(path2)

        assert len(spec2.sections[0].tiers) == 2
        assert spec2.sections[0].tiers[0].label == "basic"
        assert spec2.sections[0].tiers[0].min_entries == 3
        assert spec2.sections[0].tiers[1].label == "detailed"
        assert len(spec2.sections[0].tiers[1].fields) == 4

    def test_legacy_roundtrip(self, tmp_path):
        """Legacy format -> load -> to_dict -> reload produces consistent tiers."""
        legacy_data = {
            "skill_name": "legacy-rt",
            "sections": [
                {
                    "name": "Items",
                    "min_entries": 2,
                    "fields": [{"name": "name", "required": True}],
                }
            ],
        }
        path1 = _write_json(tmp_path, legacy_data, name="first.json")
        spec1 = EvalSpec.from_file(path1)
        d = spec1.to_dict()

        # Output always uses tiers form
        assert "tiers" in d["sections"][0]
        assert d["sections"][0]["tiers"][0]["label"] == "default"

        # Reload produces same structure
        path2 = _write_json(tmp_path, d, name="second.json")
        spec2 = EvalSpec.from_file(path2)
        assert spec2.sections[0].tiers[0].label == "default"
        assert spec2.sections[0].tiers[0].min_entries == 2
        assert len(spec2.sections[0].tiers[0].fields) == 1

    def test_tier_description_preserved_through_roundtrip(self, tmp_path):
        """Tier description field survives from_file -> to_dict -> from_file."""
        path1 = _write_json(tmp_path, TIERED_SECTION_DATA, name="first.json")
        spec1 = EvalSpec.from_file(path1)

        d = spec1.to_dict()
        assert d["sections"][0]["tiers"][0]["description"] == "Minimum viable info"
        assert d["sections"][0]["tiers"][1]["description"] == "Rich venue data"

        path2 = _write_json(tmp_path, d, name="second.json")
        spec2 = EvalSpec.from_file(path2)
        assert spec2.sections[0].tiers[0].description == "Minimum viable info"
        assert spec2.sections[0].tiers[1].description == "Rich venue data"

    def test_tier_defaults_for_optional_fields(self, tmp_path):
        """Tiers with missing optional fields get defaults."""
        data = {
            "skill_name": "sparse",
            "sections": [
                {
                    "name": "S",
                    "tiers": [{"label": "minimal"}],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        tier = spec.sections[0].tiers[0]
        assert tier.label == "minimal"
        assert tier.description == ""
        assert tier.min_entries == 0
        assert tier.fields == []
