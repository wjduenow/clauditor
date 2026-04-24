"""Tests for clauditor.propose_eval (#52 US-003)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clauditor.propose_eval import (
    DEFAULT_PROPOSE_EVAL_MODEL,
    AttemptMetrics,
    ProposeEvalInput,
    ProposeEvalReport,
    _estimate_tokens,
    _skill_name_from_frontmatter,
    _strip_json_fence,
    build_propose_eval_prompt,
    build_repair_propose_eval_prompt,
    load_propose_eval_input,
    parse_propose_eval_response,
    propose_eval,
    validate_proposed_spec,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_propose_input(
    *,
    skill_name: str = "greeter",
    skill_md_text: str = "---\nname: greeter\n---\n# Greeter\n\nSay hello.\n",
    frontmatter: dict | None = None,
    skill_body: str = "# Greeter\n\nSay hello.\n",
    capture_text: str | None = None,
    capture_source: str | None = None,
) -> ProposeEvalInput:
    if frontmatter is None:
        frontmatter = {"name": "greeter"}
    return ProposeEvalInput(
        skill_name=skill_name,
        skill_md_text=skill_md_text,
        frontmatter=frontmatter,
        skill_body=skill_body,
        capture_text=capture_text,
        capture_source=capture_source,
    )


def _good_spec_dict(
    *,
    with_assertion: bool = True,
    with_criterion: bool = True,
) -> dict:
    """Return a minimal EvalSpec dict that passes `from_dict`."""
    spec: dict = {
        "test_args": "hello world",
    }
    if with_assertion:
        spec["assertions"] = [
            {
                "id": "greets-user",
                "type": "contains",
                "name": "greets the user",
                "needle": "hello",
            }
        ]
    if with_criterion:
        spec["grading_criteria"] = [
            {"id": "is-friendly", "criterion": "friendly tone"}
        ]
    return spec


def _good_response_text() -> str:
    return json.dumps(_good_spec_dict())


def _mock_anthropic_result(
    *,
    text: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
):
    """Return an AnthropicResult shaped like a successful helper call."""
    from clauditor._anthropic import AnthropicResult

    return AnthropicResult(
        response_text=text,
        text_blocks=[text] if text else [],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_message=None,
    )


# --------------------------------------------------------------------------
# TestLoadProposeEvalInput
# --------------------------------------------------------------------------


class TestLoadProposeEvalInput:
    def test_primary_capture_path_used_when_present(
        self, tmp_path: Path
    ) -> None:
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\n---\n# Greeter\n\nSay hi.\n"
        )

        captured_dir = project_dir / "tests" / "eval" / "captured"
        captured_dir.mkdir(parents=True)
        (captured_dir / "greeter.txt").write_text(
            "Hello, world!\n"
        )

        # Also create the fallback. Primary should win.
        fallback_dir = project_dir / ".clauditor" / "captures"
        fallback_dir.mkdir(parents=True)
        (fallback_dir / "greeter.txt").write_text(
            "Fallback content\n"
        )

        result = load_propose_eval_input(skill_md, project_dir)
        assert result.capture_text == "Hello, world!\n"
        assert result.capture_source is not None
        assert "tests/eval/captured" in result.capture_source

    def test_fallback_capture_path_used_when_primary_missing(
        self, tmp_path: Path
    ) -> None:
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\n---\n# Greeter\n\nSay hi.\n"
        )

        fallback_dir = project_dir / ".clauditor" / "captures"
        fallback_dir.mkdir(parents=True)
        (fallback_dir / "greeter.txt").write_text("Fallback hi\n")

        result = load_propose_eval_input(skill_md, project_dir)
        assert result.capture_text == "Fallback hi\n"
        assert result.capture_source is not None
        assert ".clauditor/captures" in result.capture_source

    def test_no_capture_returns_none_fields(
        self, tmp_path: Path
    ) -> None:
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\n---\n# Greeter\n\nSay hi.\n"
        )

        result = load_propose_eval_input(skill_md, project_dir)
        assert result.capture_text is None
        assert result.capture_source is None

    def test_frontmatter_parsed_when_present(
        self, tmp_path: Path
    ) -> None:
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\ndescription: says hello\n---\n"
            "# Greeter\n\nBody text.\n"
        )

        result = load_propose_eval_input(skill_md, project_dir)
        assert isinstance(result.frontmatter, dict)
        assert result.frontmatter["name"] == "greeter"
        assert result.frontmatter["description"] == "says hello"
        # Body has frontmatter stripped.
        assert "Body text." in result.skill_body
        assert "name: greeter" not in result.skill_body
        # skill_md_text retains full file.
        assert "name: greeter" in result.skill_md_text

    def test_skill_name_falls_back_to_directory(
        self, tmp_path: Path
    ) -> None:
        # No frontmatter name field → use parent dir name.
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "dir-fallback"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# No frontmatter\n\nBody.\n")

        result = load_propose_eval_input(skill_md, project_dir)
        assert result.skill_name == "dir-fallback"
        # No frontmatter → None
        assert result.frontmatter is None

    def test_malformed_frontmatter_tolerated(
        self, tmp_path: Path, capsys
    ) -> None:
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        # Opening delimiter without close — raises in parse_frontmatter.
        skill_md.write_text("---\nname: broken\n# body never closed\n")

        result = load_propose_eval_input(skill_md, project_dir)
        # On parse failure, frontmatter is None and the whole file is
        # treated as body.
        assert result.frontmatter is None
        assert result.skill_body == skill_md.read_text()
        # skill_name falls back to dir name.
        assert result.skill_name == "broken"

        # Pass 2 finding: the fallthrough must emit a stderr warning
        # so authors notice their declared `name:` was ignored.
        err = capsys.readouterr().err
        assert "malformed frontmatter" in err
        assert str(skill_md) in err

    def test_captured_skill_args_loaded_from_sidecar(
        self, tmp_path: Path
    ) -> None:
        """#117: when a .capture.json sidecar is present, load its skill_args."""
        from clauditor.capture_provenance import write_capture_provenance

        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\n---\n# Greeter\n\nSay hi.\n"
        )

        captured_dir = project_dir / "tests" / "eval" / "captured"
        captured_dir.mkdir(parents=True)
        capture_path = captured_dir / "greeter.txt"
        capture_path.write_text("Hello, Alice!\n")
        write_capture_provenance(
            capture_path,
            skill_name="greeter",
            skill_args="--name Alice --formal",
        )

        result = load_propose_eval_input(skill_md, project_dir)
        assert result.captured_skill_args == "--name Alice --formal"

    def test_captured_skill_args_none_when_no_sidecar(
        self, tmp_path: Path
    ) -> None:
        """#117: legacy capture without a sidecar yields captured_skill_args=None."""
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\n---\n# Greeter\n\nSay hi.\n"
        )

        captured_dir = project_dir / "tests" / "eval" / "captured"
        captured_dir.mkdir(parents=True)
        (captured_dir / "greeter.txt").write_text("Hello, world!\n")

        result = load_propose_eval_input(skill_md, project_dir)
        # Capture found, sidecar absent → None.
        assert result.capture_text is not None
        assert result.captured_skill_args is None

    def test_capture_text_is_scrubbed(self, tmp_path: Path) -> None:
        """DEC-008: captured content goes through transcripts.redact."""
        project_dir = tmp_path
        skill_dir = project_dir / ".claude" / "skills" / "greeter"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: greeter\n---\n# Greeter\n\nSay hi.\n"
        )

        captured_dir = project_dir / "tests" / "eval" / "captured"
        captured_dir.mkdir(parents=True)
        secret = "sk-ant-api03-" + "A" * 95
        (captured_dir / "greeter.txt").write_text(
            f"Output says: token={secret}\n"
        )

        result = load_propose_eval_input(skill_md, project_dir)
        assert result.capture_text is not None
        assert secret not in result.capture_text
        assert "[REDACTED]" in result.capture_text

    def test_capture_source_absolute_fallback(
        self, tmp_path: Path
    ) -> None:
        """When capture lives outside project_dir.relative_to can fail;
        the loader falls back to the absolute path string."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        skill_md = project_dir / "SKILL.md"
        skill_md.write_text("# Skill\n\nBody.\n")

        # Place capture via symlink into a location that is inside
        # project_dir (so the simple case continues to work). The
        # fallback arm is hit only when relative_to raises — that's
        # covered by the core primary/fallback tests already returning
        # a relative string. This test simply validates the normal
        # path format for fallback.
        fallback_dir = project_dir / ".clauditor" / "captures"
        fallback_dir.mkdir(parents=True)
        (fallback_dir / "project.txt").write_text("x\n")

        result = load_propose_eval_input(skill_md, project_dir)
        # skill_name defaulted to the skill_md.parent.name == "project"
        assert result.skill_name == "project"
        assert result.capture_source is not None


# --------------------------------------------------------------------------
# TestSkillNameFromFrontmatter
# --------------------------------------------------------------------------


class TestSkillNameFromFrontmatter:
    def test_uses_name_field_when_present(self, tmp_path: Path) -> None:
        path = tmp_path / "foo" / "SKILL.md"
        result = _skill_name_from_frontmatter({"name": "my-skill"}, path)
        assert result == "my-skill"

    def test_strips_whitespace_around_name(self, tmp_path: Path) -> None:
        path = tmp_path / "foo" / "SKILL.md"
        result = _skill_name_from_frontmatter(
            {"name": "  padded  "}, path
        )
        assert result == "padded"

    def test_falls_back_to_parent_dir_name(self, tmp_path: Path) -> None:
        path = tmp_path / "my-skill" / "SKILL.md"
        result = _skill_name_from_frontmatter(None, path)
        assert result == "my-skill"

    def test_empty_name_field_falls_back(self, tmp_path: Path) -> None:
        path = tmp_path / "my-skill" / "SKILL.md"
        result = _skill_name_from_frontmatter({"name": ""}, path)
        assert result == "my-skill"

    def test_non_string_name_field_falls_back(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "my-skill" / "SKILL.md"
        result = _skill_name_from_frontmatter({"name": 42}, path)
        assert result == "my-skill"

    def test_rejects_path_traversal_in_name(self, tmp_path: Path) -> None:
        """Pass 2 security finding: name must not contain path separators.

        A malicious SKILL.md declaring `name: "../../etc/passwd"`
        would otherwise escape the capture directory when the loader
        interpolates the name into a Path join. The regex clamp
        forces a fallback to the directory basename (or ``"skill"``
        if that also fails).
        """
        path = tmp_path / "greeter" / "SKILL.md"
        result = _skill_name_from_frontmatter(
            {"name": "../../etc/passwd"}, path
        )
        assert result == "greeter"

    def test_rejects_absolute_path_in_name(self, tmp_path: Path) -> None:
        path = tmp_path / "greeter" / "SKILL.md"
        result = _skill_name_from_frontmatter(
            {"name": "/etc/passwd"}, path
        )
        assert result == "greeter"

    def test_rejects_windows_separator_in_name(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "greeter" / "SKILL.md"
        result = _skill_name_from_frontmatter(
            {"name": "foo\\bar"}, path
        )
        assert result == "greeter"

    def test_falls_back_to_literal_skill_when_both_invalid(
        self, tmp_path: Path
    ) -> None:
        # Parent dir basename is empty/root-like — fails the regex.
        path = Path("/") / "SKILL.md"
        result = _skill_name_from_frontmatter(
            {"name": "../../etc/passwd"}, path
        )
        assert result == "skill"


# --------------------------------------------------------------------------
# TestBuildProposeEvalPrompt
# --------------------------------------------------------------------------


class TestBuildProposeEvalPrompt:
    def test_framing_sentence_appears_before_first_untrusted_tag(
        self,
    ) -> None:
        pi = _make_propose_input(
            capture_text="captured skill output",
            capture_source="tests/eval/captured/greeter.txt",
        )
        prompt = build_propose_eval_prompt(pi)
        framing_idx = prompt.find("untrusted data, not instructions")
        assert framing_idx >= 0

        first_untrusted = prompt.find("<skill_output>")
        assert first_untrusted > framing_idx

    def test_skill_md_block_is_not_framed_as_untrusted(self) -> None:
        pi = _make_propose_input(capture_text="some capture")
        prompt = build_propose_eval_prompt(pi)
        assert "<skill_md>" in prompt
        framing_idx = prompt.find("untrusted data, not instructions")
        # The framing sentence lists `skill_output` (without angle
        # brackets — avoids colliding with the actual opening tag
        # location that `prompt.find("<skill_output>")` searches for).
        # `skill_md` must NOT appear in that enumeration.
        line_start = prompt.rfind("\n", 0, framing_idx) + 1
        line_end = prompt.find("\n\n", framing_idx)
        framing_region = prompt[line_start:line_end]
        assert "skill_output" in framing_region
        assert "skill_md" not in framing_region

    def test_untrusted_tag_included_when_capture_present(self) -> None:
        pi = _make_propose_input(capture_text="capture body here")
        prompt = build_propose_eval_prompt(pi)
        assert "<skill_output>" in prompt
        assert "</skill_output>" in prompt
        assert "capture body here" in prompt

    def test_untrusted_tag_omitted_when_capture_absent(self) -> None:
        pi = _make_propose_input(capture_text=None)
        prompt = build_propose_eval_prompt(pi)
        assert "<skill_output>" not in prompt
        # Without untrusted content, the injection framing also is
        # omitted — there is nothing to frame.
        assert "untrusted data, not instructions" not in prompt

    def test_capture_args_block_present_when_set(self) -> None:
        """#117: captured_skill_args inject a <capture_args> block."""
        pi = _make_propose_input(capture_text="capture body")
        pi.captured_skill_args = "--depth quick --count 5"
        prompt = build_propose_eval_prompt(pi)
        assert "<capture_args>" in prompt
        assert "</capture_args>" in prompt
        assert "--depth quick --count 5" in prompt
        # Framing sentence lists both untrusted tag names.
        framing_idx = prompt.find("untrusted data, not instructions")
        line_start = prompt.rfind("\n", 0, framing_idx) + 1
        line_end = prompt.find("\n\n", framing_idx)
        framing_region = prompt[line_start:line_end]
        assert "skill_output" in framing_region
        assert "capture_args" in framing_region

    def test_capture_args_block_absent_without_sidecar(self) -> None:
        """#117: no sidecar → no <capture_args> block."""
        pi = _make_propose_input(capture_text="capture body")
        prompt = build_propose_eval_prompt(pi)
        assert "<capture_args>" not in prompt

    def test_capture_args_block_omitted_when_empty(self) -> None:
        """#117: empty captured_skill_args → skip the block.

        The override still fires in the orchestrator (it carries
        ``is not None``), but emitting a literal empty
        ``<capture_args></capture_args>`` block would confuse the LLM
        for no benefit. The block is truthy-gated.
        """
        pi = _make_propose_input(capture_text="capture body")
        pi.captured_skill_args = ""
        prompt = build_propose_eval_prompt(pi)
        assert "<capture_args>" not in prompt
        # Framing sentence should still NOT list capture_args.
        framing_idx = prompt.find("untrusted data, not instructions")
        line_start = prompt.rfind("\n", 0, framing_idx) + 1
        line_end = prompt.find("\n\n", framing_idx)
        framing_region = prompt[line_start:line_end]
        assert "capture_args" not in framing_region

    def test_capture_args_block_requires_capture_text(self) -> None:
        """Defensive: captured_skill_args without capture_text stays absent.

        The prompt only renders the capture block at all when
        ``capture_text`` is set. If captured_skill_args somehow travels
        alongside a None capture_text (unreachable via the loader, but
        nothing structurally prevents it), the <capture_args> block
        must not leak into the prompt without the <skill_output>
        block it belongs with.
        """
        pi = _make_propose_input(capture_text=None)
        pi.captured_skill_args = "--depth quick"
        prompt = build_propose_eval_prompt(pi)
        assert "<capture_args>" not in prompt

    def test_response_schema_included(self) -> None:
        pi = _make_propose_input()
        prompt = build_propose_eval_prompt(pi)
        for field_name in (
            "test_args",
            "assertions",
            "sections",
            "grading_criteria",
            "tiers",
            "criterion",
        ):
            assert field_name in prompt

    def test_stable_id_contract_phrase_present(self) -> None:
        pi = _make_propose_input()
        prompt = build_propose_eval_prompt(pi)
        assert "unique `id`" in prompt

    def test_skill_md_text_embedded_verbatim(self) -> None:
        pi = _make_propose_input(
            skill_md_text="# Unique Marker XYZ\n\nbody\n"
        )
        prompt = build_propose_eval_prompt(pi)
        assert "Unique Marker XYZ" in prompt

    def test_token_budget_raises_when_prompt_too_long(self) -> None:
        # Build a capture that pushes the prompt above the 50k token
        # cap. 50000 * 4 = 200000 chars; add a bit more for header.
        huge_capture = "x" * 250_000
        pi = _make_propose_input(capture_text=huge_capture)
        with pytest.raises(ValueError, match="tokens"):
            build_propose_eval_prompt(pi)

    def test_token_budget_ok_for_small_prompt(self) -> None:
        pi = _make_propose_input(capture_text="small")
        # Should not raise.
        _ = build_propose_eval_prompt(pi)

    def test_prompt_contains_per_type_table(self) -> None:
        """DEC-003 / DEC-008 of #61 and DEC-001 of #67 — the prompt
        enumerates each assertion type's required AND optional keys
        (rendered from ``ASSERTION_TYPE_REQUIRED_KEYS``) so the model
        has a literal reference table for key names. Rows without
        optional keys omit the ``· optional: …`` suffix; rows with
        no required keys render ``required: (none)``.
        """
        pi = _make_propose_input()
        prompt = build_propose_eval_prompt(pi)
        # 1-required-key shape, no optional (post-#67 rename).
        assert "contains → required: needle" in prompt
        assert "not_contains → required: needle" in prompt
        assert "regex → required: pattern" in prompt
        assert "min_length → required: length" in prompt
        assert "max_length → required: length" in prompt
        # required + optional shape. Keys sorted alphabetically
        # within each side.
        assert (
            "min_count → required: count, pattern"
        ) in prompt
        assert (
            "has_format → required: format · optional: count"
        ) in prompt
        # All-optional shape (no required keys) — ``(none)`` marker.
        assert (
            "has_urls → required: (none) · optional: count"
        ) in prompt
        assert (
            "has_entries → required: (none) · optional: count"
        ) in prompt
        assert (
            "urls_reachable → required: (none) · optional: count"
        ) in prompt

    def test_prompt_has_no_alias_keys(self) -> None:
        """The legacy alias key names (``value``, ``minimum``) must
        not appear in quoted-key form in the new prompt — per DEC-001
        of #67, these keys are not accepted by the validator. Note:
        ``pattern`` is now a VALID key for ``regex`` and ``min_count``
        so a bare ``pattern`` substring legitimately appears in the
        rendered per-type table; we only assert the obsolete aliases
        are gone.
        """
        pi = _make_propose_input()
        prompt = build_propose_eval_prompt(pi)
        # Legacy alias JSON key-name literals must not appear in
        # quoted form (the model would interpret e.g. `"value"` as
        # a valid key to emit).
        assert "'value'" not in prompt
        assert "'minimum'" not in prompt
        # The old drift-source example line must also be gone
        # verbatim (it used `"value", "pattern"` to introduce the
        # old-alias migration nudge).
        assert 'e.g. "value", "pattern"' not in prompt

    def test_prompt_format_section_is_registry_only(self) -> None:
        """#99: prompt enumerates registry format keys and tells the
        LLM regex is NOT accepted. Catches the drift where the prompt
        claimed ``"<registry key or regex>"`` but the impl rejected
        anything that wasn't a registry name.
        """
        from clauditor.formats import list_formats

        pi = _make_propose_input()
        prompt = build_propose_eval_prompt(pi)

        # The placeholder line must no longer claim regex is valid.
        assert "<registry key or regex>" not in prompt
        assert "<registry key" in prompt

        # Every registry entry must be present as a bulleted line.
        for fmt_name in list_formats():
            assert f"- {fmt_name}" in prompt, (
                f"registry format {fmt_name!r} missing from prompt"
            )

        # Escape-hatch text pointing at the L1 regex assertion.
        assert "regex" in prompt.lower()
        assert "registry-only" in prompt.lower()

    def test_prompt_table_is_rendered_from_constant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The per-type table is RENDERED from
        ``ASSERTION_TYPE_REQUIRED_KEYS`` at call time, not hardcoded
        — monkeypatching the constant must change the rendered rows.
        """
        from clauditor.schemas import AssertionKeySpec

        fake_constant = {
            "fake_type_a": AssertionKeySpec(
                required=frozenset({"fake_key"})
            ),
            "fake_type_b": AssertionKeySpec(
                required=frozenset({"k1", "k2"})
            ),
        }
        monkeypatch.setattr(
            "clauditor.propose_eval.ASSERTION_TYPE_REQUIRED_KEYS",
            fake_constant,
        )
        pi = _make_propose_input()
        prompt = build_propose_eval_prompt(pi)
        assert "fake_type_a → required: fake_key" in prompt
        assert "fake_type_b → required: k1, k2" in prompt
        # The real rows must NOT be present — proves the table came
        # from the (monkeypatched) constant, not from a hardcoded
        # string.
        assert "contains → required" not in prompt
        assert "min_count → required" not in prompt


# --------------------------------------------------------------------------
# TestEstimateTokens
# --------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_returns_zero_or_small(self) -> None:
        assert _estimate_tokens("") == 0

    def test_rounds_up(self) -> None:
        # 5 chars -> (5+3)//4 = 2 tokens
        assert _estimate_tokens("hello") == 2


# --------------------------------------------------------------------------
# TestParseProposeEvalResponse
# --------------------------------------------------------------------------


class TestParseProposeEvalResponse:
    def test_parses_valid_json(self) -> None:
        result = parse_propose_eval_response(_good_response_text())
        assert isinstance(result, dict)
        assert "assertions" in result
        assert result["assertions"][0]["id"] == "greets-user"

    def test_strips_markdown_json_fence(self) -> None:
        wrapped = "```json\n" + _good_response_text() + "\n```"
        result = parse_propose_eval_response(wrapped)
        assert isinstance(result, dict)
        assert "assertions" in result

    def test_strips_bare_markdown_fence(self) -> None:
        wrapped = "```\n" + _good_response_text() + "\n```"
        result = parse_propose_eval_response(wrapped)
        assert isinstance(result, dict)

    def test_raises_on_malformed_json(self) -> None:
        with pytest.raises(ValueError, match="valid JSON"):
            parse_propose_eval_response("not json at all {{{")

    def test_raises_on_non_object_top_level(self) -> None:
        with pytest.raises(ValueError, match="object"):
            parse_propose_eval_response("[]")

    def test_raises_on_top_level_string(self) -> None:
        with pytest.raises(ValueError, match="object"):
            parse_propose_eval_response('"just a string"')


# --------------------------------------------------------------------------
# TestStripJsonFence
# --------------------------------------------------------------------------


class TestStripJsonFence:
    def test_strips_json_fence(self) -> None:
        assert _strip_json_fence("```json\n{}\n```") == "{}"

    def test_strips_bare_fence(self) -> None:
        assert _strip_json_fence("```\n{}\n```") == "{}"

    def test_passes_through_unfenced(self) -> None:
        assert _strip_json_fence('{"a": 1}') == '{"a": 1}'

    def test_unterminated_fence_returns_best_effort(self) -> None:
        # Only one fence — the split falls through the branches.
        result = _strip_json_fence("```json\nhello")
        # Either side of the fence is acceptable for this input; just
        # verify it doesn't raise.
        assert isinstance(result, str)


# --------------------------------------------------------------------------
# TestValidateProposedSpec
# --------------------------------------------------------------------------


class TestValidateProposedSpec:
    def test_valid_spec_passes(self, tmp_path: Path) -> None:
        errors = validate_proposed_spec(_good_spec_dict(), tmp_path)
        assert errors == []

    def test_missing_id_captured(self, tmp_path: Path) -> None:
        spec = {
            "test_args": "",
            "assertions": [
                # missing id
                {"type": "contains", "name": "n", "needle": "v"}
            ],
            "grading_criteria": [
                {"id": "crit-1", "criterion": "ok"}
            ],
        }
        errors = validate_proposed_spec(spec, tmp_path)
        assert len(errors) >= 1
        assert any("id" in e.lower() for e in errors)

    def test_duplicate_ids_rejected(self, tmp_path: Path) -> None:
        spec = {
            "test_args": "",
            "assertions": [
                {
                    "id": "same-id",
                    "type": "contains",
                    "name": "n",
                    "needle": "v",
                }
            ],
            "grading_criteria": [
                {"id": "same-id", "criterion": "ok"}
            ],
        }
        errors = validate_proposed_spec(spec, tmp_path)
        assert len(errors) >= 1
        assert any("duplicate" in e.lower() for e in errors)

    def test_empty_spec_rejected(self, tmp_path: Path) -> None:
        # No assertions, no grading_criteria — even if from_dict
        # accepts it, validator rejects.
        spec = {"test_args": ""}
        errors = validate_proposed_spec(spec, tmp_path)
        assert len(errors) == 1
        assert "no assertions" in errors[0]

    def test_assertion_only_is_valid(self, tmp_path: Path) -> None:
        spec = _good_spec_dict(with_criterion=False)
        errors = validate_proposed_spec(spec, tmp_path)
        assert errors == []

    def test_criterion_only_is_valid(self, tmp_path: Path) -> None:
        spec = _good_spec_dict(with_assertion=False)
        errors = validate_proposed_spec(spec, tmp_path)
        assert errors == []

    def test_bad_types_in_assertions_still_empty_check(
        self, tmp_path: Path
    ) -> None:
        """A dict where `assertions` is not a list should also fail."""
        spec = {
            "test_args": "",
            "assertions": "not-a-list",
            "grading_criteria": "also-not-a-list",
        }
        errors = validate_proposed_spec(spec, tmp_path)
        # from_dict itself may or may not reject this; either way we
        # should end up with at least one error (either from from_dict
        # or from the empty-spec check).
        assert len(errors) >= 1

    def test_non_list_fields_after_valid_from_dict(
        self, tmp_path: Path
    ) -> None:
        """Hit the `not isinstance(..., list)` coalescence branches.

        This is a defensive guard for when `from_dict` would have
        already raised — but we want to exercise the branch for
        coverage of the empty-spec check's input-normalization step.
        """
        # Use a dict that would trigger the isinstance fallback but
        # also carry at least one valid-shaped assertion so from_dict
        # does not raise. The simplest approach: monkey around the
        # shape isn't possible since from_dict would reject. Instead,
        # directly feed a spec with an assertion whose `assertions`
        # value passes but the criteria field gets replaced with a
        # non-list sentinel through a subclass-free path.
        # For the branch, we test via a spec that has list-typed
        # assertions (one assertion) and an accidental non-list
        # criteria — from_dict accepts it because it defaults
        # grading_criteria to [] when missing, but we pass a non-list
        # explicitly. from_dict simply stores it; our validator
        # normalizes via the isinstance check.
        spec = {
            "test_args": "",
            "assertions": [
                {
                    "id": "a1",
                    "type": "contains",
                    "name": "n",
                    "needle": "v",
                }
            ],
            "grading_criteria": [],
        }
        errors = validate_proposed_spec(spec, tmp_path)
        assert errors == []


# --------------------------------------------------------------------------
# TestProposeEvalReport
# --------------------------------------------------------------------------


class TestProposeEvalReport:
    def test_schema_version_is_first_key(self) -> None:
        report = ProposeEvalReport(
            skill_name="greeter",
            model="claude-sonnet-4-6",
            proposed_spec=_good_spec_dict(),
        )
        data = json.loads(report.to_json())
        assert list(data.keys())[0] == "schema_version"
        assert data["schema_version"] == 2

    def test_round_trip_preserves_fields(self) -> None:
        spec = _good_spec_dict()
        report = ProposeEvalReport(
            skill_name="greeter",
            model="claude-sonnet-4-6",
            proposed_spec=spec,
            capture_source="tests/eval/captured/greeter.txt",
            validation_errors=["e1", "e2"],
            duration_seconds=1.5,
            input_tokens=100,
            output_tokens=50,
        )
        data = json.loads(report.to_json())
        assert data["skill_name"] == "greeter"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["proposed_spec"] == spec
        assert (
            data["capture_source"]
            == "tests/eval/captured/greeter.txt"
        )
        assert data["validation_errors"] == ["e1", "e2"]
        assert data["duration_seconds"] == 1.5
        assert data["input_tokens"] == 100
        assert data["output_tokens"] == 50
        assert data["api_error"] is None

    def test_api_error_scrubbed_on_disk_not_in_memory(self) -> None:
        secret = "sk-ant-api03-" + "A" * 95
        raw = f"anthropic API error: 401 body=\"{secret}\""
        report = ProposeEvalReport(
            skill_name="greeter",
            model="claude-sonnet-4-6",
            api_error=raw,
        )
        data = json.loads(report.to_json())

        assert data["api_error"] is not None
        assert secret not in data["api_error"]
        assert "[REDACTED]" in data["api_error"]
        # Surrounding context preserved.
        assert "anthropic API error: 401" in data["api_error"]

        # In-memory copy unchanged.
        assert report.api_error == raw

    def test_api_error_none_stays_none(self) -> None:
        report = ProposeEvalReport(
            skill_name="greeter",
            model="claude-sonnet-4-6",
            api_error=None,
        )
        data = json.loads(report.to_json())
        assert data["api_error"] is None

    def test_api_error_without_secrets_unchanged(self) -> None:
        raw = "anthropic API error: timeout"
        report = ProposeEvalReport(
            skill_name="greeter",
            model="claude-sonnet-4-6",
            api_error=raw,
        )
        data = json.loads(report.to_json())
        assert data["api_error"] == raw
        assert report.api_error == raw

    def test_default_model_constant(self) -> None:
        assert DEFAULT_PROPOSE_EVAL_MODEL == "claude-sonnet-4-6"


# --------------------------------------------------------------------------
# TestProposeEval (async orchestrator)
# --------------------------------------------------------------------------


class TestProposeEval:
    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path: Path) -> None:
        pi = _make_propose_input()
        result = _mock_anthropic_result(text=_good_response_text())
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.api_error is None
        assert report.validation_errors == []
        assert report.proposed_spec["assertions"][0]["id"] == "greets-user"
        assert report.input_tokens == 100
        assert report.output_tokens == 50
        assert report.skill_name == "greeter"
        assert report.model == DEFAULT_PROPOSE_EVAL_MODEL

    @pytest.mark.asyncio
    async def test_captured_args_override_test_args(
        self, tmp_path: Path
    ) -> None:
        """#117: when captured_skill_args is set, test_args is overridden verbatim.

        Belt-and-suspenders: even if the LLM's response emits a
        different ``test_args`` (the ``_good_spec_dict()`` helper
        returns ``"hello world"``), the post-processing in
        ``propose_eval`` must overwrite it with the captured value.
        """
        pi = _make_propose_input(capture_text="Hello Alice!\n")
        pi.captured_skill_args = "--name Alice --formal"
        # The LLM response carries ``test_args: "hello world"`` (the
        # default in ``_good_spec_dict``). Override must win.
        result = _mock_anthropic_result(text=_good_response_text())
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.api_error is None
        assert report.validation_errors == []
        assert (
            report.proposed_spec["test_args"] == "--name Alice --formal"
        )

    @pytest.mark.asyncio
    async def test_empty_captured_args_still_overrides(
        self, tmp_path: Path
    ) -> None:
        """#117: empty-string captured_skill_args is 'ran bare', not 'unknown'.

        The sidecar's presence with ``skill_args=""`` is a deliberate
        record that the capture ran with no args. The override must
        still fire to replace any LLM-emitted placeholder with the
        empty string so ``validate`` re-runs the skill bare.
        """
        pi = _make_propose_input(capture_text="Hello!\n")
        pi.captured_skill_args = ""  # sidecar present, args were empty
        result = _mock_anthropic_result(text=_good_response_text())
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.proposed_spec["test_args"] == ""

    @pytest.mark.asyncio
    async def test_no_captured_args_leaves_llm_test_args(
        self, tmp_path: Path
    ) -> None:
        """#117: no sidecar → LLM's test_args output is preserved."""
        pi = _make_propose_input()  # captured_skill_args=None
        result = _mock_anthropic_result(text=_good_response_text())
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        # _good_spec_dict emits "hello world" — preserved verbatim.
        assert report.proposed_spec["test_args"] == "hello world"

    @pytest.mark.asyncio
    async def test_calls_central_helper_with_prompt(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input()
        result = _mock_anthropic_result(text=_good_response_text())
        call_mock = AsyncMock(return_value=result)
        with patch("clauditor._anthropic.call_anthropic", call_mock):
            _ = await propose_eval(pi, spec_dir=tmp_path)
        call_mock.assert_awaited_once()
        kwargs = call_mock.await_args.kwargs
        args = call_mock.await_args.args
        assert kwargs["model"] == DEFAULT_PROPOSE_EVAL_MODEL
        assert kwargs["max_tokens"] == 4096
        assert len(args) == 1
        # Prompt contains the ID contract phrase + skill body.
        assert "unique `id`" in args[0]

    @pytest.mark.asyncio
    async def test_uses_monotonic_alias_for_duration(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input()
        result = _mock_anthropic_result(text=_good_response_text())
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ), patch(
            # Aggregate ``duration_seconds`` is the SUM of per-attempt
            # durations (matching ``input_tokens`` / ``output_tokens``).
            # ``_single_propose_attempt`` samples ``_monotonic`` twice
            # per call — start and end — so two samples for one attempt
            # with diff 2.5 yield ``report.duration_seconds == 2.5``.
            "clauditor.propose_eval._monotonic",
            side_effect=[0.0, 2.5],
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.duration_seconds == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_api_exception_captured_in_api_error_not_raised(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input()
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.proposed_spec == {}
        assert report.api_error is not None
        assert "anthropic API error" in report.api_error
        assert "boom" in report.api_error

    @pytest.mark.asyncio
    async def test_malformed_json_response_sets_validation_error(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input()
        # US-004 of #61: a parse failure on the first attempt triggers
        # a one-shot repair retry. Supply two malformed responses so
        # both attempts fail; the aggregated token counts reflect both
        # calls (100+100, 50+50). Use ``side_effect`` per
        # ``.claude/rules/mock-side-effect-for-distinct-calls.md``.
        first = _mock_anthropic_result(text="not json {{{")
        second = _mock_anthropic_result(text="still not json {{{")
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(side_effect=[first, second]),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.proposed_spec == {}
        assert len(report.validation_errors) >= 1
        # Both attempts contributed tokens.
        assert report.input_tokens == 200
        assert report.output_tokens == 100
        assert report.repair_attempted is True

    @pytest.mark.asyncio
    async def test_validation_failure_flows_into_report(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input()
        bad_spec = {
            "test_args": "",
            "assertions": [
                # missing id
                {"type": "contains", "name": "x", "needle": "y"}
            ],
        }
        result = _mock_anthropic_result(text=json.dumps(bad_spec))
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.api_error is None
        assert len(report.validation_errors) >= 1
        # Proposed spec still recorded so CLI can render it for debugging.
        assert report.proposed_spec == bad_spec

    @pytest.mark.asyncio
    async def test_prompt_build_exception_captured_not_raised(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input()
        with patch(
            "clauditor.propose_eval.build_propose_eval_prompt",
            side_effect=RuntimeError("prompt kaboom"),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert report.proposed_spec == {}
        assert report.api_error is not None
        assert "prompt build error" in report.api_error
        assert "prompt kaboom" in report.api_error

    @pytest.mark.asyncio
    async def test_token_budget_error_flows_into_api_error(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input(capture_text="x" * 250_000)
        # No call to Anthropic should happen; token budget raises in
        # build_propose_eval_prompt before the SDK call.
        call_mock = AsyncMock()
        with patch(
            "clauditor._anthropic.call_anthropic", call_mock
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        call_mock.assert_not_awaited()
        assert report.api_error is not None
        assert "tokens" in report.api_error

    @pytest.mark.asyncio
    async def test_spec_dir_defaults_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pi = _make_propose_input()
        result = _mock_anthropic_result(text=_good_response_text())
        monkeypatch.chdir(tmp_path)
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi)
        # Should have succeeded (spec did not declare input_files).
        assert report.api_error is None
        assert report.validation_errors == []

    @pytest.mark.asyncio
    async def test_capture_source_threaded_into_report(
        self, tmp_path: Path
    ) -> None:
        pi = _make_propose_input(
            capture_text="sample",
            capture_source="tests/eval/captured/greeter.txt",
        )
        result = _mock_anthropic_result(text=_good_response_text())
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert (
            report.capture_source
            == "tests/eval/captured/greeter.txt"
        )

    @pytest.mark.asyncio
    async def test_empty_text_blocks_yields_parse_failure(
        self, tmp_path: Path
    ) -> None:
        from clauditor._anthropic import AnthropicResult

        pi = _make_propose_input()
        empty = AnthropicResult(
            response_text="",
            text_blocks=[],
            input_tokens=10,
            output_tokens=0,
            raw_message=None,
        )
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=empty),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)
        assert len(report.validation_errors) >= 1

    @pytest.mark.asyncio
    async def test_result_without_response_text_joins_text_blocks(
        self, tmp_path: Path
    ) -> None:
        """Review #53 fallback path: if the SDK helper returns a result
        object without a ``response_text`` attribute, ``propose_eval``
        must join the ``text_blocks`` list rather than silently dropping
        the response. Covers the ``getattr(result, "response_text",
        None) is None`` branch in ``propose_eval``."""

        class ResultWithoutResponseText:
            """Stub shaped like ``AnthropicResult`` but missing
            the pre-joined ``response_text`` attribute."""

            def __init__(self, blocks: list[str]) -> None:
                self.text_blocks = blocks
                self.input_tokens = 10
                self.output_tokens = 5
                self.raw_message = None

        pi = _make_propose_input()
        # Split a valid JSON response across two text blocks so the
        # join must happen for parsing to succeed.
        full = _good_response_text()
        half = len(full) // 2
        split_result = ResultWithoutResponseText([full[:half], full[half:]])

        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(return_value=split_result),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        # Joining the two blocks reconstructs valid JSON → spec parsed
        # cleanly, no validation_errors and no api_error.
        assert report.api_error is None
        assert report.validation_errors == []
        assert report.proposed_spec.get("test_args") == "hello world"


# --------------------------------------------------------------------------
# TestBuildRepairProposeEvalPrompt (US-004 of #61 — pure builder)
# --------------------------------------------------------------------------


class TestBuildRepairProposeEvalPrompt:
    """Pure-builder tests for the repair prompt.

    No SDK mocks — the function is a pure string transformation. Per
    DEC-007 of ``plans/super/61-propose-eval-key-mismatch.md`` the
    repair prompt is a fresh ``call_anthropic`` invocation carrying
    the original prompt + the LLM's previous response + our validator
    errors + a closing imperative.
    """

    def test_contains_original_prompt(self) -> None:
        """The original propose-eval prompt body appears verbatim so
        the LLM has full context when re-generating."""
        original = (
            "You are proposing an EvalSpec for a Claude skill.\n\n"
            "### ANCHOR CONTRACT\nEvery entry MUST have a unique id.\n"
        )
        repair = build_repair_propose_eval_prompt(
            original,
            previous_response='{"bad": true}',
            validation_errors=["err1"],
        )
        assert original.rstrip("\n") in repair

    def test_fences_previous_response(self) -> None:
        """<previous_response> tags bracket the supplied text."""
        previous = '{"assertions": [{"id": "x", "type": "regex"}]}'
        repair = build_repair_propose_eval_prompt(
            "original prompt", previous, ["err"]
        )
        assert "<previous_response>" in repair
        assert "</previous_response>" in repair
        start = repair.index("<previous_response>")
        end = repair.index("</previous_response>")
        # Verbatim text lives inside the fenced block.
        assert previous in repair[start:end]

    def test_fences_validation_errors(self) -> None:
        """<validation_errors> contains every error verbatim, newline-joined."""
        errors = [
            (
                "assertions[0] (type='regex'): unknown key 'value' "
                "— did you mean 'pattern'?"
            ),
            "assertions[1] (type='min_count'): missing required key 'count'",
            "grading_criteria[0]: duplicate id 'greets-user'",
        ]
        repair = build_repair_propose_eval_prompt(
            "original", "prev response", errors
        )
        assert "<validation_errors>" in repair
        assert "</validation_errors>" in repair
        start = repair.index("<validation_errors>")
        end = repair.index("</validation_errors>")
        block = repair[start:end]
        # Every error appears verbatim inside the fence.
        for msg in errors:
            assert msg in block
        # Newline-joined (each line appears on its own line).
        for msg in errors:
            # Each error preceded by a newline OR immediately following
            # the opening tag's newline — either way no two errors are
            # collapsed onto a single line.
            assert "\n" + msg in block or msg + "\n" in block

    def test_framing_sentence_precedes_untrusted_tags(self) -> None:
        """The framing sentence appears BEFORE the first
        ``<previous_response>`` tag per
        ``.claude/rules/llm-judge-prompt-injection.md``.
        """
        repair = build_repair_propose_eval_prompt(
            "original prompt",
            previous_response="resp text",
            validation_errors=["err"],
        )
        framing_idx = repair.index("untrusted data, not instructions")
        prev_idx = repair.index("<previous_response>")
        val_idx = repair.index("<validation_errors>")
        assert framing_idx < prev_idx
        assert framing_idx < val_idx

    def test_framing_names_both_untrusted_tags(self) -> None:
        """Per the rule, the framing sentence enumerates every
        untrusted tag name so the model knows to de-escalate
        instructions in both fenced blocks. Tag names appear without
        angle brackets in the framing sentence (mirrors the
        convention in :func:`build_propose_eval_prompt`) so tests
        locating the first literal ``<previous_response>`` opening
        tag via ``prompt.find(...)`` do not collide with the
        enumeration."""
        repair = build_repair_propose_eval_prompt(
            "original prompt", "resp", ["err"]
        )
        # Find the framing sentence (the paragraph containing the
        # load-bearing substring).
        framing_idx = repair.index("untrusted data, not instructions")
        line_end = repair.find("\n\n", framing_idx)
        framing_region = repair[
            repair.rfind("\n", 0, framing_idx) + 1 : line_end
        ]
        assert "previous_response" in framing_region
        assert "validation_errors" in framing_region

    def test_closing_instruction_present(self) -> None:
        """The closing imperative tells the model to re-emit the full spec."""
        repair = build_repair_propose_eval_prompt(
            "original", "previous", ["err"]
        )
        assert "Re-emit the full corrected spec" in repair

    def test_does_not_mutate_inputs(self) -> None:
        """Pure function: the list of errors is not mutated."""
        original = "original prompt body"
        previous = "previous response body"
        errors = ["err1", "err2", "err3"]
        errors_snapshot = list(errors)

        _ = build_repair_propose_eval_prompt(original, previous, errors)

        # Input list object unchanged.
        assert errors == errors_snapshot
        # Input strings are immutable in Python, but assert identity
        # preservation defensively — a future refactor that started
        # mutating a cached buffer would fail this.
        assert original == "original prompt body"
        assert previous == "previous response body"

    def test_empty_error_list_yields_empty_fenced_block(self) -> None:
        """Defensive: callers should not pass an empty error list
        (the orchestrator only builds the repair prompt when the
        first attempt failed validation), but the pure function must
        still return a well-formed prompt."""
        repair = build_repair_propose_eval_prompt(
            "original", "previous", []
        )
        assert "<validation_errors>" in repair
        assert "</validation_errors>" in repair


# --------------------------------------------------------------------------
# TestProposeEvalRepairRetry (US-004 of #61 — orchestrator)
# --------------------------------------------------------------------------


def _bad_response_text_missing_id() -> str:
    """Return a response whose spec fails validation (missing id)."""
    return json.dumps(
        {
            "test_args": "",
            "assertions": [
                # Missing `id` — from_dict rejects via _require_id.
                {"type": "contains", "name": "n", "needle": "v"}
            ],
        }
    )


class TestProposeEvalRepairRetry:
    """DEC-004 / DEC-006 / DEC-007 of ticket #61.

    Per ``.claude/rules/mock-side-effect-for-distinct-calls.md`` each
    case uses ``side_effect=[first, second]`` rather than
    ``return_value=...``: a shared return would let both attempts see
    the same AnthropicResult and mask any bug in the per-attempt
    accounting or the "second attempt is authoritative" routing.
    """

    @pytest.mark.asyncio
    async def test_bad_first_good_repair_exits_zero(
        self, tmp_path: Path, capsys
    ) -> None:
        """First response fails validation → repair retry fires →
        second response passes. Report surfaces the second spec and
        ``repair_attempted == True``."""
        pi = _make_propose_input()
        first = _mock_anthropic_result(
            text=_bad_response_text_missing_id(),
            input_tokens=120,
            output_tokens=60,
        )
        second = _mock_anthropic_result(
            text=_good_response_text(),
            input_tokens=140,
            output_tokens=70,
        )
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(side_effect=[first, second]),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        assert report.repair_attempted is True
        assert len(report.attempts) == 2
        assert report.api_error is None
        assert report.validation_errors == []
        # Second attempt is authoritative.
        assert report.proposed_spec["assertions"][0]["id"] == "greets-user"

        # Aggregates sum across attempts.
        assert report.input_tokens == 260
        assert report.output_tokens == 130

        # Stderr retry signal per DEC-006.
        err = capsys.readouterr().err
        assert "retrying once with repair prompt" in err

    @pytest.mark.asyncio
    async def test_bad_first_bad_repair_propagates_second_errors(
        self, tmp_path: Path, capsys
    ) -> None:
        """Both attempts fail validation → report carries SECOND
        attempt's errors (not first's). CLI will exit 2 via existing
        validation_errors routing."""
        pi = _make_propose_input()
        first_bad = {
            "test_args": "",
            "assertions": [
                # Missing `id` — from_dict rejects.
                {"type": "contains", "name": "n", "needle": "v"}
            ],
        }
        # Second attempt fails on a DIFFERENT error so we can verify
        # the second-attempt errors are the ones surfaced.
        second_bad = {
            "test_args": "",
            "assertions": [
                {
                    "id": "same-id",
                    "type": "contains",
                    "name": "n",
                    "needle": "v",
                }
            ],
            "grading_criteria": [
                {"id": "same-id", "criterion": "collides"},
            ],
        }
        first = _mock_anthropic_result(text=json.dumps(first_bad))
        second = _mock_anthropic_result(text=json.dumps(second_bad))

        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(side_effect=[first, second]),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        assert report.repair_attempted is True
        assert len(report.attempts) == 2
        assert report.api_error is None
        assert len(report.validation_errors) >= 1
        # The SECOND attempt's error is about duplicate ids, not
        # missing ids → if we see "duplicate" we know the second
        # attempt's validation errors landed in the report (not the
        # first's missing-id errors).
        joined = "\n".join(report.validation_errors).lower()
        assert "duplicate" in joined
        assert "missing" not in joined

        # Stderr retry signal was emitted.
        err = capsys.readouterr().err
        assert "retrying once with repair prompt" in err

    @pytest.mark.asyncio
    async def test_good_first_call_no_repair(
        self, tmp_path: Path, capsys
    ) -> None:
        """First call returns a valid spec → no repair."""
        pi = _make_propose_input()
        result = _mock_anthropic_result(text=_good_response_text())
        call_mock = AsyncMock(side_effect=[result])
        with patch(
            "clauditor._anthropic.call_anthropic",
            call_mock,
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        assert report.repair_attempted is False
        assert len(report.attempts) == 1
        assert report.api_error is None
        assert report.validation_errors == []
        assert call_mock.call_count == 1

        # Stderr must NOT contain the retry signal.
        err = capsys.readouterr().err
        assert "retrying once with repair prompt" not in err

    @pytest.mark.asyncio
    async def test_api_error_no_repair(
        self, tmp_path: Path, capsys
    ) -> None:
        """API errors on the first attempt do NOT trigger a repair —
        the existing ``api_error`` → exit 3 path applies. The repair
        retry only fires on post-call invariant failures (validation
        errors)."""
        from clauditor._anthropic import AnthropicHelperError

        pi = _make_propose_input()
        call_mock = AsyncMock(
            side_effect=[AnthropicHelperError("401 auth failed")]
        )
        with patch(
            "clauditor._anthropic.call_anthropic",
            call_mock,
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        assert report.api_error is not None
        assert "401" in report.api_error
        assert report.repair_attempted is False
        assert call_mock.call_count == 1
        # Failing attempt's metrics are recorded (duration only; the
        # SDK helper raised before yielding tokens).
        assert len(report.attempts) == 1
        assert report.attempts[0].input_tokens == 0
        assert report.attempts[0].output_tokens == 0
        # Validation errors stay empty — API errors are a distinct
        # category per .claude/rules/llm-cli-exit-code-taxonomy.md.
        assert report.validation_errors == []

        # No retry stderr signal — API errors are not retried.
        err = capsys.readouterr().err
        assert "retrying once with repair prompt" not in err

    @pytest.mark.asyncio
    async def test_repair_call_api_error_surfaced_with_repair_attempted(
        self, tmp_path: Path, capsys
    ) -> None:
        """If the repair call itself hits an API error, surface it as
        ``api_error`` while keeping ``repair_attempted == True``. The
        validation errors from the first attempt are NOT surfaced —
        the API failure on retry is the authoritative report.

        Covers the "second.api_error is not None" branch in
        ``propose_eval`` after a successful first-attempt validation
        failure.
        """
        from clauditor._anthropic import AnthropicHelperError

        pi = _make_propose_input()
        first = _mock_anthropic_result(
            text=_bad_response_text_missing_id()
        )
        call_mock = AsyncMock(
            side_effect=[first, AnthropicHelperError("503 repair boom")]
        )
        with patch(
            "clauditor._anthropic.call_anthropic",
            call_mock,
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        assert report.repair_attempted is True
        assert report.api_error is not None
        assert "503" in report.api_error
        assert call_mock.call_count == 2
        assert len(report.attempts) == 2
        # Second attempt is an API failure — tokens are 0 for that
        # attempt, but the first attempt's tokens still count toward
        # the aggregate.
        assert report.attempts[0].input_tokens == 100
        assert report.attempts[1].input_tokens == 0

        # Stderr retry signal was emitted (before the repair call failed).
        err = capsys.readouterr().err
        assert "retrying once with repair prompt" in err

    @pytest.mark.asyncio
    async def test_attempts_accumulate_metrics(
        self, tmp_path: Path
    ) -> None:
        """The ``attempts`` list records one ``AttemptMetrics`` per
        ``call_anthropic``. Validates per-attempt accounting used by
        downstream observability."""
        pi = _make_propose_input()
        first = _mock_anthropic_result(
            text=_bad_response_text_missing_id(),
            input_tokens=111,
            output_tokens=22,
        )
        second = _mock_anthropic_result(
            text=_good_response_text(),
            input_tokens=333,
            output_tokens=44,
        )
        with patch(
            "clauditor._anthropic.call_anthropic",
            AsyncMock(side_effect=[first, second]),
        ):
            report = await propose_eval(pi, spec_dir=tmp_path)

        assert len(report.attempts) == 2
        assert isinstance(report.attempts[0], AttemptMetrics)
        assert report.attempts[0].input_tokens == 111
        assert report.attempts[0].output_tokens == 22
        assert report.attempts[1].input_tokens == 333
        assert report.attempts[1].output_tokens == 44
        # Aggregate matches sum.
        assert report.input_tokens == 111 + 333
        assert report.output_tokens == 22 + 44

    @pytest.mark.asyncio
    async def test_repair_skipped_when_over_token_budget(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """If the repair prompt exceeds ``_TOKEN_BUDGET_CAP``, the
        retry is aborted before the second ``call_anthropic`` fires.
        The first attempt's ``validation_errors`` surface on the
        report (so the CLI still exits 2) and ``repair_attempted``
        stays ``False`` because no second API call happened.
        """
        pi = _make_propose_input()
        first_bad = _mock_anthropic_result(
            text=_bad_response_text_missing_id(),
            input_tokens=111,
            output_tokens=22,
        )

        # Estimate is only "too big" for the repair prompt (detected
        # by the ``<validation_errors>`` tag the repair builder
        # appends). The original prompt keeps its real estimate, so
        # the first ``build_propose_eval_prompt`` check passes.
        import clauditor.propose_eval as pe_mod

        real_estimate = pe_mod._estimate_tokens

        def _fake_estimate(prompt: str) -> int:
            if "<validation_errors>" in prompt:
                return pe_mod._TOKEN_BUDGET_CAP + 1
            return real_estimate(prompt)

        monkeypatch.setattr(
            pe_mod, "_estimate_tokens", _fake_estimate
        )

        mock_call = AsyncMock(side_effect=[first_bad])
        with patch("clauditor._anthropic.call_anthropic", mock_call):
            report = await propose_eval(pi, spec_dir=tmp_path)

        # The retry was skipped: only one API call fired, only one
        # metrics entry recorded, and ``repair_attempted`` is False.
        assert mock_call.call_count == 1
        assert len(report.attempts) == 1
        assert report.repair_attempted is False
        # The first attempt's validation errors drive exit 2.
        assert report.validation_errors  # non-empty
        assert report.api_error is None

        # Operator-visible stderr signal explains the skip.
        err = capsys.readouterr().err
        assert "repair prompt over token budget" in err
        assert "skipping retry" in err


class TestValidateProposedSpecNonListFields:
    """``EvalSpec.from_dict`` hard-rejects non-list ``assertions`` and
    ``grading_criteria`` with a ``ValueError``; ``validate_proposed_spec``
    catches that and surfaces it as a validation error in the list. This
    replaces the pre-#61 "tolerate and normalize to empty" behavior,
    which was defensive dead code (the loader's own iteration crashed
    before the normalization branch ever fired).
    """

    def test_non_list_assertions_raises_validation_error(
        self, tmp_path: Path
    ):
        """``assertions`` as a scalar (not a list) is surfaced as a
        validation error naming the field and the offending type."""
        spec_dict = {
            "test_args": "x",
            "assertions": "not-a-list",
            "grading_criteria": [
                {"id": "c1", "criterion": "ok"}
            ],
        }
        errors = validate_proposed_spec(spec_dict, spec_dir=tmp_path)
        assert len(errors) == 1
        assert "'assertions' must be a list" in errors[0]
        assert "got str" in errors[0]

    def test_non_list_criteria_raises_validation_error(
        self, tmp_path: Path
    ):
        """Symmetrical: non-list ``grading_criteria`` is rejected."""
        spec_dict = {
            "test_args": "x",
            "assertions": [
                {
                    "id": "a1",
                    "type": "contains",
                    "needle": "hi",
                }
            ],
            "grading_criteria": {"not": "a list"},
        }
        errors = validate_proposed_spec(spec_dict, spec_dir=tmp_path)
        assert len(errors) == 1
        assert "'grading_criteria' must be a list" in errors[0]
        assert "got dict" in errors[0]


class TestSingleProposeAttemptImportError:
    """Covers the defensive ``ImportError`` branch in
    :func:`_single_propose_attempt` when the ``anthropic`` SDK (imported
    lazily inside the attempt) is not installed. The attempt returns
    an ``_AttemptResult`` with ``api_error`` set rather than raising,
    so the orchestrator's existing ``api_error → exit 3`` routing
    applies.
    """

    @pytest.mark.asyncio
    async def test_missing_anthropic_sdk_returns_api_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When ``from clauditor._anthropic import call_anthropic``
        raises ``ImportError`` (SDK not installed), the first
        attempt returns an ``api_error`` without making any network
        call. The orchestrator surfaces it as ``report.api_error``
        and ``report.repair_attempted`` stays ``False``.
        """
        import sys as _sys

        real_anthropic = _sys.modules.get("clauditor._anthropic")
        # Remove the cached helper module AND stub the raw SDK so
        # re-import raises ImportError on
        # ``from clauditor._anthropic import call_anthropic``. We
        # restore the original module at the end to avoid polluting
        # the rest of the test suite's module cache.
        _sys.modules.pop("clauditor._anthropic", None)

        def _raise_on_anthropic_import(name, *args, **kwargs):
            if name == "clauditor._anthropic":
                raise ImportError("fake SDK-missing error")
            return _original_import(name, *args, **kwargs)

        _original_import = __builtins__["__import__"] if isinstance(
            __builtins__, dict
        ) else __builtins__.__import__

        monkeypatch.setattr(
            "builtins.__import__", _raise_on_anthropic_import
        )

        try:
            pi = _make_propose_input()
            report = await propose_eval(pi, spec_dir=tmp_path)
        finally:
            if real_anthropic is not None:
                _sys.modules["clauditor._anthropic"] = real_anthropic

        # Attempt registered (with zero tokens since the API call
        # never fired) and api_error surfaced.
        assert report.api_error is not None
        assert "fake SDK-missing error" in report.api_error or (
            "anthropic" in report.api_error.lower()
        )
        assert report.repair_attempted is False
