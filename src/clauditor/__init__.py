"""Clauditor — Auditor for Claude Code skills and slash commands."""

from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.runner import SkillResult, SkillRunner
from clauditor.schemas import EvalSpec, FieldRequirement, SectionRequirement
from clauditor.spec import SkillSpec

__all__ = [
    "AssertionResult",
    "AssertionSet",
    "EvalSpec",
    "FieldRequirement",
    "SectionRequirement",
    "SkillResult",
    "SkillRunner",
    "SkillSpec",
]

__version__ = "0.1.0"
