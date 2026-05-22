"""Clauditor — Auditor for Claude Code skills and slash commands."""

from clauditor.asserters import SkillAsserter, assert_from
from clauditor.assertions import AssertionResult, AssertionSet
from clauditor.context import IterationContext
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
    "AssertionResult",
    "AssertionSet",
    "EvalSpec",
    "FieldRequirement",
    "GradingReport",
    "GradingResult",
    "IterationContext",
    "SectionRequirement",
    "TierRequirement",
    "SkillAsserter",
    "SkillResult",
    "SkillRunner",
    "SkillSpec",
    "assert_from",
    "TriggerReport",
    "TriggerResult",
    "TriggerTests",
    "VarianceConfig",
    "VarianceReport",
]

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("clauditor-eval")
except PackageNotFoundError:  # pragma: no cover - editable/uninstalled tree
    __version__ = "0.0.0+unknown"


def __getattr__(name: str):
    _lazy_imports = {
        "GradingResult": "clauditor.quality_grader",
        "GradingReport": "clauditor.quality_grader",
        "VarianceReport": "clauditor.quality_grader",
        "TriggerResult": "clauditor.triggers",
        "TriggerReport": "clauditor.triggers",
    }
    if name in _lazy_imports:
        import importlib

        module = importlib.import_module(_lazy_imports[name])
        return getattr(module, name)
    raise AttributeError(f"module 'clauditor' has no attribute {name!r}")
