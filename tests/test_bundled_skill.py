"""Tests for the bundled /clauditor skill shipped under ``src/clauditor/skills/``.

Validates frontmatter shape, naming constraints, and that the sibling eval
spec loads via :func:`clauditor.schemas.EvalSpec.from_file`. The bundled skill
itself is the canonical example of a clauditor slash-command; these tests
guard against silent drift in the frontmatter contract (agentskills.io core
spec + Claude Code extensions, per DEC-004 of
``plans/super/43-setup-slash-command.md``).

Frontmatter parsing uses a small hand-rolled YAML reader to avoid adding
``PyYAML`` as a runtime dependency — the contract is intentionally small
(strings, booleans, one nested mapping, one list of tool patterns).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from clauditor.schemas import EvalSpec, criterion_text
from clauditor.spec import SkillSpec

SKILLS_ROOT = (
    Path(__file__).resolve().parent.parent / "src" / "clauditor" / "skills"
)
SKILL_DIR = SKILLS_ROOT / "clauditor"
SKILL_MD = SKILL_DIR / "SKILL.md"
EVAL_JSON = SKILL_DIR / "assets" / "clauditor.eval.json"
REVIEW_SKILL_MD = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "review-agentskills-spec"
    / "SKILL.md"
)

# agentskills.io naming constraints: lowercase a-z + digits + hyphens, with
# hyphens between segments, 1-64 chars total.
NAME_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_LEN = 64
DESCRIPTION_MAX_LEN = 1024
BODY_MAX_LINES = 500


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, body_text)`` for a SKILL.md.

    Expects the canonical ``---\\n<yaml>\\n---\\n<body>`` shape. Raises
    ``AssertionError`` if the delimiters are missing or malformed.
    """
    lines = text.splitlines(keepends=True)
    assert lines and lines[0].rstrip("\r\n") == "---", (
        "frontmatter must start with '---' delimiter on the first line"
    )
    # Find the closing '---' on its own line, starting after line 0.
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            close_idx = i
            break
    assert close_idx is not None, "frontmatter missing closing '---' delimiter"
    frontmatter = "".join(lines[1:close_idx])
    body = "".join(lines[close_idx + 1 :])
    return frontmatter, body


def _parse_frontmatter(frontmatter_text: str) -> dict:
    """Parse a minimal YAML-ish frontmatter block into a dict.

    Supports:
      - top-level ``key: value`` pairs (scalar string / bool / quoted string)
      - one level of nested mapping via leading-space indentation

    NOT a general YAML parser — intentionally tight to keep the bundled-skill
    frontmatter shape from drifting into anything we cannot validate inline.
    """
    result: dict[str, object] = {}
    current_dict: dict[str, object] | None = None

    for raw in frontmatter_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # Indented line: belongs to current_dict (nested mapping).
        if raw.startswith(" ") or raw.startswith("\t"):
            assert current_dict is not None, (
                f"unexpected indented line with no parent mapping: {raw!r}"
            )
            k, _, v = raw.strip().partition(":")
            current_dict[k.strip()] = _coerce_scalar(v.strip())
            continue
        # Top-level line.
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            # Begins a nested mapping.
            nested: dict[str, object] = {}
            result[key] = nested
            current_dict = nested
        else:
            result[key] = _coerce_scalar(value)
            current_dict = None

    return result


def _coerce_scalar(raw: str) -> object:
    """Coerce a YAML-ish scalar: quoted string, bool, or bare string."""
    if raw == "true":
        return True
    if raw == "false":
        return False
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    return raw


@pytest.fixture(scope="module")
def skill_md_text() -> str:
    return SKILL_MD.read_text()


@pytest.fixture(scope="module")
def frontmatter_and_body(skill_md_text: str) -> tuple[dict, str]:
    fm_text, body = _split_frontmatter(skill_md_text)
    return _parse_frontmatter(fm_text), body


class TestSkillMdFrontmatter:
    def test_skill_md_exists_and_has_frontmatter_delimiters(
        self, skill_md_text: str
    ) -> None:
        assert SKILL_MD.is_file(), f"bundled SKILL.md missing at {SKILL_MD}"
        lines = skill_md_text.splitlines()
        assert lines[0] == "---", "first line must be '---'"
        assert "---" in lines[1:], "closing '---' delimiter missing"

    def test_skill_md_has_required_frontmatter_fields(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        # Core agentskills.io required fields.
        assert isinstance(fm.get("name"), str) and fm["name"], (
            "frontmatter 'name' must be a non-empty string"
        )
        assert isinstance(fm.get("description"), str) and fm["description"], (
            "frontmatter 'description' must be a non-empty string"
        )

    def test_skill_md_name_equals_directory_name(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        # Per agentskills.io spec, `name` must match the parent directory
        # name of the skill dir.
        assert fm["name"] == SKILL_DIR.name, (
            f"frontmatter name={fm['name']!r} must equal parent dir "
            f"name={SKILL_DIR.name!r}"
        )

    def test_skill_md_name_matches_spec_regex(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        name = fm["name"]
        assert 1 <= len(name) <= NAME_MAX_LEN, (
            f"name length {len(name)} outside [1, {NAME_MAX_LEN}]"
        )
        assert NAME_REGEX.match(name), (
            f"name={name!r} does not match agentskills.io regex "
            f"{NAME_REGEX.pattern}"
        )

    def test_skill_md_description_length_under_1024(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        description = fm["description"]
        assert len(description) <= DESCRIPTION_MAX_LEN, (
            f"description length {len(description)} exceeds "
            f"{DESCRIPTION_MAX_LEN} chars"
        )

    def test_skill_md_uses_disable_model_invocation(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # DEC-004: clauditor writes sidecars and spawns subprocesses, so
        # the skill must not be speculatively invoked by the model.
        fm, _ = frontmatter_and_body
        assert fm.get("disable-model-invocation") is True, (
            "disable-model-invocation must be true per DEC-004"
        )

    def test_skill_md_body_under_500_lines(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        _, body = frontmatter_and_body
        line_count = len(body.splitlines())
        assert line_count <= BODY_MAX_LINES, (
            f"body has {line_count} lines; must be ≤ {BODY_MAX_LINES}"
        )


class TestSkillMdBody:
    def test_body_mentions_propose_eval(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # Regression guard (DEC-007 of
        # plans/super/54-teach-propose-eval-workflow.md): the bundled
        # SKILL.md body must reference `propose-eval` so Step 3 of the
        # workflow (LLM-assisted eval bootstrap) does not silently
        # disappear on a future edit.
        _, body = frontmatter_and_body
        assert "propose-eval" in body, (
            "bundled SKILL.md body must mention 'propose-eval' "
            "(DEC-007 regression guard)"
        )

    def test_body_mentions_suggest(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # Regression guard per
        # .claude/rules/bundled-skill-docs-sync.md: the bundled SKILL.md
        # body must reference `clauditor suggest` so the closing half of
        # the workflow (propose SKILL.md edits from failing L3
        # criteria) does not silently disappear on a future edit.
        _, body = frontmatter_and_body
        assert "clauditor suggest" in body, (
            "bundled SKILL.md body must mention 'clauditor suggest' "
            "(bundled-skill-docs-sync regression guard)"
        )

    def test_body_mentions_lint(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # Regression guard (DEC-004 of
        # plans/super/134-bundled-skill-fixes.md): the bundled SKILL.md
        # body must reference `clauditor lint` in the Common errors
        # subsection so the spec-conformance entry point does not
        # silently disappear on a future edit.
        _, body = frontmatter_and_body
        assert "clauditor lint" in body, (
            "bundled SKILL.md body must mention 'clauditor lint' "
            "(DEC-004 regression guard)"
        )

    def test_body_mentions_doctor(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # Regression guard (DEC-004 of
        # plans/super/134-bundled-skill-fixes.md): the bundled SKILL.md
        # body must reference `clauditor doctor` in the Common errors
        # subsection so the environment-diagnostics entry point does
        # not silently disappear on a future edit.
        _, body = frontmatter_and_body
        assert "clauditor doctor" in body, (
            "bundled SKILL.md body must mention 'clauditor doctor' "
            "(DEC-004 regression guard)"
        )


class TestBundledSkillViaSpec:
    def test_bundled_skill_loads_via_skillspec(self) -> None:
        # Regression guard (DEC-005 of plans/super/62-skill-md-layout.md):
        # the bundled SKILL.md must load cleanly through
        # ``SkillSpec.from_file`` with modern-layout name derivation —
        # ``skill_name`` comes from the frontmatter ``name:`` field, not
        # the file stem. We do NOT assert on ``spec.eval_spec`` here
        # because auto-discovery looks for a sibling ``SKILL.eval.json``
        # and the bundled eval intentionally lives at
        # ``assets/clauditor.eval.json`` (covered by ``TestBundledEvalSpec``).
        spec = SkillSpec.from_file(SKILL_MD)
        assert spec.skill_name == "clauditor"
        assert spec.skill_path.name == "SKILL.md"
        assert spec.skill_path.parent.name == "clauditor"


class TestBundledEvalSpec:
    def test_eval_spec_loads_via_eval_spec_from_file(self) -> None:
        # This must not raise — a raise here means the bundled eval spec
        # is structurally invalid or fails stable-id uniqueness.
        spec = EvalSpec.from_file(EVAL_JSON)
        assert spec.skill_name == "clauditor"
        assert spec.grading_model == "claude-sonnet-4-6"
        assert len(spec.assertions) >= 3, (
            f"bundled eval spec must declare at least 3 assertions, "
            f"got {len(spec.assertions)}"
        )
        assert len(spec.grading_criteria) >= 2, (
            f"bundled eval spec must declare at least 2 grading_criteria, "
            f"got {len(spec.grading_criteria)}"
        )

    def test_eval_spec_all_ids_unique(self) -> None:
        # Per .claude/rules/eval-spec-stable-ids.md: every assertion, field,
        # and criterion carries a unique id, spanning all three layers.
        data = json.loads(EVAL_JSON.read_text())
        ids: list[str] = []
        for a in data.get("assertions", []):
            ids.append(a["id"])
        for s in data.get("sections", []):
            for tier in s.get("tiers", []):
                for fld in tier.get("fields", []):
                    ids.append(fld["id"])
        for c in data.get("grading_criteria", []):
            ids.append(c["id"])
        assert len(ids) == len(set(ids)), (
            f"duplicate ids in bundled eval spec: {ids}"
        )
        # Belt-and-suspenders: every criterion text should be non-empty.
        for c in data.get("grading_criteria", []):
            assert criterion_text(c).strip(), (
                f"criterion id={c['id']!r} has empty text"
            )


class TestBundledSkillConformance:
    """#71 US-007: regression guard that both bundled skills stay conformant.

    Both shipped bundled skills MUST pass ``check_conformance`` with zero
    errors. The ``AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL`` warning is
    expected for both skills because each declares ``allowed-tools:`` —
    the agentskills.io spec marks that field as experimental and emits
    the warning for every skill that uses it. This is spec-faithful
    signal, not noise to silence, so we do NOT extend
    ``KNOWN_CLAUDE_CODE_EXTENSION_KEYS`` (DEC-009 is scoped to
    UNKNOWN_KEY false positives, not experimental-field true
    positives). Instead, the test records the single expected warning
    code in its own allowlist and flags any other unexpected warning
    as a regression.
    """

    _ACCEPTABLE_WARNING_CODES: frozenset[str] = frozenset(
        {
            "AGENTSKILLS_ALLOWED_TOOLS_EXPERIMENTAL",
        }
    )

    def test_bundled_clauditor_skill_has_no_errors(self) -> None:
        from clauditor.conformance import check_conformance

        issues = check_conformance(SKILL_MD.read_text(), SKILL_MD)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], (
            f"Bundled /clauditor has conformance errors: {errors}"
        )

    def test_bundled_clauditor_skill_warnings_are_acceptable(self) -> None:
        from clauditor.conformance import check_conformance

        warnings = [
            i
            for i in check_conformance(SKILL_MD.read_text(), SKILL_MD)
            if i.severity == "warning"
        ]
        unexpected = [
            w for w in warnings if w.code not in self._ACCEPTABLE_WARNING_CODES
        ]
        assert unexpected == [], (
            f"Bundled /clauditor has unexpected warnings: {unexpected}"
        )

    def test_bundled_review_skill_has_no_errors(self) -> None:
        from clauditor.conformance import check_conformance

        issues = check_conformance(
            REVIEW_SKILL_MD.read_text(), REVIEW_SKILL_MD
        )
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], (
            f"/review-agentskills-spec has conformance errors: "
            f"{errors}"
        )

    def test_bundled_review_skill_warnings_are_acceptable(self) -> None:
        from clauditor.conformance import check_conformance

        warnings = [
            i
            for i in check_conformance(
                REVIEW_SKILL_MD.read_text(), REVIEW_SKILL_MD
            )
            if i.severity == "warning"
        ]
        unexpected = [
            w for w in warnings if w.code not in self._ACCEPTABLE_WARNING_CODES
        ]
        assert unexpected == [], (
            f"/review-agentskills-spec has unexpected warnings: "
            f"{unexpected}"
        )
