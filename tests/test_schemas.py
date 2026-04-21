"""Tests for eval spec loading and schema definitions."""

import importlib
import json
import tempfile

import pytest

import clauditor.schemas as _schemas_mod

importlib.reload(_schemas_mod)

from clauditor.schemas import (  # noqa: E402
    _ASSERTION_DRIFT_HINTS,
    ASSERTION_TYPE_REQUIRED_KEYS,
    AssertionKeySpec,
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
        {"id": "a_venues", "type": "contains", "needle": "Venues"},
        {"id": "a_entries", "type": "has_entries", "count": 3},
    ],
    "sections": [
        {
            "name": "Venues",
            "tiers": [
                {
                    "label": "default",
                    "min_entries": 3,
                    "fields": [
                        {"id": "v_name", "name": "name", "required": True},
                        {"id": "v_address", "name": "address", "required": True},
                        {"id": "v_hours", "name": "hours", "required": True},
                        {"id": "v_website", "name": "website", "required": True},
                        {"id": "v_phone", "name": "phone", "required": False},
                    ],
                }
            ],
        },
        {
            "name": "Events",
            "tiers": [
                {
                    "label": "default",
                    "min_entries": 0,
                    "fields": [
                        {"id": "e_name", "name": "name", "required": True},
                        {"id": "e_date", "name": "date", "required": True},
                        {"id": "e_url", "name": "event_url", "required": True},
                    ],
                }
            ],
        },
    ],
    "grading_criteria": [
        {"id": "c_distance", "criterion": "Are venues within the specified distance?"}
    ],
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
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 1,
                            "fields": [
                                {
                                    "id": "f_phone",
                                    "name": "phone",
                                    "required": True,
                                    "format": "phone_us",
                                },
                                {
                                    "id": "f_email",
                                    "name": "email",
                                    "required": False,
                                    "format": "email",
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
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 1,
                            "fields": [
                                {"id": "f_name", "name": "name", "required": True},
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
                                    "id": "f_url",
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
        {"id": "a_contains", "type": "contains", "needle": "Results"},
        {"id": "a_minlen", "type": "min_length", "length": 200},
    ],
    "sections": [
        {
            "name": "Places",
            "tiers": [
                {
                    "label": "default",
                    "min_entries": 2,
                    "fields": [
                        {"id": "p_name", "name": "name", "required": True},
                        {"id": "p_address", "name": "address", "required": True},
                        {
                            "id": "p_zip",
                            "name": "zip",
                            "required": False,
                            "format": r"^\d{5}$",
                        },
                    ],
                }
            ],
        },
    ],
    "grading_criteria": [
        {"id": "c_relevant", "criterion": "Are results relevant?"},
        {"id": "c_format", "criterion": "Is formatting correct?"},
    ],
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
            {"id": "c_relevant", "criterion": "Are results relevant?"},
            {"id": "c_format", "criterion": "Is formatting correct?"},
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
                {
                    "name": "S",
                    "tiers": [
                        {
                            "label": "default",
                            "fields": [
                                {"id": "f1", "name": "f1", "required": True}
                            ],
                        }
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        assert spec.sections[0].tiers[0].fields[0].format is None

    def test_assertion_missing_id_rejected(self, tmp_path):
        """DEC-001 (#25): every assertion must carry an explicit id."""
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "ok", "type": "contains", "needle": "a"},
                {"type": "contains", "needle": "b"},  # missing id
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(ValueError, match=r"assertions\[1\]: missing 'id'"):
            EvalSpec.from_file(path)

    def test_assertion_duplicate_id_rejected(self, tmp_path):
        """DEC-001 (#25): assertion ids must be unique within the skill."""
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "dup", "type": "contains", "needle": "a"},
                {"id": "dup", "type": "contains", "needle": "b"},
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match=r"assertions\[1\]: duplicate id 'dup'"
        ):
            EvalSpec.from_file(path)

    def test_field_requirement_missing_id_rejected(self, tmp_path):
        """DEC-001 (#25): every FieldRequirement must carry an explicit id."""
        data = {
            "skill_name": "s",
            "sections": [
                {
                    "name": "S",
                    "tiers": [
                        {
                            "label": "default",
                            "fields": [
                                {"id": "ok", "name": "a"},
                                {"name": "b"},  # missing id
                            ],
                        }
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError,
            match=r"sections\[0\]\.tiers\[0\]\.fields\[1\]: missing 'id'",
        ):
            EvalSpec.from_file(path)

    def test_flat_fields_without_tiers_rejected(self, tmp_path):
        """Section with flat fields (no tiers) raises ValueError."""
        data = {
            "skill_name": "s",
            "sections": [
                {
                    "name": "S",
                    "fields": [{"name": "x"}],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match="flat .fields. without .tiers"
        ):
            EvalSpec.from_file(path)

    def test_criterion_missing_id_rejected(self, tmp_path):
        """DEC-001 (#25): every grading criterion must carry an explicit id."""
        data = {
            "skill_name": "s",
            "grading_criteria": [
                {"id": "ok", "criterion": "a"},
                {"criterion": "b"},  # missing id
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match=r"grading_criteria\[1\]: missing 'id'"
        ):
            EvalSpec.from_file(path)

    def test_criterion_missing_text_rejected(self, tmp_path):
        """Criterion entries must include a non-empty string 'criterion'."""
        data = {
            "skill_name": "s",
            "grading_criteria": [{"id": "c1"}],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError,
            match=r"grading_criteria\[0\]: 'criterion' must be a non-empty string",
        ):
            EvalSpec.from_file(path)

    def test_criterion_empty_text_rejected(self, tmp_path):
        """An empty ``criterion`` string must be rejected the same way as a
        missing one — mirrors the non-empty check on ids."""
        data = {
            "skill_name": "s",
            "grading_criteria": [{"id": "c1", "criterion": ""}],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError,
            match=r"grading_criteria\[0\]: 'criterion' must be a non-empty string",
        ):
            EvalSpec.from_file(path)

    def test_assertion_empty_id_rejected(self, tmp_path):
        """``id: ""`` must be rejected — covers the non-empty string branch
        of ``_require_id``."""
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "", "type": "contains", "needle": "x"},
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError,
            match=r"assertions\[0\]: 'id' must be a non-empty string",
        ):
            EvalSpec.from_file(path)

    def test_assertion_non_dict_entry_rejected(self, tmp_path):
        """Non-dict assertion entries (strings, numbers, lists) must be
        rejected with a clear type error — covers the non-dict branch of
        ``_require_id``."""
        data = {
            "skill_name": "s",
            "assertions": ["not-a-dict"],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError,
            match=r"assertions\[0\] — expected object, got str",
        ):
            EvalSpec.from_file(path)

    def test_criterion_duplicate_id_rejected(self, tmp_path):
        """Criterion ids must be unique within the skill."""
        data = {
            "skill_name": "s",
            "grading_criteria": [
                {"id": "same", "criterion": "a"},
                {"id": "same", "criterion": "b"},
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match=r"grading_criteria\[1\]: duplicate id 'same'"
        ):
            EvalSpec.from_file(path)

    def test_valid_spec_with_ids_loads(self, tmp_path):
        """A spec with ids on all assertions/fields/criteria loads cleanly
        and the ids are surfaced on FieldRequirement / criteria dicts."""
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "a1", "type": "contains", "needle": "x"},
                {"id": "a2", "type": "min_length", "length": 10},
            ],
            "sections": [
                {
                    "name": "Places",
                    "tiers": [
                        {
                            "label": "default",
                            "fields": [
                                {"id": "f_name", "name": "name"},
                                {"id": "f_url", "name": "url"},
                            ],
                        }
                    ],
                }
            ],
            "grading_criteria": [
                {"id": "c_rel", "criterion": "Is it relevant?"},
                {"id": "c_spec", "criterion": "Is it specific?"},
            ],
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)

        assert [a["id"] for a in spec.assertions] == ["a1", "a2"]
        tier = spec.sections[0].tiers[0]
        assert [f.id for f in tier.fields] == ["f_name", "f_url"]
        assert [c["id"] for c in spec.grading_criteria] == ["c_rel", "c_spec"]

    def test_id_uniqueness_spans_all_layers(self, tmp_path):
        """A duplicate id across an assertion and a field is rejected —
        uniqueness scope is the whole skill, not per-layer."""
        data = {
            "skill_name": "s",
            "assertions": [{"id": "shared", "type": "contains", "needle": "x"}],
            "sections": [
                {
                    "name": "S",
                    "tiers": [
                        {
                            "label": "default",
                            "fields": [{"id": "shared", "name": "name"}],
                        }
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(ValueError, match=r"duplicate id 'shared'"):
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
        # Sections structure matches
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
            "assertions": [{"id": "a_hello", "type": "contains", "needle": "hello"}],
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
                            "fields": [
                                {"id": "f_name", "name": "name", "required": True}
                            ],
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

    def test_flat_fields_with_max_entries_rejected(self, tmp_path):
        """Flat fields at section level are rejected even with max_entries."""
        data = {
            "skill_name": "legacy-skill",
            "sections": [
                {
                    "name": "Venues",
                    "min_entries": 1,
                    "max_entries": 3,
                    "fields": [{"id": "f_name", "name": "name", "required": True}],
                }
            ],
        }
        path = tmp_path / "spec.eval.json"
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="flat .fields. without .tiers"):
            EvalSpec.from_file(path)

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
                            "fields": [
                                {"id": "f_name", "name": "name", "required": True}
                            ],
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
                        {"id": "basic_name", "name": "name", "required": True},
                        {"id": "basic_address", "name": "address", "required": True},
                    ],
                },
                {
                    "label": "detailed",
                    "description": "Rich venue data",
                    "min_entries": 1,
                    "fields": [
                        {"id": "detailed_name", "name": "name", "required": True},
                        {
                            "id": "detailed_address",
                            "name": "address",
                            "required": True,
                        },
                        {
                            "id": "detailed_hours",
                            "name": "hours",
                            "required": True,
                        },
                        {
                            "id": "detailed_website",
                            "name": "website",
                            "required": False,
                        },
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

    def test_from_file_rejects_flat_fields_shape(self, tmp_path):
        """Flat fields (without tiers) raises ValueError."""
        legacy_data = {
            "skill_name": "legacy",
            "sections": [
                {
                    "name": "Results",
                    "min_entries": 5,
                    "fields": [
                        {"id": "r_title", "name": "title", "required": True},
                        {"id": "r_url", "name": "url", "required": False},
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, legacy_data)
        with pytest.raises(ValueError, match="flat .fields. without .tiers"):
            EvalSpec.from_file(path)

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

    def test_flat_fields_rejected_even_with_min_entries(self, tmp_path):
        """Flat fields at section level are rejected regardless of other keys."""
        legacy_data = {
            "skill_name": "legacy-rt",
            "sections": [
                {
                    "name": "Items",
                    "min_entries": 2,
                    "fields": [{"id": "f_name", "name": "name", "required": True}],
                }
            ],
        }
        path = _write_json(tmp_path, legacy_data, name="first.json")
        with pytest.raises(ValueError, match="flat .fields. without .tiers"):
            EvalSpec.from_file(path)

    def test_pattern_key_rejected(self, tmp_path):
        """Field entries using 'pattern' instead of 'format' are rejected."""
        data = {
            "skill_name": "test",
            "sections": [
                {
                    "name": "S",
                    "tiers": [
                        {
                            "label": "required",
                            "fields": [
                                {"id": "f1", "name": "f1", "required": True,
                                 "pattern": r"\d+"},
                            ],
                        }
                    ],
                }
            ],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(ValueError, match="use 'format', not 'pattern'"):
            EvalSpec.from_file(path)

    def test_section_missing_tiers_rejected(self, tmp_path):
        """Section with neither 'tiers' nor 'fields' raises ValueError."""
        data = {
            "skill_name": "test",
            "sections": [{"name": "S"}],
        }
        path = _write_json(tmp_path, data)
        with pytest.raises(ValueError, match="missing 'tiers'"):
            EvalSpec.from_file(path)

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


class TestEvalSpecInputFiles:
    def _write_spec(self, tmp_path, extra):
        data = {"skill_name": "test-skill", **extra}
        spec_path = tmp_path / "test.eval.json"
        spec_path.write_text(json.dumps(data))
        return spec_path

    def test_input_files_defaults_to_empty_list(self, tmp_path):
        spec_path = self._write_spec(tmp_path, {})
        spec = EvalSpec.from_file(spec_path)
        assert spec.input_files == []

    def test_relative_paths_resolved_against_spec_dir(self, tmp_path):
        sibling = tmp_path / "sales.csv"
        sibling.write_text("a,b\n")
        spec_path = self._write_spec(tmp_path, {"input_files": ["sales.csv"]})
        spec = EvalSpec.from_file(spec_path)
        assert len(spec.input_files) == 1
        assert spec.input_files[0] == str(sibling.resolve())
        from pathlib import Path
        assert Path(spec.input_files[0]).is_absolute()

    def test_multiple_entries_resolved(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        f1 = tmp_path / "a" / "one.csv"
        f2 = tmp_path / "b" / "two.csv"
        f1.write_text("x")
        f2.write_text("y")
        spec_path = self._write_spec(
            tmp_path, {"input_files": ["a/one.csv", "b/two.csv"]}
        )
        spec = EvalSpec.from_file(spec_path)
        assert spec.input_files == [str(f1.resolve()), str(f2.resolve())]

    def test_absolute_path_rejected(self, tmp_path):
        spec_path = self._write_spec(
            tmp_path, {"input_files": ["/etc/passwd"]}
        )
        with pytest.raises(ValueError, match="absolute"):
            EvalSpec.from_file(spec_path)

    def test_path_traversal_rejected(self, tmp_path):
        subdir = tmp_path / "spec"
        subdir.mkdir()
        outside = tmp_path / "outside.csv"
        outside.write_text("x")
        spec_path = subdir / "test.eval.json"
        spec_path.write_text(
            json.dumps(
                {"skill_name": "t", "input_files": ["../outside.csv"]}
            )
        )
        with pytest.raises(ValueError, match="escapes"):
            EvalSpec.from_file(spec_path)

    def test_missing_input_file_raises_valueerror(self, tmp_path):
        spec_path = self._write_spec(
            tmp_path, {"input_files": ["nope.csv"]}
        )
        with pytest.raises(ValueError, match="not found"):
            EvalSpec.from_file(spec_path)

    def test_symlink_target_inside_spec_dir_accepted(self, tmp_path):
        real = tmp_path / "real.csv"
        real.write_text("data")
        link = tmp_path / "link.csv"
        link.symlink_to(real)
        spec_path = self._write_spec(tmp_path, {"input_files": ["link.csv"]})
        spec = EvalSpec.from_file(spec_path)
        assert spec.input_files == [str(real.resolve())]

    def test_symlink_target_outside_spec_dir_rejected(self, tmp_path):
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        escape = tmp_path / "escape.csv"
        escape.write_text("x")
        link = spec_dir / "link.csv"
        link.symlink_to(escape)
        spec_path = spec_dir / "test.eval.json"
        spec_path.write_text(
            json.dumps({"skill_name": "t", "input_files": ["link.csv"]})
        )
        with pytest.raises(ValueError, match="escapes"):
            EvalSpec.from_file(spec_path)

    def test_duplicate_basenames_across_entries_rejected(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "sales.csv").write_text("x")
        (tmp_path / "b" / "sales.csv").write_text("y")
        spec_path = self._write_spec(
            tmp_path, {"input_files": ["a/sales.csv", "b/sales.csv"]}
        )
        with pytest.raises(ValueError, match="basename"):
            EvalSpec.from_file(spec_path)

    def test_output_files_collision_guard_rejects_overlap(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "sales.csv").write_text("x")
        spec_path = self._write_spec(
            tmp_path,
            {
                "input_files": ["data/sales.csv"],
                "output_files": ["sales.csv"],
            },
        )
        with pytest.raises(ValueError, match="collides"):
            EvalSpec.from_file(spec_path)

    def test_to_dict_roundtrip_preserves_input_files(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("x")
        spec_path = self._write_spec(tmp_path, {"input_files": ["data.csv"]})
        spec = EvalSpec.from_file(spec_path)
        d = spec.to_dict()
        assert "input_files" in d
        assert d["input_files"] == [str(f.resolve())]
        # Loading the same spec twice yields the same in-memory state.
        # (to_dict emits absolute paths for inspection, not as a re-loadable
        # authoring format — by design, since from_file rejects absolute paths.)
        reloaded = EvalSpec.from_file(spec_path)
        assert reloaded.input_files == spec.input_files

    @pytest.mark.parametrize("bad_entry", [None, 42, ["nested"], ""])
    def test_non_string_or_empty_entry_rejected(self, tmp_path, bad_entry):
        spec_path = self._write_spec(tmp_path, {"input_files": [bad_entry]})
        with pytest.raises(ValueError, match="non-empty string"):
            EvalSpec.from_file(spec_path)

    def test_directory_entry_rejected(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        spec_path = self._write_spec(tmp_path, {"input_files": ["subdir"]})
        with pytest.raises(ValueError, match="not a regular file"):
            EvalSpec.from_file(spec_path)

    def test_dot_entry_rejected_as_directory(self, tmp_path):
        spec_path = self._write_spec(tmp_path, {"input_files": ["."]})
        with pytest.raises(ValueError, match="not a regular file"):
            EvalSpec.from_file(spec_path)


class TestEvalSpecUserPrompt:
    """Tests for the optional ``user_prompt`` field (#39)."""

    def test_from_file_loads_user_prompt(self, tmp_path):
        """A spec with user_prompt set parses into the dataclass."""
        data = {
            "skill_name": "s",
            "user_prompt": "What's the best sushi in Tokyo?",
        }
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        assert spec.user_prompt == "What's the best sushi in Tokyo?"

    def test_from_file_user_prompt_absent_defaults_to_none(self, tmp_path):
        """Omitting user_prompt leaves the attribute None."""
        data = {"skill_name": "s"}
        path = _write_json(tmp_path, data)
        spec = EvalSpec.from_file(path)
        assert spec.user_prompt is None

    def test_from_file_user_prompt_empty_string_rejected(self, tmp_path):
        """An empty-string user_prompt must be rejected — callers should
        not have to disambiguate ``None`` vs ``""``."""
        data = {"skill_name": "s", "user_prompt": ""}
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match="user_prompt must be a non-empty"
        ):
            EvalSpec.from_file(path)

    def test_from_file_user_prompt_whitespace_only_rejected(self, tmp_path):
        """Whitespace-only user_prompt must be rejected at load time —
        otherwise the failure only surfaces much later when the blind
        judge is invoked."""
        data = {"skill_name": "s", "user_prompt": "   \n\t"}
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match="user_prompt must be a non-empty, non-whitespace"
        ):
            EvalSpec.from_file(path)

    def test_from_file_user_prompt_non_string_rejected(self, tmp_path):
        """A non-string user_prompt (e.g. a number) is rejected."""
        data = {"skill_name": "s", "user_prompt": 42}
        path = _write_json(tmp_path, data)
        with pytest.raises(
            ValueError, match="user_prompt must be a non-empty"
        ):
            EvalSpec.from_file(path)

    def test_to_dict_omits_user_prompt_when_unset(self):
        """Round-tripping a spec without user_prompt does not inject the key."""
        spec = EvalSpec(skill_name="s")
        d = spec.to_dict()
        assert "user_prompt" not in d

    def test_to_dict_emits_user_prompt_when_set(self):
        spec = EvalSpec(skill_name="s", user_prompt="hello there?")
        d = spec.to_dict()
        assert d["user_prompt"] == "hello there?"

    def test_user_prompt_round_trip(self, tmp_path):
        """to_dict -> JSON file -> from_file preserves user_prompt."""
        original = EvalSpec(
            skill_name="s",
            description="d",
            user_prompt="Is ramen good?",
        )
        path = tmp_path / "roundtrip.eval.json"
        path.write_text(json.dumps(original.to_dict()))
        loaded = EvalSpec.from_file(path)
        assert loaded.user_prompt == "Is ramen good?"


class TestEvalSpecFromDict:
    """Direct tests for :meth:`EvalSpec.from_dict` (DEC-007 of #52).

    Mirror the coverage of ``TestFromFile`` against the in-memory
    entry point. Every ``ValueError`` message must remain byte-
    identical to the ``from_file`` path (callers and tests anchor on
    substrings).
    """

    def test_valid_minimal_spec(self, tmp_path):
        data = {"skill_name": "test"}
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.skill_name == "test"
        assert spec.assertions == []
        assert spec.sections == []
        assert spec.grading_criteria == []
        assert spec.grading_model == "claude-sonnet-4-6"
        assert spec.test_args == ""
        assert spec.input_files == []

    @pytest.mark.parametrize(
        "bad_payload",
        [
            [],                     # list
            [{"skill_name": "x"}],  # list with a dict inside
            "not a dict",           # bare string
            42,                     # number
            None,                   # null
        ],
    )
    def test_non_dict_top_level_raises_value_error(
        self, tmp_path, bad_payload
    ):
        """Review #53: a non-dict JSON top-level used to crash with
        AttributeError on `.get()`; now it must surface a clean
        ``ValueError`` for the caller to translate into exit-2."""
        with pytest.raises(
            ValueError, match="top-level JSON value must be an object"
        ):
            EvalSpec.from_dict(bad_payload, spec_dir=tmp_path)

    def test_full_spec_fields(self, tmp_path):
        spec = EvalSpec.from_dict(SAMPLE_EVAL, spec_dir=tmp_path)
        assert spec.skill_name == "find-kid-activities"
        assert len(spec.assertions) == 2
        assert len(spec.sections) == 2
        assert spec.sections[0].name == "Venues"
        tier = spec.sections[0].tiers[0]
        assert tier.label == "default"
        assert tier.min_entries == 3
        assert len(tier.fields) == 5

    def test_empty_assertions_list_valid(self, tmp_path):
        """Empty assertions list is allowed (not required to have any)."""
        data = {"skill_name": "s", "assertions": []}
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.assertions == []

    def test_missing_skill_name_defaults_to_empty(self, tmp_path):
        """Without a skill_name key, defaults to empty string (no path stem)."""
        data: dict = {}
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.skill_name == ""

    def test_duplicate_assertion_id_rejected(self, tmp_path):
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "dup", "type": "contains", "needle": "a"},
                {"id": "dup", "type": "contains", "needle": "b"},
            ],
        }
        with pytest.raises(
            ValueError, match=r"assertions\[1\]: duplicate id 'dup'"
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_duplicate_id_across_layers_rejected(self, tmp_path):
        """An assertion id clashing with a grading_criterion id is rejected."""
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "shared", "type": "contains", "needle": "x"},
            ],
            "grading_criteria": [
                {"id": "shared", "criterion": "Is it good?"},
            ],
        }
        with pytest.raises(ValueError, match=r"duplicate id 'shared'"):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_missing_assertion_id_rejected(self, tmp_path):
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "ok", "type": "contains", "needle": "a"},
                {"type": "contains", "needle": "b"},
            ],
        }
        with pytest.raises(
            ValueError, match=r"assertions\[1\]: missing 'id'"
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_missing_field_id_rejected(self, tmp_path):
        data = {
            "skill_name": "s",
            "sections": [
                {
                    "name": "S",
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 1,
                            "fields": [
                                {"id": "ok", "name": "a", "required": True},
                                {"name": "b", "required": True},
                            ],
                        }
                    ],
                }
            ],
        }
        with pytest.raises(
            ValueError,
            match=r"sections\[0\]\.tiers\[0\]\.fields\[1\]: missing 'id'",
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_missing_criterion_id_rejected(self, tmp_path):
        data = {
            "skill_name": "s",
            "grading_criteria": [
                {"id": "ok", "criterion": "a?"},
                {"criterion": "b?"},
            ],
        }
        with pytest.raises(
            ValueError, match=r"grading_criteria\[1\]: missing 'id'"
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_grading_criteria_as_plain_strings_rejected(self, tmp_path):
        """Criteria must be dicts with id+criterion, not plain strings."""
        data = {
            "skill_name": "s",
            "grading_criteria": ["is it good?"],
        }
        with pytest.raises(
            ValueError,
            match=r"grading_criteria\[0\] — expected object, got str",
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_tiered_sections_shape_valid(self, tmp_path):
        """Sections with the canonical tiered shape load fine."""
        data = {
            "skill_name": "s",
            "sections": [
                {
                    "name": "Venues",
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 2,
                            "fields": [
                                {"id": "fn", "name": "name", "required": True},
                            ],
                        }
                    ],
                }
            ],
        }
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert len(spec.sections) == 1
        assert spec.sections[0].tiers[0].min_entries == 2

    def test_legacy_flat_sections_rejected(self, tmp_path):
        """Legacy flat shape (``fields`` at top level) raises migration hint."""
        data = {
            "skill_name": "s",
            "sections": [
                {
                    "name": "Venues",
                    "fields": [
                        {"id": "fn", "name": "name", "required": True},
                    ],
                }
            ],
        }
        with pytest.raises(
            ValueError, match=r"flat .fields. without .tiers"
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_sections_missing_tiers_rejected(self, tmp_path):
        data = {
            "skill_name": "s",
            "sections": [{"name": "Venues"}],
        }
        with pytest.raises(ValueError, match=r"missing 'tiers'"):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_input_files_absolute_rejected(self, tmp_path):
        data = {"skill_name": "s", "input_files": ["/etc/passwd"]}
        with pytest.raises(ValueError, match="absolute"):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_input_files_missing_file_rejected(self, tmp_path):
        data = {"skill_name": "s", "input_files": ["nope.csv"]}
        with pytest.raises(ValueError, match="not found"):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_input_files_path_traversal_rejected(self, tmp_path):
        """``../escape.csv`` resolves outside spec_dir and is rejected."""
        subdir = tmp_path / "spec"
        subdir.mkdir()
        (tmp_path / "escape.csv").write_text("x")
        data = {"skill_name": "s", "input_files": ["../escape.csv"]}
        with pytest.raises(ValueError, match="escapes"):
            EvalSpec.from_dict(data, spec_dir=subdir.resolve())

    def test_input_files_resolves_against_spec_dir(self, tmp_path):
        """Relative path resolves against the caller-provided spec_dir."""
        f = tmp_path / "data.csv"
        f.write_text("x")
        data = {"skill_name": "s", "input_files": ["data.csv"]}
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path.resolve())
        assert spec.input_files == [str(f.resolve())]

    def test_criterion_empty_string_rejected(self, tmp_path):
        data = {
            "skill_name": "s",
            "grading_criteria": [{"id": "c1", "criterion": ""}],
        }
        with pytest.raises(
            ValueError,
            match=r"grading_criteria\[0\]: 'criterion' must be a non-empty string",
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_user_prompt_empty_string_rejected(self, tmp_path):
        data = {"skill_name": "s", "user_prompt": "   "}
        with pytest.raises(
            ValueError, match="user_prompt must be a non-empty, non-whitespace"
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_from_file_delegates_to_from_dict(self, tmp_path):
        """Loading via from_file yields the same spec as direct from_dict.

        Covers every field on EvalSpec to catch a drift regression in
        any branch of ``from_dict`` (per QG pass 2: the per-field
        asserts this used to have missed ``sections`` / ``user_prompt``
        / ``variance`` / etc).
        """
        import dataclasses

        f = tmp_path / "data.csv"
        f.write_text("x")
        payload = {
            "skill_name": "delegate-test",
            "description": "d",
            "user_prompt": "Do the thing",
            "test_args": "--flag",
            "assertions": [{"id": "a1", "type": "contains", "needle": "ok"}],
            "sections": [
                {
                    "name": "Items",
                    "tiers": [
                        {
                            "label": "default",
                            "min_entries": 1,
                            "fields": [
                                {
                                    "id": "f1",
                                    "name": "title",
                                    "required": True,
                                }
                            ],
                        }
                    ],
                }
            ],
            "input_files": ["data.csv"],
            "grading_criteria": [
                {"id": "c1", "criterion": "Is it good?"},
            ],
        }
        spec_path = tmp_path / "test.eval.json"
        spec_path.write_text(json.dumps(payload))

        via_file = EvalSpec.from_file(spec_path)
        via_dict = EvalSpec.from_dict(payload, spec_dir=tmp_path.resolve())

        # Byte-level equivalence via asdict — catches drift in any
        # field (sections, user_prompt, trigger_tests, variance,
        # grade_thresholds, output_file[s], grading_model, etc).
        assert dataclasses.asdict(via_file) == dataclasses.asdict(via_dict)

    def test_from_file_and_from_dict_emit_identical_value_errors(
        self, tmp_path
    ):
        """Both paths must emit byte-identical ValueError messages.

        Pre-1.0 convention + QG pass 2 concern: the class docstring
        promises byte-identical error messages but every existing
        match pattern was substring regex. This test pins one bad
        payload and asserts equality on str(exc).
        """
        bad_payload = {
            "skill_name": "s",
            "assertions": [
                # Missing 'id' field triggers _require_id failure.
                {"type": "contains", "needle": "ok"}
            ],
        }
        spec_path = tmp_path / "bad.eval.json"
        spec_path.write_text(json.dumps(bad_payload))

        with pytest.raises(ValueError) as ei_file:
            EvalSpec.from_file(spec_path)
        with pytest.raises(ValueError) as ei_dict:
            EvalSpec.from_dict(bad_payload, spec_dir=tmp_path.resolve())

        assert str(ei_file.value) == str(ei_dict.value)


class TestAssertionKeySpec:
    """Tests for ``AssertionKeySpec`` + ``ASSERTION_TYPE_REQUIRED_KEYS``
    (DEC-008 of #61, DEC-001/DEC-012 of #67). The constant is the
    single source of truth that the loader validator and the
    ``propose-eval`` prompt builder both consume.
    """

    # Maps type → (required, optional, field_types) tuples. Mirrors
    # the production ``ASSERTION_TYPE_REQUIRED_KEYS`` constant;
    # drift between this table and that constant is surfaced by the
    # tests below.
    EXPECTED_KEYS: dict[
        str, tuple[set[str], set[str], dict[str, type]]
    ] = {
        "contains": ({"needle"}, set(), {"needle": str}),
        "not_contains": ({"needle"}, set(), {"needle": str}),
        "regex": ({"pattern"}, set(), {"pattern": str}),
        "min_count": (
            {"pattern", "count"}, set(),
            {"pattern": str, "count": int},
        ),
        "min_length": ({"length"}, set(), {"length": int}),
        "max_length": ({"length"}, set(), {"length": int}),
        "has_urls": (set(), {"count"}, {"count": int}),
        "has_entries": (set(), {"count"}, {"count": int}),
        "urls_reachable": (set(), {"count"}, {"count": int}),
        "has_format": (
            {"format"}, {"count"},
            {"format": str, "count": int},
        ),
    }

    def test_constant_contains_all_ten_assertion_types(self):
        """The constant's keys are exactly the 10 canonical types."""
        assert set(ASSERTION_TYPE_REQUIRED_KEYS.keys()) == set(
            self.EXPECTED_KEYS.keys()
        )
        assert len(ASSERTION_TYPE_REQUIRED_KEYS) == 10

    @pytest.mark.parametrize(
        "atype,expected_required,expected_optional,expected_field_types",
        sorted(
            (atype, req, opt, ft)
            for atype, (req, opt, ft) in EXPECTED_KEYS.items()
        ),
    )
    def test_contains_expected_keys(
        self,
        atype,
        expected_required,
        expected_optional,
        expected_field_types,
    ):
        """Each type's ``required``, ``optional``, and
        ``field_types`` declarations match the canonical spec. The
        required/optional split mirrors handler runtime behavior:
        keys whose ``.get(key, <default>)`` returns a safe default
        (e.g. ``1`` for a minimum count) are optional; keys whose
        default is a vacuous sentinel (``""``, ``0``) are required.
        ``field_types`` declares the expected native JSON type for
        each payload key so the loader rejects string-typed ints at
        load time (DEC-012 of #67).
        """
        spec = ASSERTION_TYPE_REQUIRED_KEYS[atype]
        assert isinstance(spec, AssertionKeySpec)
        assert spec.required == frozenset(expected_required)
        assert spec.optional == frozenset(expected_optional)
        assert spec.field_types == expected_field_types
        # Both sets are frozensets — load-bearing hashable contract
        # for downstream callers that use them as dict keys or
        # set members.
        assert isinstance(spec.required, frozenset)
        assert isinstance(spec.optional, frozenset)
        assert isinstance(spec.field_types, dict)

    def test_field_types_match(self):
        """Every ``required`` ∪ ``optional`` key must have an entry
        in ``field_types``, and every ``field_types`` key must be in
        ``required`` ∪ ``optional``. Guards against a future type
        rename that forgets to extend ``field_types`` — or vice
        versa, a stale ``field_types`` entry for a removed key.
        """
        for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items():
            payload_keys = set(spec.required) | set(spec.optional)
            field_keys = set(spec.field_types.keys())
            assert payload_keys == field_keys, (
                f"type={atype!r}: payload keys "
                f"(required∪optional)={payload_keys!r} vs "
                f"field_types keys={field_keys!r} — sets must match"
            )
            # Every declared type must be one of the primitive
            # types the loader's isinstance check supports. The
            # only legal values today are ``str`` and ``int``; any
            # future addition (``bool``, ``float``) is an explicit
            # loader-side change.
            for key, expected_type in spec.field_types.items():
                assert expected_type in (str, int), (
                    f"type={atype!r} key={key!r}: unexpected "
                    f"field_type {expected_type!r} — loader only "
                    f"supports str/int"
                )

    def test_required_and_optional_are_disjoint(self):
        """A key is either required or optional for a given type, not
        both. The validator's allowed-set takes the union, so overlap
        would not cause a bug, but it would signal a confused spec.
        """
        for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items():
            overlap = spec.required & spec.optional
            assert overlap == frozenset(), (
                f"type={atype!r} has {overlap!r} in both required and "
                "optional — each key must be in exactly one set"
            )

    def test_handler_signature_agrees_with_constant(self):
        """Drift guard: every required AND optional key appears in
        the handler's lambda source as a quoted key name.

        Catches a future handler edit that silently drops a key (e.g.
        swapping ``a.get("format", "")`` for ``a.get("fmt", "")`` in
        the ``has_format`` handler). Uses ``inspect.getsource`` on
        each ``_ASSERTION_HANDLERS`` entry and looks for the key as
        a single- or double-quoted literal substring. Both required
        and optional keys must appear — if the handler doesn't even
        read a key, the constant should not list it.
        """
        import inspect

        from clauditor.assertions import _ASSERTION_HANDLERS

        # The constant and the dispatch table must cover the same
        # type set first; otherwise the per-handler check below could
        # silently pass by not iterating over the missing type.
        assert set(ASSERTION_TYPE_REQUIRED_KEYS.keys()) == set(
            _ASSERTION_HANDLERS.keys()
        )

        for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items():
            handler = _ASSERTION_HANDLERS[atype]
            source = inspect.getsource(handler)
            for key in spec.required | spec.optional:
                double_quoted = f'"{key}"'
                single_quoted = f"'{key}'"
                assert (
                    double_quoted in source or single_quoted in source
                ), (
                    f"handler for type={atype!r} does not reference "
                    f"key {key!r} in its source (expected "
                    f"{double_quoted!r} or {single_quoted!r} "
                    f"substring); source was: {source!r}"
                )


def _minimal_assertion_entry(atype: str, aid: str = "a1") -> dict:
    """Build a minimal valid assertion dict for ``atype``.

    Drives off :data:`ASSERTION_TYPE_REQUIRED_KEYS` so the shape
    stays in lockstep with the production constant. Per DEC-012 of
    #67 the loader also type-checks each required key against
    ``spec.field_types``, so this helper emits a value of the
    declared native type (``"1"`` for ``str``, ``1`` for ``int``).
    """
    spec = ASSERTION_TYPE_REQUIRED_KEYS[atype]
    entry: dict = {"id": aid, "type": atype}
    for key in spec.required:
        expected = spec.field_types.get(key, str)
        entry[key] = 1 if expected is int else "1"
    return entry


class TestRequireAssertionKeys:
    """Tests for the ``_require_assertion_keys`` loader validator
    (US-002 of #61). Drives the parametrize tables off
    :data:`ASSERTION_TYPE_REQUIRED_KEYS` so coverage stays exhaustive
    when a new assertion type lands.
    """

    @pytest.mark.parametrize(
        "atype",
        sorted(ASSERTION_TYPE_REQUIRED_KEYS.keys()),
    )
    def test_valid_entry_passes(self, tmp_path, atype):
        """A minimal entry (id + type + every required key) loads."""
        entry = _minimal_assertion_entry(atype)
        data = {
            "skill_name": "s",
            "test_args": "y",
            "assertions": [entry],
        }
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.assertions == [entry]

    @pytest.mark.parametrize(
        "atype,missing_key",
        sorted(
            (atype, key)
            for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items()
            for key in spec.required
        ),
    )
    def test_missing_required_key_rejected(
        self, tmp_path, atype, missing_key
    ):
        """Dropping any required key for ``atype`` raises ValueError."""
        entry = _minimal_assertion_entry(atype)
        del entry[missing_key]
        data = {
            "skill_name": "s",
            "assertions": [entry],
        }
        with pytest.raises(
            ValueError,
            match=(
                rf"assertions\[0\] \(type='{atype}'\): "
                rf"missing required key '{missing_key}'"
            ),
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    @pytest.mark.parametrize(
        "atype,missing_key",
        sorted(
            (atype, key)
            for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items()
            for key in spec.required
        ),
    )
    def test_none_valued_required_key_rejected(
        self, tmp_path, atype, missing_key
    ):
        """A required key with a ``None`` value is also rejected —
        the helper treats missing and null identically.
        """
        entry = _minimal_assertion_entry(atype)
        entry[missing_key] = None
        data = {
            "skill_name": "s",
            "assertions": [entry],
        }
        with pytest.raises(
            ValueError,
            match=(
                rf"assertions\[0\] \(type='{atype}'\): "
                rf"missing required key '{missing_key}'"
            ),
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    @pytest.mark.parametrize(
        "atype,bad_key,hint_fragment",
        sorted(
            (atype, bad_key, f"did you mean {correct!r}?")
            for atype, hints in _ASSERTION_DRIFT_HINTS.items()
            for bad_key, correct in hints.items()
        )
        + [
            # Generic case — no hint suffix when the key is not in
            # any type's drift-hint table.
            ("contains", "foo_bar", None),
        ],
    )
    def test_unknown_key_rejected(
        self, tmp_path, atype, bad_key, hint_fragment
    ):
        """Unknown keys raise ``ValueError`` with the right per-type
        hint (or no hint for keys outside the drift alias table).

        Drives the parametrize table off
        :data:`_ASSERTION_DRIFT_HINTS` so every (type, wrong-key)
        pair in the production table is exercised exactly once.
        """
        entry = _minimal_assertion_entry(atype)
        # Pick a value whose native type is a string; we just need
        # something non-None so the unknown-key branch sees the key.
        entry[bad_key] = "whatever"
        data = {
            "skill_name": "s",
            "assertions": [entry],
        }
        with pytest.raises(ValueError) as ei:
            EvalSpec.from_dict(data, spec_dir=tmp_path)
        msg = str(ei.value)
        assert f"unknown key {bad_key!r}" in msg
        assert "assertions[0]" in msg
        assert f"(type={atype!r})" in msg
        if hint_fragment is None:
            # Generic unknown key — must NOT carry a "did you mean"
            # hint so we don't teach the user a wrong alias.
            assert "did you mean" not in msg
        else:
            assert hint_fragment in msg

    def test_missing_type_rejected(self, tmp_path):
        """An entry with no ``type`` field is rejected before any
        required-key check fires."""
        data = {
            "skill_name": "s",
            "assertions": [{"id": "a1", "value": "x"}],
        }
        with pytest.raises(
            ValueError,
            match=(
                r"assertions\[0\]: unknown or missing 'type' "
                r"\(got None\)"
            ),
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_unknown_type_rejected(self, tmp_path):
        """A ``type`` value outside ``ASSERTION_TYPE_REQUIRED_KEYS``
        is rejected with the same message shape as missing type."""
        data = {
            "skill_name": "s",
            "assertions": [
                {"id": "a1", "type": "nonexistent_type", "value": "x"}
            ],
        }
        with pytest.raises(
            ValueError,
            match=(
                r"assertions\[0\]: unknown or missing 'type' "
                r"\(got 'nonexistent_type'\)"
            ),
        ):
            EvalSpec.from_dict(data, spec_dir=tmp_path)

    def test_name_and_id_are_allowed_metadata(self, tmp_path):
        """``id``, ``type``, and ``name`` are always-allowed metadata
        keys — adding ``name`` alongside the required payload keys
        must not trigger the unknown-key branch.
        """
        entry = _minimal_assertion_entry("contains")
        entry["name"] = "human label"
        data = {
            "skill_name": "s",
            "assertions": [entry],
        }
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.assertions[0]["name"] == "human label"

    @pytest.mark.parametrize(
        "atype,opt_key",
        sorted(
            (atype, key)
            for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items()
            for key in spec.optional
        ),
    )
    def test_optional_key_is_allowed_when_present(
        self, tmp_path, atype, opt_key
    ):
        """Specifying an optional key (e.g. ``count`` on ``has_urls``
        or ``has_format``) is accepted; the validator does not reject
        it as unknown. The value respects ``spec.field_types[opt_key]``
        so ``count`` lands as a native int per DEC-012 of #67.
        """
        spec_entry = ASSERTION_TYPE_REQUIRED_KEYS[atype]
        entry = _minimal_assertion_entry(atype)
        expected = spec_entry.field_types.get(opt_key, str)
        value: object = 1 if expected is int else "1"
        entry[opt_key] = value
        data = {
            "skill_name": "s",
            "test_args": "y",
            "assertions": [entry],
        }
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.assertions[0][opt_key] == value

    @pytest.mark.parametrize(
        "atype",
        sorted(
            atype
            for atype, spec in ASSERTION_TYPE_REQUIRED_KEYS.items()
            if not spec.required
        ),
    )
    def test_optional_key_not_required_by_validator(self, tmp_path, atype):
        """Types with ONLY optional keys (``has_urls``, ``has_entries``,
        ``urls_reachable``) load successfully with no payload keys at
        all — the handler's default (count=1) applies at runtime.
        """
        entry = {"id": "a1", "type": atype}
        data = {
            "skill_name": "s",
            "test_args": "y",
            "assertions": [entry],
        }
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.assertions == [entry]

    def test_optional_key_allows_none(self, tmp_path):
        """An optional key with a ``None`` value is accepted — we
        allow it but don't special-case None the way we do for
        required keys. This documents the boundary: None is only
        rejected when the key is REQUIRED.
        """
        entry = {"id": "a1", "type": "has_urls", "count": None}
        data = {
            "skill_name": "s",
            "test_args": "y",
            "assertions": [entry],
        }
        # Optional + None passes validation; the downstream handler
        # reads ``a.get("count", 1)`` and a None-valued key short-
        # circuits to the default at runtime.
        spec = EvalSpec.from_dict(data, spec_dir=tmp_path)
        assert spec.assertions == [entry]

    @pytest.mark.parametrize(
        "atype,key,bad_value,expected_fragment",
        [
            (
                "min_length",
                "length",
                "500",
                "key 'length' must be int, got str '500'",
            ),
            (
                "contains",
                "needle",
                123,
                "key 'needle' must be str, got int 123",
            ),
            (
                "min_count",
                "count",
                "3",
                "key 'count' must be int, got str '3'",
            ),
            (
                "has_format",
                "format",
                42,
                "key 'format' must be str, got int 42",
            ),
        ],
    )
    def test_wrong_type_rejected(
        self, tmp_path, atype, key, bad_value, expected_fragment
    ):
        """DEC-012 of #67 — every payload key's value must match
        the native type declared in ``spec.field_types``. String-
        typed ints (``{"length": "500"}``) and int-typed strings
        (``{"needle": 123}``) reject at load time with a message
        naming the key, expected type, actual type, and the
        offending value.
        """
        entry = _minimal_assertion_entry(atype)
        entry[key] = bad_value
        data = {
            "skill_name": "s",
            "test_args": "y",
            "assertions": [entry],
        }
        with pytest.raises(ValueError) as ei:
            EvalSpec.from_dict(data, spec_dir=tmp_path)
        msg = str(ei.value)
        assert expected_fragment in msg
        assert "assertions[0]" in msg
        assert f"(type={atype!r})" in msg

    def test_wrong_type_rejects_bool_where_int_expected(self, tmp_path):
        """``bool`` is a subclass of ``int`` in Python, so a naive
        ``isinstance`` check would silently accept
        ``{"count": True}``. DEC-012 of #67 guards against this by
        excluding ``bool`` from the int branch; the loader must
        raise.
        """
        entry = {"id": "a1", "type": "has_urls", "count": True}
        data = {
            "skill_name": "s",
            "assertions": [entry],
        }
        with pytest.raises(ValueError) as ei:
            EvalSpec.from_dict(data, spec_dir=tmp_path)
        msg = str(ei.value)
        assert "key 'count' must be int" in msg
        assert "bool" in msg
