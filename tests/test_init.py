"""Tests for clauditor package __init__.py."""

import importlib

import pytest


def test_version_exists():
    """__version__ is defined and non-empty."""
    import clauditor

    assert hasattr(clauditor, "__version__")
    assert isinstance(clauditor.__version__, str)
    assert len(clauditor.__version__) > 0


def test_reload_executes_module_level_code():
    """Reloading the module re-executes top-level imports under coverage."""
    import clauditor

    reloaded = importlib.reload(clauditor)
    assert isinstance(reloaded.__version__, str) and len(reloaded.__version__) > 0
    assert hasattr(reloaded, "SkillRunner")
    assert hasattr(reloaded, "EvalSpec")
    assert hasattr(reloaded, "AssertionResult")


def test_lazy_import_grading_report():
    """__getattr__ lazily imports GradingReport from quality_grader."""
    import clauditor

    GradingReport = clauditor.GradingReport  # noqa: N806
    assert GradingReport is not None
    assert GradingReport.__name__ == "GradingReport"


def test_lazy_import_ab_result():
    """__getattr__ lazily imports ABResult from comparator."""
    import clauditor

    ABResult = clauditor.ABResult  # noqa: N806
    assert ABResult is not None
    assert ABResult.__name__ == "ABResult"


def test_lazy_import_trigger_report():
    """__getattr__ lazily imports TriggerReport from triggers."""
    import clauditor

    TriggerReport = clauditor.TriggerReport  # noqa: N806
    assert TriggerReport is not None
    assert TriggerReport.__name__ == "TriggerReport"


def test_invalid_attribute_raises():
    """Accessing a nonexistent attribute raises AttributeError."""
    import clauditor

    with pytest.raises(AttributeError, match="has no attribute 'no_such_thing'"):
        _ = clauditor.no_such_thing


def test_all_names_importable():
    """Every name listed in __all__ is importable from the package."""
    import clauditor

    for name in clauditor.__all__:
        obj = getattr(clauditor, name)
        assert obj is not None, f"{name} resolved to None"
