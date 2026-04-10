"""Layer 2: LLM-graded schema extraction.

Uses Haiku (cheap, fast) to extract structured fields from skill output,
then validates the extracted data against the eval spec's schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.schemas import EvalSpec


@dataclass
class ExtractedEntry:
    """A single entry extracted from skill output by the grader."""

    fields: dict[str, str | None] = field(default_factory=dict)

    def has_field(self, name: str) -> bool:
        return bool(self.fields.get(name))


@dataclass
class ExtractedOutput:
    """Structured data extracted from skill output."""

    sections: dict[str, dict[str, list[ExtractedEntry]]] = field(default_factory=dict)
    raw_json: dict | None = None


def build_extraction_prompt(eval_spec: EvalSpec) -> str:
    """Build a prompt that asks the LLM to extract structured data."""
    sections_desc = []
    for section in eval_spec.sections:
        tier_descs = []
        for tier in section.tiers:
            field_names = [f.name for f in tier.fields]
            desc_part = f' ({tier.description})' if tier.description else ''
            tier_descs.append(
                f'  - Tier "{tier.label}"{desc_part}: '
                f'fields [{", ".join(field_names)}]'
            )
        sections_desc.append(
            f'- Section "{section.name}":\n' + "\n".join(tier_descs)
        )

    # Build schema example lines
    schema_lines = []
    for s in eval_spec.sections:
        tier_lines = []
        for t in s.tiers:
            field_pairs = ", ".join(
                f'"{f.name}": "value or null"' for f in t.fields
            )
            tier_lines.append(f'    "{t.label}": [{{ {field_pairs} }}]')
        schema_lines.append(
            f'  "{s.name}": {{\n' + ",\n".join(tier_lines) + "\n  }"
        )
    schema_block = ",\n".join(schema_lines)
    sections_block = "\n".join(sections_desc)

    return (
        "Extract structured data from the following skill output.\n"
        "Return ONLY valid JSON matching this schema:\n\n"
        "{\n"
        f"{schema_block}\n"
        "}\n\n"
        "Sections to extract:\n"
        f"{sections_block}\n\n"
        "Rules:\n"
        "- Return null for fields that are missing or unclear\n"
        "- Extract the raw value as a string, do not interpret or reformat\n"
        "- Include ALL entries found in each section\n"
        "- Group entries by tier within each section\n"
    )


def grade_extraction(extracted: ExtractedOutput, eval_spec: EvalSpec) -> AssertionSet:
    """Validate extracted data against the eval spec's schema requirements."""
    results = AssertionSet()

    for section_req in eval_spec.sections:
        section_data = extracted.sections.get(section_req.name, {})

        for tier in section_req.tiers:
            entries = section_data.get(tier.label, [])

            # Check minimum entry count
            results.results.append(
                AssertionResult(
                    name=f"section:{section_req.name}:count/{tier.label}",
                    passed=len(entries) >= tier.min_entries,
                    message=(
                        f"Section '{section_req.name}' tier '{tier.label}' "
                        f"has {len(entries)} entries "
                        f"(need \u2265{tier.min_entries})"
                    ),
                )
            )

            # Check required fields on each entry
            for i, entry in enumerate(entries):
                for field_req in tier.fields:
                    if not field_req.required:
                        continue
                    has_value = entry.has_field(field_req.name)
                    results.results.append(
                        AssertionResult(
                            name=f"section:{section_req.name}/{tier.label}[{i}].{field_req.name}",
                            passed=has_value,
                            message=(
                                "Field present"
                                if has_value
                                else f"Missing required field "
                                f"'{field_req.name}' in "
                                f"{section_req.name} tier "
                                f"'{tier.label}' entry {i + 1}"
                            ),
                            evidence=entry.fields.get(field_req.name),
                        )
                    )

    return results


async def extract_and_grade(
    output: str,
    eval_spec: EvalSpec,
    model: str = "claude-haiku-4-5-20251001",
) -> AssertionSet:
    """Layer 2: Extract structured data with Haiku, then validate against schema.

    Requires the 'grader' extra: pip install clauditor[grader]
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ImportError(
            "Layer 2 grading requires the anthropic SDK. "
            "Install with: pip install clauditor[grader]"
        )

    client = AsyncAnthropic()
    prompt = build_extraction_prompt(eval_spec)

    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "user", "content": f"{prompt}\n\nSkill output:\n{output}"},
        ],
    )

    # Parse the JSON response
    response_text = response.content[0].text
    try:
        # Extract JSON from response (may be wrapped in markdown code block)
        json_str = response_text
        if "```json" in json_str:
            json_str = json_str.split("```json")[-1].split("```")[0]
        elif "```" in json_str:
            # Generic fence without language tag
            parts = json_str.split("```")
            # Take the content between the first pair of fences
            if len(parts) >= 3:
                json_str = parts[1]

        raw = json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        return AssertionSet(
            results=[
                AssertionResult(
                    name="grader:parse",
                    passed=False,
                    message="Failed to parse grader response as JSON",
                    evidence=response_text[:200],
                )
            ]
        )

    # Convert raw JSON to ExtractedOutput
    extracted = ExtractedOutput(raw_json=raw)
    parse_errors: list[AssertionResult] = []
    expected_sections = {s.name for s in eval_spec.sections}

    for section_name, section_data in raw.items():
        if isinstance(section_data, dict):
            # Tiered format: {"tier_label": [entries]}
            tier_map: dict[str, list[ExtractedEntry]] = {}
            for tier_label, entries_data in section_data.items():
                if isinstance(entries_data, list):
                    tier_map[tier_label] = [
                        ExtractedEntry(fields=e)
                        for e in entries_data
                        if isinstance(e, dict)
                    ]
            extracted.sections[section_name] = tier_map
        elif (
            isinstance(section_data, list)
            and section_name in expected_sections
        ):
            # Flat list for an expected section = shape mismatch error
            parse_errors.append(
                AssertionResult(
                    name=f"grader:parse:{section_name}",
                    passed=False,
                    message=(
                        f"Section '{section_name}' returned flat list "
                        f"instead of tier-grouped dict"
                    ),
                )
            )

    if parse_errors:
        return AssertionSet(results=parse_errors)

    return grade_extraction(extracted, eval_spec)
