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

To avoid flagging non-clauditor JSON (e.g. Discord-API option schemas
where ``"type": 3`` is an integer-tagged STRING option type and
``"value"`` is the choice value field), the predicate also requires a
known clauditor assertion-type literal (``"contains"``, ``"regex"``,
``"min_length"``, etc.) to appear in the block. This makes the
keyword detector semantic: only blocks that actually look like a
clauditor assertion get flagged.

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
* ``docs/temp/**/*.md`` — gitignored research drafts. Not part of
  the human-facing doc triangle, and routinely contain pasted-in
  external API examples (Discord, OpenAPI, etc.) whose ``"type":``
  / ``"value":`` keys collide with this scan as false positives.
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


_TEMP_DIR = DOCS_DIR / "temp"


def _doc_files() -> list[Path]:
    """Return every in-scope doc file under the repository.

    ``docs/temp/`` is gitignored draft material (research notes,
    pasted-in external API examples) and is not part of the
    human-facing doc triangle — exclude it so a foreign ``"type":`` /
    ``"value":`` snippet does not register as a false positive.
    """
    files = [README, SKILL_MD]
    files.extend(
        p
        for p in sorted(DOCS_DIR.glob("**/*.md"))
        if _TEMP_DIR not in p.parents
    )
    return [p for p in files if p.is_file()]


def _json_fenced_blocks(text: str) -> list[str]:
    """Extract every ```json ... ``` fenced code block from ``text``.

    Returns the block bodies (fence markers stripped) in file order.
    A fenced block is matched by a line beginning with ```json`` and
    a subsequent line whose entire content is ``` (allowing trailing
    whitespace). Non-JSON fences (```python```, ```text```, …) are
    skipped so rubric / pytest examples do not trigger false positives.
    """
    # ``^...$`` anchors (MULTILINE) keep the match scoped to
    # line-start / line-end so inline backtick sequences inside prose
    # cannot accidentally open or close a fence. ``.`` with DOTALL
    # lets the body span lines; non-greedy ``.*?`` stops at the first
    # bare closing fence.
    pattern = re.compile(
        r"^```json\s*\n(.*?)^```\s*$",
        re.DOTALL | re.MULTILINE,
    )
    return pattern.findall(text)


# Clauditor assertion ``type`` literals — the discriminator values
# accepted by ``ASSERTION_TYPE_REQUIRED_KEYS`` in
# ``src/clauditor/schemas.py``. Used to disambiguate clauditor
# assertion examples from foreign JSON that happens to share a
# ``"type":`` / ``"value":`` shape (e.g. Discord-API option schemas
# where ``"type": 3`` is an integer-tagged STRING option type, not a
# clauditor assertion). When this list grows in schemas.py, mirror it
# here.
_CLAUDITOR_ASSERTION_TYPE_LITERALS: tuple[str, ...] = (
    '"contains"',
    '"not_contains"',
    '"regex"',
    '"min_length"',
    '"max_length"',
    '"min_count"',
    '"has_urls"',
    '"has_format"',
)


def _looks_like_clauditor_assertion(block: str) -> bool:
    """Return True if ``block`` mentions a clauditor assertion-type literal.

    Tightens the legacy ``"value"`` detector so it only fires on
    blocks that actually look like a clauditor assertion. A
    Discord-API choice block (``{"type": 3, "value": "x"}``) has
    integer ``"type":`` — none of the clauditor literal strings are
    present — and is correctly skipped.
    """
    return any(
        literal in block for literal in _CLAUDITOR_ASSERTION_TYPE_LITERALS
    )


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
            # Tighten: only flag blocks that look like clauditor
            # assertions. Foreign JSON whose ``"type":`` value is
            # something else (Discord option enums, OpenAPI schema
            # types, etc.) is not a copy-paste hazard for clauditor.
            if not _looks_like_clauditor_assertion(block):
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


class TestLintCommandMentionedInDocs:
    """Prose-presence regression for ``clauditor lint`` (#71, DEC-002).

    Per ``.claude/rules/bundled-skill-docs-sync.md``, load-bearing
    command names added to the CLI must appear in the human-facing
    doc triangle so a future prose cleanup cannot silently drop the
    reference. The assertions below are simple substring checks — NOT
    structural (header placement, table position, line count) — so
    they stay stable across ordinary prose rewrites while still
    catching an accidental drop of the command from either surface.

    Two surfaces are pinned:

    * ``docs/cli-reference.md`` — the canonical reference page must
      list the command (both in the quick-reference block and in a
      dedicated ``## lint`` section).
    * ``README.md`` — the one-line CLI Reference list must include
      ``clauditor lint <skill.md>``.
    """

    def test_cli_reference_mentions_lint(self) -> None:
        """``docs/cli-reference.md`` mentions ``clauditor lint``."""
        text = (REPO_ROOT / "docs" / "cli-reference.md").read_text(
            encoding="utf-8"
        )
        assert "clauditor lint" in text, (
            "docs/cli-reference.md must mention 'clauditor lint' — "
            "the command's canonical reference page dropped the "
            "string. Restore the ## lint section and the "
            "quick-reference row."
        )

    def test_readme_mentions_lint(self) -> None:
        """``README.md`` mentions ``clauditor lint``."""
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "clauditor lint" in text, (
            "README.md must mention 'clauditor lint' in the CLI "
            "Reference subcommand list. Restore the bullet/row under "
            "the '## CLI Reference' section."
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
            # Same tightening as ``test_no_value_key_in_assertion_json_block``:
            # only blocks that look like a clauditor assertion are in
            # scope. Foreign JSON examples whose ``"type":`` value is
            # not a clauditor assertion-type literal are skipped.
            if not _looks_like_clauditor_assertion(block):
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


class TestLooksLikeClauditorAssertion:
    """The ``_looks_like_clauditor_assertion`` predicate's contract.

    Direct unit tests on the helper, not via the file-walker. Ensures
    the tightening introduced for clauditor-07p does not (a) drop
    legitimate clauditor examples — those WITH ``"value"`` must still
    be flagged via the original detector — or (b) start flagging
    foreign JSON whose ``"type":`` value is not a clauditor
    assertion-type literal (Discord-API option schemas, OpenAPI
    type-tagged unions, etc.).
    """

    def test_discord_api_choice_block_is_not_flagged(self) -> None:
        """A Discord-API ``ApplicationCommandOptionChoice`` block is not a
        clauditor assertion.

        Reproduces the failure mode that motivated clauditor-07p:
        ``type: 3`` is Discord's STRING option type (an integer-tagged
        enum, not a clauditor assertion literal) and ``value`` is the
        choice's value field. The predicate must reject this.
        """
        block = (
            '{ "name": "animal", "type": 3, "required": true,\n'
            '  "choices": [ {"name": "Dog", "value": "animal_dog"} ] }'
        )
        assert _looks_like_clauditor_assertion(block) is False

    def test_openapi_type_tag_is_not_flagged(self) -> None:
        """A JSON schema fragment with ``"type": "object"`` is not a
        clauditor assertion."""
        block = '{"type": "object", "value": "foo"}'
        assert _looks_like_clauditor_assertion(block) is False

    def test_legacy_clauditor_contains_block_is_flagged(self) -> None:
        """A real legacy clauditor ``contains`` block WITH ``"value"`` is
        still flagged so the original detector keeps catching it.

        Without this assertion the tightening could over-rotate and
        let legitimate copy-paste hazards slip through.
        """
        block = '{"type": "contains", "value": "Deltas"}'
        assert _looks_like_clauditor_assertion(block) is True

    def test_legacy_clauditor_min_length_block_is_flagged(self) -> None:
        """A legacy ``min_length`` block is flagged."""
        block = '{"type": "min_length", "value": "500"}'
        assert _looks_like_clauditor_assertion(block) is True

    def test_modern_clauditor_regex_block_is_flagged(self) -> None:
        """A modern post-#67 ``regex`` block is also flagged.

        The predicate keys on the type literal, not on the presence
        of ``value`` — so it correctly identifies any clauditor
        assertion block in scope, whether legacy or modern. The
        downstream ``"value":`` check is what gates flagging.
        """
        block = '{"type": "regex", "pattern": "^[A-Z]"}'
        assert _looks_like_clauditor_assertion(block) is True


class TestNoValueKeyDetectorEndToEnd:
    """End-to-end coverage for the tightened detector.

    Drives the same logic the file-walker uses on synthesized blocks,
    verifying that (a) the foreign-JSON false positive is not flagged
    and (b) the legitimate legacy ``"value"`` shape IS flagged.
    """

    def test_discord_block_does_not_register_as_legacy_assertion(self) -> None:
        """The exact failing block from clauditor-07p does not register.

        Walks the same ``"type":`` / ``"value":`` predicate chain the
        file-walker applies, with the new ``_looks_like_clauditor_assertion``
        gate inserted. The Discord choice block has both keys but
        must not be flagged.
        """
        block = (
            '{ "name": "animal", "type": 3, "required": true,\n'
            '  "choices": [ {"name": "Dog", "value": "animal_dog"} ] }'
        )
        assert '"type":' in block
        assert '"value":' in block
        # Tightened predicate skips this block.
        assert _looks_like_clauditor_assertion(block) is False

    def test_legacy_clauditor_block_does_register(self) -> None:
        """A real clauditor assertion with the legacy ``value`` key is still
        flagged, so the original migration guard keeps working."""
        block = '{"id": "a1", "type": "contains", "value": "Deltas"}'
        assert '"type":' in block
        assert '"value":' in block
        # Tightened predicate accepts this block (a clauditor
        # assertion-type literal is present).
        assert _looks_like_clauditor_assertion(block) is True
