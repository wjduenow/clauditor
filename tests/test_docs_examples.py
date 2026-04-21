"""Regression test for assertion JSON examples in human-facing docs.

Guards against the specific failure mode that landed the #67 per-type
semantic-key redesign: a doc file silently keeping the legacy
``{"type": "...", "value": "..."}`` shape after the loader has stopped
accepting ``value`` as a valid assertion key. The redesign renamed the
single overloaded ``value`` field to per-type semantic keys
(``needle`` / ``pattern`` / ``length`` / ``count`` / ``format``) and
switched integer fields from stringly-typed to native JSON ints —
docs that still show the old shape would copy-paste into a broken
eval spec.

The test is deliberately grep-based (string-level) rather than
JSON-parsing. The motivating bug is "wrong key name in a code-fence
example"; a substring scan catches every instance without the
fragility of extracting and parsing every fenced block (which would
need to handle triple-backtick-inside-quoted-string, skipped
```text``` blocks that aren't JSON, etc.). The alternative full-parse
approach is tracked in US-003 of ``plans/super/67-per-type-assertion-keys.md``
as a future tightening if a false positive ever slips through.

Files covered (the human-facing doc triangle per
``.claude/rules/bundled-skill-docs-sync.md``):

* ``README.md``
* every ``docs/*.md`` file
* ``src/clauditor/skills/clauditor/SKILL.md``

Explicitly NOT covered:

* ``plans/**/*.md`` — plan files legitimately cite the old shape
  when discussing the ``#67`` migration and its history. Plans are
  audit history, not examples users copy-paste.
* ``CHANGELOG.md`` — may legitimately cite the old shape when
  describing the ``#67`` rename.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Scope: the human-facing doc triangle. See module docstring for the
# rationale for excluding plans/ and CHANGELOG.md.
README = REPO_ROOT / "README.md"
DOCS_DIR = REPO_ROOT / "docs"
SKILL_MD = REPO_ROOT / "src" / "clauditor" / "skills" / "clauditor" / "SKILL.md"


def _doc_files() -> list[Path]:
    """Return every in-scope doc file under the repository."""
    files = [README, SKILL_MD]
    files.extend(sorted(DOCS_DIR.glob("**/*.md")))
    return [p for p in files if p.is_file()]


def _json_fenced_blocks(text: str) -> list[str]:
    """Extract every ```json ... ``` fenced code block from ``text``.

    Returns the block bodies (fence markers stripped) in file order.
    A fenced block is matched by a line beginning with ```json``` and
    a subsequent line beginning with ``` on its own (allowing trailing
    whitespace). Non-JSON fences (```python```, ```text```, …) are
    skipped so rubric / pytest examples do not trigger false positives.
    """
    # Multiline-dotall: ``.`` matches newline so we can capture block
    # bodies across lines. Non-greedy ``.*?`` stops at the first
    # closing fence.
    pattern = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
    return pattern.findall(text)


class TestAssertionExamplesUsePerTypeKeys:
    """Every ``"value":`` in a JSON-fenced assertion example is a bug.

    Per DEC-001 of ``plans/super/67-per-type-assertion-keys.md``, the
    loader rejects ``value`` as an assertion key. If a doc shows a
    ``{"type": "contains", "value": "..."}`` example the reader's
    copy-paste will fail validation at load time.
    """

    @pytest.mark.parametrize(
        "doc_path",
        _doc_files(),
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_no_value_key_in_assertion_json_block(self, doc_path: Path) -> None:
        """No json-fenced block that mentions an assertion ``"type":`` also
        carries a ``"value":`` key."""
        text = doc_path.read_text(encoding="utf-8")
        offenders: list[tuple[int, str]] = []
        for idx, block in enumerate(_json_fenced_blocks(text)):
            if '"type":' not in block:
                continue
            if '"value":' in block:
                offenders.append((idx, block))
        assert not offenders, (
            f"{doc_path.relative_to(REPO_ROOT)}: found "
            f"{len(offenders)} assertion JSON block(s) with legacy "
            f"'value' key. Migrate to per-type semantic keys per "
            f"#67: needle / pattern / length / count / format.\n"
            f"First offender body:\n{offenders[0][1][:400]}"
        )


class TestAssertionExamplesUseNativeIntPayloads:
    """Integer payload fields (length / count) must be JSON ints.

    Per DEC-002 of ``plans/super/67-per-type-assertion-keys.md``, the
    loader rejects stringly-typed ints (e.g. ``"length": "500"``) with
    a wrong-type ``ValueError``. Docs showing the old stringly-typed
    shape would copy-paste into a broken spec.
    """

    # Regex: a JSON key listed below, followed by a quoted numeric
    # string. Matches e.g. ``"length": "500"`` or ``"count":"3"``.
    _STRINGLY_INT_RE = re.compile(
        r'"(length|count)":\s*"\d+"'
    )

    @pytest.mark.parametrize(
        "doc_path",
        _doc_files(),
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_int_fields_are_native_json_ints(self, doc_path: Path) -> None:
        """No json-fenced block that mentions an assertion ``"type":`` has
        a stringly-typed ``length`` or ``count`` field."""
        text = doc_path.read_text(encoding="utf-8")
        offenders: list[tuple[int, str]] = []
        for idx, block in enumerate(_json_fenced_blocks(text)):
            if '"type":' not in block:
                continue
            matches = self._STRINGLY_INT_RE.findall(block)
            if matches:
                offenders.append((idx, block))
        assert not offenders, (
            f"{doc_path.relative_to(REPO_ROOT)}: found "
            f"{len(offenders)} assertion JSON block(s) with "
            f"stringly-typed int field(s). Per DEC-002 of #67, "
            f"length/count are native JSON ints — use 500, not "
            f'"500".\nFirst offender body:\n{offenders[0][1][:400]}'
        )
