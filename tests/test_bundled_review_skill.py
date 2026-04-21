"""Tests for the bundled ``/review-agentskills-spec`` skill.

Two things under test:

1. **Skill contract** — frontmatter shape + ``SkillSpec.from_file`` +
   ``EvalSpec.from_file`` loaders, mirroring ``test_bundled_skill.py``.
   Guards against silent drift in the agentskills.io-core frontmatter
   fields the skill must satisfy.

2. **Real-world clauditor example** — runs the L1 assertions declared
   in ``assets/review-agentskills-spec.eval.json`` against a canned
   representative drift-report output via ``run_assertions``. This
   exercises clauditor's L1 pipeline end-to-end on a realistic payload
   without requiring a live subprocess or an Anthropic API call, so
   the bundled skill doubles as a deterministic usage example in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from clauditor.assertions import run_assertions
from clauditor.schemas import EvalSpec, criterion_text
from clauditor.spec import SkillSpec

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "clauditor"
    / "skills"
    / "review-agentskills-spec"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
EVAL_JSON = SKILL_DIR / "assets" / "review-agentskills-spec.eval.json"

NAME_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_LEN = 64
DESCRIPTION_MAX_LEN = 1024
BODY_MAX_LINES = 500


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    assert lines and lines[0].rstrip("\r\n") == "---", (
        "frontmatter must start with '---' on the first line"
    )
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            close_idx = i
            break
    assert close_idx is not None, "frontmatter missing closing '---'"
    return "".join(lines[1:close_idx]), "".join(lines[close_idx + 1 :])


def _coerce_scalar(raw: str) -> object:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    return raw


def _parse_frontmatter(frontmatter_text: str) -> dict:
    result: dict[str, object] = {}
    current_dict: dict[str, object] | None = None
    for raw in frontmatter_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith(" ") or raw.startswith("\t"):
            assert current_dict is not None, (
                f"unexpected indented line with no parent mapping: {raw!r}"
            )
            k, _, v = raw.strip().partition(":")
            current_dict[k.strip()] = _coerce_scalar(v.strip())
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            nested: dict[str, object] = {}
            result[key] = nested
            current_dict = nested
        else:
            result[key] = _coerce_scalar(value)
            current_dict = None
    return result


@pytest.fixture(scope="module")
def skill_md_text() -> str:
    return SKILL_MD.read_text()


@pytest.fixture(scope="module")
def frontmatter_and_body(skill_md_text: str) -> tuple[dict, str]:
    fm_text, body = _split_frontmatter(skill_md_text)
    return _parse_frontmatter(fm_text), body


# Canned drift-report output used by ``TestRealWorldClauditorExample``.
# Represents what the skill would produce on a run where two spec rules
# have drifted. Must satisfy every L1 assertion declared in
# ``review-agentskills-spec.eval.json`` — if you add an assertion, update
# this canned output to keep the example test green.
CANNED_DRIFT_REPORT = """\
## agentskills.io spec drift report

Fetched: https://agentskills.io/specification (2026-04-20T12:00:00Z)

### Deltas

- **name parent-dir match** (status: drifted)
  - Spec: `name` MUST equal the parent directory name.
  - Clauditor: warns on mismatch but loads anyway.
  - Proposed change: src/clauditor/conformance.py — promote warning to
    a hard error in strict mode.
  - Rule anchor: .claude/rules/skill-identity-from-frontmatter.md.

- **description max length** (status: missing)
  - Spec: `description` must be 1-1024 characters.
  - Clauditor: does not check the upper bound.
  - Proposed change: src/clauditor/conformance.py — add the length
    check alongside the existing non-empty guard.
  - Rule anchor: .claude/rules/pre-llm-contract-hard-validate.md.

### No-change rows

4 rules match clauditor's current behavior.
"""


class TestSkillMdFrontmatter:
    def test_skill_md_exists_and_has_frontmatter_delimiters(
        self, skill_md_text: str
    ) -> None:
        assert SKILL_MD.is_file(), f"bundled SKILL.md missing at {SKILL_MD}"
        lines = skill_md_text.splitlines()
        assert lines[0] == "---"
        assert "---" in lines[1:], "closing '---' delimiter missing"

    def test_required_fields_present(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        assert isinstance(fm.get("name"), str) and fm["name"]
        assert isinstance(fm.get("description"), str) and fm["description"]

    def test_name_equals_directory_name(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        assert fm["name"] == SKILL_DIR.name

    def test_name_matches_spec_regex(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        name = fm["name"]
        assert 1 <= len(name) <= NAME_MAX_LEN
        assert NAME_REGEX.match(name), (
            f"name={name!r} does not match agentskills.io regex "
            f"{NAME_REGEX.pattern}"
        )

    def test_description_under_1024_chars(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        fm, _ = frontmatter_and_body
        assert len(fm["description"]) <= DESCRIPTION_MAX_LEN

    def test_disable_model_invocation_true(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # This skill spawns WebFetch + gh subprocesses; must not be
        # speculatively invoked. Same guard as the clauditor skill.
        fm, _ = frontmatter_and_body
        assert fm.get("disable-model-invocation") is True

    def test_body_under_500_lines(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        _, body = frontmatter_and_body
        assert len(body.splitlines()) <= BODY_MAX_LINES


class TestSkillMdBody:
    def test_body_mentions_spec_fetch_step(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # Regression guard: Step 1 of the workflow must reference
        # WebFetch against the agentskills.io spec URL.
        _, body = frontmatter_and_body
        assert "WebFetch" in body
        assert "agentskills.io/specification" in body

    def test_body_asks_before_opening_issue(
        self, frontmatter_and_body: tuple[dict, str]
    ) -> None:
        # Load-bearing contract from issue #72: the skill previews
        # changes and ASKS before creating a GitHub issue.
        _, body = frontmatter_and_body
        assert "gh issue create" in body
        assert "Open a GitHub issue" in body


class TestBundledSkillViaSpec:
    def test_skill_loads_via_skillspec_from_file(self) -> None:
        spec = SkillSpec.from_file(SKILL_MD)
        assert spec.skill_name == "review-agentskills-spec"
        assert spec.skill_path.name == "SKILL.md"
        assert spec.skill_path.parent.name == "review-agentskills-spec"


class TestBundledEvalSpec:
    def test_eval_spec_loads_via_eval_spec_from_file(self) -> None:
        spec = EvalSpec.from_file(EVAL_JSON)
        assert spec.skill_name == "review-agentskills-spec"
        assert spec.grading_model == "claude-sonnet-4-6"
        assert len(spec.assertions) >= 3
        assert len(spec.grading_criteria) >= 2

    def test_all_ids_unique(self) -> None:
        # Per .claude/rules/eval-spec-stable-ids.md.
        data = json.loads(EVAL_JSON.read_text())
        ids: list[str] = [a["id"] for a in data.get("assertions", [])]
        for s in data.get("sections", []):
            for tier in s.get("tiers", []):
                for fld in tier.get("fields", []):
                    ids.append(fld["id"])
        ids.extend(c["id"] for c in data.get("grading_criteria", []))
        assert len(ids) == len(set(ids)), (
            f"duplicate ids in bundled eval spec: {ids}"
        )
        for c in data.get("grading_criteria", []):
            assert criterion_text(c).strip()


class TestRealWorldClauditorExample:
    """Exercises clauditor's L1 pipeline against a canned skill output.

    This is the dogfood test for issue #72 — it treats the bundled skill
    as a live example of clauditor usage, running the spec's own
    assertions against a representative drift report. No subprocess, no
    API call, fully deterministic in CI.
    """

    def test_canned_report_passes_all_l1_assertions(self) -> None:
        spec = EvalSpec.from_file(EVAL_JSON)
        # ``spec.assertions`` is a list of dicts carrying the per-type
        # semantic keys expected by ``run_assertions``.
        assertion_set = run_assertions(CANNED_DRIFT_REPORT, spec.assertions)
        failing = [r for r in assertion_set.results if not r.passed]
        assert not failing, (
            "canned drift report should pass every declared L1 "
            f"assertion; failures: "
            f"{[(r.id, r.message) for r in failing]}"
        )
        # Belt-and-suspenders: every declared assertion ran.
        assert len(assertion_set.results) == len(spec.assertions)

    def test_empty_output_fails_assertions(self) -> None:
        # Negative case: an empty output must fail the min_length +
        # every ``contains`` assertion. Confirms the eval spec is
        # actually discriminating (not trivially always-true).
        spec = EvalSpec.from_file(EVAL_JSON)
        assertion_set = run_assertions("", spec.assertions)
        failures = [r for r in assertion_set.results if not r.passed]
        assert failures, "empty output should fail at least one assertion"
