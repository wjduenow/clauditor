# Rule: Multi-provider dispatch via `check_provider_auth` + `eval_spec.grading_provider`

When an LLM-mediated CLI command supports more than one model
provider (today: `"anthropic"` and `"openai"`; future: `"vertex"`,
`"bedrock"`, …), resolve the active provider from
`eval_spec.grading_provider` (defaulting to `"anthropic"` when
unset) and route the pre-call auth guard through the centralized
`check_provider_auth(provider, cmd_name)` dispatcher in
`clauditor._providers._auth`. The dispatcher delegates to the
per-provider helper (`check_any_auth_available` for Anthropic,
`check_openai_auth` for OpenAI); each helper raises a **distinct**
exception class so the CLI's `except` ladder remains structural —
one branch per provider, one exit code per branch — per
`.claude/rules/llm-cli-exit-code-taxonomy.md`. Adding a future
provider is one new branch in the dispatcher and one new
auth-missing exception class; no CLI command needs to learn about
it.

## The pattern

### Layer 1 — spec field carries the provider selection

```python
# schemas.py
@dataclass
class EvalSpec:
    # ... other fields ...
    # ``None`` (default) preserves pre-#145 behavior: caller treats
    # ``None`` as ``"anthropic"``. When set, must be one of
    # ``"anthropic"`` / ``"openai"``. Validated at load time.
    grading_provider: str | None = None
```

### Layer 2 — CLI seam resolves provider, dispatches auth guard

Five LLM-mediated commands share the same shape (grade, extract,
triggers, compare --blind, propose-eval). Each resolves the
provider from `eval_spec.grading_provider`, falls back to
`"anthropic"`, and runs `check_provider_auth(provider, cmd_name)`
with two distinct `except` branches:

```python
# cli/grade.py (and extract, triggers, compare, propose_eval).
from clauditor._providers import (
    AnthropicAuthMissingError,
    OpenAIAuthMissingError,
    check_provider_auth,
)

def cmd_grade(args) -> int:
    # ... arg validation, spec load, dry-run early-return ...

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

    # ... allocate workspace + call_model below ...
```

### Layer 3 — dispatcher routes by provider

```python
# _providers/_auth.py
def check_provider_auth(provider: str, cmd_name: str) -> None:
    if provider == "anthropic":
        check_any_auth_available(cmd_name)   # raises AnthropicAuthMissingError
        return None
    if provider == "openai":
        check_openai_auth(cmd_name)          # raises OpenAIAuthMissingError
        return None
    raise ValueError(
        f"check_provider_auth: unknown provider {provider!r} — "
        "expected 'anthropic' or 'openai'"
    )
```

## Why this shape

- **One dispatcher seam, N provider branches.** A future
  `provider="vertex"` is one new `if provider == "vertex"` branch
  in `check_provider_auth` plus one new `VertexAuthMissingError`
  class. No CLI command needs editing — they already pass the
  resolved provider through the dispatcher and catch all known
  auth-missing exceptions. This is the linear-extensibility
  property the centralized dispatcher buys.
- **Distinct exception classes per provider.** A common ancestor
  (`AuthMissingError`) would defeat the structural-routing
  invariant: every `except AnthropicAuthMissingError` ladder
  exists *because* readers can tell at a glance which provider
  failed and what exit code to map to. Catching a parent class
  collapses two distinct categories into one branch and forces
  a substring match on the message to recover the discriminator.
  Per `.claude/rules/llm-cli-exit-code-taxonomy.md` the routing
  must stay structural.
- **`None` as "use the default" sentinel.** Pre-#145 specs have
  no `grading_provider` field; treating `None` as `"anthropic"`
  preserves byte-identical behavior for those specs. Authors who
  want OpenAI grading add `"grading_provider": "openai"` to
  their `eval.json`.
- **Resolution at the CLI seam, not inside the orchestrator.**
  The five orchestrators (`grade_quality`, `extract_and_grade`,
  `extract_and_report`, `blind_compare`, `test_triggers_async`)
  receive the resolved provider as a parameter and pass it to
  `call_model(provider=...)`. Resolving at the CLI seam keeps
  the orchestrator pure (no `eval_spec` lookups) and makes the
  pre-call auth guard the only place where the provider string
  branches into per-provider control flow.
- **Dispatcher is pure per `.claude/rules/pure-compute-vs-io-split.md`.**
  `check_provider_auth` reads `os.environ` only via its
  delegates; raises on missing auth; emits no stderr; calls no
  `sys.exit`. The CLI wrapper owns stderr and exit-code mapping.
  Tests construct dispatcher cases with `pytest.raises(...)`,
  not `capsys`.
- **Spec field validated at load time.** `EvalSpec.from_dict`
  rejects `grading_provider` values outside the literal
  `{"anthropic", "openai"}` set with a `ValueError`. A typo
  (`"openi"`) fails at `EvalSpec.from_file`, exit 2 — not at
  the dispatcher's `ValueError` mid-run. Per
  `.claude/rules/constant-with-type-info.md`, `bool` values
  are rejected explicitly even though `True` is `int` /
  `str`-coercible.

## What NOT to do

- Do NOT substring-match on error messages to identify the
  provider. The exception class IS the discriminator; reaching
  into `str(exc)` for `"OPENAI"` or `"ANTHROPIC"` is the
  anti-pattern this rule structurally prevents.
- Do NOT hardcode `check_any_auth_available("grade")` (or any
  other Anthropic-only helper) in a new LLM-mediated CLI command.
  Always go through `check_provider_auth(provider, cmd_name)`
  with both `except` branches present, even if the command's
  initial caller graph only exercises one provider — the
  forward-compat shape is cheap and prevents the next provider's
  rollout from touching every command.
- Do NOT subclass `OpenAIAuthMissingError` from
  `AnthropicAuthMissingError` (or vice versa) for code reuse.
  The two classes are siblings of `Exception` by design; a
  shared ancestor would let a stale `except
  AnthropicAuthMissingError` ladder catch the OpenAI case and
  print an Anthropic-flavored error message.
- Do NOT default `grading_provider` to a string at the
  dataclass level (e.g. `grading_provider: str = "anthropic"`).
  The `None` sentinel is what lets `from_dict` distinguish
  "author did not write this field" from "author wrote
  'anthropic' explicitly", and what makes pre-#145 specs round-
  trip without a synthetic field appearing in `to_dict` output.
- Do NOT resolve the provider inside the orchestrator (e.g.
  `grade_quality` doing its own `eval_spec.grading_provider or
  "anthropic"`). The CLI seam owns resolution; orchestrators
  receive the resolved string.

## Canonical implementation

Spec field: `src/clauditor/schemas.py::EvalSpec.grading_provider`
— `str | None` field, default `None`, validated at load time
against the literal set `{"anthropic", "openai"}` per DEC-003 of
`plans/super/145-openai-provider.md`.

Dispatcher + per-provider helpers (all in
`src/clauditor/_providers/_auth.py` per DEC-006 of
`plans/super/145-openai-provider.md`):

- `check_provider_auth(provider, cmd_name)` — public dispatcher.
  Branches on `provider`, delegates to per-provider helper,
  raises `ValueError` on unknown values.
- `check_any_auth_available(cmd_name)` — Anthropic relaxed
  guard (key OR CLI). Raises `AnthropicAuthMissingError`.
- `check_openai_auth(cmd_name)` — OpenAI strict guard (key
  only; no CLI fallback). Raises `OpenAIAuthMissingError`.
- `_openai_api_key_is_set()` — pure env-read helper for
  `OPENAI_API_KEY` (whitespace-only counts as absent, mirroring
  `_api_key_is_set` for `ANTHROPIC_API_KEY`).

Auth-missing exception classes (defined in
`src/clauditor/_providers/__init__.py` so the class-identity
invariant holds across re-exports per
`.claude/rules/back-compat-shim-discipline.md`):

- `AnthropicAuthMissingError` — direct subclass of `Exception`.
- `OpenAIAuthMissingError` — direct subclass of `Exception`,
  NOT of `AnthropicAuthMissingError`.

CLI call sites (five LLM-mediated commands using the
provider-aware guard; `suggest` is a sixth LLM-mediated command
but routes through `check_any_auth_available` directly because
its prompt builder has no `eval_spec` to read):

- `src/clauditor/cli/grade.py::cmd_grade` — resolves provider
  from `spec.eval_spec.grading_provider`; calls
  `check_provider_auth(provider, "grade")`.
- `src/clauditor/cli/extract.py::cmd_extract` — same shape;
  `check_provider_auth(provider, "extract")`.
- `src/clauditor/cli/triggers.py::cmd_triggers` — same shape;
  `check_provider_auth(provider, "triggers")`.
- `src/clauditor/cli/compare.py::_run_blind_compare` — reads
  `skill_spec.eval_spec.grading_provider`;
  `check_provider_auth(provider, "compare --blind")`.
- `src/clauditor/cli/propose_eval.py::_cmd_propose_eval_impl` —
  proposer is the eval-creation step itself, so there is no
  `eval_spec` to read; hardcodes
  `check_provider_auth("anthropic", "propose-eval")`. The
  `OpenAIAuthMissingError` `except` branch is forward-compat
  for a future `--proposer-provider` flag.

Traces to DEC-003 and DEC-006 of
`plans/super/145-openai-provider.md`. Companion rules:
`.claude/rules/centralized-sdk-call.md` (the dispatcher seam
this auth guard sits in front of),
`.claude/rules/precall-env-validation.md` (the per-provider
auth-missing-exception shape),
`.claude/rules/llm-cli-exit-code-taxonomy.md` (the structural
exit-code routing the distinct exception classes preserve), and
`.claude/rules/spec-cli-precedence.md` (the future four-layer
precedence resolver for `grading_provider` lands in #146).

## When this rule applies

Any future LLM-mediated CLI command that constructs a
`call_model(provider=..., ...)` call. The shape is uniform:

1. Resolve `provider = eval_spec.grading_provider or "anthropic"`
   at the CLI seam (or hardcode for commands that have no
   `eval_spec`).
2. Call `check_provider_auth(provider, cmd_name)`.
3. Catch each provider's auth-missing class in a distinct
   `except` branch, print to stderr, return exit 2.
4. Pass `provider` through to the orchestrator so the
   `call_model` call uses the same value.

The rule also applies retroactively when a new provider lands:
the new provider's auth-missing class must be added to the
`except` ladder of every existing LLM-mediated CLI command in
the same change, so a stale ladder cannot silently miss the new
class.

## When this rule does NOT apply

- LLM-mediated CLI commands that have no `eval_spec` to read
  AND no per-provider semantics to express (today: none).
  A command that genuinely only ever runs against one provider
  can still use `check_provider_auth("<provider>", cmd_name)`
  for uniformity, but a direct call to the per-provider helper
  is acceptable.
- Non-LLM CLI commands (`validate`, `capture`, `run`, `lint`,
  `init`, `badge`, `audit`, `trend`). They do not call
  `call_model` and need no auth guard.
- Pytest fixtures. The three grader fixtures
  (`clauditor_grader`, `clauditor_blind_compare`,
  `clauditor_triggers`) currently route through
  `check_api_key_only` / `check_any_auth_available` directly
  rather than `check_provider_auth`; per-provider fixture
  dispatch is forward-compat work for a future ticket once
  fixtures grow OpenAI-graded test cases.
- One-off diagnostic scripts in `scripts/` that hit a provider
  SDK directly. They can rely on the SDK's own error path.
