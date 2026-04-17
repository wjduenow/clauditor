"""Minimal YAML-subset parser for SKILL.md frontmatter.

This module isolates the tiny frontmatter parser that used to live in
``scripts/validate_skill_frontmatter.py``. It is deliberately narrow:
it understands only the shape real SKILL.md files use today, not full
YAML.

Supported YAML subset:

- Top-level scalar entries: ``key: value`` and ``key: "quoted value"``.
- Nested one-level mapping: a ``metadata:`` block with indented scalar
  entries.
- Inline lists: ``allowed-tools: Bash(*) Read Grep`` is stored as the
  raw string value (the parser does not split on whitespace).
- Comments after ``#`` are preserved in the value (the historical
  behavior — the old script's docstring briefly claimed otherwise,
  which was corrected in PR #44).

Anything else — anchors, aliases, flow-style collections, block
scalars (``|`` / ``>``), multi-level nesting — raises ``ValueError``.

Public API:

- :func:`parse_frontmatter` — ``(text) -> (dict | None, str)``.
"""

from __future__ import annotations

__all__ = ["parse_frontmatter"]


def _strip_quotes(value: str) -> str:
    """Strip a single pair of matched single/double quotes from ``value``.

    Mirrors the behavior of the old ``_parse_top_level_string`` helper:
    only strips when the first and last character are identical quotes.
    Leaves the value untouched if no quotes wrap it.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _split_key_value(line: str, line_number: int) -> tuple[str, str]:
    """Split ``key: value`` on the first ``:`` separator.

    Raises ``ValueError`` when the line has no ``:`` (unparseable under
    our YAML subset) or when the key is empty.
    """
    if ":" not in line:
        raise ValueError(
            f"frontmatter line {line_number}: expected 'key: value', "
            f"got {line!r}"
        )
    key, _, value = line.partition(":")
    key = key.strip()
    if key == "":
        raise ValueError(
            f"frontmatter line {line_number}: empty key in {line!r}"
        )
    return key, value.strip()


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return ``(parsed_frontmatter_dict, body_text)``.

    If ``text`` has no ``---``-delimited frontmatter block at the top
    (after optional leading whitespace lines), returns ``(None, text)``.

    When a frontmatter block is present, the dict contains:

    - Top-level scalar entries as ``str`` values (with surrounding
      quotes stripped).
    - A ``metadata`` key mapped to a ``dict[str, str]`` when the
      frontmatter declares a ``metadata:`` block with indented scalar
      entries.

    Raises ``ValueError`` on malformed frontmatter:

    - Opening delimiter present, closing delimiter missing.
    - Top-level entry that is not ``key: value`` shape.
    - Indented entry that does not belong to an open nested mapping.
    """
    lines = text.splitlines()

    # Find the opening delimiter. We accept blank lines before it as a
    # courtesy — real SKILL.md files never have leading blanks, but
    # being tolerant keeps the parser cheap and predictable.
    opening_index: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "":
            continue
        if line.strip() == "---":
            opening_index = idx
            break
        # First non-blank line is not a delimiter → no frontmatter.
        return None, text

    if opening_index is None:
        # All-blank input, or empty string → no frontmatter.
        return None, text

    # Find the closing delimiter.
    closing_index: int | None = None
    for idx in range(opening_index + 1, len(lines)):
        if lines[idx].strip() == "---":
            closing_index = idx
            break

    if closing_index is None:
        raise ValueError("missing closing frontmatter delimiter '---'")

    block_lines = lines[opening_index + 1 : closing_index]

    parsed: dict = {}
    # Track the currently-open nested mapping (if any). Only one level
    # of nesting is supported — nested-inside-nested raises ValueError.
    current_nested: dict | None = None

    for offset, raw in enumerate(block_lines):
        line_number = opening_index + 2 + offset  # 1-indexed file line

        # Blank lines inside frontmatter are ignored.
        if raw.strip() == "":
            continue

        indented = raw.startswith((" ", "\t"))

        if indented:
            if current_nested is None:
                raise ValueError(
                    f"frontmatter line {line_number}: indented entry "
                    f"{raw!r} has no parent mapping"
                )
            key, value = _split_key_value(raw.strip(), line_number)
            # Nested value is always a scalar under our subset.
            current_nested[key] = _strip_quotes(value)
            continue

        # Top-level entry — closes any open nested mapping.
        current_nested = None

        key, value = _split_key_value(raw, line_number)

        if value == "":
            # Either an empty scalar or the opener of a nested mapping.
            # Decide by peeking at the next non-blank line: if it's
            # indented, this is a mapping; otherwise it's a genuinely
            # empty scalar.
            next_offset = offset + 1
            next_is_indented = (
                next_offset < len(block_lines)
                and block_lines[next_offset].strip() != ""
                and block_lines[next_offset].startswith((" ", "\t"))
            )
            if next_is_indented:
                current_nested = {}
                parsed[key] = current_nested
                continue
            parsed[key] = ""
            continue

        parsed[key] = _strip_quotes(value)

    # Body text: everything after the closing delimiter line, rejoined
    # with "\n". Preserves blank lines and code fences byte-for-byte
    # (modulo a possible trailing newline difference handled below).
    body_lines = lines[closing_index + 1 :]
    body = "\n".join(body_lines)
    # If the original text ended with a newline, preserve it; splitlines
    # drops the trailing newline.
    if body_lines and text.endswith(("\n", "\r")):
        body += "\n"
    elif not body_lines:
        body = ""

    return parsed, body
