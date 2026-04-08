"""Tests for eval spec loading and schema definitions."""

import json
import tempfile

from clauditor.schemas import EvalSpec, FieldRequirement, SectionRequirement

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
        assert spec.sections[0].min_entries == 3
        assert len(spec.sections[0].fields) == 5
        assert spec.sections[0].fields[0].name == "name"
        assert spec.sections[0].fields[0].required is True
        assert spec.sections[0].fields[4].required is False

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
        assert d["sections"][0]["fields"][0]["name"] == "name"


class TestFieldRequirement:
    def test_defaults(self):
        f = FieldRequirement(name="test")
        assert f.required is True
        assert f.pattern is None

    def test_with_pattern(self):
        f = FieldRequirement(name="phone", pattern=r"\(\d{3}\)\s\d{3}-\d{4}")
        assert f.pattern is not None


class TestSectionRequirement:
    def test_defaults(self):
        s = SectionRequirement(name="Results")
        assert s.min_entries == 1
        assert s.fields == []
