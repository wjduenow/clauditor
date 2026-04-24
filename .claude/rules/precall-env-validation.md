# Rule: Pre-call environment validation for LLM-mediated CLI commands

When an LLM-mediated CLI command needs an environment variable
(`ANTHROPIC_API_KEY`, a future proxy cert path, a Vertex project ID,
a rate-limit budget) to even attempt its side-effectful call, put the
presence check in a **pure helper co-located with the SDK seam**,
raise a **distinct exception class** the CLI catches to map to a
specific exit code, and call the helper from each CLI command
**AFTER** the `--dry-run` early-return and **BEFORE** any
`call_anthropic` / orchestrator invocation. Do NOT reuse
`AnthropicHelperError` for pre-call env misconfig — that class is
already routed to exit 3 (real API failure) and conflating the two
makes the CLI's exit-code routing substring-matched rather than
structural. Do NOT let the SDK's own error propagate as a raw
traceback; operators should see an actionable message naming the
missing env var, why it is required, and the concrete next step.

## The pattern

### Layer 1 — domain exception + pure helper, co-located with the SDK seam

The exception class lives in the same module as `call_anthropic`
(`src/clauditor/_anthropic.py`) so the auth-concern surface is one
module, not scattered. The class subclasses `Exception` directly
(NOT `AnthropicHelperError`) so a CLI `except` ladder routes it to
a distinct exit code structurally:

```python
# src/clauditor/_anthropic.py — sibling to call_anthropic.
class AnthropicAuthMissingError(Exception):
    """Raised by :func:`check_anthropic_auth` when ``ANTHROPIC_API_KEY`` is missing.

    Distinct from :class:`AnthropicHelperError` by design: the CLI
    layer routes ``AnthropicAuthMissingError`` to exit 2 (pre-call
    input-validation error per
    ``.claude/rules/llm-cli-exit-code-taxonomy.md``), while
    ``AnthropicHelperError`` is routed to exit 3 (actual API
    failure). Reusing the helper-error class would conflate those
    exit codes and make the routing a string-match hack instead of
    a structural ``except`` ladder.
    """


_AUTH_MISSING_TEMPLATE = (
    "ERROR: ANTHROPIC_API_KEY is not set.\n"
    "clauditor {cmd_name} calls the Anthropic API directly and needs an API\n"
    "key — a Claude Pro/Max subscription alone does not grant API access.\n"
    "Get a key at https://console.anthropic.com/, then export\n"
    "ANTHROPIC_API_KEY=... and re-run. Subscription support via claude -p\n"
    "is tracked in #86.\n"
    "Commands that don't need a key: validate, capture, run, lint, init,\n"
    "badge, audit, trend."
)


def check_anthropic_auth(cmd_name: str) -> None:
    """Pre-flight guard: raise if ``ANTHROPIC_API_KEY`` is missing.

    Pure function per ``.claude/rules/pure-compute-vs-io-split.md``:
    reads ``os.environ`` only; does NOT print to stderr, does NOT
    call ``sys.exit``, does NOT log. The CLI wrapper catches
    :class:`AnthropicAuthMissingError` and maps it to ``return 2`` +
    stderr surfacing.
    """
    value = os.environ.get("ANTHROPIC_API_KEY")
    if value is None or value.strip() == "":
        raise AnthropicAuthMissingError(
            _AUTH_MISSING_TEMPLATE.format(cmd_name=cmd_name)
        )
    return None
```

### Layer 2 — CLI call site: AFTER dry-run, BEFORE any API spend

Every LLM-mediated command follows the same shape. Guard lands
after the `--dry-run` early-return (dry-run is a cost-free preview
— no API call, no key needed) and before any `call_anthropic` or
orchestrator invocation:

```python
# src/clauditor/cli/grade.py (and propose_eval, suggest, triggers,
# extract, compare --blind — the six guarded seams today).
def cmd_grade(args) -> int:
    # ... arg validation, spec load, dry-run early-return ...
    if args.dry_run:
        print(build_grading_prompt(spec.eval_spec))
        return 0

    # Fail fast if ANTHROPIC_API_KEY is missing. Guard lands AFTER
    # --dry-run and BEFORE allocate_iteration / call_anthropic so we
    # do not leave an abandoned iteration-N-tmp/ staging dir behind
    # when the guard fires, and we do not hit the SDK with a missing
    # key (which raises a raw TypeError from deep in client init).
    try:
        check_anthropic_auth("grade")
    except AnthropicAuthMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # ... staging + call_anthropic below ...
```

### Layer 3 — pytest fixtures raise the same exception

Fixture factories that wrap the same async orchestrator call
`check_anthropic_auth(<fixture-name>)` at the factory-invocation
seam and let the exception propagate as a pytest setup failure
(NOT `pytest.skip` — a silent skip in CI under subscription-only
auth hides a test-config regression; a hard error surfaces it).
Same class as the CLI uses, so users see one error shape
regardless of where they hit it:

```python
# src/clauditor/pytest_plugin.py
@pytest.fixture
def clauditor_grader(request, clauditor_spec):
    from clauditor._anthropic import check_anthropic_auth
    from clauditor.quality_grader import grade_quality

    def _factory(skill_path, eval_path=None, output=None):
        # Pre-flight auth guard at factory-invocation time. Raises
        # ``AnthropicAuthMissingError`` (same class the CLI catches)
        # when ``ANTHROPIC_API_KEY`` is missing, so a CI run under
        # subscription-only auth surfaces a clear error instead of
        # silently skipping.
        check_anthropic_auth("grader")
        # ... spec.run + grade_quality ...
    return _factory
```

### Layer 4 — defense-in-depth at the SDK seam

Even with the pre-flight guard, `call_anthropic` itself wraps the
SDK's `TypeError` (raised from `AsyncAnthropic()` construction and
from `messages.create()` when no auth is configured) as an
`AnthropicHelperError` with a **fixed sanitized message** — no
`str(exc)`, no `exc.args`, no SDK-sourced text. Any future caller
that bypasses the guard (a new orchestrator, a script that forgets
the pre-flight) sees a crisp error instead of a raw traceback:

```python
# src/clauditor/_anthropic.py::call_anthropic
try:
    client = AsyncAnthropic()
except TypeError as exc:
    raise AnthropicHelperError(
        "Anthropic SDK client initialization failed — "
        "verify ANTHROPIC_API_KEY is set."
    ) from exc

# ... and again around messages.create(...) ...
```

## Why each piece matters

- **Distinct exception class per exit-code category.** The CLI's
  `except AnthropicAuthMissingError: return 2` vs
  `except AnthropicHelperError: return 3` routing is **structural**
  — a reader can see, at a glance, which category a failure belongs
  to without knowing the error message contents. Reusing
  `AnthropicHelperError` for pre-call env misconfig would force the
  CLI to either (a) substring-match the message ("if 'API_KEY' in
  str(exc)") or (b) sniff some attribute to disambiguate. Both
  collapse the exit-2-vs-exit-3 distinction that
  `.claude/rules/llm-cli-exit-code-taxonomy.md` depends on. The new
  class keeps routing at the `except` level where it belongs.
- **Co-location with the SDK seam.** The helper sits next to
  `call_anthropic` in `_anthropic.py`, not in `cli/__init__.py` or
  a new `env_check.py`. Anyone reading the SDK seam sees the guard
  in the same file; anyone adding a new SDK-backed entry point will
  grep `_anthropic.py` for the helper and wire it in. Scattering
  the guard to the CLI package makes it easy to miss when a future
  non-CLI caller (a pytest fixture, a batch-runner script) needs
  the same protection.
- **Pure helper + CLI wrapper (pure/IO split).** `check_anthropic_auth`
  reads `os.environ` and raises — no stderr, no `sys.exit`, no
  logging. The CLI wrapper emits stderr and returns the exit code.
  This honors `.claude/rules/pure-compute-vs-io-split.md`: the
  helper is trivially unit-testable (`monkeypatch.delenv`, assert
  `pytest.raises`), the CLI integration is a narrow `try/except`,
  and the pytest fixture inherits the same check without
  re-implementing the env read.
- **Guard AFTER `--dry-run` early-return.** `--dry-run` prints the
  prompt without any API call; requiring an API key for the preview
  would be a hostile UX regression. Order is load-bearing: if the
  guard fires before the dry-run check, the user who wants to see
  what the prompt looks like (to iterate on their eval spec) is
  blocked even though no API spend would happen.
- **Guard BEFORE allocate-workspace / API call.** Firing the guard
  at the earliest safe point (right after arg validation +
  dry-run) avoids leaving an abandoned staging dir
  (`iteration-N-tmp/`), avoids burning API tokens on a retry loop
  that will never succeed, and gives the operator an immediate
  actionable error. Delaying the check until the SDK's own
  `TypeError` fires produces a confusing multi-line traceback from
  deep inside the `anthropic` package.
- **Single message template with `{cmd_name}` interpolation.** One
  template interpolated at raise-time (`clauditor {cmd_name}`)
  names the specific invocation so users know exactly which command
  triggered the guard. Five hand-tailored per-command strings would
  drift stylistically; a single template with one substitution
  point stays DRY and produces consistent output.
- **Three durable substrings, not a byte-identical assertion.**
  Tests pin three load-bearing anchors (`"ANTHROPIC_API_KEY"`,
  `"Claude Pro"`, `"console.anthropic.com"`) — the env-var name,
  the product name, the concrete next step. Stylistic copy edits
  (typo fixes, a reworded clause, a forward-pointer issue number
  renumber) do not churn tests. A verbatim assertion would force a
  test change on every prose polish.
- **Fixture raises, not skips.** Pytest-fixture callers that hit
  the guard see the **same exception class** the CLI catches, not
  a `pytest.skip`. A CI pipeline that runs under subscription-only
  auth with a silent skip would hide the configuration regression
  ("our test suite skipped all LLM tests because no key was set");
  a hard error surfaces the gap immediately. If a caller later
  needs skip semantics, `try/except AnthropicAuthMissingError:
  pytest.skip(...)` at the test-function level is a one-line
  change.
- **Defense-in-depth `TypeError` wrap inside `call_anthropic`.**
  Even with the pre-flight guard removing the primary path, the
  SDK's client-init and `messages.create` sites both wrap
  `TypeError` as a sanitized `AnthropicHelperError`. A new caller
  that forgets the pre-flight (a new CLI command, a script, a
  future orchestrator) still sees a crisp error instead of a raw
  traceback. **Sanitized message only**: no `str(exc)`, no
  `exc.args`, no SDK-sourced text — defense-in-depth against
  future SDK versions that might include partial auth state in
  diagnostics. Original exception preserved on `__cause__` via
  `raise ... from exc` for debugging.

## What NOT to do

- Do NOT reuse `AnthropicHelperError` for the pre-call env check.
  That class is routed to exit 3 (API failure); conflating it with
  exit 2 (input validation) breaks the taxonomy.
- Do NOT put the guard in an argparse `type=` validator. Env-var
  presence is not an arg-value check; it doesn't fit the argparse
  contract, and argparse's auto-exit-2 on validation failure fires
  BEFORE the `--dry-run` check, blocking the preview path.
- Do NOT let the guard emit stderr itself. The helper stays pure;
  stderr belongs to the CLI wrapper. Tests for the helper use
  `pytest.raises`, not `capsys`.
- Do NOT fire the guard before `--dry-run`. Dry-run is a cost-free
  preview; requiring auth for a preview is a hostile regression.
- Do NOT fire the guard after workspace allocation / API call.
  Late failures leave abandoned staging dirs and confuse operators
  about whether an API round-trip happened.
- Do NOT skip the defense-in-depth `TypeError` wrap in
  `call_anthropic`. A future caller that bypasses the guard (a
  new orchestrator, a migration script, a REPL session) must still
  see a clean error, not a raw SDK traceback. The wrap costs
  ~4 lines and catches the whole class of "forgot the guard" bug.
- Do NOT surface the SDK's `TypeError` text in the user-facing
  message. A fixed sanitized string is defense-in-depth against
  hypothetical future SDK versions that include partial auth state
  in diagnostics. Preserve the original on `__cause__` via
  `raise ... from exc` for debuggers.
- Do NOT forget to add the pytest fixtures. Three fixtures wrap
  `grade_quality`, `blind_compare`, and `test_triggers` today; a
  future fixture that wraps any `call_anthropic` caller needs the
  same guard or CI under subscription-only auth surfaces the
  problem as a multi-line SDK traceback from deep inside an
  orphaned async task.

## Canonical implementation

Helper + exception class:

- `src/clauditor/_anthropic.py::AnthropicAuthMissingError` — domain
  exception class, subclass of `Exception`, sibling of
  `AnthropicHelperError`.
- `src/clauditor/_anthropic.py::check_anthropic_auth` — pure
  helper, reads `os.environ["ANTHROPIC_API_KEY"]`, raises on
  absent/empty/whitespace-only. Single template
  (`_AUTH_MISSING_TEMPLATE`) with `{cmd_name}` interpolation.
- `src/clauditor/_anthropic.py::call_anthropic` — defense-in-depth
  `except TypeError` branches around `AsyncAnthropic()` construction
  and `messages.create()`, both raising
  `AnthropicHelperError("Anthropic SDK client initialization failed
  — verify ANTHROPIC_API_KEY is set.") from exc`.

CLI call sites (six guarded commands today — `compare --blind`
added in QG pass 2 after initial enumeration of five):

- `src/clauditor/cli/grade.py::cmd_grade` — after `--dry-run`,
  before `allocate_iteration()`. `check_anthropic_auth("grade")`.
- `src/clauditor/cli/propose_eval.py::_cmd_propose_eval_impl` —
  after `--dry-run`. `check_anthropic_auth("propose-eval")`.
- `src/clauditor/cli/suggest.py::_cmd_suggest_impl` — after
  zero-signal early-exit (no `--dry-run` on this command).
  `check_anthropic_auth("suggest")`.
- `src/clauditor/cli/triggers.py::cmd_triggers` — after
  `--dry-run`. `check_anthropic_auth("triggers")`.
- `src/clauditor/cli/extract.py::cmd_extract` — after `--dry-run`.
  `check_anthropic_auth("extract")`.
- `src/clauditor/cli/compare.py::_run_blind_compare` — after
  `validate_blind_compare_spec`, before `blind_compare_from_spec`.
  `check_anthropic_auth("compare --blind")`.

Pytest fixtures (three, all raise the same
`AnthropicAuthMissingError` at factory-invocation time):

- `src/clauditor/pytest_plugin.py::clauditor_grader` —
  `check_anthropic_auth("grader")`.
- `src/clauditor/pytest_plugin.py::clauditor_blind_compare` —
  `check_anthropic_auth("blind_compare")`.
- `src/clauditor/pytest_plugin.py::clauditor_triggers` —
  `check_anthropic_auth("triggers")`.

Regression tests verify eight commands (`validate`, `capture`,
`run`, `lint`, `init`, `badge`, `audit`, `trend`) still work with
`ANTHROPIC_API_KEY` unset — they do not route through
`call_anthropic`, so the guard must not have been wired into them
by accident.

Traces to DEC-001, DEC-002, DEC-004, DEC-008, DEC-010, DEC-011,
DEC-012, DEC-013, DEC-015, DEC-017 of
`plans/super/83-subscription-auth-gap.md`.

## Companion rules

- `.claude/rules/llm-cli-exit-code-taxonomy.md` — codifies the
  exit-2-vs-exit-3 category split this rule structurally preserves.
  That rule governs the table of exit codes and the "distinct
  report fields" shape for async orchestrators that "never raise";
  this rule governs how to surface a pre-call env misconfig at
  exit 2 via a **synchronous** exception class upstream of any
  orchestrator. The two compose cleanly — an LLM-mediated CLI
  command that wraps a single Anthropic call follows both.
- `.claude/rules/pure-compute-vs-io-split.md` — codifies the
  pure-helper + thin-wrapper shape. `check_anthropic_auth` is a
  direct application: pure env read, caller owns stderr + exit
  code.
- `.claude/rules/centralized-sdk-call.md` — codifies the single
  `call_anthropic` seam every Anthropic call routes through. This
  rule extends the seam with a **pre-call** guard; the defense-in-
  depth `TypeError` wrap is an additive fix inside the existing
  centralized helper, not a new call surface.
- `.claude/rules/pre-llm-contract-hard-validate.md` — same spirit
  ("fail loudly at the earliest safe moment with a specific
  actionable error"), applied to LLM-output invariants rather than
  pre-call env. The two rules rhyme: each enforces a structural
  invariant at a hard boundary, with the validator as the source of
  truth rather than a prompt assertion or an SDK-side fallback.

## When this rule applies

Any future env-var precondition for an LLM-mediated clauditor CLI
command. Plausible future callers:

- A Vertex / Bedrock / proxy endpoint env var (`ANTHROPIC_VERTEX_PROJECT_ID`,
  `ANTHROPIC_BEDROCK_REGION`, a CA cert path) where the SDK would
  otherwise fail deep with an opaque error.
- A rate-limit budget env var (`CLAUDITOR_API_BUDGET_USD`) that the
  CLI must refuse to exceed — the pre-call budget check is the
  same shape (pure helper, distinct exception class, exit-2
  routing).
- A required model-override env var (`CLAUDITOR_GRADING_MODEL`) for
  a command that has no default model (if we ever remove defaults).
- An auth-source-selector env var that gates on the presence of
  one of multiple alternatives (`ANTHROPIC_API_KEY` OR
  `ANTHROPIC_AUTH_TOKEN` OR a credentials file path — widen the
  helper's check, keep the exception class).

The rule also applies retroactively: any existing CLI command that
hits `call_anthropic` without a pre-call env guard is a latent
foot-gun — operators will see a raw SDK traceback when they first
run the command under a misconfigured env. Wire the guard in the
next time the command is touched.

## When this rule does NOT apply

- Non-LLM-mediated commands (`validate`, `capture`, `run`, `lint`,
  `init`, `badge`, `audit`, `trend`). They do not route through
  `call_anthropic`; they do not need an API key; wiring the guard
  would give a false "this command needs a key" error.
- Commands whose env-var precondition is enforced elsewhere in the
  stack with equivalent crispness (e.g. a subprocess that itself
  prints a clean actionable error on missing env — no need for a
  parent-side pre-flight duplicate).
- One-off diagnostic scripts in `scripts/` that hit the SDK
  directly. They can rely on the SDK's own error path; operators
  running an ad-hoc script are expected to know about their env.
- Tests that mock `call_anthropic` entirely. The mock substitutes
  for the real SDK; no env check is needed. Tests that exercise
  the guard itself set `ANTHROPIC_API_KEY` via `monkeypatch.setenv`
  (or `delenv` for the raise-path) per standard test discipline.
