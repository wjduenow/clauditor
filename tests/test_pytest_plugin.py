"""Tests for the clauditor pytest plugin (marker registration, skip logic)."""

from __future__ import annotations

import pytest


def test_clauditor_grade_marker_registered(pytestconfig):
    """Verify the clauditor_grade marker is registered via pytest_configure."""
    markers = pytestconfig.getini("markers")
    assert any("clauditor_grade" in m for m in markers)


@pytest.mark.clauditor_grade
def test_marked_test_is_skipped_without_flag():
    """This test should be skipped when --clauditor-grade is not passed."""
    pytest.fail("This test should have been skipped")


def test_skip_logic_via_subprocess(tmp_path):
    """Verify skip behaviour end-to-end using a subprocess pytest run."""
    import subprocess
    import sys

    test_file = tmp_path / "test_grade_check.py"
    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark.clauditor_grade\n"
        "def test_graded():\n"
        "    pass\n\n"
        "def test_normal():\n"
        "    pass\n"
    )

    # Run WITHOUT --clauditor-grade: graded test should be skipped
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v"],
        capture_output=True,
        text=True,
    )
    assert "test_graded SKIPPED" in result.stdout
    assert "test_normal PASSED" in result.stdout

    # Run WITH --clauditor-grade: graded test should pass
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v", "--clauditor-grade"],
        capture_output=True,
        text=True,
    )
    assert "test_graded PASSED" in result.stdout
    assert "test_normal PASSED" in result.stdout
