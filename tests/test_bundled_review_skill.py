"""Tests for the bundled ``/review-agentskills-spec`` skill.

Three layers under test:

1. **Skill contract** — frontmatter shape + ``SkillSpec.from_file`` +
   ``EvalSpec.from_file`` loaders, mirroring ``test_bundled_skill.py``.

2. **Replay (always-on, deterministic)** —
   ``TestRealWorldClauditorExample`` runs the declared L1 assertions
   against a captured representative output at
   ``tests/fixtures/review-agentskills-spec/captured-output.txt``.
   Deterministic, free, no API call. The fixture README documents how
   to refresh it via ``clauditor capture``.

3. **Live run (gated, opt-in canary)** — ``TestLiveSkillRun`` invokes
   ``SkillRunner`` against the real skill and runs the same L1
   assertions on the actual Claude Code output. Skipped unless
   ``CLAUDITOR_RUN_LIVE=1`` is set AND the ``claude`` CLI is
   available AND ``ANTHROPIC_API_KEY`` is set. Marked ``live`` so it
   can also be selected via ``-m live`` / deselected via ``-m 'not
   live'``. Never runs in default CI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from clauditor.assertions import run_assertions
from clauditor.runner import SkillRunner
from clauditor.schemas import EvalSpec, criterion_text
from clauditor.spec import SkillSpec

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "review-agentskills-spec"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
EVAL_JSON = SKILL_DIR / "assets" / "review-agentskills-spec.eval.json"
CAPTURED_OUTPUT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "review-agentskills-spec"
    / "captured-output.txt"
)

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


def _load_captured_output() -> str:
    """Load the captured representative skill output from the fixture dir.

    Fixture provenance and refresh protocol are documented in
    ``tests/fixtures/review-agentskills-spec/README.md``.
    """
    return CAPTURED_OUTPUT.read_text(encoding="utf-8")


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
    """Replay: L1 assertions against a captured representative output.

    Always-on CI guard. Reads
    ``tests/fixtures/review-agentskills-spec/captured-output.txt`` and
    runs every declared L1 assertion against it. Deterministic, free,
    no subprocess, no API call. See the fixture README for refresh
    protocol.
    """

    def test_fixture_exists(self) -> None:
        assert CAPTURED_OUTPUT.is_file(), (
            f"captured-output fixture missing at {CAPTURED_OUTPUT}"
        )

    def test_replay_passes_all_l1_assertions(self) -> None:
        spec = EvalSpec.from_file(EVAL_JSON)
        output = _load_captured_output()
        assertion_set = run_assertions(output, spec.assertions)
        failing = [r for r in assertion_set.results if not r.passed]
        assert not failing, (
            "captured fixture should pass every declared L1 assertion; "
            f"failures: {[(r.id, r.message) for r in failing]}. "
            "If the skill's expected output shape has genuinely changed, "
            "refresh the fixture per tests/fixtures/review-agentskills-spec/"
            "README.md."
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


def _live_run_skip_reason() -> str | None:
    """Return a skip reason, or ``None`` if the live run may proceed.

    Three gates, all required:
    - ``CLAUDITOR_RUN_LIVE=1`` (explicit opt-in, never implicit).
    - ``claude`` CLI on ``PATH``.
    - ``ANTHROPIC_API_KEY`` set in the environment.
    """
    if os.environ.get("CLAUDITOR_RUN_LIVE") != "1":
        return (
            "live skill run is opt-in; set CLAUDITOR_RUN_LIVE=1 to enable"
        )
    if shutil.which("claude") is None:
        return "live run requires the 'claude' CLI on PATH"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "live run requires ANTHROPIC_API_KEY"
    return None


@pytest.mark.live
class TestLiveSkillRun:
    """Canary: invoke the real skill and run L1 assertions on its output.

    Gated triple-lock: ``CLAUDITOR_RUN_LIVE=1`` + ``claude`` CLI present
    + ``ANTHROPIC_API_KEY`` set. Skipped cleanly otherwise so this class
    never runs in default CI and never spends tokens implicitly.

    Spends Haiku/Sonnet tokens when it does run (the skill uses
    ``WebFetch`` against https://agentskills.io/specification and is
    subject to network availability and Claude behavior drift). Intended
    for a weekly canary workflow, not for per-PR CI.
    """

    def test_live_run_passes_l1_assertions(self, tmp_path: Path) -> None:
        skip = _live_run_skip_reason()
        if skip:
            pytest.skip(skip)

        # The skill is maintainer-only: its source lives at repo-root
        # `.claude/skills/review-agentskills-spec/` rather than being
        # installed under the package, and `clauditor setup` only
        # symlinks the user-facing `/clauditor` skill. A live test
        # invoked from an arbitrary `tmp_path` project dir would have
        # no `.claude/skills/review-agentskills-spec/` to resolve, so
        # the claude CLI would return "Unknown command". Build a
        # throwaway project dir with a one-off symlink so this test
        # doesn't force the skill to become user-facing.
        project_dir = tmp_path / "project"
        (project_dir / ".claude" / "skills").mkdir(parents=True)
        (project_dir / ".git").mkdir()  # satisfy project-root detection
        skill_root = SKILL_MD.parent
        (project_dir / ".claude" / "skills" / "review-agentskills-spec").symlink_to(
            skill_root
        )

        spec = SkillSpec.from_file(SKILL_MD, eval_path=EVAL_JSON)
        # Longer timeout: the skill issues WebFetch + a codebase inventory,
        # which easily exceeds the 180s default on Sonnet.
        runner = SkillRunner(project_dir=project_dir, timeout=360)
        result = runner.run(spec.skill_name)
        assert result.succeeded, (
            f"live run failed: exit_code={result.exit_code} "
            f"error={result.error!r} "
            f"output_head={result.output[:500]!r}"
        )
        assertion_set = run_assertions(
            result.output, spec.eval_spec.assertions
        )
        failing = [r for r in assertion_set.results if not r.passed]
        assert not failing, (
            f"live run output failed L1 assertions: "
            f"{[(r.id, r.message) for r in failing]}\n"
            f"output head: {result.output[:500]!r}"
        )
