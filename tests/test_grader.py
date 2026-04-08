"""Tests for Layer 2 grading (extraction validation, not LLM calls)."""

from clauditor.grader import ExtractedEntry, ExtractedOutput, grade_extraction
from clauditor.schemas import EvalSpec, FieldRequirement, SectionRequirement


def _make_spec() -> EvalSpec:
    return EvalSpec(
        skill_name="test-skill",
        sections=[
            SectionRequirement(
                name="Venues",
                min_entries=2,
                fields=[
                    FieldRequirement(name="name", required=True),
                    FieldRequirement(name="address", required=True),
                    FieldRequirement(name="website", required=True),
                    FieldRequirement(name="phone", required=False),
                ],
            ),
        ],
    )


class TestGradeExtraction:
    def test_all_fields_present(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": [
                    ExtractedEntry(
                        fields={
                            "name": "CDM",
                            "address": "180 Woz Way",
                            "website": "https://cdm.org",
                            "phone": "(408) 298-5437",
                        }
                    ),
                    ExtractedEntry(
                        fields={
                            "name": "Deer Hollow",
                            "address": "22500 Cristo Rey Dr",
                            "website": "https://deerhollowfarm.org",
                            "phone": None,
                        }
                    ),
                ]
            }
        )
        results = grade_extraction(extracted, _make_spec())
        assert results.passed

    def test_missing_required_field(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": [
                    ExtractedEntry(
                        fields={
                            "name": "CDM",
                            "address": None,  # missing required
                            "website": "https://cdm.org",
                        }
                    ),
                    ExtractedEntry(
                        fields={
                            "name": "Deer Hollow",
                            "address": "22500 Cristo Rey Dr",
                            "website": "https://deerhollowfarm.org",
                        }
                    ),
                ]
            }
        )
        results = grade_extraction(extracted, _make_spec())
        assert not results.passed
        failed_names = [r.name for r in results.failed]
        assert "section:Venues[0].address" in failed_names

    def test_not_enough_entries(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": [
                    ExtractedEntry(
                        fields={"name": "Only One", "address": "x", "website": "x"}
                    ),
                ]
            }
        )
        results = grade_extraction(extracted, _make_spec())
        assert not results.passed
        assert any("count" in r.name for r in results.failed)

    def test_missing_section(self):
        extracted = ExtractedOutput(sections={})
        results = grade_extraction(extracted, _make_spec())
        assert not results.passed

    def test_optional_field_missing_ok(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": [
                    ExtractedEntry(
                        fields={"name": "A", "address": "B", "website": "C"}
                    ),
                    ExtractedEntry(
                        fields={"name": "D", "address": "E", "website": "F"}
                    ),
                ]
            }
        )
        # phone is optional, should still pass
        results = grade_extraction(extracted, _make_spec())
        assert results.passed
