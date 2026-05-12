# Rule: Multi-provider dispatch via `check_provider_auth` + `eval_spec.grading_provider`

When an LLM-mediated CLI command supports more than one model
provider (today: `"anthropic"` and `"openai"`; future: `"vertex"`,
`"bedrock"`, …), resolve the active provider via the four-layer
resolver `clauditor.cli._resolve_grading_provider(args, eval_spec)`
(CLI flag > `CLAUDITOR_GRADING_PROVIDER` env >
`EvalSpec.grading_provider` > default `"auto"`) and route the
pre-call auth guard through the centralized
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

The legacy `eval_spec.grading_provider or "anthropic"` falsy-`None`
short-circuit was retired in #182 / DEC-001b — `grading_provider`
now defaults to `"auto"`, and the resolver handles auto-inference
from the resolved model plus the subscription-first fallback to
anthropic when no model is available. Do NOT reintroduce the
short-circuit; always go through the resolver helper.

## The pattern

### Layer 1 — spec field carries the provider selection

```python
# schemas.py
@dataclass
class EvalSpec:
    # ... other fields ...
    # Default ``"auto"`` (post-#182 / DEC-001b). Resolution flows
    # through ``resolve_grading_provider``, which delegates to
    # ``infer_provider_from_model`` when the winning value is
    # ``"auto"`` (and falls back to ``"anthropic"`` when no model is
    # available — preserves byte-identical pre-#146 behavior).
    # Explicit ``null`` in JSON still loads as ``None`` so legacy
    # #145-vintage round-trips stay readable; the resolver treats
    # ``None`` the same as the default.
    grading_provider: Literal["anthropic", "openai", "auto"] | None = "auto"
```

### Layer 2 — CLI seam resolves provider, dispatches auth guard

Six LLM-mediated commands share the same shape (grade, extract,
triggers, compare --blind, propose-eval, suggest). Each routes
through the four-layer `_resolve_grading_provider` helper and runs
`check_provider_auth(provider, cmd_name)` with two distinct
`except` branches:

```python
# cli/grade.py (and extract, triggers, compare, propose_eval, suggest).
from clauditor.cli import _resolve_grading_provider
from clauditor._providers import (
    AnthropicAuthMissingError,
    OpenAIAuthMissingError,
    check_provider_auth,
)

def cmd_grade(args) -> int:
    # ... arg validation, spec load, dry-run early-return ...

    # Four-layer resolver (CLI flag > env > spec > default "auto").
    # Returns a concrete ``"anthropic"`` or ``"openai"`` — never
    # ``"auto"`` (the resolver inflates auto via inference).
    provider = _resolve_grading_provider(args, spec.eval_spec)
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
  class. CLI commands DO require one mechanical edit per
  command — adding a new ``except VertexAuthMissingError``
  branch to each ladder per the rollout recipe in "When this
  rule applies" below. That edit shape is uniform copy-paste
  with no new control-flow logic; uniformity is the
  linear-extensibility property the centralized dispatcher buys.
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

CLI call sites (six LLM-mediated commands, all using the
provider-aware guard post-#162):

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
- `src/clauditor/cli/suggest.py::_cmd_suggest_impl` — post-#162
  US-003 loads `SkillSpec.from_file(args.skill)` after the
  zero-failing-signals early-exit, resolves
  `provider = skill_spec.eval_spec.grading_provider or
  "anthropic"`, calls
  `check_provider_auth(provider, "suggest")` with distinct
  `AnthropicAuthMissingError` and `OpenAIAuthMissingError`
  exit-2 branches, and plumbs `provider=` into
  `propose_edits(...)`. Mirrors the
  `cli/triggers.py:114-127` pattern.

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

### Provider-dispatch shape extends to non-auth lookups (#169)

The auth-guard dispatcher above is the load-bearing example of
this rule, but the same shape generalizes to any per-provider
lookup that wants graceful fallback on unknown values rather than
hard-failing the caller. The pricing-table lookup in `#169`
(`src/clauditor/_providers/_pricing.py::estimate_cost`) is the
first non-auth instantiation:

- `estimate_cost(provider, model, ...)` accepts a `provider: str`
  and looks up `_PRICING_TABLE.get(provider)`.
- **Unknown provider** → returns `None` (graceful fallback per
  this rule's "graceful fallback on unknown values" pattern);
  never raises. A future provider that has not yet earned a
  rate-card entry produces `cost_usd: null` on disk rather than
  blocking the grading run.
- **Known provider + unknown model** → returns `None` AND emits
  a one-shot per-`(provider, model)` stderr warning via
  `announce_unknown_model(provider, model)` (DEC-006 of #169 —
  see `.claude/rules/centralized-sdk-call.md` "Implicit-coupling
  announcements — an emerging family" for the announcement
  contract). The warning fires only when the provider IS known;
  unknown providers stay silent so a typo'd provider does not
  flood stderr with every subsequent model.
- **Bad input types** (non-`str` provider/model, bool /
  non-`int` / negative tokens) → raises `ValueError` per
  `.claude/rules/pre-llm-contract-hard-validate.md`'s input-side
  contract. Programmer errors fail loudly; lookup misses do not
  crash a production grading run.

The structural-routing invariant the auth dispatcher preserves
(distinct exception classes per provider routed to distinct
`except` branches) re-shapes here as a two-category split:
**lookup-miss → `None`** vs **contract-violation → `ValueError`**.
Two distinct categories, structurally distinguishable at the
caller; no substring-matching on error messages. File anchor:
`src/clauditor/_providers/_pricing.py::estimate_cost`.

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
- Pytest fixtures. Post-#162 the three grader fixtures
  (`clauditor_grader`, `clauditor_blind_compare`,
  `clauditor_triggers`) load the spec first, then dispatch
  through `_dispatch_fixture_auth_guard`, which resolves the
  provider via the same pure
  `clauditor._providers.resolve_grading_provider` helper the CLI
  uses — honoring `CLAUDITOR_GRADING_PROVIDER` env,
  `eval_spec.grading_provider`, and auto-inference from
  `grading_model` (per #146 US-007). The Anthropic branch retains
  the `CLAUDITOR_FIXTURE_ALLOW_CLI` opt-in toggle (relaxed
  `check_any_auth_available` vs strict `check_api_key_only`);
  the OpenAI branch is always strict via `check_openai_auth`
  (no CLI-fallback / subscription analogue per #145 DEC-002).
  Per DEC-004 of #162, `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is
  silently no-op when the resolved provider is `"openai"`.
  Distinct exception classes per provider preserve the
  structural-routing invariant — fixture callers branch on
  `AnthropicAuthMissingError` vs `OpenAIAuthMissingError`
  rather than substring-matching the message. File anchor:
  `src/clauditor/pytest_plugin.py` (the three fixture factories).
- One-off diagnostic scripts in `scripts/` that hit a provider
  SDK directly. They can rely on the SDK's own error path.
