"""Clauditor — Auditor for Claude Code skills and slash commands."""

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.runner import SkillResult, SkillRunner
from clauditor.schemas import (
    EvalSpec,
    FieldRequirement,
    SectionRequirement,
    TierRequirement,
    TriggerTests,
    VarianceConfig,
)
from clauditor.spec import SkillSpec

__all__ = [
    "ABReport",
    "ABResult",
    "AssertionResult",
    "AssertionSet",
    "EvalSpec",
    "FieldRequirement",
    "GradingReport",
    "GradingResult",
    "SectionRequirement",
    "TierRequirement",
    "SkillResult",
    "SkillRunner",
    "SkillSpec",
    "TriggerReport",
    "TriggerResult",
    "TriggerTests",
    "VarianceConfig",
    "VarianceReport",
]

__version__ = "0.1.0"


def __getattr__(name: str):
    _lazy_imports = {
        "GradingResult": "clauditor.quality_grader",
        "GradingReport": "clauditor.quality_grader",
        "VarianceReport": "clauditor.quality_grader",
        "ABResult": "clauditor.comparator",
        "ABReport": "clauditor.comparator",
        "TriggerResult": "clauditor.triggers",
        "TriggerReport": "clauditor.triggers",
    }
    if name in _lazy_imports:
        import importlib

        module = importlib.import_module(_lazy_imports[name])
        return getattr(module, name)
    raise AttributeError(f"module 'clauditor' has no attribute {name!r}")
