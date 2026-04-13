"""Eval spec and schema definitions for skill output validation.

Loads eval.json files that define what a skill's output should look like.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FieldRequirement:
    """A required field in a structured entry (venue, event, etc.).

    The ``format`` field does double duty (DEC-007): it accepts either a
    registered format name (e.g. ``"phone_us"``, ``"domain"``) or an inline
    regex. Registry lookup wins when both could apply. Invalid values raise
    ``ValueError`` at construction time.
    """

    name: str
    required: bool = True
    format: str | None = None  # Registry key or inline regex (DEC-007)

    def __post_init__(self) -> None:
        if self.format is None:
            return
        from clauditor.formats import FORMAT_REGISTRY
        if self.format in FORMAT_REGISTRY:
            return
        try:
            re.compile(self.format)
        except re.error as e:
            raise ValueError(
                f"FieldRequirement(name={self.name!r}): format "
                f"{self.format!r} is neither a registered format name "
                f"({sorted(FORMAT_REGISTRY)}) nor a valid regex: {e}"
            ) from e


@dataclass
class TierRequirement:
    """A tier within a section, grouping fields with a label and threshold."""

    label: str
    description: str = ""
    min_entries: int = 0
    max_entries: int | None = None
    fields: list[FieldRequirement] = field(default_factory=list)


@dataclass
class SectionRequirement:
    """A required section in the output (e.g., 'Venues', 'Events')."""

    name: str
    tiers: list[TierRequirement] = field(default_factory=list)


@dataclass
class TriggerTests:
    """Test queries for trigger precision testing."""

    should_trigger: list[str] = field(default_factory=list)
    should_not_trigger: list[str] = field(default_factory=list)


@dataclass
class GradeThresholds:
    """Thresholds for pass/fail determination in quality grading."""

    min_pass_rate: float = 0.7
    min_mean_score: float = 0.5


@dataclass
class VarianceConfig:
    """Configuration for variance measurement."""

    n_runs: int = 5
    min_stability: float = 0.8


def _resolve_field_format(field_dict: dict) -> str | None:
    """Resolve the ``format`` value for a field entry during spec load.

    Rejects the legacy ``pattern`` key with a clear migration message
    (DEC-006/DEC-007): eval specs should use ``format`` which now accepts
    either a registered format name or an inline regex.
    """
    if "pattern" in field_dict:
        raise ValueError(
            f"Field {field_dict.get('name')!r}: the 'pattern' key is no "
            f"longer supported. Use 'format' instead — it accepts either "
            f"a registered format name (e.g. 'phone_us') or an inline regex."
        )
    return field_dict.get("format")


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
    output_file: str | None = None  # Single output file path
    output_files: list[str] = field(default_factory=list)  # Multiple file paths/globs
    trigger_tests: TriggerTests | None = None
    variance: VarianceConfig | None = None
    grade_thresholds: GradeThresholds | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> EvalSpec:
        """Load an eval spec from a JSON file."""
        path = Path(path)
        with open(path) as f:
            data = json.load(f)

        sections = []
        for s in data.get("sections", []):
            if "tiers" in s:
                # New tiered format
                tiers = []
                for t in s["tiers"]:
                    tier_fields = [
                        FieldRequirement(
                            name=f["name"],
                            required=f.get("required", True),
                            format=_resolve_field_format(f),
                        )
                        for f in t.get("fields", [])
                    ]
                    tiers.append(
                        TierRequirement(
                            label=t["label"],
                            description=t.get("description", ""),
                            min_entries=t.get("min_entries", 0),
                            max_entries=t.get("max_entries"),
                            fields=tier_fields,
                        )
                    )
            else:
                # Legacy fields-style: normalize to single default tier
                legacy_fields = [
                    FieldRequirement(
                        name=f["name"],
                        required=f.get("required", True),
                        format=_resolve_field_format(f),
                    )
                    for f in s.get("fields", [])
                ]
                tiers = [
                    TierRequirement(
                        label="default",
                        min_entries=s.get("min_entries", 1),
                        fields=legacy_fields,
                    )
                ]
            sections.append(
                SectionRequirement(
                    name=s["name"],
                    tiers=tiers,
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

        grade_thresholds = None
        if "grade_thresholds" in data:
            gt = data["grade_thresholds"]
            grade_thresholds = GradeThresholds(
                min_pass_rate=gt.get("min_pass_rate", 0.7),
                min_mean_score=gt.get("min_mean_score", 0.5),
            )

        return cls(
            skill_name=data.get("skill_name", path.stem),
            description=data.get("description", ""),
            test_args=data.get("test_args", ""),
            assertions=data.get("assertions", []),
            sections=sections,
            grading_criteria=data.get("grading_criteria", []),
            grading_model=data.get("grading_model", "claude-sonnet-4-6"),
            output_file=data.get("output_file"),
            output_files=data.get("output_files", []),
            trigger_tests=trigger_tests,
            variance=variance,
            grade_thresholds=grade_thresholds,
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
                    "tiers": [
                        {
                            "label": t.label,
                            **(
                                {"description": t.description}
                                if t.description
                                else {}
                            ),
                            "min_entries": t.min_entries,
                            **(
                                {"max_entries": t.max_entries}
                                if t.max_entries is not None
                                else {}
                            ),
                            "fields": [
                                {
                                    "name": f.name,
                                    "required": f.required,
                                    **(
                                        {"format": f.format}
                                        if f.format
                                        else {}
                                    ),
                                }
                                for f in t.fields
                            ],
                        }
                        for t in s.tiers
                    ],
                }
                for s in self.sections
            ],
            "grading_criteria": self.grading_criteria,
            "grading_model": self.grading_model,
        }
        if self.output_file is not None:
            result["output_file"] = self.output_file
        if self.output_files:
            result["output_files"] = self.output_files
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
        if self.grade_thresholds is not None:
            result["grade_thresholds"] = {
                "min_pass_rate": self.grade_thresholds.min_pass_rate,
                "min_mean_score": self.grade_thresholds.min_mean_score,
            }
        return result
