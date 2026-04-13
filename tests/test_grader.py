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
        assert "\u22643" in count_max.message

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


def _make_pattern_spec() -> EvalSpec:
    """Spec with pattern and format validation on fields."""
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
                            FieldRequirement(
                                name="name", required=True
                            ),
                            FieldRequirement(
                                name="phone",
                                required=True,
                                pattern=r"\(\d{3}\) \d{3}-\d{4}",
                            ),
                            FieldRequirement(
                                name="website",
                                required=True,
                                format="url",
                            ),
                            FieldRequirement(
                                name="email",
                                required=False,
                                format="email",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestPatternFormatEnforcement:
    def test_pattern_match(self):
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
        results = grade_extraction(extracted, _make_pattern_spec())
        pattern_results = [
            r for r in results.results if ":pattern" in r.name
        ]
        assert all(r.passed for r in pattern_results)

    def test_pattern_mismatch(self):
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
        results = grade_extraction(extracted, _make_pattern_spec())
        pattern_results = [
            r for r in results.results if ":pattern" in r.name
        ]
        assert len(pattern_results) == 1
        assert not pattern_results[0].passed
        assert pattern_results[0].evidence == "call for hours"

    def test_format_match(self):
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
        results = grade_extraction(extracted, _make_pattern_spec())
        format_results = [
            r for r in results.results if ":format" in r.name
        ]
        assert all(r.passed for r in format_results)

    def test_format_mismatch(self):
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
        results = grade_extraction(extracted, _make_pattern_spec())
        format_results = [
            r for r in results.results if ":format" in r.name
        ]
        failed = [r for r in format_results if not r.passed]
        assert len(failed) == 1
        assert "website" in failed[0].name

    def test_unknown_format_name(self):
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
                                    name="val",
                                    required=True,
                                    format="bogus_format",
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
                        ExtractedEntry(fields={"val": "anything"}),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, spec)
        format_results = [
            r for r in results.results if ":format" in r.name
        ]
        assert len(format_results) == 1
        assert not format_results[0].passed
        assert "Unknown format" in format_results[0].message

    def test_invalid_pattern_regex(self):
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
                                    name="val",
                                    required=True,
                                    pattern="[invalid",
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
                        ExtractedEntry(fields={"val": "test"}),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, spec)
        pattern_results = [
            r for r in results.results if ":pattern" in r.name
        ]
        assert len(pattern_results) == 1
        assert not pattern_results[0].passed
        assert "Invalid pattern" in pattern_results[0].message

    def test_optional_field_missing_skips_validation(self):
        """If an optional field is missing, pattern/format are not checked."""
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
        results = grade_extraction(extracted, _make_pattern_spec())
        # No format check on email since it's not present
        email_format = [
            r
            for r in results.results
            if "email" in r.name and ":format" in r.name
        ]
        assert len(email_format) == 0

    def test_both_pattern_and_format(self):
        """When both pattern and format are set, both must match."""
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
                                    name="phone",
                                    required=True,
                                    pattern=r"\(\d{3}\) \d{3}-\d{4}",
                                    format="phone_us",
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
                        ExtractedEntry(
                            fields={
                                "phone": "(408) 298-5437"
                            }
                        ),
                    ]
                }
            }
        )
        results = grade_extraction(extracted, spec)
        pattern_r = [
            r for r in results.results if ":pattern" in r.name
        ]
        format_r = [
            r for r in results.results if ":format" in r.name
        ]
        assert len(pattern_r) == 1
        assert pattern_r[0].passed
        assert len(format_r) == 1
        assert format_r[0].passed

    def test_non_string_field_value_coerced(self):
        """Non-string values (e.g. int from LLM) should be coerced to str."""
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
                                    pattern=r"\d+",
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
        pattern_r = [
            r for r in results.results if ":pattern" in r.name
        ]
        assert len(pattern_r) == 1
        assert pattern_r[0].passed
        assert pattern_r[0].evidence == "42"
