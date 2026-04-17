"""Tests for :mod:`clauditor._frontmatter`.

The parser is the YAML-subset helper that used to live inline in
``scripts/validate_skill_frontmatter.py`` and is now shared across
the validator script and the future ``propose-eval`` CLI. The tests
here focus on the parser's direct contract: return-value shape,
body-text passthrough, and error behavior. End-to-end coverage of
the CLI script lives in ``tests/test_skill_validator.py``.
"""

from __future__ import annotations

import pytest

from clauditor._frontmatter import parse_frontmatter


class TestParseFrontmatter:
    def test_no_frontmatter_returns_none(self) -> None:
        text = "# Just a markdown file\n\nNo frontmatter here.\n"
        parsed, body = parse_frontmatter(text)
        assert parsed is None
        assert body == text

    def test_valid_frontmatter_and_body(self) -> None:
        text = (
            "---\n"
            "name: clauditor\n"
            "description: A test skill.\n"
            "---\n"
            "\n"
            "# Body text\n"
            "\n"
            "Some paragraph.\n"
        )
        parsed, body = parse_frontmatter(text)
        assert parsed == {
            "name": "clauditor",
            "description": "A test skill.",
        }
        assert body == "\n# Body text\n\nSome paragraph.\n"

    def test_missing_closing_delimiter_raises(self) -> None:
        text = (
            "---\n"
            "name: clauditor\n"
            "description: No closing delimiter.\n"
            "\n"
            "# Body\n"
        )
        with pytest.raises(ValueError, match="closing frontmatter delimiter"):
            parse_frontmatter(text)

    def test_no_opening_delimiter_returns_none_body_unchanged(self) -> None:
        text = "# Heading without frontmatter\n\nBody text.\n"
        parsed, body = parse_frontmatter(text)
        assert parsed is None
        assert body == text

    def test_top_level_scalars_parsed(self) -> None:
        text = (
            '---\n'
            'name: clauditor\n'
            'description: "quoted desc"\n'
            "other: 'single-quoted'\n"
            "---\n"
            "body\n"
        )
        parsed, _ = parse_frontmatter(text)
        assert parsed == {
            "name": "clauditor",
            "description": "quoted desc",
            "other": "single-quoted",
        }

    def test_nested_metadata_block_parsed(self) -> None:
        text = (
            "---\n"
            "name: clauditor\n"
            "metadata:\n"
            '  clauditor-version: "0.0.0-dev"\n'
            "  origin: inline\n"
            "description: after the nest\n"
            "---\n"
            "body\n"
        )
        parsed, _ = parse_frontmatter(text)
        assert parsed == {
            "name": "clauditor",
            "metadata": {
                "clauditor-version": "0.0.0-dev",
                "origin": "inline",
            },
            "description": "after the nest",
        }

    def test_inline_list_field_parsed(self) -> None:
        # ``allowed-tools`` uses a space-separated inline list shape.
        # The parser stores it as the raw value string — it does not
        # split on whitespace (the validator script never split, and
        # splitting here would be a behavior change callers don't ask
        # for).
        text = (
            "---\n"
            "name: clauditor\n"
            "description: d\n"
            "allowed-tools: Bash(clauditor *) Read Grep\n"
            "---\n"
            "body\n"
        )
        parsed, _ = parse_frontmatter(text)
        assert parsed is not None
        assert parsed["allowed-tools"] == "Bash(clauditor *) Read Grep"

    def test_empty_body_after_frontmatter(self) -> None:
        text = "---\nname: clauditor\ndescription: d\n---\n"
        parsed, body = parse_frontmatter(text)
        assert parsed == {"name": "clauditor", "description": "d"}
        assert body == ""

    def test_body_preserves_blank_lines_and_code_fences(self) -> None:
        body_raw = (
            "\n"
            "# Heading\n"
            "\n"
            "```python\n"
            "def hello():\n"
            "    return 'world'\n"
            "```\n"
            "\n"
            "Trailing paragraph.\n"
        )
        text = f"---\nname: clauditor\ndescription: d\n---\n{body_raw}"
        parsed, body = parse_frontmatter(text)
        assert parsed == {"name": "clauditor", "description": "d"}
        # Byte-exact passthrough of the body — every blank line and
        # the code-fence triple backticks must survive unchanged.
        assert body == body_raw

    def test_comment_in_value_is_preserved(self) -> None:
        # The old validator script did NOT strip ``# ...`` comments
        # out of values (the docstring's outdated claim was corrected
        # in PR #44). The new parser preserves this behavior so any
        # caller that compared on raw string values still matches.
        text = (
            "---\n"
            "name: clauditor\n"
            "description: value # not a comment\n"
            "---\n"
            "body\n"
        )
        parsed, _ = parse_frontmatter(text)
        assert parsed is not None
        assert parsed["description"] == "value # not a comment"

    def test_indented_entry_without_parent_raises(self) -> None:
        text = (
            "---\n"
            "  orphan: value\n"
            "name: clauditor\n"
            "---\n"
        )
        with pytest.raises(ValueError, match="no parent mapping"):
            parse_frontmatter(text)

    def test_empty_input_returns_none(self) -> None:
        parsed, body = parse_frontmatter("")
        assert parsed is None
        assert body == ""

    def test_line_without_colon_raises(self) -> None:
        text = "---\nno colon here\n---\n"
        with pytest.raises(ValueError, match="expected 'key: value'"):
            parse_frontmatter(text)

    def test_empty_key_raises(self) -> None:
        text = "---\n: value\n---\n"
        with pytest.raises(ValueError, match="empty key"):
            parse_frontmatter(text)

    def test_empty_scalar_value_preserved(self) -> None:
        # A top-level key with no value and no indented children
        # (because EOF follows) is treated as an empty string — the
        # consumer decides whether that's an error.
        text = "---\nname: clauditor\nempty:\ndescription: d\n---\n"
        parsed, _ = parse_frontmatter(text)
        assert parsed == {
            "name": "clauditor",
            "empty": "",
            "description": "d",
        }

    def test_leading_blank_lines_tolerated_before_opening_delimiter(
        self,
    ) -> None:
        text = "\n\n---\nname: clauditor\ndescription: d\n---\nbody\n"
        parsed, body = parse_frontmatter(text)
        assert parsed == {"name": "clauditor", "description": "d"}
        assert body == "body\n"

    def test_blank_line_inside_frontmatter_is_skipped(self) -> None:
        # Blank separator lines between entries inside the frontmatter
        # block are tolerated (some authors like to group keys
        # visually).
        text = (
            "---\n"
            "name: clauditor\n"
            "\n"
            "description: d\n"
            "---\n"
            "body\n"
        )
        parsed, body = parse_frontmatter(text)
        assert parsed == {"name": "clauditor", "description": "d"}
        assert body == "body\n"
