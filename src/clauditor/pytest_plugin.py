"""Pytest plugin for clauditor.

Registered via entry_points in pyproject.toml.
Provides fixtures and markers for testing Claude Code skills.

Usage in tests:
    def test_my_skill(clauditor_runner, clauditor_asserter):
        result = clauditor_runner.run("my-skill", "--depth quick")
        asserter = clauditor_asserter(result)
        asserter.assert_contains("Expected Section")
        asserter.assert_has_entries(minimum=3)

    def test_with_spec(clauditor_spec):
        spec = clauditor_spec(".claude/commands/my-skill.md")
        results = spec.evaluate()
        assert results.passed, results.summary()
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clauditor._harnesses._claude_code import env_without_api_key
from clauditor.asserters import SkillAsserter
from clauditor.cli import _harness_choice, _provider_choice
from clauditor.runner import SkillResult, SkillRunner
from clauditor.spec import SkillSpec


def _fixture_allow_cli() -> bool:
    """Return True when ``CLAUDITOR_FIXTURE_ALLOW_CLI`` opts into CLI transport.

    DEC-009 of ``plans/super/86-claude-cli-transport.md``: the three
    grading fixtures (``clauditor_grader``, ``clauditor_triggers``,
    ``clauditor_blind_compare``) default to stricter-than-CLI semantics
    (require ``ANTHROPIC_API_KEY``) so a CI run under subscription-only
    auth surfaces a config regression rather than silently falling back
    to the CLI transport. Users who deliberately want fixtures to
    exercise the ``claude`` subprocess transport set this env var to
    any non-empty, non-falsy value (``"1"``, ``"true"``, ``"yes"``).
    """
    value = os.environ.get("CLAUDITOR_FIXTURE_ALLOW_CLI")
    if value is None:
        return False
    return value.strip().lower() not in ("", "0", "false", "no")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("clauditor", "Claude Code skill testing")
    group.addoption(
        "--clauditor-project-dir",
        default=None,
        help="Project directory containing .claude/commands/ (default: cwd)",
    )
    group.addoption(
        "--clauditor-timeout",
        type=int,
        default=300,
        help="Timeout for skill execution in seconds (default: 300)",
    )
    group.addoption(
        "--clauditor-no-api-key",
        action="store_true",
        default=False,
        help=(
            "Strip ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN from the "
            "subprocess environment so `claude -p` uses cached "
            "subscription auth (~/.claude/) instead of env-based API auth"
        ),
    )
    group.addoption(
        "--clauditor-claude-bin",
        default="claude",
        help="Path to claude CLI binary (default: claude)",
    )
    group.addoption(
        "--clauditor-grade",
        action="store_true",
        default=False,
        help="Enable Layer 3 LLM-graded quality tests (requires API key, costs money)",
    )
    group.addoption(
        "--clauditor-model",
        default=None,
        help="Override grading model for Layer 3 tests (default: claude-sonnet-4-6)",
    )
    group.addoption(
        "--clauditor-harness",
        type=_harness_choice,
        default=None,
        help=(
            "Override the harness used by clauditor fixtures "
            "({claude-code, codex, auto}). Operator-intent precedence: "
            "factory kwarg > pytest CLI > CLAUDITOR_HARNESS env > "
            "EvalSpec.harness > default 'auto'. See DEC-008 of "
            "plans/super/155-pytest-fixtures-parametrize.md."
        ),
    )
    group.addoption(
        "--clauditor-grading-provider",
        type=_provider_choice,
        default=None,
        help=(
            "Override the grading provider used by clauditor fixtures "
            "({anthropic, openai, auto}). Operator-intent precedence: "
            "factory kwarg > pytest CLI > CLAUDITOR_GRADING_PROVIDER "
            "env > EvalSpec.grading_provider > default 'auto'. See "
            "DEC-008 of plans/super/155-pytest-fixtures-parametrize.md."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "clauditor_grade: mark test as requiring Layer 3 LLM grading "
        "(skipped without --clauditor-grade)",
    )
    config.addinivalue_line(
        "markers",
        "network: real HTTP; deselect with -m 'not network'",
    )
    config.addinivalue_line(
        "markers",
        "slow: slow-running tests; deselect with -m 'not slow'",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if not config.getoption("--clauditor-grade"):
        skip_grade = pytest.mark.skip(
            reason="need --clauditor-grade to run Layer 3 tests"
        )
        for item in items:
            if "clauditor_grade" in item.keywords:
                item.add_marker(skip_grade)


@pytest.fixture
def clauditor_runner(request: pytest.FixtureRequest):
    """Factory fixture returning a configured ``SkillRunner``.

    DEC-002 / DEC-003 / DEC-004 / DEC-005 / DEC-007 of
    ``plans/super/155-pytest-fixtures-parametrize.md``. The pre-#155
    value-fixture form (``def test(clauditor_runner): runner.run(...)``)
    is replaced by a factory; callers MUST invoke ``clauditor_runner()``
    to get the runner. This is a hard break — see CHANGELOG (US-007)
    for migration notes.

    Harness resolution composes three of the four CLI precedence layers
    (the fourth, ``EvalSpec.harness``, is meaningless at the runner
    seam since no spec is loaded here):

    1. Factory ``harness=`` kwarg (operator intent at the call site).
    2. ``--clauditor-harness`` pytest option (operator intent at
       session start).
    3. ``CLAUDITOR_HARNESS`` env var.
    4. Default ``"auto"`` — PATH lookup picks ``claude`` then
       ``codex`` per
       :func:`clauditor._providers.resolve_harness`.

    When the resolved harness is ``"codex"`` an eager
    :func:`clauditor._providers.check_codex_auth` fires (DEC-005);
    missing auth raises :class:`CodexAuthMissingError` (a sibling of
    :class:`Exception`, NOT a subclass of
    :class:`AnthropicAuthMissingError`) which propagates as a pytest
    setup failure rather than a silent ``pytest.skip`` so a CI
    misconfig surfaces loudly.

    Usage::

        def test_my_skill(clauditor_runner, clauditor_asserter):
            runner = clauditor_runner()           # default harness
            # or:
            runner = clauditor_runner(harness="codex")  # pin to codex
            result = runner.run("my-skill")
    """
    import os

    from clauditor._harnesses import construct_harness
    from clauditor._providers import check_codex_auth, resolve_harness

    def _factory(harness: str | None = None) -> SkillRunner:
        # Precedence: factory kwarg > pytest option > env > default.
        cli_override = harness
        if cli_override is None:
            cli_override = request.config.getoption("--clauditor-harness")
        env_value = os.environ.get("CLAUDITOR_HARNESS")
        if env_value is not None and env_value.strip() == "":
            env_value = None

        # No EvalSpec at the runner seam — pass spec_value=None.
        name, _auto_resolved_to = resolve_harness(
            cli_override, env_value, None
        )

        # DEC-005: eager codex auth guard. Surfaces as
        # CodexAuthMissingError (sibling of Exception per DEC-008) so
        # callers route on a structural ``except`` ladder.
        if name == "codex":
            check_codex_auth("runner")

        project_dir = request.config.getoption("--clauditor-project-dir")
        timeout = request.config.getoption("--clauditor-timeout")
        claude_bin = request.config.getoption("--clauditor-claude-bin")

        # When the resolved harness is ``"claude-code"`` (the default
        # SkillRunner shape), do NOT pass an explicit ``harness=`` —
        # SkillRunner's __init__ constructs a ClaudeCodeHarness using
        # the configured ``--clauditor-claude-bin``. Passing a
        # construct_harness("claude-code") instance here would lose
        # that custom binary path (CodeRabbit-style same-harness skip
        # mirroring the clauditor_spec wrapper logic).
        if name == "claude-code":
            return SkillRunner(
                project_dir=project_dir,
                timeout=timeout,
                claude_bin=claude_bin,
            )
        return SkillRunner(
            project_dir=project_dir,
            timeout=timeout,
            claude_bin=claude_bin,
            harness=construct_harness(name),
        )

    return _factory


@pytest.fixture
def clauditor_asserter():
    """Factory fixture wrapping a ``SkillResult`` in a ``SkillAsserter``.

    Usage::

        def test_my_skill(clauditor_runner, clauditor_asserter):
            result = clauditor_runner.run("my-skill")
            asserter = clauditor_asserter(result)
            asserter.assert_contains("Expected")
    """

    def _factory(result: SkillResult) -> SkillAsserter:
        return SkillAsserter(result)

    return _factory


@pytest.fixture
def clauditor_spec(request: pytest.FixtureRequest, tmp_path: Path):
    """Fixture factory for loading SkillSpecs.

    Usage:
        def test_skill(clauditor_spec):
            spec = clauditor_spec(".claude/commands/my-skill.md")

    When the loaded spec declares non-empty ``eval_spec.input_files``, the
    returned ``SkillSpec`` has its ``.run`` method transparently wrapped so
    that calls without an explicit ``run_dir`` use a stable subdirectory
    under pytest's ``tmp_path``. This causes the declared input files to be
    staged automatically. Specs with no ``input_files`` are returned
    unmodified (zero behavior change).
    """
    runner = SkillRunner(
        project_dir=request.config.getoption("--clauditor-project-dir"),
        timeout=request.config.getoption("--clauditor-timeout"),
        claude_bin=request.config.getoption("--clauditor-claude-bin"),
    )
    # DEC-006 (US-007): when ``--clauditor-no-api-key`` is set, compute
    # the env dict once per fixture call and thread it as
    # ``env_override`` through ``SkillSpec.run``. ``None`` otherwise —
    # the spec's ``env_override`` kwarg default preserves today's
    # behavior.
    no_api_key = request.config.getoption("--clauditor-no-api-key")
    fixture_env_override = env_without_api_key() if no_api_key else None

    def _factory(skill_path: str | Path, eval_path: str | Path | None = None):
        spec = SkillSpec.from_file(skill_path, eval_path=eval_path, runner=runner)
        has_input_files = (
            spec.eval_spec is not None and bool(spec.eval_spec.input_files)
        )
        # US-007 (#151) / DEC-005: when ``eval_spec.harness`` is set to
        # a concrete value (``"claude-code"`` or ``"codex"``), thread it
        # to ``SkillSpec.run`` as ``harness_name_override`` so the
        # spec-author preference is honored automatically — no new
        # fixture kwarg per DEC-005. ``"auto"`` (and unset) leaves the
        # override as ``None`` so ``SkillSpec.run`` falls through to
        # ``self.runner`` (the resolver's auto path is the CLI seam's
        # responsibility, not the fixture's).
        #
        # Same-harness skip (CodeRabbit review feedback on PR #166):
        # when the spec's harness already matches the runner's current
        # harness (e.g. spec sets ``harness="claude-code"`` and the
        # fixture's base ``runner`` is ``ClaudeCodeHarness`` with a
        # custom ``--clauditor-claude-bin``), do NOT request an override
        # — ``SkillSpec.run`` would otherwise reconstruct a fresh runner
        # via ``construct_harness("claude-code")`` and lose the fixture's
        # configured Claude binary. ``SkillSpec.run`` itself also has
        # a same-harness skip layer; this fixture-side check is
        # defense-in-depth and avoids the unnecessary override-path
        # entry in the closure below.
        spec_harness_override: str | None = None
        if (
            spec.eval_spec is not None
            and spec.eval_spec.harness != "auto"
        ):
            current_harness_name = getattr(
                getattr(runner, "harness", None), "name", None
            )
            if spec.eval_spec.harness != current_harness_name:
                spec_harness_override = spec.eval_spec.harness
        if (
            has_input_files
            or fixture_env_override is not None
            or spec_harness_override is not None
        ):
            original_run = spec.run
            default_run_dir = tmp_path / f"clauditor_run_{id(spec)}"

            def _run_with_overrides(
                args: str | None = None,
                *,
                run_dir: Path | None = None,
                env_override: dict[str, str] | None = None,
                timeout_override: int | None = None,
                harness_name_override: str | None = None,
            ):
                effective_run_dir = run_dir
                if has_input_files and effective_run_dir is None:
                    default_run_dir.mkdir(parents=True, exist_ok=True)
                    effective_run_dir = default_run_dir
                # Caller-provided overrides win over the fixture-level
                # default computed from ``--clauditor-no-api-key``;
                # otherwise the fixture value is used. Keeping the
                # pre-US-007 call shape means existing tests that don't
                # pass either override see ``original_run(args,
                # run_dir=effective_run_dir)`` verbatim.
                effective_env = (
                    env_override
                    if env_override is not None
                    else fixture_env_override
                )
                # US-007 (#151) / DEC-005: caller-provided
                # ``harness_name_override=`` (operator intent) wins over
                # the spec-field default (author intent), mirroring the
                # ``env_override`` / ``timeout_override`` precedence.
                effective_harness = (
                    harness_name_override
                    if harness_name_override is not None
                    else spec_harness_override
                )
                if (
                    effective_env is not None
                    or timeout_override is not None
                    or effective_harness is not None
                ):
                    return original_run(
                        args,
                        run_dir=effective_run_dir,
                        env_override=effective_env,
                        timeout_override=timeout_override,
                        harness_name_override=effective_harness,
                    )
                return original_run(args, run_dir=effective_run_dir)

            spec.run = _run_with_overrides  # type: ignore[method-assign]
        return spec

    return _factory


def _resolve_fixture_provider(
    eval_spec,
    model_override: str | None = None,
    *,
    provider_override: str | None = None,
) -> str:
    """Pure resolver for the active provider in fixture-land.

    Mirrors the inner branch of :func:`_dispatch_fixture_auth_guard`
    so the orchestrator-call sites in the three grading fixtures
    (``clauditor_grader``, ``clauditor_blind_compare``,
    ``clauditor_triggers``) can pass the resolved value through to
    the orchestrator's ``provider=`` kwarg per #146 US-006.

    ``model_override`` threads the ``--clauditor-model`` pytest option
    (operator intent) into auto-inference per
    ``.claude/rules/spec-cli-precedence.md`` operator > author. Without
    it, ``pytest --clauditor-model gpt-5.4`` on a default spec would
    silently route as Anthropic and then send an OpenAI model through
    the Anthropic backend (CodeRabbit finding on PR #164).

    ``provider_override`` carries the operator-intent provider value
    (factory kwarg or ``--clauditor-grading-provider`` pytest option,
    whichever was set, with the kwarg winning at the call site per
    DEC-007 of ``plans/super/155-pytest-fixtures-parametrize.md``).
    Threads to ``resolve_grading_provider`` as ``cli_override`` —
    the highest-precedence layer.

    Returns the resolved provider; does NOT swallow ``ValueError``.
    Fail-fast on resolver failure (e.g. ``grading_provider="auto"``
    with an unknown model prefix) per DEC-003 so a misconfigured spec
    or CLI override surfaces as a pytest test-setup error instead of
    silently routing through the wrong provider.
    """
    import os

    from clauditor._providers import resolve_grading_provider

    if eval_spec is None:
        # When ``provider_override`` is set even without an eval_spec,
        # honor it (operator intent always wins). Otherwise preserve
        # the pre-#146 fallback to ``"anthropic"``.
        if provider_override is not None:
            return resolve_grading_provider(
                provider_override, None, None, model_override
            )
        return "anthropic"

    env_value = os.environ.get("CLAUDITOR_GRADING_PROVIDER")
    if env_value is not None and env_value.strip() == "":
        env_value = None
    spec_value = getattr(eval_spec, "grading_provider", None)
    # Operator intent (--clauditor-model) wins over author intent
    # (eval_spec.grading_model) for auto-inference per
    # .claude/rules/spec-cli-precedence.md.
    model = model_override or getattr(eval_spec, "grading_model", None)
    return resolve_grading_provider(
        provider_override, env_value, spec_value, model
    )


def _dispatch_fixture_auth_guard(
    eval_spec,
    fixture_name: str,
    model_override: str | None = None,
    *,
    provider_override: str | None = None,
) -> None:
    """Pre-flight auth guard for the three grading fixtures.

    DEC-006 of ``plans/super/146-grading-provider-precedence.md``:
    each grading fixture (``clauditor_grader``,
    ``clauditor_blind_compare``, ``clauditor_triggers``) resolves the
    active provider via :func:`clauditor.cli._resolve_grading_provider`
    (passing ``args=None`` since pytest fixtures have no argparse
    namespace) and routes the auth guard per provider:

    - ``provider="anthropic"`` → strict-vs-relaxed split via
      ``CLAUDITOR_FIXTURE_ALLOW_CLI`` (preserved from #86 DEC-009):
      strict-by-default uses :func:`check_api_key_only`, opt-in
      relaxed uses :func:`check_any_auth_available`. The opt-in
      branch is intentional — fixtures stay stricter than the CLI
      by default so a CI run under subscription-only auth surfaces
      a config regression instead of silently passing.
    - ``provider="openai"`` → always strict via
      :func:`check_openai_auth`. OpenAI has no CLI-fallback /
      subscription concept (per #145 DEC-002), so the
      strict-vs-relaxed split has no analogue here.

    Per ``.claude/rules/multi-provider-dispatch.md`` the two providers
    raise distinct exception classes (``AnthropicAuthMissingError`` /
    ``OpenAIAuthMissingError``), letting fixture callers route on a
    structural ``except`` ladder rather than substring-matching on
    error text.
    """
    import os

    from clauditor._providers import (
        check_any_auth_available,
        check_api_key_only,
        check_openai_auth,
        resolve_grading_provider,
    )

    # When ``eval_spec`` is ``None`` (e.g. an in-flight spec load
    # failure we do not control here), preserve pre-#146 behavior and
    # use the Anthropic strict-vs-relaxed guard. The wrapping fixture
    # raises its own ``ValueError`` for the missing-spec case.
    #
    # If ``provider_override`` is set explicitly (operator intent),
    # honor it and route through the pure resolver so the override
    # can take effect even without an eval_spec. Per DEC-007 of #155.
    if eval_spec is None:
        if provider_override is not None:
            provider = resolve_grading_provider(
                provider_override, None, None, model_override
            )
            if provider == "openai":
                check_openai_auth(fixture_name)
                return
            # Anthropic falls through to the strict-vs-relaxed split.
        if _fixture_allow_cli():
            check_any_auth_available(fixture_name)
        else:
            check_api_key_only(fixture_name)
        return

    # Resolve via the pure helper directly (NOT the CLI wrapper) so a
    # bogus ``eval_spec.grading_model`` does not become ``SystemExit(2)``
    # mid-test — the CLI wrapper escalates ``ValueError`` to a CLI
    # exit-code routing that has no analogue in fixture-land. Per
    # CodeRabbit finding on PR #164, this helper does NOT swallow
    # ``ValueError`` — fail-fast on misconfigured spec / CLI override
    # so the test setup phase surfaces the problem instead of
    # silently routing through the wrong provider.
    env_value = os.environ.get("CLAUDITOR_GRADING_PROVIDER")
    if env_value is not None and env_value.strip() == "":
        env_value = None
    spec_value = getattr(eval_spec, "grading_provider", None)
    # Operator intent (--clauditor-model) wins over author intent
    # (eval_spec.grading_model) for auto-inference per
    # .claude/rules/spec-cli-precedence.md.
    model = model_override or getattr(eval_spec, "grading_model", None)
    # ``provider_override`` (operator intent: factory kwarg or pytest
    # CLI option) is the highest-precedence layer per DEC-007 of #155.
    provider = resolve_grading_provider(
        provider_override, env_value, spec_value, model
    )

    if provider == "anthropic":
        if _fixture_allow_cli():
            check_any_auth_available(fixture_name)
        else:
            check_api_key_only(fixture_name)
        return
    if provider == "openai":
        check_openai_auth(fixture_name)
        return
    # ``_resolve_grading_provider`` only returns ``"anthropic"`` or
    # ``"openai"`` — defensive branch for forward-compat additions.
    raise ValueError(
        f"_dispatch_fixture_auth_guard: unknown provider {provider!r} — "
        "expected 'anthropic' or 'openai'"
    )


@pytest.fixture
def clauditor_grader(request: pytest.FixtureRequest, clauditor_spec):
    """Fixture factory for quality grading. Returns a callable that grades a skill.

    Factory kwargs (US-003 of #155):

    - ``provider``: override the grading provider for this call.
      Sits at the top of the operator-intent precedence stack
      (kwarg > ``--clauditor-grading-provider`` > env > spec) per
      DEC-007 of ``plans/super/155-pytest-fixtures-parametrize.md``.
    - ``model``: override the grading model for this call. Wins over
      ``--clauditor-model`` pytest option.
    """
    import asyncio

    from clauditor.quality_grader import grade_quality

    pytest_model_option = request.config.getoption("--clauditor-model")
    pytest_provider_option = request.config.getoption(
        "--clauditor-grading-provider"
    )

    def _factory(
        skill_path: str | Path,
        eval_path: str | Path | None = None,
        output: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ):
        # DEC-006 (#146 US-007) + #162 US-001: load the spec FIRST so
        # the auth-guard dispatch can read ``eval_spec.grading_provider``
        # / ``grading_model`` and pick the right provider auth. Pre-#162
        # fixtures hardcoded Anthropic auth, so an OpenAI-graded skill
        # saw a misleading ``"ANTHROPIC_API_KEY missing"`` error when
        # only ``OPENAI_API_KEY`` was set. The factored-out
        # ``_dispatch_fixture_auth_guard`` (per #146 US-007) honors the
        # full four-layer resolution (CLAUDITOR_GRADING_PROVIDER env >
        # eval_spec.grading_provider > auto-inference from
        # grading_model). For the Anthropic branch,
        # ``CLAUDITOR_FIXTURE_ALLOW_CLI=1`` opts into the relaxed guard
        # (DEC-009 of #86). DEC-004 of #162: that env var is silently
        # no-op when provider resolves to OpenAI (no CLI transport).
        # Distinct ``except`` branches per
        # ``.claude/rules/multi-provider-dispatch.md``.
        #
        # US-003 of #155 (DEC-007): operator-intent precedence —
        # factory kwarg > pytest CLI option. The kwarg wins at the
        # call site; either feeds into ``provider_override`` /
        # ``model_override`` below, which then thread through the
        # helpers as ``cli_override`` to the pure resolver.
        provider_override = provider or pytest_provider_option
        model_override = model or pytest_model_option

        spec = clauditor_spec(skill_path, eval_path)
        # Validate spec shape BEFORE the auth dispatch (CodeRabbit
        # finding on PR #163): otherwise a missing/invalid auth key
        # would mask the more useful ``"No eval spec found..."``
        # error for users whose underlying problem is a missing
        # eval.json, sending them to debug their auth instead.
        if spec.eval_spec is None:
            raise ValueError(f"No eval spec found for {skill_path}")
        _dispatch_fixture_auth_guard(
            spec.eval_spec,
            "grader",
            model_override,
            provider_override=provider_override,
        )
        resolved_provider = _resolve_fixture_provider(
            spec.eval_spec,
            model_override,
            provider_override=provider_override,
        )
        # Provider-aware model defaulting (QG pass 1): explicit CLI
        # override > spec.grading_model > per-provider default. Avoids
        # passing an Anthropic-default model into an OpenAI-graded
        # spec.
        from clauditor._providers import resolve_grading_model
        resolved_model = (
            model_override
            or spec.eval_spec.grading_model
            or resolve_grading_model(spec.eval_spec, resolved_provider)
        )
        if output is None:
            result = spec.run()
            output = result.output
            harness = result.harness
        elif (
            spec.eval_spec is not None
            and spec.eval_spec.harness not in ("auto", None)
        ):
            harness = spec.eval_spec.harness
        else:
            harness = "claude-code"
        return asyncio.run(
            grade_quality(
                output,
                spec.eval_spec,
                resolved_model,
                provider=resolved_provider,
                harness=harness,
            )
        )

    return _factory


@pytest.fixture(scope="session")
def clauditor_capture(request: pytest.FixtureRequest):
    """Fixture factory returning a Path to a captured skill output file.

    Usage:
        def test_my_skill(clauditor_capture):
            path = clauditor_capture("find-restaurants")
            output = path.read_text()  # raises FileNotFoundError if missing

    Default location: ``tests/eval/captured/<skill_name>.txt`` resolved
    relative to the pytest rootdir. Pass ``base_dir`` to override.
    The fixture does NOT run capture or skip on missing files — a missing
    file is the test's problem (DEC-006).
    """
    rootdir = Path(str(request.config.rootdir))

    def _factory(
        skill_name: str, base_dir: str | Path | None = None
    ) -> Path:
        if base_dir is None:
            base = rootdir / "tests" / "eval" / "captured"
        else:
            base = Path(base_dir)
        return base / f"{skill_name}.txt"

    return _factory


@pytest.fixture
def clauditor_blind_compare(request: pytest.FixtureRequest, clauditor_spec):
    """Fixture factory for blind A/B comparison of two skill outputs.

    Returns a callable that loads a ``SkillSpec`` from ``skill_path`` and
    runs :func:`clauditor.quality_grader.blind_compare_from_spec` on the
    two caller-supplied output strings. The fixture does NOT read files —
    outputs must be passed as strings. Raises ``ValueError`` if the spec
    lacks an eval spec or ``user_prompt`` is empty; the exception is
    propagated untouched so tests can assert on it.

    Factory kwargs (US-004 of #155):

    - ``provider``: override the grading provider for this call.
      Sits at the top of the operator-intent precedence stack
      (kwarg > ``--clauditor-grading-provider`` > env > spec) per
      DEC-007 of ``plans/super/155-pytest-fixtures-parametrize.md``.
    - ``model``: override the grading model for this call. Wins over
      ``--clauditor-model``.

    Model precedence (highest → lowest):

    1. Explicit ``model=`` kwarg on this factory call.
    2. ``--clauditor-model`` pytest CLI option (matches
       ``clauditor_grader`` / ``clauditor_triggers``).
    3. ``spec.eval_spec.grading_model`` (resolved inside
       :func:`blind_compare_from_spec`).
    """
    import asyncio

    from clauditor.quality_grader import BlindReport, blind_compare_from_spec

    pytest_model_option = request.config.getoption("--clauditor-model")
    pytest_provider_option = request.config.getoption(
        "--clauditor-grading-provider"
    )

    def _factory(
        skill_path: str | Path,
        output_a: str,
        output_b: str,
        eval_path: str | Path | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> BlindReport:
        # DEC-006 (#146 US-007) + #162 US-001: load spec first so the
        # dispatch can read ``eval_spec.grading_provider`` /
        # ``grading_model``. See :func:`clauditor_grader` for rationale.
        # The guard still fires before any SDK call. CodeRabbit finding
        # (PR #163): validate spec shape BEFORE the auth dispatch so a
        # missing/invalid auth key does not mask the more useful
        # ``ValueError`` raised by ``blind_compare_from_spec`` when
        # ``eval_spec`` / ``user_prompt`` are absent.
        spec = clauditor_spec(skill_path, eval_path)
        if spec.eval_spec is None:
            raise ValueError(f"No eval spec found for {skill_path}")
        # US-004 of #155 (DEC-007): operator-intent precedence —
        # factory kwarg > pytest CLI option. The kwarg wins at the
        # call site; either feeds into ``provider_override`` /
        # ``model_override`` below, which then thread through the
        # helpers as ``cli_override`` to the pure resolver.
        provider_override = provider or pytest_provider_option
        effective_model = model or pytest_model_option
        # Per CodeRabbit finding on PR #164: thread the operator-intent
        # model (``model=`` kwarg or ``--clauditor-model``) into auth
        # dispatch + provider resolution so an OpenAI model on a
        # default spec routes to OpenAI auth, not Anthropic.
        _dispatch_fixture_auth_guard(
            spec.eval_spec,
            "blind_compare",
            effective_model,
            provider_override=provider_override,
        )
        resolved_provider = _resolve_fixture_provider(
            spec.eval_spec,
            effective_model,
            provider_override=provider_override,
        )
        return asyncio.run(
            blind_compare_from_spec(
                spec,
                output_a,
                output_b,
                model=effective_model,
                provider=resolved_provider,
            )
        )

    return _factory


@pytest.fixture
def clauditor_triggers(request: pytest.FixtureRequest, clauditor_spec):
    """Fixture factory for trigger precision testing."""
    import asyncio

    from clauditor.triggers import test_triggers as run_triggers

    model_override = request.config.getoption("--clauditor-model")

    def _factory(
        skill_path: str | Path, eval_path: str | Path | None = None
    ):
        # DEC-006 (#146 US-007) + #162 US-001: load spec first so the
        # dispatch can read ``eval_spec.grading_provider`` /
        # ``grading_model``. See :func:`clauditor_grader` for rationale.
        # The guard still fires before any SDK call.
        spec = clauditor_spec(skill_path, eval_path)
        # Validate spec shape BEFORE the auth dispatch (CodeRabbit
        # finding on PR #163): a missing/invalid auth key would
        # otherwise mask the more useful ``"No eval spec found..."``
        # error for users whose underlying problem is a missing
        # eval.json.
        if spec.eval_spec is None:
            raise ValueError(f"No eval spec found for {skill_path}")
        _dispatch_fixture_auth_guard(
            spec.eval_spec, "triggers", model_override
        )
        provider = _resolve_fixture_provider(
            spec.eval_spec, model_override
        )
        # Provider-aware model defaulting (QG pass 1).
        from clauditor._providers import resolve_grading_model
        model = (
            model_override
            or spec.eval_spec.grading_model
            or resolve_grading_model(spec.eval_spec, provider)
        )
        return asyncio.run(run_triggers(spec.eval_spec, model, provider=provider))

    return _factory
