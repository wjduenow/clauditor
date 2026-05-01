# Rule: Pre-call environment validation for LLM-mediated CLI commands

When an LLM-mediated CLI command needs an environment variable
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, a future proxy cert path, a
Vertex project ID, a rate-limit budget) to even attempt its
side-effectful call, put the presence check in a **pure helper
co-located with the SDK seam**, raise a **distinct exception class
per provider** the CLI catches to map to a specific exit code, and
call the helper from each CLI command **AFTER** the `--dry-run`
early-return and **BEFORE** any `call_model` / orchestrator
invocation. Do NOT reuse `AnthropicHelperError` / `OpenAIHelperError`
for pre-call env misconfig — those classes are already routed to
exit 3 (real API failure) and conflating them with the auth-missing
classes makes the CLI's exit-code routing substring-matched rather
than structural. Do NOT let the SDK's own error propagate as a raw
traceback; operators should see an actionable message naming the
missing env var, why it is required, and the concrete next step.

For multi-provider commands (today: 5 LLM-mediated CLI commands —
`grade`, `extract`, `triggers`, `compare --blind`, `propose-eval`),
route through the shared `check_provider_auth(provider, cmd_name)`
dispatcher in `clauditor._providers._auth` — see the companion rule
`.claude/rules/multi-provider-dispatch.md`.

## The pattern

### Layer 1 — domain exception classes + pure helpers, co-located with the SDK seam

The exception classes live in `src/clauditor/_providers/__init__.py`
so the auth-concern surface is one package and the **class-identity
invariant** holds across re-exports (per
`.claude/rules/back-compat-shim-discipline.md` Pattern 2): every
`except <Provider>AuthMissingError` ladder catches the same class
object regardless of which module imported it. Each class
subclasses `Exception` directly (NOT a helper-error class, NOT a
shared parent) so a CLI `except` ladder routes each provider's
auth-missing case to a distinct branch structurally:

```python
# src/clauditor/_providers/__init__.py — sibling auth-missing classes.
class AnthropicAuthMissingError(Exception):
    """Raised when no usable Anthropic auth path is available.

    Distinct from :class:`AnthropicHelperError` by design: the CLI
    layer routes ``AnthropicAuthMissingError`` to exit 2 (pre-call
    input-validation error per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``), while
    ``AnthropicHelperError`` is routed to exit 3 (actual API
    failure). Reusing the helper-error class would conflate those
    exit codes and make the routing a string-match hack instead of
    a structural ``except`` ladder.
    """


class OpenAIAuthMissingError(Exception):
    """Raised when ``OPENAI_API_KEY`` is missing for the OpenAI provider.

    Subclass of :class:`Exception` directly, NOT of
    :class:`AnthropicAuthMissingError` or any helper-error class —
    a common ancestor would defeat the structural-routing
    invariant every CLI dispatcher depends on.
    """
```

The pure helpers + message templates live in
`src/clauditor/_providers/_auth.py`:

```python
# src/clauditor/_providers/_auth.py — auth helpers shared across providers.

# Anthropic relaxed guard (key OR claude CLI on PATH); strict
# variant ``check_api_key_only`` is used by pytest fixtures.
_AUTH_MISSING_TEMPLATE = (
    "ERROR: No usable authentication found.\n"
    "clauditor {cmd_name} needs either:\n"
    "  1. ANTHROPIC_API_KEY exported (API key from "
    "https://console.anthropic.com/), OR\n"
    "  2. claude CLI installed and authenticated (Claude Pro/Max "
    "subscription)\n"
    "Commands that don't need authentication: validate, capture, run, "
    "lint, init,\n"
    "badge, audit, trend."
)


def _api_key_is_set() -> bool:
    value = os.environ.get("ANTHROPIC_API_KEY")
    return value is not None and value.strip() != ""


def check_any_auth_available(cmd_name: str) -> None:
    """Anthropic relaxed guard — raise only when no auth path exists."""
    if _api_key_is_set() or _claude_cli_is_available():
        return None
    raise AnthropicAuthMissingError(
        _AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
    )


# OpenAI strict guard — no CLI fallback, no Pro/Max subscription
# concept. Mirrors check_api_key_only's shape on the Anthropic side.
_OPENAI_AUTH_MISSING_TEMPLATE = (
    "ERROR: OPENAI_API_KEY is not set.\n"
    "clauditor {cmd_name} calls the OpenAI API directly and needs an API\n"
    "key. Get a key at https://platform.openai.com/api-keys, then export\n"
    "OPENAI_API_KEY=... and re-run.\n"
    "Commands that don't need a key: validate, capture, run, lint, init,\n"
    "badge, audit, trend."
)


def _openai_api_key_is_set() -> bool:
    value = os.environ.get("OPENAI_API_KEY")
    return value is not None and value.strip() != ""


def check_openai_auth(cmd_name: str) -> None:
    """OpenAI strict guard — raise if ``OPENAI_API_KEY`` is missing."""
    if _openai_api_key_is_set():
        return None
    raise OpenAIAuthMissingError(
        _OPENAI_AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
    )


def check_provider_auth(provider: str, cmd_name: str) -> None:
    """Public dispatcher routing pre-flight auth guards by provider."""
    if provider == "anthropic":
        check_any_auth_available(cmd_name)
        return None
    if provider == "openai":
        check_openai_auth(cmd_name)
        return None
    raise ValueError(
        f"check_provider_auth: unknown provider {provider!r} — "
        "expected 'anthropic' or 'openai'"
    )
```

### Layer 2 — CLI call site: AFTER dry-run, BEFORE any API spend

Every LLM-mediated command follows the same shape. Guard lands
after the `--dry-run` early-return (dry-run is a cost-free preview
— no API call, no key needed) and before any `call_model` /
orchestrator invocation. Multi-provider commands resolve
`provider = eval_spec.grading_provider or "anthropic"` and route
through `check_provider_auth(provider, cmd_name)` with **distinct
`except` branches per provider** so each auth-missing class maps
to its own exit-2 routing (see
`.claude/rules/multi-provider-dispatch.md`):

```python
# src/clauditor/cli/grade.py (and extract, triggers, compare --blind,
# propose-eval — the five provider-aware seams today).
from clauditor._providers import (
    AnthropicAuthMissingError,
    OpenAIAuthMissingError,
    check_provider_auth,
)

def cmd_grade(args) -> int:
    # ... arg validation, spec load, dry-run early-return ...
    if args.dry_run:
        print(build_grading_prompt(spec.eval_spec))
        return 0

    # Fail fast if the resolved provider's required auth is missing.
    # Guard lands AFTER --dry-run and BEFORE allocate_iteration /
    # call_model so we do not leave an abandoned iteration-N-tmp/
    # staging dir behind when the guard fires, and we do not hit the
    # SDK with a missing key (which raises a raw TypeError /
    # OpenAIError from deep in client init).
    provider = (
        spec.eval_spec.grading_provider
        if spec.eval_spec is not None
        and spec.eval_spec.grading_provider is not None
        else "anthropic"
    )
    try:
        check_provider_auth(provider, "grade")
    except AnthropicAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OpenAIAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # ... staging + call_model below ...
```

The `suggest` command is a sixth LLM-mediated CLI seam but is
single-provider today (no `eval_spec` to read at the call site);
it calls `check_any_auth_available("suggest")` directly and
catches `AnthropicAuthMissingError` only.

### Layer 3 — pytest fixtures raise the same exceptions

Fixture factories that wrap the same async orchestrator call
the appropriate auth helper at the factory-invocation seam and
let the exception propagate as a pytest setup failure (NOT
`pytest.skip` — a silent skip in CI under subscription-only auth
hides a test-config regression; a hard error surfaces it). Same
classes as the CLI uses, so users see one error shape regardless
of where they hit it:

```python
# src/clauditor/pytest_plugin.py
@pytest.fixture
def clauditor_grader(request, clauditor_spec):
    from clauditor._providers import (
        check_any_auth_available,
        check_api_key_only,
    )
    from clauditor.quality_grader import grade_quality

    def _factory(skill_path, eval_path=None, output=None):
        # Pre-flight auth guard at factory-invocation time. Raises
        # ``AnthropicAuthMissingError`` or ``OpenAIAuthMissingError``
        # (same classes the CLI catches) so a CI run under
        # subscription-only auth surfaces a clear error instead of
        # silently skipping. Post-#162 the fixtures resolve the
        # provider from the loaded spec and dispatch through
        # ``check_provider_auth(provider, "grader")`` for non-Anthropic
        # providers; the Anthropic branch retains the
        # ``CLAUDITOR_FIXTURE_ALLOW_CLI`` opt-in for relaxed-vs-strict
        # auth (env var is silently no-op when provider="openai" per
        # DEC-004 of #162 — OpenAI has no CLI transport).
        provider = (
            spec.eval_spec.grading_provider
            if spec.eval_spec is not None
            and spec.eval_spec.grading_provider is not None
            else "anthropic"
        )
        if provider == "anthropic":
            if os.environ.get("CLAUDITOR_FIXTURE_ALLOW_CLI") == "1":
                check_any_auth_available("grader")
            else:
                check_api_key_only("grader")
        else:
            check_provider_auth(provider, "grader")
        # ... spec.run + grade_quality ...
    return _factory
```

### Layer 4 — defense-in-depth at each provider's SDK seam

Even with the pre-flight guard, each provider's `call_*` helper
wraps the SDK's construction failure as the corresponding
`<Provider>HelperError` with a **fixed sanitized message** — no
`str(exc)`, no `exc.args`, no SDK-sourced text. Any future caller
that bypasses the guard (a new orchestrator, a script that forgets
the pre-flight) sees a crisp error instead of a raw traceback.
The two providers' wraps differ in **which exceptions** they
catch because the two SDKs fail at different points:

```python
# src/clauditor/_providers/_anthropic.py::call_anthropic
# Anthropic's AsyncAnthropic() does not validate auth at
# construction; the missing-key TypeError surfaces only when
# messages.create() runs. Wrap both sites with the same defense.
try:
    client = AsyncAnthropic()
except TypeError as exc:
    raise AnthropicHelperError(
        "Anthropic SDK client initialization failed — "
        "verify ANTHROPIC_API_KEY is set."
    ) from exc

# ... and again around messages.create(...) ...
```

```python
# src/clauditor/_providers/_openai.py::call_openai
# OpenAI's AsyncOpenAI() raises OpenAIError IMMEDIATELY at
# construction when OPENAI_API_KEY is missing (different shape
# from Anthropic's deferred TypeError). Catch the (TypeError,
# OpenAIError) tuple at the construction site only — the
# OpenAIError arm is the load-bearing one for missing-key, and
# TypeError covers any future SDK config-error path.
try:
    client = AsyncOpenAI()
except ImportError:
    raise   # un-wrapped per the install-hint contract
except (TypeError, OpenAIError) as exc:
    raise OpenAIHelperError(
        "OpenAI SDK client initialization failed — "
        "verify OPENAI_API_KEY is set."
    ) from exc
```

The asymmetry — Anthropic catches `TypeError` only, OpenAI
catches `(TypeError, OpenAIError)` — reflects **where** each SDK
raises on missing-auth, not a difference in defense philosophy.
Both wraps preserve the original on `__cause__` via
`raise ... from exc` so debuggers can introspect.

## Why each piece matters

- **Distinct exception classes per exit-code category AND per
  provider.** The CLI's `except AnthropicAuthMissingError: return 2`
  vs `except OpenAIAuthMissingError: return 2` vs
  `except AnthropicHelperError: return 3` vs
  `except OpenAIHelperError: return 3` routing is **structural**
  — a reader can see, at a glance, which category a failure
  belongs to without knowing the error message contents. Reusing
  any helper-error class for pre-call env misconfig would force
  the CLI to either (a) substring-match the message
  ("if 'API_KEY' in str(exc)") or (b) sniff some attribute to
  disambiguate. Both collapse the exit-2-vs-exit-3 distinction
  that `.claude/rules/llm-cli-exit-code-taxonomy.md` depends on.
  Subclassing `OpenAIAuthMissingError` from
  `AnthropicAuthMissingError` would equally collapse the
  per-provider distinction — both classes are siblings of
  `Exception` by design.
- **Co-location with the SDK seam.** The auth helpers sit next to
  the SDK callers in `_providers/_auth.py`, not in
  `cli/__init__.py` or a new `env_check.py`. Anyone reading the
  `_providers/` package sees the guard in the same package;
  anyone adding a new SDK-backed entry point will grep
  `_providers/_auth.py` for the helper and wire it in.
  Scattering the guard to the CLI package makes it easy to miss
  when a future non-CLI caller (a pytest fixture, a batch-runner
  script) needs the same protection.
- **Pure helpers + CLI wrapper (pure/IO split).**
  `check_any_auth_available`, `check_openai_auth`, and
  `check_provider_auth` all read `os.environ` (and probe PATH for
  the Anthropic-CLI branch) and raise — no stderr, no
  `sys.exit`, no logging. The CLI wrapper emits stderr and
  returns the exit code. This honors
  `.claude/rules/pure-compute-vs-io-split.md`: each helper is
  trivially unit-testable (`monkeypatch.delenv`, assert
  `pytest.raises`), the CLI integration is a narrow `try/except`,
  and the pytest fixture inherits the same check without
  re-implementing the env read.
- **Guard AFTER `--dry-run` early-return.** `--dry-run` prints
  the prompt without any API call; requiring an API key for the
  preview would be a hostile UX regression. Order is
  load-bearing: if the guard fires before the dry-run check, the
  user who wants to see what the prompt looks like (to iterate on
  their eval spec) is blocked even though no API spend would
  happen.
- **Guard BEFORE allocate-workspace / API call.** Firing the
  guard at the earliest safe point (right after arg validation +
  dry-run) avoids leaving an abandoned staging dir
  (`iteration-N-tmp/`), avoids burning API tokens on a retry loop
  that will never succeed, and gives the operator an immediate
  actionable error. Delaying the check until the SDK's own
  `TypeError` / `OpenAIError` fires produces a confusing
  multi-line traceback from deep inside the provider's package.
- **Single message template per provider with `{cmd_name}`
  interpolation.** Each provider has one template
  (`_AUTH_MISSING_TEMPLATE` for Anthropic relaxed,
  `_AUTH_MISSING_TEMPLATE_KEY_ONLY` for Anthropic strict,
  `_OPENAI_AUTH_MISSING_TEMPLATE` for OpenAI) interpolated at
  raise-time (`clauditor {cmd_name}`) so users know exactly
  which command triggered the guard. Five hand-tailored
  per-command strings would drift stylistically; one template
  per provider with one substitution point stays DRY and
  produces consistent output.
- **Durable substrings per provider, not byte-identical
  assertions.** Tests pin per-provider load-bearing anchors —
  Anthropic: `"ANTHROPIC_API_KEY"`, `"Claude Pro"`,
  `"console.anthropic.com"` (and `"claude CLI"` for the relaxed
  variant); OpenAI: `"OPENAI_API_KEY"`, `"platform.openai.com"`.
  Stylistic copy edits (typo fixes, a reworded clause, a
  forward-pointer issue number renumber) do not churn tests. A
  verbatim assertion would force a test change on every prose
  polish.
- **Fixture raises, not skips.** Pytest-fixture callers that hit
  the guard see the **same exception class** the CLI catches,
  not a `pytest.skip`. A CI pipeline that runs under
  subscription-only auth with a silent skip would hide the
  configuration regression ("our test suite skipped all LLM
  tests because no key was set"); a hard error surfaces the gap
  immediately.
- **Defense-in-depth provider-specific exception wraps inside
  each `call_*` helper.** Even with the pre-flight guard
  removing the primary path, each provider's SDK construction
  site wraps its own provider's missing-auth exception family
  as a sanitized `<Provider>HelperError`. **Sanitized message
  only**: no `str(exc)`, no `exc.args`, no SDK-sourced text —
  defense-in-depth against future SDK versions that might
  include partial auth state in diagnostics. Original exception
  preserved on `__cause__` via `raise ... from exc` for
  debugging.
- **Asymmetric `except` tuples reflect SDK behavior.**
  Anthropic raises `TypeError` at `messages.create()` time when
  `ANTHROPIC_API_KEY` is missing; OpenAI raises its base
  `OpenAIError` immediately at `AsyncOpenAI()` construction.
  Wrapping `(TypeError, OpenAIError)` on the OpenAI side
  catches the OpenAI-specific failure shape; wrapping
  `TypeError` only on the Anthropic side avoids
  over-catching unrelated `TypeError`s that the future SDK
  might raise for legitimate API misuse. The defense is shaped
  to each SDK, not blanket-applied.

## What NOT to do

- Do NOT reuse `AnthropicHelperError` / `OpenAIHelperError` for
  the pre-call env check. Those classes are routed to exit 3
  (API failure); conflating them with exit 2 (input validation)
  breaks the taxonomy.
- Do NOT subclass `OpenAIAuthMissingError` from
  `AnthropicAuthMissingError` (or invent a shared
  `AuthMissingError` parent). The two classes are siblings of
  `Exception` by design — a shared ancestor lets a stale
  `except AnthropicAuthMissingError` ladder catch the OpenAI
  case and print the wrong-flavored error message.
- Do NOT put the guard in an argparse `type=` validator. Env-var
  presence is not an arg-value check; it doesn't fit the
  argparse contract, and argparse's auto-exit-2 on validation
  failure fires BEFORE the `--dry-run` check, blocking the
  preview path.
- Do NOT let the guard emit stderr itself. The helpers stay
  pure; stderr belongs to the CLI wrapper. Tests for the
  helpers use `pytest.raises`, not `capsys`.
- Do NOT fire the guard before `--dry-run`. Dry-run is a
  cost-free preview; requiring auth for a preview is a hostile
  regression.
- Do NOT fire the guard after workspace allocation / API call.
  Late failures leave abandoned staging dirs and confuse
  operators about whether an API round-trip happened.
- Do NOT skip the defense-in-depth construction-error wrap in
  either `call_anthropic` or `call_openai`. A future caller that
  bypasses the guard (a new orchestrator, a migration script, a
  REPL session) must still see a clean error, not a raw SDK
  traceback. The wrap costs ~4 lines per provider and catches
  the whole class of "forgot the guard" bug.
- Do NOT collapse the asymmetric `except` tuples to a uniform
  shape across providers. Anthropic-`TypeError`-only and
  OpenAI-`(TypeError, OpenAIError)` are deliberate — the
  OpenAIError arm catches OpenAI's earlier-failing
  missing-auth shape that has no Anthropic equivalent.
- Do NOT surface either SDK's exception text in the
  user-facing message. A fixed sanitized string is
  defense-in-depth against hypothetical future SDK versions
  that include partial auth state in diagnostics. Preserve the
  original on `__cause__` via `raise ... from exc` for
  debuggers.
- Do NOT forget to add the pytest fixtures' guard. Three
  fixtures wrap `grade_quality`, `blind_compare`, and
  `test_triggers` today; a future fixture that wraps any
  `call_model` caller needs the same guard or CI under
  subscription-only auth surfaces the problem as a multi-line
  SDK traceback from deep inside an orphaned async task.

## Canonical implementation

Auth-missing exception classes:

- `src/clauditor/_providers/__init__.py::AnthropicAuthMissingError`
  — direct subclass of `Exception`, defined at the package
  root so the class-identity invariant holds across re-exports.
- `src/clauditor/_providers/__init__.py::OpenAIAuthMissingError`
  — direct subclass of `Exception`, NOT a subclass of
  `AnthropicAuthMissingError` or any helper-error class. Per
  DEC-006 of `plans/super/145-openai-provider.md`.

Helpers + templates (all in
`src/clauditor/_providers/_auth.py` per DEC-005 of
`plans/super/144-providers-call-model.md` and DEC-006 of
`plans/super/145-openai-provider.md`):

- `_AUTH_MISSING_TEMPLATE` — Anthropic relaxed-guard message
  template (key OR claude CLI). Four durable substrings.
- `_AUTH_MISSING_TEMPLATE_KEY_ONLY` — Anthropic strict variant
  used by pytest fixtures.
- `_OPENAI_AUTH_MISSING_TEMPLATE` — OpenAI strict-guard
  template. Two durable substrings (`OPENAI_API_KEY`,
  `platform.openai.com`).
- `_api_key_is_set` / `_openai_api_key_is_set` — pure env-read
  helpers; whitespace-only counts as absent for both providers.
- `check_any_auth_available(cmd_name)` — Anthropic relaxed
  guard. Raises `AnthropicAuthMissingError`.
- `check_api_key_only(cmd_name)` — Anthropic strict guard
  (pytest fixtures). Raises `AnthropicAuthMissingError`.
- `check_openai_auth(cmd_name)` — OpenAI strict guard. Raises
  `OpenAIAuthMissingError`.
- `check_provider_auth(provider, cmd_name)` — public dispatcher
  routing by `provider`. Branches to `check_any_auth_available`
  for `"anthropic"` and `check_openai_auth` for `"openai"`;
  raises `ValueError` on unknown values. See
  `.claude/rules/multi-provider-dispatch.md` for the call-site
  contract.

Defense-in-depth construction wraps:

- `src/clauditor/_providers/_anthropic.py::call_anthropic` —
  `except TypeError` branches around `AsyncAnthropic()`
  construction and `messages.create()`, both raising
  `AnthropicHelperError("Anthropic SDK client initialization
  failed — verify ANTHROPIC_API_KEY is set.") from exc`.
- `src/clauditor/_providers/_openai.py::call_openai` —
  `except (TypeError, OpenAIError)` branch around
  `AsyncOpenAI()` construction (the OpenAI-only failure point;
  no `responses.create()`-time wrap needed because the
  missing-key shape surfaces at construction).

CLI call sites — five LLM-mediated commands using the
provider-aware dispatcher
`check_provider_auth(provider, cmd_name)` with distinct
`except` branches per provider:

- `src/clauditor/cli/grade.py::cmd_grade` — after `--dry-run`,
  before `allocate_iteration()`.
  `check_provider_auth(provider, "grade")`.
- `src/clauditor/cli/extract.py::cmd_extract` — after
  `--dry-run`. `check_provider_auth(provider, "extract")`.
- `src/clauditor/cli/triggers.py::cmd_triggers` — after
  `--dry-run`. `check_provider_auth(provider, "triggers")`.
- `src/clauditor/cli/compare.py::_run_blind_compare` — after
  `validate_blind_compare_spec`, before
  `blind_compare_from_spec`.
  `check_provider_auth(provider, "compare --blind")`.
- `src/clauditor/cli/propose_eval.py::_cmd_propose_eval_impl`
  — after `--dry-run`. Hardcodes
  `check_provider_auth("anthropic", "propose-eval")` because
  the proposer is the eval-creation step itself (no
  `eval_spec` to read); the `OpenAIAuthMissingError`
  `except` branch is forward-compat for a future
  `--proposer-provider` flag.

Plus the single-provider seam:

- `src/clauditor/cli/suggest.py::_cmd_suggest_impl` — after
  zero-signal early-exit (no `--dry-run` on this command). Per
  US-003 of #162, loads `SkillSpec.from_file(args.skill)` at the
  CLI seam, resolves `provider = skill_spec.eval_spec.grading_provider
  or "anthropic"`, then calls `check_provider_auth(provider,
  "suggest")` with distinct `AnthropicAuthMissingError` and
  `OpenAIAuthMissingError` exit-2 branches. Plumbs `provider=`
  to `propose_edits(...)`. Mirrors the
  `cli/triggers.py:114-127` shape.

Pytest fixtures (three, post-#162 routing through
`check_provider_auth(provider, fixture_label)` for non-Anthropic
providers; Anthropic branch retains the
`CLAUDITOR_FIXTURE_ALLOW_CLI` opt-in toggle):

- `src/clauditor/pytest_plugin.py::clauditor_grader` —
  `check_any_auth_available` (relaxed, opt-in via
  `CLAUDITOR_FIXTURE_ALLOW_CLI=1`) or `check_api_key_only`
  (strict default).
- `src/clauditor/pytest_plugin.py::clauditor_blind_compare` —
  same shape.
- `src/clauditor/pytest_plugin.py::clauditor_triggers` —
  same shape.

Regression tests verify eight commands (`validate`, `capture`,
`run`, `lint`, `init`, `badge`, `audit`, `trend`) still work
with `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` unset — they do
not route through `call_model`, so neither guard must have
been wired into them by accident.

Traces to DEC-001, DEC-002, DEC-004, DEC-008, DEC-010, DEC-011,
DEC-012, DEC-013, DEC-015, DEC-017 of
`plans/super/83-subscription-auth-gap.md` and DEC-006 of
`plans/super/145-openai-provider.md`.

## Companion rules

- `.claude/rules/multi-provider-dispatch.md` — codifies the
  `check_provider_auth(provider, cmd_name)` dispatch pattern
  and the `eval_spec.grading_provider or "anthropic"`
  resolution at CLI seams. This rule defines the
  per-provider auth-missing exception shape; the multi-
  provider rule defines how the dispatcher composes them.
- `.claude/rules/llm-cli-exit-code-taxonomy.md` — codifies
  the exit-2-vs-exit-3 category split this rule structurally
  preserves. That rule governs the table of exit codes and
  the "distinct report fields" shape for async orchestrators
  that "never raise"; this rule governs how to surface a
  pre-call env misconfig at exit 2 via a **synchronous**
  exception class upstream of any orchestrator. The two
  compose cleanly — an LLM-mediated CLI command that wraps a
  single model call follows both.
- `.claude/rules/pure-compute-vs-io-split.md` — codifies the
  pure-helper + thin-wrapper shape. `check_any_auth_available`,
  `check_openai_auth`, and `check_provider_auth` are direct
  applications: pure env read, caller owns stderr + exit
  code.
- `.claude/rules/centralized-sdk-call.md` — codifies the
  single `call_model` / `call_anthropic` / `call_openai` seam
  every model call routes through. This rule extends the
  seam with a **pre-call** guard; the defense-in-depth
  construction-error wraps are additive fixes inside the
  centralized helpers, not new call surfaces.
- `.claude/rules/pre-llm-contract-hard-validate.md` — same
  spirit ("fail loudly at the earliest safe moment with a
  specific actionable error"), applied to LLM-output
  invariants rather than pre-call env. The two rules rhyme:
  each enforces a structural invariant at a hard boundary,
  with the validator as the source of truth rather than a
  prompt assertion or an SDK-side fallback.

## When this rule applies

Any future env-var precondition for an LLM-mediated clauditor
CLI command. Plausible future callers:

- A Vertex / Bedrock / proxy endpoint env var
  (`ANTHROPIC_VERTEX_PROJECT_ID`, `ANTHROPIC_BEDROCK_REGION`,
  a CA cert path) where the SDK would otherwise fail deep
  with an opaque error. Add a `<Provider>AuthMissingError`
  class, a per-provider helper, and a new branch in
  `check_provider_auth`.
- A rate-limit budget env var (`CLAUDITOR_API_BUDGET_USD`)
  that the CLI must refuse to exceed — the pre-call budget
  check is the same shape (pure helper, distinct exception
  class, exit-2 routing).
- A required model-override env var
  (`CLAUDITOR_GRADING_MODEL`) for a command that has no
  default model (if we ever remove defaults).
- An auth-source-selector env var that gates on the presence
  of one of multiple alternatives (`ANTHROPIC_API_KEY` OR
  `ANTHROPIC_AUTH_TOKEN` OR a credentials file path — widen
  the helper's check, keep the exception class).

The rule also applies retroactively: any existing CLI
command that hits `call_model` without a pre-call env guard
is a latent foot-gun — operators will see a raw SDK
traceback when they first run the command under a
misconfigured env. Wire the guard in the next time the
command is touched.

## When this rule does NOT apply

- Non-LLM-mediated commands (`validate`, `capture`, `run`,
  `lint`, `init`, `badge`, `audit`, `trend`). They do not
  route through `call_model`; they do not need an API key;
  wiring the guard would give a false "this command needs a
  key" error.
- Commands whose env-var precondition is enforced elsewhere
  in the stack with equivalent crispness (e.g. a subprocess
  that itself prints a clean actionable error on missing env
  — no need for a parent-side pre-flight duplicate).
- One-off diagnostic scripts in `scripts/` that hit the SDK
  directly. They can rely on the SDK's own error path;
  operators running an ad-hoc script are expected to know
  about their env.
- Tests that mock `call_model` / `call_anthropic` /
  `call_openai` entirely. The mock substitutes for the real
  SDK; no env check is needed. Tests that exercise the guards
  themselves set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` via
  `monkeypatch.setenv` (or `delenv` for the raise-path) per
  standard test discipline.
