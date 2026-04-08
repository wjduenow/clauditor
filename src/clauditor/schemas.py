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
class TriggerTests:
    """Test queries for trigger precision testing."""

    should_trigger: list[str] = field(default_factory=list)
    should_not_trigger: list[str] = field(default_factory=list)


@dataclass
class VarianceConfig:
    """Configuration for variance measurement."""

    n_runs: int = 5
    min_stability: float = 0.8


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
    grading_model: str = "claude-sonnet-4-6"
    trigger_tests: TriggerTests | None = None
    variance: VarianceConfig | None = None

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

        trigger_tests = None
        if "trigger_tests" in data:
            tt = data["trigger_tests"]
            trigger_tests = TriggerTests(
                should_trigger=tt.get("should_trigger", []),
                should_not_trigger=tt.get("should_not_trigger", []),
            )

        variance = None
        if "variance" in data:
            v = data["variance"]
            variance = VarianceConfig(
                n_runs=v.get("n_runs", 5),
                min_stability=v.get("min_stability", 0.8),
            )

        return cls(
            skill_name=data.get("skill_name", path.stem),
            description=data.get("description", ""),
            test_args=data.get("test_args", ""),
            assertions=data.get("assertions", []),
            sections=sections,
            grading_criteria=data.get("grading_criteria", []),
            grading_model=data.get("grading_model", "claude-sonnet-4-6"),
            trigger_tests=trigger_tests,
            variance=variance,
        )

    def to_dict(self) -> dict:
        """Serialize to a dict (for JSON output)."""
        result: dict = {
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
            "grading_model": self.grading_model,
        }
        if self.trigger_tests is not None:
            result["trigger_tests"] = {
                "should_trigger": self.trigger_tests.should_trigger,
                "should_not_trigger": self.trigger_tests.should_not_trigger,
            }
        if self.variance is not None:
            result["variance"] = {
                "n_runs": self.variance.n_runs,
                "min_stability": self.variance.min_stability,
            }
        return result
