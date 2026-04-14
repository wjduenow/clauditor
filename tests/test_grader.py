"""Tests for Layer 2 grading (extraction validation, not LLM calls)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clauditor.grader import (
    ExtractedEntry,
    ExtractedOutput,
    ExtractionReport,
    build_extraction_prompt,
    build_extraction_report,
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


def _make_max_entries_spec(max_entries: int | None = 3) -> EvalSpec:
    return EvalSpec(
        skill_name="test-skill",
        sections=[
            SectionRequirement(
                name="Venues",
                tiers=[
                    TierRequirement(
                        label="default",
                        min_entries=1,
                        max_entries=max_entries,
                        fields=[
                            FieldRequirement(name="name", required=True),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestMaxEntries:
    """DEC-003: TierRequirement.max_entries as a precision signal."""

    def _entries(self, n: int) -> list[ExtractedEntry]:
        return [ExtractedEntry(fields={"name": f"V{i}"}) for i in range(n)]

    def test_over_max_emits_count_max_failure(self):
        extracted = ExtractedOutput(
            sections={"Venues": {"default": self._entries(6)}}
        )
        results = grade_extraction(extracted, _make_max_entries_spec(max_entries=3))
        names = [r.name for r in results.results]
        assert "section:Venues:count_max/default" in names
        count_max = next(
            r for r in results.results
            if r.name == "section:Venues:count_max/default"
        )
        assert not count_max.passed
        assert "6 entries" in count_max.message
        assert "<=3" in count_max.message

    def test_over_max_still_grades_all_entry_fields(self):
        """DEC-003: field checks still run for all extracted entries."""
        extracted = ExtractedOutput(
            sections={"Venues": {"default": self._entries(6)}}
        )
        results = grade_extraction(extracted, _make_max_entries_spec(max_entries=3))
        presence_names = [
            r.name for r in results.results
            if r.name.startswith("section:Venues/default[") and r.name.endswith(".name")
        ]
        assert len(presence_names) == 6

    def test_equal_to_max_passes(self):
        extracted = ExtractedOutput(
            sections={"Venues": {"default": self._entries(3)}}
        )
        results = grade_extraction(extracted, _make_max_entries_spec(max_entries=3))
        count_max = next(
            r for r in results.results
            if r.name == "section:Venues:count_max/default"
        )
        assert count_max.passed

    def test_under_max_passes(self):
        extracted = ExtractedOutput(
            sections={"Venues": {"default": self._entries(2)}}
        )
        results = grade_extraction(extracted, _make_max_entries_spec(max_entries=3))
        count_max = next(
            r for r in results.results
            if r.name == "section:Venues:count_max/default"
        )
        assert count_max.passed

    def test_none_max_emits_no_assertion(self):
        extracted = ExtractedOutput(
            sections={"Venues": {"default": self._entries(100)}}
        )
        results = grade_extraction(extracted, _make_max_entries_spec(max_entries=None))
        names = [r.name for r in results.results]
        assert not any("count_max" in n for n in names)

    def test_both_min_and_max_fail_when_extraction_exceeds_max(self):
        """min_entries and max_entries produce independent assertions."""
        spec = EvalSpec(
            skill_name="test-skill",
            sections=[
                SectionRequirement(
                    name="Venues",
                    tiers=[
                        TierRequirement(
                            label="default",
                            min_entries=3,
                            max_entries=3,
                            fields=[FieldRequirement(name="name", required=True)],
                        ),
                    ],
                ),
            ],
        )
        # Exactly 3 entries → both min and max pass
        ok = grade_extraction(
            ExtractedOutput(sections={"Venues": {"default": self._entries(3)}}),
            spec,
        )
        count_min = next(
            r for r in ok.results
            if r.name == "section:Venues:count/default"
        )
        count_max = next(
            r for r in ok.results
            if r.name == "section:Venues:count_max/default"
        )
        assert count_min.passed
        assert count_max.passed

        # 5 entries → min passes, max fails
        over = grade_extraction(
            ExtractedOutput(sections={"Venues": {"default": self._entries(5)}}),
            spec,
        )
        assert next(
            r for r in over.results
            if r.name == "section:Venues:count/default"
        ).passed
        assert not next(
            r for r in over.results
            if r.name == "section:Venues:count_max/default"
        ).passed

        # 1 entry → min fails, max passes
        under = grade_extraction(
            ExtractedOutput(sections={"Venues": {"default": self._entries(1)}}),
            spec,
        )
        assert not next(
            r for r in under.results
            if r.name == "section:Venues:count/default"
        ).passed
        assert next(
            r for r in under.results
            if r.name == "section:Venues:count_max/default"
        ).passed


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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [MagicMock(text=wrapped)]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed

    @pytest.mark.asyncio
    async def test_json_parse_failure(self):
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
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
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed

    @pytest.mark.asyncio
    async def test_shape_failure_attaches_raw_data(self):
        """US-005: parseable-but-wrong-shape response attaches raw_data."""
        data = {
            "Venues": [
                {"name": "CDM", "address": "X", "website": "Y"},
            ],
        }
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        failed = [r for r in result.results if r.name == "grader:parse:Venues"]
        assert len(failed) == 1
        assert failed[0].raw_data == data

    @pytest.mark.asyncio
    async def test_json_parse_failure_leaves_raw_data_none(self):
        """US-005: unparseable response keeps raw_data=None and evidence."""
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [MagicMock(text="not valid json at all")]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        parse_failures = [r for r in result.results if r.name == "grader:parse"]
        assert len(parse_failures) == 1
        assert parse_failures[0].raw_data is None
        assert parse_failures[0].evidence == "not valid json at all"

    @pytest.mark.asyncio
    async def test_captures_token_usage(self):
        """extract_and_grade propagates SDK usage into the AssertionSet."""
        data = {
            "Venues": {
                "default": [
                    {"name": "A", "address": "B", "website": "C"},
                    {"name": "D", "address": "E", "website": "F"},
                ]
            }
        }
        mock_response = MagicMock(
            usage=MagicMock(input_tokens=500, output_tokens=200)
        )
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.input_tokens == 500
        assert result.output_tokens == 200

    @pytest.mark.asyncio
    async def test_non_failure_result_has_no_raw_data(self):
        """US-005 negative regression: passing assertions never carry raw_data."""
        data = {
            "Venues": {
                "default": [
                    {"name": "A", "address": "B", "website": "C"},
                    {"name": "D", "address": "E", "website": "F"},
                ]
            }
        }
        mock_response = MagicMock(usage=MagicMock(input_tokens=500, output_tokens=200))
        mock_response.content = [MagicMock(text=json.dumps(data))]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await extract_and_grade("some output", _make_spec())
        assert result.passed
        for r in result.results:
            assert r.raw_data is None


def _make_format_spec() -> EvalSpec:
    """Spec exercising both registry formats and inline-regex formats (DEC-007)."""
    return EvalSpec(
        skill_name="test-skill",
        sections=[
            SectionRequirement(
                name="Restaurants",
                tiers=[
                    TierRequirement(
                        label="default",
                        min_entries=1,
                        fields=[
                            FieldRequirement(name="name", required=True),
                            FieldRequirement(
                                name="phone",
                                required=True,
                                format=r"\(\d{3}\) \d{3}-\d{4}",  # inline regex
                            ),
                            FieldRequirement(
                                name="website",
                                required=True,
                                format="url",  # registry
                            ),
                            FieldRequirement(
                                name="email",
                                required=False,
                                format="email",  # registry
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestFormatEnforcement:
    """DEC-007: format field does registry-lookup-then-regex-fallback."""

    def test_inline_regex_match(self):
        extracted = ExtractedOutput(
            sections={
                "Restaurants": {
                    "default": [
                        ExtractedEntry(
                            fields={
                                "name": "Paesano's",
                                "phone": "(408) 298-5437",
                                "website": "https://paesanos.com",
                            }
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, _make_format_spec())
        format_results = [
            r for r in results.results if r.name.endswith(":format")
        ]
        assert all(r.passed for r in format_results)

    def test_inline_regex_mismatch(self):
        extracted = ExtractedOutput(
            sections={
                "Restaurants": {
                    "default": [
                        ExtractedEntry(
                            fields={
                                "name": "Paesano's",
                                "phone": "call for hours",
                                "website": "https://paesanos.com",
                            }
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, _make_format_spec())
        failing = [
            r for r in results.results
            if r.name.endswith(":format") and not r.passed
        ]
        assert len(failing) == 1
        assert "phone" in failing[0].name
        assert failing[0].evidence == "call for hours"
        assert "regex" in failing[0].message.lower()

    def test_registry_format_mismatch(self):
        extracted = ExtractedOutput(
            sections={
                "Restaurants": {
                    "default": [
                        ExtractedEntry(
                            fields={
                                "name": "Paesano's",
                                "phone": "(408) 298-5437",
                                "website": "not-a-url",
                            }
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, _make_format_spec())
        failing = [
            r for r in results.results
            if r.name.endswith(":format") and not r.passed
        ]
        assert len(failing) == 1
        assert "website" in failing[0].name
        assert "url" in failing[0].message

    def test_unknown_format_not_valid_regex_raises_at_construction(self):
        """DEC-011: an unknown format that is also invalid regex fails loud."""
        with pytest.raises(ValueError, match="nor a valid regex"):
            FieldRequirement(name="val", format="[invalid")

    def test_optional_field_missing_skips_format_check(self):
        extracted = ExtractedOutput(
            sections={
                "Restaurants": {
                    "default": [
                        ExtractedEntry(
                            fields={
                                "name": "Paesano's",
                                "phone": "(408) 298-5437",
                                "website": "https://paesanos.com",
                                # email is optional, not present
                            }
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, _make_format_spec())
        email_format = [
            r for r in results.results
            if "email" in r.name and r.name.endswith(":format")
        ]
        assert len(email_format) == 0

    def test_non_string_field_value_coerced(self):
        """Non-string values (e.g. int from LLM) are coerced to str for format check."""
        spec = EvalSpec(
            skill_name="test",
            sections=[
                SectionRequirement(
                    name="Items",
                    tiers=[
                        TierRequirement(
                            label="default",
                            min_entries=1,
                            fields=[
                                FieldRequirement(
                                    name="count",
                                    required=True,
                                    format=r"\d+",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        extracted = ExtractedOutput(
            sections={
                "Items": {
                    "default": [
                        ExtractedEntry(fields={"count": 42}),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, spec)
        format_r = [
            r for r in results.results if r.name.endswith(":format")
        ]
        assert len(format_r) == 1
        assert format_r[0].passed
        assert format_r[0].evidence == "42"


class TestExtractionReport:
    """US-003 (#25): ExtractionReport field-id keyed persistence."""

    def _sectioned_spec(self) -> EvalSpec:
        return EvalSpec(
            skill_name="test-skill",
            sections=[
                SectionRequirement(
                    name="Venues",
                    tiers=[
                        TierRequirement(
                            label="primary",
                            min_entries=1,
                            fields=[
                                FieldRequirement(
                                    name="venue_name",
                                    required=True,
                                    id="venues.primary.venue_name.v1",
                                ),
                                FieldRequirement(
                                    name="phone",
                                    required=False,
                                    format="phone_us",
                                    id="venues.primary.phone.v1",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )

    def test_build_extraction_report_records_keyed_by_field_id(self):
        spec = self._sectioned_spec()
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "primary": [
                        ExtractedEntry(
                            fields={
                                "venue_name": "Cafe Foo",
                                "phone": "(415) 555-0100",
                            }
                        ),
                    ]
                }
            }
        )
        report = build_extraction_report(
            extracted,
            spec,
            skill_name="test-skill",
            model="haiku",
            input_tokens=10,
            output_tokens=3,
        )
        assert len(report.results) == 2
        by_id = {r.field_id: r for r in report.results}
        assert "venues.primary.venue_name.v1" in by_id
        assert "venues.primary.phone.v1" in by_id
        assert by_id["venues.primary.venue_name.v1"].passed is True
        assert by_id["venues.primary.phone.v1"].format_passed is True
        assert report.passed is True

    def test_extraction_report_roundtrip_json(self):
        spec = self._sectioned_spec()
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "primary": [
                        ExtractedEntry(
                            fields={
                                "venue_name": "Cafe Foo",
                                "phone": "not-a-phone",
                            }
                        ),
                    ]
                }
            }
        )
        original = build_extraction_report(
            extracted,
            spec,
            skill_name="test-skill",
            model="haiku",
            input_tokens=11,
            output_tokens=4,
        )

        text = original.to_json()
        payload = json.loads(text)
        # Shape: fields keyed by stable id.
        assert "fields" in payload
        assert "venues.primary.venue_name.v1" in payload["fields"]
        # format_passed=False surfaces in the phone record.
        phone_entries = payload["fields"]["venues.primary.phone.v1"]
        assert phone_entries[0]["format_passed"] is False
        assert phone_entries[0]["passed"] is False

        roundtrip = ExtractionReport.from_json(text)
        assert roundtrip.skill_name == "test-skill"
        assert roundtrip.input_tokens == 11
        assert roundtrip.output_tokens == 4
        assert {r.field_id for r in roundtrip.results} == {
            "venues.primary.venue_name.v1",
            "venues.primary.phone.v1",
        }
        # Round-tripped FieldExtractionResult preserves per-record fields.
        phone_rt = next(
            r
            for r in roundtrip.results
            if r.field_id == "venues.primary.phone.v1"
        )
        assert phone_rt.format_passed is False
        assert phone_rt.evidence == "not-a-phone"

    def test_build_extraction_report_falls_back_to_name_when_id_empty(self):
        spec = EvalSpec(
            skill_name="test-skill",
            sections=[
                SectionRequirement(
                    name="Venues",
                    tiers=[
                        TierRequirement(
                            label="primary",
                            min_entries=0,
                            fields=[
                                FieldRequirement(name="venue_name"),
                            ],
                        ),
                    ],
                ),
            ],
        )
        extracted = ExtractedOutput(
            sections={
                "Venues": {
                    "primary": [
                        ExtractedEntry(fields={"venue_name": "Cafe"}),
                    ]
                }
            }
        )
        report = build_extraction_report(extracted, spec)
        assert report.results[0].field_id == "venue_name"
