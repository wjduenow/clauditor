"""SkillSpec — the main entry point for testing a skill.

Combines the skill file, eval spec, and runner into a single interface.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

from clauditor._frontmatter import parse_frontmatter
from clauditor.assertions import AssertionSet, run_assertions
from clauditor.conformance import check_conformance, format_issue_line
from clauditor.paths import derive_project_dir, derive_skill_name, resolve_agents_md
from clauditor.runner import SkillResult, SkillRunner, env_with_sync_tasks
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
        See DEC-001, DEC-002 of ``plans/super/62-skill-md-layout.md``.
        Per DEC-008 of ``plans/super/71-agentskills-lint.md``, any
        warning surfacing for invalid-name or name/filesystem
        disagreement is now emitted by
        :func:`clauditor.conformance.check_conformance` via the
        soft-warn hook (US-006), not by this loader.
        """
        skill_path = Path(skill_path)
        if not skill_path.exists():
            raise FileNotFoundError(f"Skill file not found: {skill_path}")

        text = skill_path.read_text(encoding="utf-8")
        skill_name = derive_skill_name(skill_path, text)

        # US-006 soft-warn hook (DEC-003 / DEC-014 of
        # ``plans/super/71-agentskills-lint.md``): surface
        # agentskills.io conformance warnings to stderr. Only
        # ``severity="warning"`` issues fire here — errors are silent
        # at this layer and must be discovered via ``clauditor lint``.
        # ``check_conformance`` never raises, so no try/except needed.
        # Uses ``format_issue_line`` (conformance module) so the prefix
        # format stays in lockstep with the CLI renderer — a single
        # seam per DEC-014.
        for issue in check_conformance(text, skill_path):
            if issue.severity == "warning":
                print(format_issue_line(issue), file=sys.stderr)

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
        timeout_override: int | None = None,
        env_override: dict[str, str] | None = None,
        sync_tasks_override: bool | None = None,
        harness_name_override: str | None = None,
    ) -> SkillResult:
        """Run the skill and return captured output.

        If args is None and an eval spec exists, uses the eval spec's test_args.

        If ``run_dir`` is provided and the eval spec declares non-empty
        ``input_files``, those files are staged into ``run_dir / "inputs"``
        and the subprocess runs with that directory as its CWD.

        ``harness_name_override`` (US-006 / DEC-004 of #151): when non-
        ``None``, materialize a fresh :class:`SkillRunner` with the
        named harness via :func:`clauditor._harnesses.construct_harness`
        and use it for this call. ``"auto"`` is rejected by
        ``construct_harness``; callers must resolve auto via
        :func:`clauditor._providers.resolve_harness` before passing.
        When ``None`` (default), ``self.runner`` is used as-is.
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

        # DEC-005: thread the per-eval escape hatch into the runner. When
        # no eval_spec is attached, default to True (back-compat).
        allow_hang_heuristic = (
            self.eval_spec.allow_hang_heuristic if self.eval_spec else True
        )
        # DEC-002: timeout precedence is CLI > spec > default. ``None``
        # falls through to ``SkillRunner.run``, which then uses its own
        # ``self.timeout`` default (300s). DEC-013: ``env_override`` has
        # no merge — passed through to ``runner.run(env=...)`` unchanged.
        effective_timeout = (
            timeout_override
            if timeout_override is not None
            else (
                self.eval_spec.timeout
                if self.eval_spec is not None
                else None
            )
        )
        # Tier 1.5 of GitHub #103: sync_tasks precedence is
        # CLI > spec > default False. When effective, mutate the
        # outgoing ``env_override`` to add
        # ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` so the subprocess
        # runs Task(run_in_background=true) synchronously. Composes
        # with the existing ``env_override`` (e.g. from --no-api-key):
        # both effects apply.
        effective_sync_tasks: bool
        if sync_tasks_override is not None:
            effective_sync_tasks = sync_tasks_override
        elif self.eval_spec is not None:
            effective_sync_tasks = self.eval_spec.sync_tasks
        else:
            effective_sync_tasks = False
        effective_env = env_override
        if effective_sync_tasks:
            effective_env = env_with_sync_tasks(effective_env)

        # US-004 of issue #150: resolve the effective system_prompt.
        # US-003 of #154 (DEC-003 / DEC-008 / DEC-009): three-tier
        # resolution with provenance stamping into
        # ``SkillResult.harness_metadata["system_prompt_source"]``.
        #
        # Order:
        #   (a) Explicit ``EvalSpec.system_prompt`` set →
        #       source = "explicit".
        #   (b) AGENTS.md found via :func:`clauditor.paths.resolve_agents_md`
        #       (skill-dir first, then project-root) → source = "agents_md".
        #   (c) Auto-derive from SKILL.md body (post-frontmatter) →
        #       source = "skill_md".
        #
        # Wrap I/O failures (missing file, malformed frontmatter,
        # permission errors) on the SKILL.md auto-derive path as a
        # friendly ``RuntimeError`` that names the skill and path; the
        # original exception chains through ``__cause__`` for debug.
        # The empty-string body case threads through verbatim — we do
        # NOT fall back to None, so misconfigured skills surface clearly
        # rather than silently masking the missing prompt.
        effective_system_prompt: str | None = None
        system_prompt_source: str
        if self.eval_spec is not None and self.eval_spec.system_prompt is not None:
            effective_system_prompt = self.eval_spec.system_prompt
            system_prompt_source = "explicit"
        else:
            # AGENTS.md tier — pure resolver applies the
            # ``.claude/rules/path-validation.md`` recipe (resolve
            # strict + ``is_relative_to`` anchor) before the read. A
            # ValueError from the resolver is intentionally
            # un-wrapped: a hostile/typoed symlink escape is a
            # security-relevant signal, not a friendly auto-derive
            # failure, and should propagate to the CLI seam.
            agents_md_path = resolve_agents_md(
                self.skill_path, self.runner.project_dir
            )
            if agents_md_path is not None:
                # Theoretical race: ``resolve_agents_md`` validated via
                # ``Path.resolve(strict=True)`` so the file existed when
                # the resolver ran. A concurrent delete or perms change
                # between resolve and read surfaces as a friendly
                # RuntimeError naming the skill + path; ``__cause__``
                # chains the original. The ``except`` arm is defensive
                # and not exercised by tests.
                try:
                    effective_system_prompt = agents_md_path.read_text(
                        encoding="utf-8"
                    )
                    system_prompt_source = "agents_md"
                except (FileNotFoundError, OSError) as exc:  # pragma: no cover
                    raise RuntimeError(
                        f"clauditor.spec: failed to read AGENTS.md "
                        f"for skill {self.skill_name!r} from "
                        f"{agents_md_path}: {exc}"
                    ) from exc
            else:
                try:
                    skill_text = self.skill_path.read_text(encoding="utf-8")
                    _meta, body = parse_frontmatter(skill_text)
                    effective_system_prompt = body
                    system_prompt_source = "skill_md"
                except (FileNotFoundError, OSError, ValueError) as exc:
                    raise RuntimeError(
                        f"clauditor.spec: failed to auto-derive system_prompt "
                        f"for skill {self.skill_name!r} from {self.skill_path}: "
                        f"{exc}"
                    ) from exc

        # US-006 / DEC-004 of #151: when the caller (typically the CLI
        # seam) has resolved a concrete harness name, materialize a fresh
        # ``SkillRunner`` with that harness. Constructing a new runner
        # (rather than mutating ``self.runner.harness``) avoids mutating
        # shared state across calls; construction cost is negligible.
        # ``construct_harness`` rejects ``"auto"`` — callers must resolve
        # it through :func:`clauditor._providers.resolve_harness` first.
        #
        # Same-harness skip (Copilot / CodeRabbit review feedback on PR
        # #166): when ``harness_name_override`` matches the harness the
        # existing ``self.runner`` already uses, reuse the runner. This
        # preserves any harness-specific configuration carried by that
        # runner (notably the pytest plugin's ``--clauditor-claude-bin``
        # which lives on ``self.runner.harness.claude_bin``). Constructing
        # a fresh ``SkillRunner`` with the default ``construct_harness``
        # call would silently swap that custom binary back to ``"claude"``.
        if harness_name_override is not None:
            current_harness_name = getattr(
                getattr(self.runner, "harness", None), "name", None
            )
            if current_harness_name == harness_name_override:
                active_runner = self.runner
            else:
                from clauditor._harnesses import construct_harness

                override_harness = construct_harness(harness_name_override)
                active_runner = SkillRunner(
                    project_dir=self.runner.project_dir,
                    timeout=self.runner.timeout,
                    harness=override_harness,
                )
        else:
            active_runner = self.runner

        result = active_runner.run(
            self.skill_name,
            run_args,
            cwd=effective_cwd,
            allow_hang_heuristic=allow_hang_heuristic,
            timeout=effective_timeout,
            env=effective_env,
            system_prompt=effective_system_prompt,
        )

        # US-003 of #154 (DEC-008): stamp the resolved provenance label
        # into ``SkillResult.harness_metadata`` so the context-sidecar
        # writer can read it without re-running the resolver.
        # ``harness_metadata`` is the additive forward-compat surface
        # introduced by DEC-007 of #148; merging here is correct
        # because each call returns its own ``SkillResult`` (the
        # underlying ``InvokeResult`` is constructed fresh per harness
        # invocation, never shared across runs).
        result.harness_metadata["system_prompt_source"] = system_prompt_source

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
            if not result.succeeded_cleanly:
                # Prefer an explicit ``error`` string; fall back to the
                # interactive-hang warning when that's the only signal
                # (US-003 sets ``error_category="interactive"`` without
                # setting ``error``). Else keep the generic fallback for
                # defensive "should not happen" cases. Per DEC-006 /
                # DEC-010 of ``plans/super/63-runner-error-surfacing.md``.
                if result.error:
                    msg = result.error
                elif result.error_category == "interactive":
                    msg = next(
                        (
                            w
                            for w in result.warnings
                            if w.startswith("interactive-hang:")
                        ),
                        "interactive hang detected",
                    )
                else:
                    msg = "Unknown error"
                return AssertionSet(
                    results=[_failed_run_result(self.skill_name, msg)]
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
