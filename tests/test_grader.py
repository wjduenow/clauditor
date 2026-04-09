"""Tests for Layer 2 grading (extraction validation, not LLM calls)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor.grader import (
    ExtractedEntry,
    ExtractedOutput,
    build_extraction_prompt,
    extract_and_grade,
    grade_extraction,
)
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


class TestBuildExtractionPrompt:
    def test_contains_section_and_fields(self):
        spec = _make_spec()
        prompt = build_extraction_prompt(spec)
        assert "Venues" in prompt
        assert "name" in prompt
        assert "address" in prompt
        assert "website" in prompt
        assert "phone" in prompt

    def test_includes_json_schema_block(self):
        spec = _make_spec()
        prompt = build_extraction_prompt(spec)
        assert '"Venues"' in prompt
        assert '"name": "value or null"' in prompt

    def test_multiple_sections(self):
        spec = EvalSpec(
            skill_name="multi",
            sections=[
                SectionRequirement(
                    name="Hotels",
                    min_entries=1,
                    fields=[FieldRequirement(name="name", required=True)],
                ),
                SectionRequirement(
                    name="Restaurants",
                    min_entries=1,
                    fields=[
                        FieldRequirement(name="name", required=True),
                        FieldRequirement(name="cuisine", required=False),
                    ],
                ),
            ],
        )
        prompt = build_extraction_prompt(spec)
        assert "Hotels" in prompt
        assert "Restaurants" in prompt
        assert "cuisine" in prompt

    def test_returns_string(self):
        prompt = build_extraction_prompt(_make_spec())
        assert isinstance(prompt, str)
        assert prompt.startswith("Extract structured data")


class TestExtractAndGrade:
    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        data = {
            "Venues": [
                {"name": "CDM", "address": "180 Woz Way", "website": "https://cdm.org"},
                {
                    "name": "Deer Hollow",
                    "address": "22500 Cristo Rey Dr",
                    "website": "https://deerhollowfarm.org",
                },
            ]
        }
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed

    @pytest.mark.asyncio
    async def test_markdown_code_block_stripped(self):
        data = {
            "Venues": [
                {"name": "A", "address": "B", "website": "C"},
                {"name": "D", "address": "E", "website": "F"},
            ]
        }
        wrapped = f"```json\n{json.dumps(data)}\n```"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=wrapped)]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed

    @pytest.mark.asyncio
    async def test_generic_code_block_stripped(self):
        data = {
            "Venues": [
                {"name": "A", "address": "B", "website": "C"},
                {"name": "D", "address": "E", "website": "F"},
            ]
        }
        wrapped = f"```\n{json.dumps(data)}\n```"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=wrapped)]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed

    @pytest.mark.asyncio
    async def test_json_parse_failure(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json at all")]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert not result.passed
        assert any("parse" in r.name for r in result.results)

    @pytest.mark.asyncio
    async def test_missing_required_field_in_response(self):
        data = {
            "Venues": [
                {"name": "CDM", "address": None, "website": "https://cdm.org"},
                {"name": "Deer Hollow", "address": "addr", "website": "https://dh.org"},
            ]
        }
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert not result.passed

    @pytest.mark.asyncio
    async def test_import_error_when_no_anthropic(self):
        # Remove anthropic from sys.modules and block re-import
        import sys

        real_anthropic = sys.modules.pop("anthropic", None)
        try:
            with patch.dict("sys.modules", {"anthropic": None}):
                with pytest.raises(ImportError, match="anthropic SDK"):
                    await extract_and_grade("output", _make_spec())
        finally:
            if real_anthropic is not None:
                sys.modules["anthropic"] = real_anthropic
