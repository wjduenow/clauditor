"""SkillSpec — the main entry point for testing a skill.

Combines the skill file, eval spec, and runner into a single interface.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

from clauditor.assertions import AssertionSet, run_assertions
from clauditor.paths import derive_project_dir, derive_skill_name
from clauditor.runner import SkillResult, SkillRunner
from clauditor.schemas import EvalSpec
from clauditor.workspace import stage_inputs


class SkillSpec:
    """Test specification for a Claude Code skill.

    Usage:
        spec = SkillSpec.from_file(".claude/commands/find-kid-activities.md")
        result = spec.run()
        SkillAsserter(result).assert_contains("Venues")

        # Or run the full eval spec:
        assertion_set = spec.evaluate()
        assert assertion_set.passed
    """

    def __init__(
        self,
        skill_path: Path,
        eval_spec: EvalSpec | None = None,
        runner: SkillRunner | None = None,
        *,
        skill_name_override: str | None = None,
    ):
        self.skill_path = skill_path
        # Name derivation: `skill_name_override` is the happy path from
        # `from_file`, which has already read the file and consulted
        # frontmatter. When omitted (direct-constructor callers that may
        # pass a non-existent path, e.g. tests/test_quality_grader.py
        # uses `Path("dummy.md")`), fall back to layout-aware filesystem
        # derivation without any file I/O. Modern (`SKILL.md` under a
        # named dir) → parent.name; legacy → stem. See DEC-006.
        if skill_name_override is not None:
            self.skill_name = skill_name_override
        elif skill_path.name == "SKILL.md":
            self.skill_name = skill_path.parent.name
        else:
            self.skill_name = skill_path.stem
        self.eval_spec = eval_spec
        # Layout-aware project_dir derivation. `derive_project_dir`
        # walks up for a `.git`/`.claude` marker first (with home-dir
        # exclusion) and falls back to the appropriate ascent depth for
        # modern vs legacy layouts. Replaces the previous hardcoded
        # 3-deep ascent, which landed inside `.claude/` for modern
        # skills. See DEC-003.
        self.runner = runner or SkillRunner(project_dir=derive_project_dir(skill_path))

    @classmethod
    def from_file(
        cls,
        skill_path: str | Path,
        eval_path: str | Path | None = None,
        runner: SkillRunner | None = None,
    ) -> SkillSpec:
        """Load a skill spec from a skill .md file.

        Automatically looks for a sibling eval.json if eval_path
        is not specified. For `my-skill.md`, looks for `my-skill.eval.json`;
        for the modern `<dir>/SKILL.md` layout, looks for
        `<dir>/SKILL.eval.json` (sibling of SKILL.md).

        The skill's identity (``skill_name``) is derived from the file's
        frontmatter ``name:`` field when present and valid; otherwise
        from the filesystem (parent dir for modern, stem for legacy).
        When frontmatter disagrees with the filesystem name, the
        frontmatter wins and a warning is emitted to stderr. See DEC-001,
        DEC-002, DEC-009.
        """
        skill_path = Path(skill_path)
        if not skill_path.exists():
            raise FileNotFoundError(f"Skill file not found: {skill_path}")

        text = skill_path.read_text(encoding="utf-8")
        skill_name, warning = derive_skill_name(skill_path, text)
        if warning is not None:
            print(warning, file=sys.stderr)

        # Auto-discover eval spec
        eval_spec = None
        if eval_path:
            eval_spec = EvalSpec.from_file(eval_path)
        else:
            default_eval = skill_path.with_suffix(".eval.json")
            if default_eval.exists():
                eval_spec = EvalSpec.from_file(default_eval)

        return cls(
            skill_path=skill_path,
            eval_spec=eval_spec,
            runner=runner,
            skill_name_override=skill_name,
        )

    def run(
        self,
        args: str | None = None,
        *,
        run_dir: Path | None = None,
    ) -> SkillResult:
        """Run the skill and return captured output.

        If args is None and an eval spec exists, uses the eval spec's test_args.

        If ``run_dir`` is provided and the eval spec declares non-empty
        ``input_files``, those files are staged into ``run_dir / "inputs"``
        and the subprocess runs with that directory as its CWD.
        """
        run_args = (
            args
            if args is not None
            else (self.eval_spec.test_args if self.eval_spec else "")
        )

        effective_cwd: Path | None = None
        if (
            run_dir is not None
            and self.eval_spec is not None
            and self.eval_spec.input_files
        ):
            sources = [Path(p) for p in self.eval_spec.input_files]
            stage_inputs(run_dir, sources)
            effective_cwd = run_dir / "inputs"
            print(f"Staged {len(sources)} input file(s) into {effective_cwd}")

        result = self.runner.run(self.skill_name, run_args, cwd=effective_cwd)

        # Read output from files if eval spec specifies file-based output
        # Only read files on successful runs to avoid stale output
        if self.eval_spec and result.succeeded:
            base_dir = (
                effective_cwd
                if effective_cwd is not None
                else self.runner.project_dir
            )
            if self.eval_spec.output_file:
                file_path = base_dir / self.eval_spec.output_file
                if file_path.exists():
                    result.output = file_path.read_text()
            elif self.eval_spec.output_files:
                first_output = None
                for pattern in self.eval_spec.output_files:
                    full_pattern = str(base_dir / pattern)
                    for match in sorted(glob.glob(full_pattern)):
                        match_path = Path(match)
                        if match_path.is_file():
                            output_text = match_path.read_text()
                            key = match_path.relative_to(base_dir).as_posix()
                            result.outputs[key] = output_text
                            if first_output is None:
                                first_output = output_text
                if first_output is not None:
                    result.output = first_output

        return result

    def evaluate(self, output: str | None = None) -> AssertionSet:
        """Run Layer 1 assertions from the eval spec against output.

        If output is None, runs the skill first to get output. Note that this
        path does not stage ``input_files`` — for that, call ``run(run_dir=...)``
        directly (the CLI and pytest plugin do this for you).
        """
        if not self.eval_spec:
            raise ValueError(
                f"No eval spec found for {self.skill_name}. "
                f"Create {self.skill_path.with_suffix('.eval.json')}"
            )

        if output is None:
            result = self.run()
            if not result.succeeded:
                return AssertionSet(
                    results=[
                        _failed_run_result(
                            self.skill_name, result.error or "Unknown error"
                        )
                    ]
                )
            output = result.output

        return run_assertions(output, self.eval_spec.assertions)


def _failed_run_result(skill_name: str, error: str):
    from clauditor.assertions import AssertionResult

    return AssertionResult(
        name="skill_execution",
        passed=False,
        message=f"Skill '{skill_name}' failed to run: {error}",
        kind="custom",
    )
