"""Eval spec and schema definitions for skill output validation.

Loads eval.json files that define what a skill's output should look like.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FieldRequirement:
    """A required field in a structured entry (venue, event, etc.)."""

    name: str
    required: bool = True
    pattern: str | None = None  # Optional regex the field value must match


@dataclass
class SectionRequirement:
    """A required section in the output (e.g., 'Venues', 'Events')."""

    name: str
    min_entries: int = 1
    fields: list[FieldRequirement] = field(default_factory=list)


@dataclass
class EvalSpec:
    """Complete evaluation specification for a skill.

    Loaded from an eval.json file alongside the skill's .md file.
    """

    skill_name: str
    description: str = ""
    test_args: str = ""  # Pre-filled args to skip interactive Q&A
    assertions: list[dict] = field(default_factory=list)  # Layer 1 checks
    sections: list[SectionRequirement] = field(default_factory=list)  # Layer 2 schema
    grading_criteria: list[str] = field(default_factory=list)  # Layer 3 rubric

    @classmethod
    def from_file(cls, path: str | Path) -> EvalSpec:
        """Load an eval spec from a JSON file."""
        path = Path(path)
        with open(path) as f:
            data = json.load(f)

        sections = []
        for s in data.get("sections", []):
            fields = [
                FieldRequirement(
                    name=f["name"],
                    required=f.get("required", True),
                    pattern=f.get("pattern"),
                )
                for f in s.get("fields", [])
            ]
            sections.append(
                SectionRequirement(
                    name=s["name"],
                    min_entries=s.get("min_entries", 1),
                    fields=fields,
                )
            )

        return cls(
            skill_name=data.get("skill_name", path.stem),
            description=data.get("description", ""),
            test_args=data.get("test_args", ""),
            assertions=data.get("assertions", []),
            sections=sections,
            grading_criteria=data.get("grading_criteria", []),
        )

    def to_dict(self) -> dict:
        """Serialize to a dict (for JSON output)."""
        return {
            "skill_name": self.skill_name,
            "description": self.description,
            "test_args": self.test_args,
            "assertions": self.assertions,
            "sections": [
                {
                    "name": s.name,
                    "min_entries": s.min_entries,
                    "fields": [
                        {
                            "name": f.name,
                            "required": f.required,
                            **({"pattern": f.pattern} if f.pattern else {}),
                        }
                        for f in s.fields
                    ],
                }
                for s in self.sections
            ],
            "grading_criteria": self.grading_criteria,
        }
