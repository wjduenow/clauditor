"""Tests for :mod:`clauditor.conformance` — agentskills.io spec checker.

The pure helper ``check_conformance(skill_md_text, skill_path)`` takes
already-read Markdown text plus a ``Path`` (used only for layout-shape
classification and parent-dir-match checks) and returns a
``list[ConformanceIssue]``. No I/O, no stderr emission.

Tests are organized one class per rule category, matching DEC-001 and
the plan's US-001 TDD outline. Every rule from the Discovery section
gets a dedicated test so a regression surfaces with a clear signal.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import clauditor.conformance as _conformance_mod

# Module is imported by the pytest plugin (via spec.py) before coverage
# instrumentation starts, so reload to get accurate per-line coverage
# (matches the ``tests/test_schemas.py`` pattern documented in CLAUDE.md).
importlib.reload(_conformance_mod)

from clauditor.conformance import (  # noqa: E402
    AGENTSKILLS_NAME_RE,
    KNOWN_CLAUDE_CODE_EXTENSION_KEYS,
    ConformanceIssue,
    check_conformance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _modern_path(name: str = "my-skill") -> Path:
    """Return a representative modern-layout path (``<name>/SKILL.md``).

    No tmp_path: the helper is pure and the path is used only for
    ``name == "SKILL.md"`` classification and ``parent.name`` comparison.
    """
    return Path(name) / "SKILL.md"


def _legacy_path(name: str = "my-skill") -> Path:
    """Return a representative legacy-layout path (``<name>.md``)."""
    return Path(f"{name}.md")


def _build_skill(
    *,
    name: str | None = "my-skill",
    description: str | None = "A test skill description.",
    license: str | None = None,
    compatibility: str | None = None,
    metadata: dict[str, str] | None = None,
    allowed_tools: str | None = None,
    extra: dict | None = None,
    body: str = "# Body\n\nSome content.\n",
) -> str:
    """Build a SKILL.md text from a conformant baseline.

    Any kwarg set to ``None`` omits the field. ``extra`` lets a test
    inject additional key/value lines (e.g. unknown keys or extension
    keys from the allowlist) verbatim.
    """
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    if description is not None:
        lines.append(f"description: {description}")
    if license is not None:
        lines.append(f"license: {license}")
    if compatibility is not None:
        lines.append(f"compatibility: {compatibility}")
    if metadata is not None:
        lines.append("metadata:")
        for k, v in metadata.items():
            lines.append(f"  {k}: {v}")
    if allowed_tools is not None:
        lines.append(f"allowed-tools: {allowed_tools}")
    if extra is not None:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    return text


def _codes(issues: list[ConformanceIssue]) -> list[str]:
    return [i.code for i in issues]


def _by_code(
    issues: list[ConformanceIssue], code: str
) -> list[ConformanceIssue]:
    return [i for i in issues if i.code == code]


# ---------------------------------------------------------------------------
# Baseline: a valid minimal skill produces zero issues
# ---------------------------------------------------------------------------


class TestMinimalValidSkill:
    def test_minimal_valid_skill_produces_zero_issues(self):
        """A conformant modern-layout skill has no issues."""
        text = _build_skill()
        issues = check_conformance(text, _modern_path())
        assert issues == []

    def test_full_optional_fields_produce_zero_issues(self):
        text = _build_skill(
            license="MIT",
            compatibility="Requires Python 3.11+",
            metadata={"author": "alice"},
            allowed_tools="Bash(ls) Read",
        )
        # allowed_tools experimental WARNING still fires; assert error-free
        issues = check_conformance(text, _modern_path())
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []


# ---------------------------------------------------------------------------
# Frontmatter structure
# ---------------------------------------------------------------------------


class TestFrontmatterStructure:
    def test_frontmatter_missing_reports_error(self):
        text = "# No frontmatter here\n\nJust markdown.\n"
        issues = check_conformance(text, _modern_path())
        codes = _codes(issues)
        assert "AGENTSKILLS_FRONTMATTER_MISSING" in codes
        issue = _by_code(issues, "AGENTSKILLS_FRONTMATTER_MISSING")[0]
        assert issue.severity == "error"

    def test_frontmatter_invalid_yaml_reports_error(self):
        # Missing closing delimiter → parse_frontmatter raises ValueError.
        text = "---\nname: my-skill\ndescription: d\n\n# No closing\n"
        issues = check_conformance(text, _modern_path())
        codes = _codes(issues)
        assert "AGENTSKILLS_FRONTMATTER_INVALID_YAML" in codes
        issue = _by_code(issues, "AGENTSKILLS_FRONTMATTER_INVALID_YAML")[0]
        assert issue.severity == "error"

    def test_malformed_yaml_does_not_raise(self):
        # The colon-less line inside frontmatter makes parse_frontmatter
        # raise; check_conformance must convert that to an issue, not
        # let the exception escape.
        text = "---\nno colon here\n---\n"
        # Must not raise.
        issues = check_conformance(text, _modern_path())
        assert any(
            i.code == "AGENTSKILLS_FRONTMATTER_INVALID_YAML" for i in issues
        )

    def test_malformed_yaml_with_multiline_exception_message_stays_single_line(
        self, monkeypatch
    ):
        """ValueError messages from parse_frontmatter can contain newlines.

        Regression for the CodeRabbit finding: `parse_frontmatter` may
        raise a multi-line diagnostic (caret-indicator format or
        embedded context). Interpolating that verbatim into the
        ConformanceIssue message would trigger the __post_init__
        single-line invariant and break `check_conformance`'s "never
        raises" contract. Sanitize before embedding.
        """
        import clauditor.conformance as _conformance_mod  # noqa: PLC0415

        def _raise_multiline(_text: str):
            raise ValueError("line 1\nline 2 (offending token)\n  ^")

        monkeypatch.setattr(
            _conformance_mod, "parse_frontmatter", _raise_multiline, raising=False
        )
        # Monkeypatch the local import target — check_conformance imports
        # inline, so patch _frontmatter directly.
        import clauditor._frontmatter as _fm_mod  # noqa: PLC0415

        monkeypatch.setattr(_fm_mod, "parse_frontmatter", _raise_multiline)

        # Must NOT raise even when the exception string has newlines.
        issues = check_conformance(
            "---\nname: x\n---\n# body\n", _modern_path()
        )
        invalid_yaml = _by_code(issues, "AGENTSKILLS_FRONTMATTER_INVALID_YAML")
        assert len(invalid_yaml) == 1
        # Newlines replaced with visible escape sequences to preserve
        # DEC-014 single-line stderr contract.
        assert "\n" not in invalid_yaml[0].message
        assert "\r" not in invalid_yaml[0].message
        assert "\\n" in invalid_yaml[0].message  # escape is visible

    def test_unknown_key_reports_warning(self):
        text = _build_skill(extra={"bogus-field": "value"})
        issues = check_conformance(text, _modern_path())
        unknown = _by_code(issues, "AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY")
        assert len(unknown) == 1
        assert unknown[0].severity == "warning"
        assert "bogus-field" in unknown[0].message

    def test_allowlisted_claude_code_extension_keys_accepted(self):
        text = _build_skill(
            extra={
                "argument-hint": '"[skill-path]"',
                "disable-model-invocation": "true",
            }
        )
        issues = check_conformance(text, _modern_path())
        unknown = _by_code(issues, "AGENTSKILLS_FRONTMATTER_UNKNOWN_KEY")
        assert unknown == []

    def test_allowlist_is_frozenset_with_required_entries(self):
        assert isinstance(KNOWN_CLAUDE_CODE_EXTENSION_KEYS, frozenset)
        assert "argument-hint" in KNOWN_CLAUDE_CODE_EXTENSION_KEYS
        assert "disable-model-invocation" in KNOWN_CLAUDE_CODE_EXTENSION_KEYS


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


class TestNameValidation:
    def test_name_missing_reports_error(self):
        text = _build_skill(name=None)
        issues = check_conformance(text, _modern_path())
        codes = _codes(issues)
        assert "AGENTSKILLS_NAME_MISSING" in codes
        issue = _by_code(issues, "AGENTSKILLS_NAME_MISSING")[0]
        assert issue.severity == "error"

    def test_name_empty_reports_error(self):
        # Empty scalar "name:" produces an empty-string value.
        text = "---\nname:\ndescription: d\n---\n\nbody\n"
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_NAME_EMPTY" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_NAME_EMPTY")[0]
        assert issue.severity == "error"

    def test_name_too_long_reports_error(self):
        long_name = "a" * 65  # > 64 chars
        # Parent dir matches so PARENT_DIR_MISMATCH does not fire.
        text = _build_skill(name=long_name)
        issues = check_conformance(text, _modern_path(long_name))
        assert "AGENTSKILLS_NAME_TOO_LONG" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_NAME_TOO_LONG")[0]
        assert issue.severity == "error"

    def test_name_uppercase_reports_invalid_chars(self):
        text = _build_skill(name="MySkill")
        issues = check_conformance(text, _modern_path("MySkill"))
        assert "AGENTSKILLS_NAME_INVALID_CHARS" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_NAME_INVALID_CHARS")[0]
        assert issue.severity == "error"

    def test_name_underscore_reports_invalid_chars(self):
        text = _build_skill(name="my_skill")
        issues = check_conformance(text, _modern_path("my_skill"))
        assert "AGENTSKILLS_NAME_INVALID_CHARS" in _codes(issues)

    def test_name_leading_hyphen_reports_error(self):
        text = _build_skill(name="-bad")
        issues = check_conformance(text, _modern_path("-bad"))
        codes = _codes(issues)
        assert "AGENTSKILLS_NAME_LEADING_HYPHEN" in codes
        issue = _by_code(issues, "AGENTSKILLS_NAME_LEADING_HYPHEN")[0]
        assert issue.severity == "error"

    def test_name_trailing_hyphen_reports_error(self):
        text = _build_skill(name="bad-")
        issues = check_conformance(text, _modern_path("bad-"))
        codes = _codes(issues)
        assert "AGENTSKILLS_NAME_TRAILING_HYPHEN" in codes
        issue = _by_code(issues, "AGENTSKILLS_NAME_TRAILING_HYPHEN")[0]
        assert issue.severity == "error"

    def test_name_consecutive_hyphens_reports_error(self):
        text = _build_skill(name="foo--bar")
        issues = check_conformance(text, _modern_path("foo--bar"))
        codes = _codes(issues)
        assert "AGENTSKILLS_NAME_CONSECUTIVE_HYPHENS" in codes
        issue = _by_code(issues, "AGENTSKILLS_NAME_CONSECUTIVE_HYPHENS")[0]
        assert issue.severity == "error"

    def test_name_parent_dir_mismatch_reports_error_modern_only(self):
        # Frontmatter says "my-skill", parent dir is "other-dir".
        text = _build_skill(name="my-skill")
        path = Path("other-dir") / "SKILL.md"
        issues = check_conformance(text, path)
        codes = _codes(issues)
        assert "AGENTSKILLS_NAME_PARENT_DIR_MISMATCH" in codes
        issue = _by_code(issues, "AGENTSKILLS_NAME_PARENT_DIR_MISMATCH")[0]
        assert issue.severity == "error"

    def test_parent_dir_mismatch_not_fired_for_legacy_layout(self):
        # Legacy <name>.md skip parent-dir-match check.
        text = _build_skill(name="my-skill")
        issues = check_conformance(text, _legacy_path("anything"))
        assert "AGENTSKILLS_NAME_PARENT_DIR_MISMATCH" not in _codes(issues)

    def test_valid_single_char_name_accepted(self):
        # Regex has an explicit 1-char branch.
        text = _build_skill(name="a")
        issues = check_conformance(text, _modern_path("a"))
        # No name-related issues.
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_NAME_"), (
                f"unexpected name issue on single-char name: {code}"
            )

    def test_valid_numeric_name_accepted(self):
        text = _build_skill(name="skill42")
        issues = check_conformance(text, _modern_path("skill42"))
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_NAME_"), (
                f"unexpected name issue on numeric-suffix name: {code}"
            )


class TestNameRegexConstant:
    def test_name_regex_is_compiled_pattern(self):
        # re.Pattern acceptance — must fullmatch-callable.
        assert AGENTSKILLS_NAME_RE.fullmatch("ok-name") is not None
        assert AGENTSKILLS_NAME_RE.fullmatch("") is None
        assert AGENTSKILLS_NAME_RE.fullmatch("-bad") is None
        assert AGENTSKILLS_NAME_RE.fullmatch("bad-") is None
        assert AGENTSKILLS_NAME_RE.fullmatch("foo--bar") is None
        assert AGENTSKILLS_NAME_RE.fullmatch("a") is not None


# ---------------------------------------------------------------------------
# Description validation
# ---------------------------------------------------------------------------


class TestDescriptionValidation:
    def test_description_missing_reports_error(self):
        text = _build_skill(description=None)
        issues = check_conformance(text, _modern_path())
        codes = _codes(issues)
        assert "AGENTSKILLS_DESCRIPTION_MISSING" in codes
        issue = _by_code(issues, "AGENTSKILLS_DESCRIPTION_MISSING")[0]
        assert issue.severity == "error"

    def test_description_empty_reports_error(self):
        text = "---\nname: my-skill\ndescription:\n---\n\nbody\n"
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_DESCRIPTION_EMPTY" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_DESCRIPTION_EMPTY")[0]
        assert issue.severity == "error"

    def test_description_too_long_reports_error(self):
        text = _build_skill(description="x" * 1025)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_DESCRIPTION_TOO_LONG" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_DESCRIPTION_TOO_LONG")[0]
        assert issue.severity == "error"

    def test_description_exactly_1024_chars_accepted(self):
        text = _build_skill(description="x" * 1024)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_DESCRIPTION_TOO_LONG" not in _codes(issues)


# ---------------------------------------------------------------------------
# License validation
# ---------------------------------------------------------------------------


class TestLicenseValidation:
    def test_license_absent_is_silently_accepted(self):
        text = _build_skill(license=None)
        issues = check_conformance(text, _modern_path())
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_LICENSE_"), (
                f"license issue on absent license: {code}"
            )

    def test_license_valid_is_silently_accepted(self):
        text = _build_skill(license="MIT")
        issues = check_conformance(text, _modern_path())
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_LICENSE_"), code

    def test_license_empty_string_reports_error(self):
        text = "---\nname: my-skill\ndescription: d\nlicense:\n---\n\nbody\n"
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_LICENSE_EMPTY" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_LICENSE_EMPTY")[0]
        assert issue.severity == "error"


# ---------------------------------------------------------------------------
# Compatibility validation
# ---------------------------------------------------------------------------


class TestCompatibilityValidation:
    def test_compatibility_absent_is_silently_accepted(self):
        text = _build_skill(compatibility=None)
        issues = check_conformance(text, _modern_path())
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_COMPATIBILITY_"), code

    def test_compatibility_empty_reports_error(self):
        text = (
            "---\nname: my-skill\ndescription: d\ncompatibility:\n---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_COMPATIBILITY_EMPTY" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_COMPATIBILITY_EMPTY")[0]
        assert issue.severity == "error"

    def test_compatibility_too_long_reports_error(self):
        text = _build_skill(compatibility="c" * 501)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_COMPATIBILITY_TOO_LONG" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_COMPATIBILITY_TOO_LONG")[0]
        assert issue.severity == "error"


# ---------------------------------------------------------------------------
# Metadata validation
# ---------------------------------------------------------------------------


class TestMetadataValidation:
    def test_metadata_nested_map_accepted(self):
        text = _build_skill(metadata={"author": "alice", "version": '"1.0"'})
        issues = check_conformance(text, _modern_path())
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_METADATA_"), code

    def test_metadata_scalar_reports_not_map(self):
        # Scalar "metadata: value" produces a string, not a dict.
        text = (
            "---\nname: my-skill\ndescription: d\nmetadata: plain-string\n"
            "---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_METADATA_NOT_MAP" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_METADATA_NOT_MAP")[0]
        assert issue.severity == "error"

    def test_metadata_absent_is_silently_accepted(self):
        text = _build_skill(metadata=None)
        issues = check_conformance(text, _modern_path())
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_METADATA_"), code


# ---------------------------------------------------------------------------
# Allowed-tools validation
# ---------------------------------------------------------------------------


class TestAllowedToolsValidation:
    def test_allowed_tools_absent_is_silently_accepted(self):
        text = _build_skill(allowed_tools=None)
        issues = check_conformance(text, _modern_path())
        for code in _codes(issues):
            assert not code.startswith("AGENTSKILLS_ALLOWED_TOOLS_"), code

    def test_allowed_tools_present_always_fires_experimental_warning(self):
        text = _build_skill(allowed_tools="Bash(ls) Read")
        issues = check_conformance(text, _modern_path())
        experimental = _by_code(
            issues, "AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL"
        )
        assert len(experimental) == 1
        assert experimental[0].severity == "warning"


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


class TestBodyChecks:
    def test_body_under_500_lines_accepted(self):
        # 400 lines of prose; no warning.
        body = "\n".join(f"Line {i}" for i in range(400)) + "\n"
        text = _build_skill(body=body)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_BODY_TOO_LONG" not in _codes(issues)

    def test_body_over_500_lines_reports_warning(self):
        body = "\n".join(f"Line {i}" for i in range(600)) + "\n"
        text = _build_skill(body=body)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_BODY_TOO_LONG" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_BODY_TOO_LONG")[0]
        assert issue.severity == "warning"

    def test_body_empty_is_silently_accepted(self):
        text = _build_skill(body="")
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_BODY_TOO_LONG" not in _codes(issues)

    def test_body_exactly_500_lines_no_warning(self):
        """500 lines is the inclusive ceiling — no warning."""
        body = "\n".join(f"Line {i}" for i in range(500)) + "\n"
        text = _build_skill(body=body)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_BODY_TOO_LONG" not in _codes(issues)

    def test_body_501_lines_triggers_warning(self):
        """501 lines crosses the threshold — warning fires."""
        body = "\n".join(f"Line {i}" for i in range(501)) + "\n"
        text = _build_skill(body=body)
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_BODY_TOO_LONG" in _codes(issues)


# ---------------------------------------------------------------------------
# Layout validation
# ---------------------------------------------------------------------------


class TestLayoutChecks:
    def test_modern_layout_produces_no_layout_warning(self):
        text = _build_skill()
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_LAYOUT_LEGACY" not in _codes(issues)

    def test_legacy_layout_reports_warning(self):
        text = _build_skill()
        issues = check_conformance(text, _legacy_path("my-skill"))
        assert "AGENTSKILLS_LAYOUT_LEGACY" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_LAYOUT_LEGACY")[0]
        assert issue.severity == "warning"
        # Load-bearing copy from DEC's "Load-bearing message copy".
        assert "agentskills.io" in issue.message
        assert "SKILL.md" in issue.message


# ---------------------------------------------------------------------------
# YAML type coercion / non-string guard
# ---------------------------------------------------------------------------


class TestYAMLTypeCoercion:
    """Strict ``isinstance(str)`` guards for every string-typed field.

    The in-tree ``_frontmatter.parse_frontmatter`` does not auto-coerce
    scalars (``name: true`` yields the string ``"true"``), so these
    tests drive the non-string branch by using YAML-subset nested-map
    shapes — e.g. writing ``name:\\n  sub: val`` to force
    ``parsed["name"]`` to be a ``dict`` instead of a ``str``. G9 / G10
    of the plan mandate that every string-typed field assert
    ``isinstance(val, str)`` and emit ``_NOT_STRING`` otherwise.
    """

    def test_name_nested_dict_reports_not_string(self):
        text = (
            "---\n"
            "name:\n"
            "  nested: whatever\n"
            "description: d\n"
            "---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_NAME_NOT_STRING" in _codes(issues)
        issue = _by_code(issues, "AGENTSKILLS_NAME_NOT_STRING")[0]
        assert issue.severity == "error"

    def test_description_nested_dict_reports_not_string(self):
        text = (
            "---\n"
            "name: my-skill\n"
            "description:\n"
            "  nested: whatever\n"
            "---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_DESCRIPTION_NOT_STRING" in _codes(issues)

    def test_license_nested_dict_reports_not_string(self):
        text = (
            "---\n"
            "name: my-skill\n"
            "description: d\n"
            "license:\n"
            "  nested: x\n"
            "---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_LICENSE_NOT_STRING" in _codes(issues)

    def test_compatibility_nested_dict_reports_not_string(self):
        text = (
            "---\n"
            "name: my-skill\n"
            "description: d\n"
            "compatibility:\n"
            "  nested: x\n"
            "---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_COMPATIBILITY_NOT_STRING" in _codes(issues)

    def test_allowed_tools_nested_dict_reports_not_string(self):
        text = (
            "---\n"
            "name: my-skill\n"
            "description: d\n"
            "allowed-tools:\n"
            "  nested: x\n"
            "---\n\nbody\n"
        )
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_ALLOWED_TOOLS_NOT_STRING" in _codes(issues)
        # Experimental warning should NOT fire when the field is the
        # wrong type — the not-string error is the only signal the
        # author needs.
        assert "AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL" not in _codes(
            issues
        )

    def test_metadata_normal_string_values_pass(self):
        text = _build_skill(metadata={"author": "alice"})
        issues = check_conformance(text, _modern_path())
        assert "AGENTSKILLS_METADATA_VALUE_NOT_STRING" not in _codes(issues)
        assert "AGENTSKILLS_METADATA_KEY_NOT_STRING" not in _codes(issues)

    def test_metadata_defensive_key_guard_fires_on_synthetic_non_string_key(
        self,
    ):
        """`_check_metadata` rejects non-string keys.

        `parse_frontmatter` cannot produce non-string keys (the YAML
        subset parser always returns `str` from `_split_key_value`),
        so this defensive guard is unreachable via the public API.
        Exercise it directly to cover the G10 contract — same shape
        as the reviewer's pass-1 diagnostic.
        """
        # Non-public helper; test-only import.
        from clauditor.conformance import _check_metadata  # noqa: PLC0415

        issues: list[ConformanceIssue] = []
        _check_metadata({"metadata": {42: "not a string key"}}, issues)
        codes = [i.code for i in issues]
        assert "AGENTSKILLS_METADATA_KEY_NOT_STRING" in codes
        assert all(i.severity == "error" for i in issues)

    def test_metadata_defensive_value_guard_fires_on_synthetic_non_string_value(
        self,
    ):
        """`_check_metadata` rejects non-string values (e.g. YAML-coerced ints)."""
        from clauditor.conformance import _check_metadata  # noqa: PLC0415

        issues: list[ConformanceIssue] = []
        _check_metadata({"metadata": {"version": 1.0}}, issues)
        codes = [i.code for i in issues]
        assert "AGENTSKILLS_METADATA_VALUE_NOT_STRING" in codes
        assert all(i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------
# Dataclass surface + return-type sanity
# ---------------------------------------------------------------------------


class TestConformanceIssueShape:
    def test_issue_has_required_fields(self):
        issue = ConformanceIssue(
            code="AGENTSKILLS_NAME_MISSING",
            severity="error",
            message="Missing `name` field.",
        )
        assert issue.code == "AGENTSKILLS_NAME_MISSING"
        assert issue.severity == "error"
        assert issue.message == "Missing `name` field."

    def test_check_conformance_returns_empty_list_for_valid_input(self):
        text = _build_skill()
        result = check_conformance(text, _modern_path())
        assert isinstance(result, list)
        assert result == []

    @pytest.mark.parametrize(
        "severity",
        ["error", "warning"],
    )
    def test_issue_severity_values_are_the_two_canonical_choices(
        self, severity
    ):
        issue = ConformanceIssue(
            code="TEST_CODE", severity=severity, message="msg"
        )
        assert issue.severity in {"error", "warning"}


# ---------------------------------------------------------------------------
# DEC-014 stderr-prefix format (shared by CLI + SkillSpec.from_file hook).
# ---------------------------------------------------------------------------


class TestFormatIssueLine:
    """Byte-identical pin on the ``clauditor.conformance:`` prefix format.

    The helper is the single seam per DEC-014 + QG-pass-1 M1. Any
    reshaping of the format (e.g. changing the separator, adding
    brackets, emitting a trailing space) should be an intentional
    operator-visible change and should break this test loudly — not
    silently regress the substring-only tests in `TestCmdLint` /
    `TestFromFile`.
    """

    def test_format_is_byte_identical_to_contract(self):
        from clauditor.conformance import format_issue_line  # noqa: PLC0415

        issue = ConformanceIssue(
            code="AGENTSKILLS_NAME_MISSING",
            severity="error",
            message="Required frontmatter field `name` is missing.",
        )
        assert format_issue_line(issue) == (
            "clauditor.conformance: AGENTSKILLS_NAME_MISSING: "
            "Required frontmatter field `name` is missing."
        )

    def test_format_uses_message_verbatim(self):
        """Any valid single-line message is echoed verbatim after the prefix."""
        from clauditor.conformance import format_issue_line  # noqa: PLC0415

        issue = ConformanceIssue(
            code="XYZ_CODE",
            severity="warning",
            message="a: b: c — a message that itself contains colons",
        )
        assert format_issue_line(issue) == (
            "clauditor.conformance: XYZ_CODE: "
            "a: b: c — a message that itself contains colons"
        )


class TestConformanceIssueInvariants:
    """The dataclass rejects multi-line messages at construction time."""

    @pytest.mark.parametrize("bad_char", ["\n", "\r"])
    def test_message_rejects_newline_chars(self, bad_char):
        with pytest.raises(ValueError, match="single-line"):
            ConformanceIssue(
                code="TEST",
                severity="error",
                message=f"line1{bad_char}line2",
            )

    def test_all_check_conformance_messages_are_single_line(self):
        """Every issue emitted by the full rule set is single-line.

        Walks a fixture matrix that triggers (at minimum) one error
        and one warning per top-level rule category. Defensive guard
        against a future author adding a rule whose message template
        contains a newline.
        """
        fixtures = [
            _build_skill(name="", description="ok"),  # NAME_EMPTY
            _build_skill(name="", description=""),  # NAME_EMPTY + DESCRIPTION_EMPTY
            _build_skill(description="x" * 1025),  # DESCRIPTION_TOO_LONG
            _build_skill(metadata={"author": "alice", "extra": "2"}),
            _build_skill(body="\n".join("line" for _ in range(600))),  # BODY_TOO_LONG
        ]
        for text in fixtures:
            issues = check_conformance(text, _modern_path())
            for issue in issues:
                assert "\n" not in issue.message, (
                    f"Multi-line message from {issue.code}: {issue.message!r}"
                )
                assert "\r" not in issue.message, (
                    f"CR in message from {issue.code}: {issue.message!r}"
                )
