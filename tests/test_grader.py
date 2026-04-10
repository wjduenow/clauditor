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
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    SectionRequirement,
    TierRequirement,
)


def _make_spec() -> EvalSpec:
    return EvalSpec(
        skill_name="test-skill",
        sections=[
            SectionRequirement(
                name="Venues",
                tiers=[
                    TierRequirement(
                        label="default",
                        min_entries=2,
                        fields=[
                            FieldRequirement(name="name", required=True),
                            FieldRequirement(name="address", required=True),
                            FieldRequirement(name="website", required=True),
                            FieldRequirement(name="phone", required=False),
                        ],
                    ),
                ],
            ),
        ],
    )


def _make_two_tier_spec() -> EvalSpec:
    return EvalSpec(
        skill_name="test-skill",
        sections=[
            SectionRequirement(
                name="Venues",
                tiers=[
                    TierRequirement(
                        label="main",
                        description="Primary venues with full details",
                        min_entries=1,
                        fields=[
                            FieldRequirement(name="name", required=True),
                            FieldRequirement(name="address", required=True),
                            FieldRequirement(name="website", required=True),
                        ],
                    ),
                    TierRequirement(
                        label="honorable_mention",
                        description="Brief mentions worth noting",
                        min_entries=0,
                        fields=[
                            FieldRequirement(name="name", required=True),
                            FieldRequirement(name="reason", required=False),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestGradeExtraction:
    def test_all_fields_present(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "default": [
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
            }
        )
        results = grade_extraction(extracted, _make_spec())
        assert results.passed

    def test_missing_required_field(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "default": [
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
            }
        )
        results = grade_extraction(extracted, _make_spec())
        assert not results.passed
        failed_names = [r.name for r in results.failed]
        assert "section:Venues/default[0].address" in failed_names

    def test_not_enough_entries(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "default": [
                        ExtractedEntry(
                            fields={"name": "Only One", "address": "x", "website": "x"}
                        ),
                    ]
                }
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
                "Venues": {
                    "default": [
                        ExtractedEntry(
                            fields={"name": "A", "address": "B", "website": "C"}
                        ),
                        ExtractedEntry(
                            fields={"name": "D", "address": "E", "website": "F"}
                        ),
                    ]
                }
            }
        )
        # phone is optional, should still pass
        results = grade_extraction(extracted, _make_spec())
        assert results.passed

    def test_two_tier_all_present(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "main": [
                        ExtractedEntry(
                            fields={
                                "name": "CDM",
                                "address": "180 Woz Way",
                                "website": "https://cdm.org",
                            }
                        ),
                    ],
                    "honorable_mention": [
                        ExtractedEntry(
                            fields={"name": "Deer Hollow", "reason": "scenic"}
                        ),
                    ],
                }
            }
        )
        results = grade_extraction(extracted, _make_two_tier_spec())
        assert results.passed

    def test_two_tier_missing_required_in_main(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "main": [
                        ExtractedEntry(
                            fields={
                                "name": "CDM",
                                "address": None,  # missing required
                                "website": "https://cdm.org",
                            }
                        ),
                    ],
                    "honorable_mention": [],
                }
            }
        )
        results = grade_extraction(extracted, _make_two_tier_spec())
        assert not results.passed
        failed_names = [r.name for r in results.failed]
        assert "section:Venues/main[0].address" in failed_names

    def test_two_tier_optional_field_missing_in_secondary(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "main": [
                        ExtractedEntry(
                            fields={
                                "name": "CDM",
                                "address": "180 Woz Way",
                                "website": "https://cdm.org",
                            }
                        ),
                    ],
                    "honorable_mention": [
                        ExtractedEntry(
                            fields={"name": "Deer Hollow"}
                            # reason is optional, missing is fine
                        ),
                    ],
                }
            }
        )
        results = grade_extraction(extracted, _make_two_tier_spec())
        assert results.passed

    def test_two_tier_too_few_entries(self):
        # main requires min_entries=1, but we provide 0
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "main": [],
                    "honorable_mention": [],
                }
            }
        )
        results = grade_extraction(extracted, _make_two_tier_spec())
        assert not results.passed
        failed_names = [r.name for r in results.failed]
        assert "section:Venues:count/main" in failed_names

    def test_zero_min_entries_tier_with_no_entries_passes(self):
        # honorable_mention has min_entries=0
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "main": [
                        ExtractedEntry(
                            fields={
                                "name": "CDM",
                                "address": "x",
                                "website": "x",
                            }
                        ),
                    ],
                    "honorable_mention": [],
                }
            }
        )
        results = grade_extraction(extracted, _make_two_tier_spec())
        assert results.passed

    def test_unknown_tier_label_ignored(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "default": [
                        ExtractedEntry(
                            fields={"name": "A", "address": "B", "website": "C"}
                        ),
                        ExtractedEntry(
                            fields={"name": "D", "address": "E", "website": "F"}
                        ),
                    ],
                    "unknown_tier": [
                        ExtractedEntry(fields={"name": "X"}),
                    ],
                }
            }
        )
        results = grade_extraction(extracted, _make_spec())
        # unknown_tier is silently ignored, only "default" is validated
        assert results.passed

    def test_assertion_name_format_count(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "default": [
                        ExtractedEntry(
                            fields={"name": "A", "address": "B", "website": "C"}
                        ),
                        ExtractedEntry(
                            fields={"name": "D", "address": "E", "website": "F"}
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, _make_spec())
        names = [r.name for r in results.results]
        assert "section:Venues:count/default" in names

    def test_assertion_name_format_field(self):
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "default": [
                        ExtractedEntry(
                            fields={"name": "A", "address": "B", "website": "C"}
                        ),
                        ExtractedEntry(
                            fields={"name": "D", "address": "E", "website": "F"}
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, _make_spec())
        names = [r.name for r in results.results]
        assert "section:Venues/default[0].name" in names
        assert "section:Venues/default[1].address" in names


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
        assert '"default"' in prompt
        assert '"name": "value or null"' in prompt

    def test_multiple_sections(self):
        spec = EvalSpec(
            skill_name="multi",
            sections=[
                SectionRequirement(
                    name="Hotels",
                    tiers=[
                        TierRequirement(
                            label="default",
                            min_entries=1,
                            fields=[FieldRequirement(name="name", required=True)],
                        ),
                    ],
                ),
                SectionRequirement(
                    name="Restaurants",
                    tiers=[
                        TierRequirement(
                            label="default",
                            min_entries=1,
                            fields=[
                                FieldRequirement(name="name", required=True),
                                FieldRequirement(name="cuisine", required=False),
                            ],
                        ),
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

    def test_includes_tier_labels_and_descriptions(self):
        spec = _make_two_tier_spec()
        prompt = build_extraction_prompt(spec)
        assert '"main"' in prompt
        assert '"honorable_mention"' in prompt
        assert "Primary venues with full details" in prompt
        assert "Brief mentions worth noting" in prompt

    def test_nested_json_schema(self):
        spec = _make_two_tier_spec()
        prompt = build_extraction_prompt(spec)
        # Should show nested structure: "Venues": { "main": [...], ... }
        assert '"Venues": {' in prompt
        assert '"main": [{' in prompt or '"main": [{ ' in prompt


class TestExtractAndGrade:
    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        data = {
            "Venues": {
                "default": [
                    {
                        "name": "CDM",
                        "address": "180 Woz Way",
                        "website": "https://cdm.org",
                    },
                    {
                        "name": "Deer Hollow",
                        "address": "22500 Cristo Rey Dr",
                        "website": "https://deerhollowfarm.org",
                    },
                ]
            }
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
            "Venues": {
                "default": [
                    {"name": "A", "address": "B", "website": "C"},
                    {"name": "D", "address": "E", "website": "F"},
                ]
            }
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
            "Venues": {
                "default": [
                    {"name": "A", "address": "B", "website": "C"},
                    {"name": "D", "address": "E", "website": "F"},
                ]
            }
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
            "Venues": {
                "default": [
                    {
                        "name": "CDM",
                        "address": None,
                        "website": "https://cdm.org",
                    },
                    {
                        "name": "Deer Hollow",
                        "address": "addr",
                        "website": "https://dh.org",
                    },
                ]
            }
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

    @pytest.mark.asyncio
    async def test_flat_list_response_produces_parse_error(self):
        """Flat list instead of tier-grouped dict = grader:parse:{Section} failure."""
        data = {
            "Venues": [
                {
                    "name": "CDM",
                    "address": "180 Woz Way",
                    "website": "https://cdm.org",
                },
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
        assert not result.passed
        failed_names = [r.name for r in result.failed]
        assert "grader:parse:Venues" in failed_names

    @pytest.mark.asyncio
    async def test_extra_section_flat_list_ignored(self):
        """Extra sections not in eval_spec with flat lists are ignored."""
        data = {
            "Venues": {
                "default": [
                    {"name": "A", "address": "B", "website": "C"},
                    {"name": "D", "address": "E", "website": "F"},
                ],
            },
            "ExtraStuff": [
                {"note": "LLM added this unsolicited"},
            ],
        }
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        # Extra flat-list section is not in spec, so no parse error
        assert result.passed

    @pytest.mark.asyncio
    async def test_unknown_tier_in_response_ignored(self):
        """Entries under undefined tier labels are silently ignored."""
        data = {
            "Venues": {
                "default": [
                    {"name": "A", "address": "B", "website": "C"},
                    {"name": "D", "address": "E", "website": "F"},
                ],
                "bonus": [
                    {"name": "X"},
                ],
            }
        }
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed
