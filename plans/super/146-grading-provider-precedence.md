# 146: Multi-provider — `EvalSpec.grading_provider` four-layer precedence

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/146
- **Branch:** `feature/146-grading-provider-precedence`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/146-grading-provider-precedence`
- **PR:** https://github.com/wjduenow/clauditor/pull/164
- **Phase:** devolved
- **Epic:** clauditor-apy
- **Sessions:** 1 (2026-05-01)
- **Total decisions:** 10 (DEC-001 through DEC-010)
- **Depends on:** #145 (CLOSED — OpenAI provider, `check_provider_auth`, minimal `grading_provider` spec field shipped)
- **Blocks:** #147 (sidecar v3 with `provider` field)

---

## Discovery

### Ticket summary

**What:** Promote `EvalSpec.grading_provider` to a full four-layer-precedence
knob mirroring the `transport` field from #86. Add `--grading-provider` flag
on the six LLM-mediated CLI commands, `CLAUDITOR_GRADING_PROVIDER` env var,
and a `_resolve_grading_provider(args, eval_spec)` shared helper in
`cli/__init__.py` that handles whitespace normalization and validation.
Add an **auto-inference** layer: when the resolved provider is `"auto"`,
infer from the `grading_model` string (`claude-*` → anthropic, `gpt-*` →
openai, ambiguous → anthropic). Loosen `EvalSpec.grading_model`
permissiveness so OpenAI model strings pass load-time validation (today
the field is already unvalidated `str`, so this may be a no-op — but
verify and document).

**Why:** Epic A, ticket 3 of 4 in the multi-provider initiative (#143).
#145 shipped the OpenAI backend + minimal spec field
(`grading_provider: str | None = None`). Operators currently get one
provider per spec via the spec field only — no CLI override, no env-var
override, no auto-inference. #146 promotes the knob to full operator-
intent surface so a CI run can force `--grading-provider openai`
regardless of what the spec author wrote, and an `eval.json` that just
sets `"grading_model": "gpt-5.4"` Just Works without an explicit
`grading_provider` declaration.

**Done when:**
1. `EvalSpec.grading_provider` accepts `"anthropic" | "openai" | "auto"`
   (or equivalent — see Q1 reconciliation below). Load-time validation
   rejects unknown values with a crisp `ValueError`.
2. `_resolve_grading_provider(args, eval_spec)` lives in
   `cli/__init__.py`, mirroring `_resolve_grader_transport` shape,
   four-layer precedence: CLI > env > spec > default. Whitespace-only
   `CLAUDITOR_GRADING_PROVIDER` values normalize to "unset". Auto-
   resolution against `grading_model` lives behind this seam.
3. All six LLM-mediated CLI commands (`grade`, `extract`, `triggers`,
   `compare`, `propose-eval`, `suggest`) gain `--grading-provider
   {anthropic,openai,auto}` argparse flag with `type=_provider_choice`,
   `default=None`. Six call-site changes replace inline `spec.eval_spec
   .grading_provider or "anthropic"` resolution with
   `_resolve_grading_provider(args, spec.eval_spec)`.
4. The resolved provider threads through to `call_model(provider=...)`
   at every grader call site.
5. `check_provider_auth(provider, cmd_name)` keeps firing AFTER
   `--dry-run` early-return, BEFORE API spend; the resolved provider
   is what gets passed to the auth guard (so `--grading-provider
   openai` triggers the OpenAI auth check even when the spec is silent).
6. `.claude/rules/spec-cli-precedence.md` canonical-implementations
   section gains a "fifth four-layer precedence anchor" entry for
   `grading_provider`, sibling to `transport`.
7. Coverage stays ≥80%; `uv run ruff check src/ tests/` clean.

### Codebase findings

#### Current `EvalSpec.grading_provider` (post-#145)

- `src/clauditor/schemas.py:310` — `grading_provider: str | None = None`.
- `from_dict` validation (lines 752-775): accepts `None`, `"anthropic"`,
  or `"openai"`; rejects anything else with `ValueError` naming the
  literal set; bool-guarded per `.claude/rules/constant-with-type-info.md`.
- `to_dict` (893-898) emits the field only when non-None to minimize
  diff in round-trips.
- **Gap vs ticket:** ticket says
  `Literal["anthropic","openai","auto"] = "auto"`. Need to reconcile
  the `None` sentinel (#145) against the `"auto"` default (ticket).
  See Q1 below.

#### Current `EvalSpec.grading_model`

- `src/clauditor/schemas.py:261` — `grading_model: str = "claude-sonnet-4-6"`.
- **No validation today.** Field accepts any string at load time.
  Ticket says "extend `grading_model` validation to accept OpenAI model
  strings (no allowlist — pass through to provider's own validator)".
  Since today there's NO allowlist already, the ticket's loosening is
  effectively a no-op for validation. The relevant change is
  **default-model selection** when the field is unset and the resolved
  provider is OpenAI — see Q4 below.

#### `_resolve_grader_transport` — the canonical mirror

`src/clauditor/cli/__init__.py:58-88`:

```python
def _resolve_grader_transport(args, eval_spec=None) -> str:
    """Resolve grader transport using four-layer precedence.

    CLI flag > CLAUDITOR_TRANSPORT env > EvalSpec.transport > default "auto".
    Normalizes whitespace-only env values to None.
    Raises SystemExit(2) on invalid CLAUDITOR_TRANSPORT values.
    """
    import os
    from clauditor._providers import resolve_transport

    env_transport = os.environ.get("CLAUDITOR_TRANSPORT")
    if env_transport is not None and env_transport.strip() == "":
        env_transport = None
    spec_transport = eval_spec.transport if eval_spec is not None else None
    try:
        return resolve_transport(
            getattr(args, "transport", None), env_transport, spec_transport
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
```

The actual precedence/validation logic lives in
`clauditor._providers.resolve_transport` (in `_providers/__init__.py`).
The CLI helper is purely the "thread argparse + env + spec into
the dispatcher" wrapper. `_resolve_grading_provider` should follow the
same shape: a thin CLI wrapper around a pure
`resolve_grading_provider(cli_value, env_value, spec_value, model_value)`
in `_providers/__init__.py` (or a new sibling module).

#### Six CLI commands today

All six already follow the inline-resolution pattern:

```python
# grade.py:330-335 (and identical at extract.py:106-111,
# triggers.py:114-119, etc.)
provider = (
    spec.eval_spec.grading_provider
    if spec.eval_spec is not None
    and spec.eval_spec.grading_provider is not None
    else "anthropic"
)
```

`#146` replaces each of these six blocks with one call:

```python
provider = _resolve_grading_provider(args, spec.eval_spec)
```

Each command also has a `--transport` argument block (lines ~218-232 in
`grade.py`) — the new `--grading-provider` flag mirrors it byte-for-byte
modulo names.

`compare.py`, `propose_eval.py`, and `suggest.py` are slightly different:
- `compare`: has `eval_spec`, follows the standard pattern.
- `propose-eval`: hardcodes `check_provider_auth("anthropic",
  "propose-eval")` because the proposer is the eval-creation step
  itself — there's no `eval_spec` to read at the CLI seam. **The ticket
  says `--grading-provider` lands on propose-eval too**, which means
  propose-eval gains a per-invocation provider override (the
  forward-compat path noted in `multi-provider-dispatch.md`).
- `suggest`: no `eval_spec` at the CLI seam (loaded inside the
  orchestrator). Needs care — see Q5 below.

#### Six grader call sites — provider propagation

| Function | File | Today's provider source |
|---|---|---|
| `extract_and_grade` | `grader.py:866-871` | Reads `eval_spec.grading_provider` internally |
| `extract_and_report` | `grader.py:905-912` | Same as above |
| `grade_quality` | `quality_grader.py:1168-1174` | Inline `eval_spec.grading_provider or "anthropic"` (line 1197) |
| `blind_compare` | `quality_grader.py:695-705` | Accepts `provider` as explicit kwarg, default `"anthropic"` |
| `test_triggers` | `triggers.py:254` | Reads `eval_spec.grading_provider` internally |
| `propose_eval` | `propose_eval.py:909` | Hardcoded `provider="anthropic"` per #145 DEC-006 |

**Inconsistency to normalize:** four sites read from `eval_spec`, one
takes `provider` as kwarg, one hardcodes anthropic. After #146 lands,
the SHAPE should be: every orchestrator accepts `provider: str` as a
kwarg, the CLI seams resolve via `_resolve_grading_provider` and
pass the result. The orchestrators stop reading `eval_spec.grading_provider`
themselves. This is consistent with `.claude/rules/multi-provider-
dispatch.md` ("Do NOT resolve provider inside the orchestrator").

#### Auto-inference helper

No existing helper maps a model string to a provider. New pure helper
needed (location: `_providers/__init__.py` alongside `resolve_transport`):

```python
def infer_provider_from_model(model: str | None) -> str:
    """Infer provider from model name; fallback to 'anthropic' if ambiguous."""
    if model is None or not isinstance(model, str):
        return "anthropic"
    model = model.strip().lower()
    if model.startswith("claude-") or model.startswith("claude_"):
        return "anthropic"
    if model.startswith("gpt-") or model.startswith("o"):  # o-series? see Q3
        return "openai"
    return "anthropic"  # ambiguous fallback
```

#### Pytest fixtures

`pytest_plugin.py` fixtures (`clauditor_grader`, `clauditor_blind_compare`,
`clauditor_triggers`) currently route auth via `check_any_auth_available`
or `check_api_key_only` (Anthropic-only) — they don't yet use
`check_provider_auth`. Whether #146 extends fixtures to honor
`grading_provider="openai"` is a scoping question (Q6).

#### Existing test surfaces

- `test_schemas.py:2150-2229` — `TestEvalSpecGradingProvider` (from #145).
- `test_cli_propose_eval.py:1039-1103` — `TestProposeEvalProviderAuth` (from #145).
- `tests/test_providers_*.py` — provider-side tests (no precedence today).

### Convention constraints (load-bearing for this work)

1. **`spec-cli-precedence.md`** — the canonical mirror; #146 is the
   fifth four-layer-precedence field after `timeout`,
   `allow_hang_heuristic`, `transport`, and `skill_runner_transport`.
   Helper lives in `cli/__init__.py`, four-layer order is
   CLI > env > spec > default, whitespace-empty env collapses to None.
   Update the rule's "Canonical implementations" section in the same PR.

2. **`multi-provider-dispatch.md`** — the auth dispatcher pattern.
   `check_provider_auth(provider, cmd_name)` must keep firing AFTER
   `--dry-run` and BEFORE API spend; per-provider distinct `except`
   ladders for exit-2 routing. Resolution at the CLI seam, not inside
   orchestrators.

3. **`centralized-sdk-call.md`** — `call_model(prompt, *, provider, model,
   transport, max_tokens)` is the single seam. The resolved provider
   string passes through verbatim.

4. **`constant-with-type-info.md`** — `grading_provider` field
   validation must reject anything outside the literal set at load
   time. Default sentinel (`None` or `"auto"`) handled deliberately.

5. **`pure-compute-vs-io-split.md`** — the resolver helper is pure
   (reads env, args, spec; returns string; raises `ValueError` on bad
   env). The CLI wrapper owns stderr + `SystemExit(2)`.

6. **`llm-cli-exit-code-taxonomy.md`** — invalid env-var values route
   to exit 2 via `SystemExit(2)`. Pre-call auth failure routes to
   exit 2 (same as today).

7. **`back-compat-shim-discipline.md`** — Pattern 3 applies if the new
   resolver is monkeypatched in tests. Tests should patch
   `clauditor._providers.resolve_grading_provider` (canonical), not
   `clauditor.cli._resolve_grading_provider` (the wrapper).

Non-applicable: `json-schema-version.md` (no new sidecar — provider
flows through `ModelResult` only), `monotonic-time-indirection.md`
(resolver is sync), `stream-json-schema.md`,
`pre-llm-contract-hard-validate.md` (this is config validation, not
LLM-output validation — the existing `from_dict` membership check
already enforces the contract).

---

## Phase 1 scoping (questions for user)

**Q1 — Reconciling `None` (#145) vs `"auto"` (ticket).** The ticket says
`grading_provider: Literal["anthropic","openai","auto"] = "auto"` but
#145 already shipped `grading_provider: str | None = None`. Three options:

- **A.** Add `"auto"` as a third literal value; keep `None` default. The
  field becomes `Literal["anthropic","openai","auto"] | None = None`.
  Resolver treats both `None` and `"auto"` identically (auto-infer from
  model). Pre-#146 specs round-trip unchanged. Most conservative;
  preserves #145's `to_dict` minimal-diff property.
- **B.** Replace `None` with `"auto"` as the default. Field becomes
  `Literal["anthropic","openai","auto"] = "auto"` matching ticket
  verbatim. Pre-#146 specs that omitted the field still load fine
  (default applies), but `to_dict` must emit `"auto"` explicitly. **Mild
  back-compat hazard:** existing eval.json files committed to repos
  that explicitly set `"grading_provider": null` would fail load-time
  validation (no longer in the literal set).
- **C.** Same as A but mark `None` deprecated in the validator with a
  one-time stderr warning hinting the user to migrate to `"auto"`.
  Removes the warning a release later. Net more code; deferred cleanup.

**Q2 — Spec field validation strictness.** When the spec sets
`"grading_provider": "openai"` AND `"grading_model"` is unset (or set
to a `claude-*` value), what happens at load time?

- **A.** No load-time cross-validation. The field passes; the
  resolver/orchestrator picks the OpenAI default model (per Q4) or
  fails at provider-side validation. **Permissive.**
- **B.** Load-time warning (stderr) when `grading_provider` and
  `grading_model` prefix disagree (e.g. provider="openai" + model
  starts with `claude-`). Spec still loads; user sees a hint. Pure
  `from_dict` warning.
- **C.** Hard load-time failure. Reject specs where provider and model
  prefix disagree. **Strict but blocks legitimate use cases** (e.g.
  custom proxy that maps `claude-*` to OpenAI).

**Q3 — Auto-inference rules.** When `provider="auto"` (or unset) and
the model is set, the resolver infers. Edge cases:

- `claude-sonnet-4-6` → anthropic. ✓
- `gpt-5.4`, `gpt-4o`, `gpt-3.5-turbo` → openai. ✓
- `o1`, `o4-mini`, `o3-pro` (OpenAI reasoning models) → openai? Or
  ambiguous → anthropic? OpenAI o-series naming starts with `o<digit>`,
  not `gpt-`.
- Empty string, `None`, `""` → fallback to anthropic.
- Unknown prefixes (e.g. `gemini-pro`, `llama-3`) → fallback to
  anthropic OR raise `ValueError` at resolve time.

Pick:
- **A.** Strict prefix match: `claude-*` → anthropic, `gpt-*` → openai,
  `o[0-9]*` → openai. Anything else → anthropic fallback.
- **B.** Same as A but unknown prefixes raise `ValueError` (operator
  must explicitly set `--grading-provider`). More opinionated.
- **C.** Strict `claude-*` / `gpt-*` only; `o-series` falls through to
  anthropic fallback (since #145 deferred reasoning models per DEC-005).
  Document the gap; explicit `--grading-provider openai` still works.

**Q4 — Default model when `grading_model` is unset and resolved provider
is OpenAI.** Today `grading_model: str = "claude-sonnet-4-6"` (line
261 of schemas.py). When provider resolves to OpenAI but the spec
inherits the Anthropic default, what happens?

- **A.** Provider-aware default: the resolver/orchestrator overrides
  the dataclass default with `_DEFAULT_MODEL_L3 = "gpt-5.4"` (per #145
  DEC-001) when provider is openai and the spec didn't explicitly set
  `grading_model`. Requires distinguishing "user wrote
  `claude-sonnet-4-6` explicitly" from "got the dataclass default" —
  needs a sentinel like `Optional[str] = None` and a per-call default-
  picker.
- **B.** Hard failure: when provider is openai and model starts with
  `claude-` (i.e. likely the Anthropic default leaking through), raise
  `ValueError` at the resolve seam telling the operator to set
  `grading_model` explicitly. Loud and explicit, no magic defaults.
- **C.** Change the field default to `None`, add per-call resolution
  (provider="anthropic" → `claude-sonnet-4-6`; provider="openai" →
  `gpt-5.4`). Most ergonomic but is a non-trivial schema migration —
  every existing test fixture that constructed `EvalSpec` directly
  without setting `grading_model` would now see `None` instead of the
  hardcoded default.
- **D.** Out of scope. Treat default-model resolution as a separate
  ticket. Today's behavior (Anthropic default leaks even when
  provider="openai") stays; document the gap.

**Q5 — Auth-guard wiring on `propose-eval` and `suggest`.** Today
(per #145 DEC-006) `propose-eval` hardcodes
`check_provider_auth("anthropic", "propose-eval")`, and `suggest`
calls `check_any_auth_available("suggest")` directly (Anthropic-only).
The ticket lists both as `--grading-provider`-supporting. Should
this ticket:

- **A.** Add `--grading-provider` as a no-op pass-through on both
  commands (the flag is accepted but the auth/orchestrator paths stay
  Anthropic-only). Sets the surface up for a future ticket without
  changing behavior. Risk: confusing UX (the flag exists but does
  nothing).
- **B.** Wire propose-eval / suggest through `check_provider_auth` and
  `call_model(provider=...)` fully, so `--grading-provider openai`
  actually runs the proposer/suggester against OpenAI. Adds two more
  call sites to the orchestrator-update wave; needs the proposer
  prompts to be model-agnostic (likely already are — they're plain
  text).
- **C.** Defer both to a sibling ticket; #146 lands `--grading-provider`
  on the four `eval_spec`-aware commands only (grade, extract,
  triggers, compare). Smaller scope, tight focus.

**Q6 — Pytest fixture multi-provider support.** Today
`clauditor_grader` / `clauditor_blind_compare` / `clauditor_triggers`
fixtures call `check_any_auth_available` (Anthropic-only). Should #146
extend them to dispatch via `check_provider_auth` so a test using an
OpenAI-configured eval.json gets the right auth guard?

- **A.** Yes, in scope: extend all three fixtures to read
  `eval_spec.grading_provider` (or the resolved provider via the new
  helper) and call `check_provider_auth`. Adds ~15 lines per fixture
  and matching test coverage.
- **B.** Out of scope for #146; document as forward-compat work. The
  fixtures stay Anthropic-only until a sibling ticket extends them.
  Keeps #146's blast radius tight.
- **C.** Compromise: only extend `clauditor_blind_compare` (most likely
  to be exercised against OpenAI for cross-provider blind-compare
  tests); defer the other two.

**Q7 — Order of `_resolve_grading_provider` in the CLI flow.** Today
each command resolves `provider` BEFORE calling `check_provider_auth`,
then calls `_resolve_grader_transport` AFTER auth. Should the new
provider resolver fire before transport (current order) or after?

- **A.** Provider first (current order). Auth guard fires on the
  resolved provider; transport resolves separately. Cleanest separation.
- **B.** Combine into a single `_resolve_grading_setup(args, eval_spec)
  -> tuple[provider, transport]` helper. Less repetitive at six call
  sites; tighter coupling.
- **C.** Same as A but extract a shared "resolve, then auth-guard"
  helper to reduce the three-line `provider = _resolve(...); try:
  check_provider_auth(provider, cmd_name); except: ...` boilerplate
  at six sites.

---

## Architecture Review

### Phase 2 (2026-05-01)

**Phase 1 scoping decisions (confirmed by user):**

| Q | Pick | Effect |
|---|---|---|
| Q1 | **B** | `grading_provider: Literal["anthropic","openai","auto"] = "auto"` (replaces `None` sentinel) |
| Q2 | **A** | No load-time cross-validation; provider-side validator catches bad model strings |
| Q3 | **A** | `claude-*` → anthropic, `gpt-*` / `o[0-9]*` → openai, else fallback anthropic |
| Q4 | **A** | Provider-aware default-model override; needs sentinel to distinguish "user wrote it" from "got the default" |
| Q5 | **B** | Fully wire propose-eval and suggest through `check_provider_auth` + `call_model(provider=...)` |
| Q6 | **A** | Extend all three pytest fixtures to dispatch via `check_provider_auth` |
| Q7 | **A** | Provider-first order; separate `_resolve_grading_provider` and `_resolve_grader_transport` calls |

### Architecture review table

| Area | Rating | Key finding |
|---|---|---|
| Security | pass | Auth guards already provider-aware via #145; `OPENAI_API_KEY` already in `_API_KEY_ENV_VARS` per #145 DEC-008 |
| API design | concern | Auto-inference silently falls back to anthropic on unknown prefixes — typo failure mode (`gtp-5.4` infers anthropic, surfaces opaquely as Anthropic 400) |
| Data model | **blocker** | Q4=A forces `grading_model: str = "claude-sonnet-4-6"` → `str | None = None` migration. ~10 callers do `eval_spec.grading_model` directly without None-check; `to_dict` emit rule changes; `cli/init.py:99` scaffold may emit empty model |
| Q1 back-compat | concern | Q1=B replaces the `None` sentinel with `"auto"` default. Existing eval.json files with explicit `"grading_provider": null` would fail load-time validation |
| Test strategy | pass | ~30-40 new tests; the 16-combo CLI/env/spec/default precedence matrix can be parametrized |
| Observability | concern | Should `auto → <provider>` resolution emit a one-time stderr announcement (mirroring `_announced_cli_transport`)? Consistency vs noise tradeoff |

### Findings detail

**DATA-1 (BLOCKER) — Q4=A schema migration: `grading_model` becomes nullable.**
Today every orchestrator and CLI seam does `eval_spec.grading_model.startswith(...)` or
`args.model or spec.eval_spec.grading_model` assuming the field is always a non-empty
string. Q4=A's "provider-aware default override" requires distinguishing "user
explicitly wrote `claude-sonnet-4-6`" from "got the dataclass default". The cleanest
mechanic is changing the field signature to `str | None = None` and adding a
`resolve_grading_model(eval_spec, provider) -> str` pure helper. Affected sites:

- `cli/grade.py:310` — `model = args.model or spec.eval_spec.grading_model`
- `cli/triggers.py:72-75` — same shape, plus an "ERROR: No grading model specified"
  branch that becomes the load-bearing fallback.
- `cli/compare.py:293` — interpolates `skill_spec.eval_spec.grading_model` into
  a stderr progress line; needs None-safe formatting.
- `quality_grader.py:838` — `effective_model = model if model is not None else
  spec.eval_spec.grading_model`. Becomes the new resolver call site.
- `triggers.py:255` — function default `model: str = "claude-sonnet-4-6"`. Either
  changes to `model: str | None = None` (resolve inside) or stays anthropic-default.
- `schemas.py:811` — `grading_model=data.get("grading_model", "claude-sonnet-4-6")`.
  Becomes `data.get("grading_model")` (None when unset).
- `schemas.py:879` — `to_dict` emits `"grading_model": self.grading_model` always;
  becomes conditional on non-None.
- `cli/init.py:99` — scaffolded eval.json includes `"grading_model": "claude-sonnet-4-6"`.
  Decision: keep emitting (operator-friendly default for Anthropic-graded skills) or
  emit nothing (forces operator to choose). Recommend: keep, since `clauditor init`
  produces an Anthropic-defaulted skeleton.

Also `quality_grader.py:24-63` has a runtime guard `_validate_provider_model` that
raises when `provider="openai"` paired with a `claude-*` model. This guard is the
load-bearing PRIOR ART for the Q4 issue — it currently catches the bug at runtime;
#146 promotes the catch to load-time / resolve-time. The TODO comment at line 24
explicitly names #146 as the owner ("Removable once #146 ships per-provider
default-model precedence" — line 55). The guard's removal is part of #146's scope.

**API-1 (CONCERN) — Auto-inference silently falls back on typos.**
Q3=A says `claude-*` / `gpt-*` / `o[0-9]*` → known providers; anything else falls
back to anthropic. A typo like `gtp-5.4` (transposed g/t) would silently route to
anthropic, sending an OpenAI-named model to Anthropic, producing a 400 from the
Anthropic SDK with an opaque "model not found" message. Mitigations:

- (a) Tighten Q3 to Q3=B (raise `ValueError` on unknown prefixes), forcing operator
  to set `--grading-provider` explicitly. Loud but operator-hostile for unusual
  models (custom proxies, fine-tunes, future model namespaces).
- (b) Keep Q3=A but log a one-time stderr warning when auto-inference falls back
  due to an unknown prefix (e.g. `clauditor: grading_provider=auto resolved to
  'anthropic' for unknown model 'gtp-5.4'`). Pairs with the Observability concern
  (OBS-1) below.
- (c) Accept the typo failure mode. Operator who fat-fingers the model will see
  the API's 400; root cause is recoverable with one-line edit.

Decision needed in Refinement.

**Q1-COMPAT (CONCERN) — Q1=B back-compat: explicit `null` no longer valid.**
Q1=B changes the literal set from `{"anthropic", "openai", None}` (post-#145) to
`{"anthropic", "openai", "auto"}`. A pre-#146 eval.json that wrote
`"grading_provider": null` explicitly would load-fail under #146 (validator
rejects unknown values). Mitigations:

- (a) Validator silently coerces `null` → `"auto"` at load time. Maximum
  compatibility, zero operator burden. Round-trip via `to_dict` then writes
  `"auto"` instead of `null` (the file changes on first re-save, but the
  semantics are equivalent).
- (b) Validator hard-rejects `null` with a migration hint (`"grading_provider":
  null` is no longer accepted; use `"auto"` or omit the field). Loud but breaks
  any user who shipped a `null`-bearing eval.json.
- (c) Keep `None` as a fourth literal alongside `"auto"`; validator accepts both
  as equivalent "infer from model" sentinels. Documented internally; never emitted
  by `to_dict`.

Recommend (a) — this is the quiet migration path. The number of users who
explicitly wrote `null` is small (it's a no-op), and silent coercion preserves
their intent.

**OBS-1 (CONCERN) — Auto-inference announcement parity with transport.**
`_resolve_transport` emits a one-time stderr announcement when `transport="auto"`
resolves to `"cli"` (the `_announced_cli_transport` flag). Should
`_resolve_grading_provider` do the same when `auto` infers a non-default provider?
Pros: parity with transport; surfaces a non-obvious choice to the operator.
Cons: adds another announcement family member, requires test infrastructure for
reset, may be noise (the operator typically wants auto to "just work").
Recommend: skip the announcement for #146; revisit if user feedback warrants.
Documented as a deferred forward-compat decision.

**TEST-1 (PASS) — Test surface is mechanical.**
Estimated test count by area:
- `test_schemas.py::TestEvalSpecGradingProvider` — extend with "auto" variants,
  null-coercion test (Q1-COMPAT a), grading_model nullability tests. ~8 new tests.
- `test_schemas.py::TestEvalSpecGradingModel` (NEW) — explicit-set vs default-set
  distinction, None round-trip via to_dict. ~6 new tests.
- `test_providers_*.py::TestResolveGradingProvider` (NEW) — 16-combo
  CLI/env/spec/default matrix + 5-prefix auto-inference matrix. Parametrized: ~12
  test cases packed into 2 test functions.
- `test_providers_*.py::TestInferProviderFromModel` (NEW) — pure helper. ~6 cases.
- `tests/test_cli_grade.py` (and 5 sibling files) — `--grading-provider` flag
  precedence, env-var override, invalid-value exit-2 routing. ~3 tests per command
  × 6 commands = 18 tests; parametrize where possible.
- `test_pytest_plugin.py::TestProviderAuthDispatch` (NEW for Q6=A) — three fixture
  variants × two providers each. ~6 tests.
- Updates to existing `_validate_provider_model` test removal + replacement at the
  resolve seam.

Total: ~50-60 new tests; ~600-800 LOC.

---

## Refinement Log

### Phase 1 + 2 decisions (2026-05-01)

**DEC-001 — `grading_provider: Literal["anthropic","openai","auto"] = "auto"` field shape (Q1=B).**
*Rationale:* Ticket-aligned. Replaces #145's `str | None = None` sentinel with the
explicit `"auto"` literal. The Literal-typed default is self-documenting at
construction sites (every direct `EvalSpec(...)` call sees `"auto"` rather than
`None` in the dataclass repr). `to_dict` emits the field unconditionally now that
the default is a real string (not `None`); minimal-diff property is preserved
because `"auto"` round-trips byte-identical.

**DEC-002 — No load-time cross-validation between `grading_provider` and `grading_model` (Q2=A).**
*Rationale:* Permissive at load time; provider-side validators (the OpenAI SDK,
the Anthropic SDK) catch bad model strings at API call time with their own error
shapes. Forward-compat for custom proxies that map `claude-*` model names to
OpenAI endpoints (and vice versa).

**DEC-003 — Auto-inference uses STRICT prefix match: unknown prefixes raise `ValueError` (Q3=A overridden by API-1=A).**
*Rationale:* Phase 2 review identified the typo failure mode (`gtp-5.4` →
silently anthropic → opaque 400). Tightening the helper so unknown prefixes
raise `ValueError` at resolve time (mapped to exit 2 by the CLI seam) gives
operators a crisp actionable error: "set `--grading-provider` explicitly". Known
prefixes: `claude-` → anthropic; `gpt-` and `o[0-9]+` → openai. The o-series
inclusion forward-compats the eventual reasoning-model support deferred per #145
DEC-005 (the auth + dispatch already work; only the `reasoning=` kwarg surface is
deferred). When the model itself is `None` (the new default per DEC-004), the
resolver returns the spec/CLI-specified provider without inference; `auto` with
both model=None and provider=auto raises `ValueError` ("provide grading_provider
or grading_model").

**DEC-004 — `grading_model: str | None = None` (Q4=A).**
*Rationale:* Q4=A's "provider-aware default override" requires distinguishing
"user explicitly wrote a model name" from "got the dataclass default". Cleanest
mechanic: nullable field + new `resolve_grading_model(eval_spec, provider) -> str`
pure helper in `_providers/__init__.py` that picks Anthropic-default
(`claude-sonnet-4-6`) for `provider="anthropic"` or OpenAI-default
(`_DEFAULT_MODEL_L3 = "gpt-5.4"` from `_providers/_openai.py`) for
`provider="openai"`. ~10 callers updated to use the resolver instead of reading
`eval_spec.grading_model` directly. Retires the `_validate_provider_model`
runtime guard at `quality_grader.py:34-63` (which the comment explicitly tags
"Removable once #146 ships per-provider default-model precedence"). `to_dict`
emits the field only when non-None to preserve minimal-diff round-trip.

**DEC-005 — Wire `propose-eval` and `suggest` fully through `check_provider_auth` + `call_model(provider=...)` (Q5=B).**
*Rationale:* Maximum forward-compat surface. Both commands gain
`--grading-provider {anthropic,openai,auto}`. `propose-eval` reads no eval_spec
at the CLI seam (it's the eval-creation step itself), so the resolver receives
`spec=None`. `suggest` similarly has no eval_spec at the seam. Both pass the
resolved provider to `check_provider_auth(provider, cmd_name)` and onward to
`call_model(provider=...)`. The proposer/suggester prompts are model-agnostic
plain text — verified by reading `propose_eval.py` and `suggest.py`. This
removes the `propose-eval`-hardcoded `"anthropic"` from #145 DEC-006.

**DEC-006 — Pytest fixtures dispatch via `check_provider_auth` (Q6=A).**
*Rationale:* Three fixtures (`clauditor_grader`, `clauditor_blind_compare`,
`clauditor_triggers`) currently hardcode Anthropic-only auth. Each fixture reads
`eval_spec.grading_provider` (now `"auto" | "anthropic" | "openai"`), resolves
via `_resolve_grading_provider(None, eval_spec)` (no CLI args from a pytest
runtime; env-var precedence still applies via the helper), and calls
`check_provider_auth(resolved_provider, "<fixture>")`. The strict variant for
`provider="openai"` fixtures: `check_openai_auth` (no CLI fallback). For
`provider="anthropic"` fixtures, the strict-vs-relaxed split via
`CLAUDITOR_FIXTURE_ALLOW_CLI=1` is preserved — strict default
(`check_api_key_only`), opt-in relaxed (`check_any_auth_available`).

**DEC-007 — Provider resolved BEFORE auth, transport resolved AFTER (Q7=A).**
*Rationale:* Provider determines which auth guard fires; transport is
provider-orthogonal (OpenAI ignores transport per #145 DEC-002). Two separate
helper calls preserve clean separation of concerns and keep each helper's
signature uncluttered. The combined `_resolve_grading_setup` shape (Q7=B) was
considered and rejected — tighter coupling but harder to extend when a future
fourth knob lands.

**DEC-008 — `from_dict` silently coerces legacy `"grading_provider": null` to `"auto"` (Q1-COMPAT=A).**
*Rationale:* Quiet migration path for #145-vintage eval.json files. The validator
treats `None` (post-JSON-decode) and the literal string `"null"` as equivalent
to `"auto"`. Round-trip behavior: re-saving a `null`-bearing file produces
`"auto"` on disk (the file changes once, then stays stable). Net operator cost:
zero; the field's runtime semantics are byte-identical between `null` and
`"auto"`. Documented in the field's docstring + `EvalSpec.from_dict` comment.

**DEC-009 — No `auto → <provider>` stderr announcement family member (OBS-1=A).**
*Rationale:* Skip the announcement for #146; revisit if user feedback warrants.
The `_announced_cli_transport` analogue (per `.claude/rules/centralized-sdk-call.md`
"Implicit-coupling announcements" subsection) was justified for transport because
auto→cli has security implications (subscription-only auth, env stripping). Auto
provider resolution has no equivalent security weight — it's a routing choice
that's transparent to the operator at the API-call level (the model name itself
discloses the provider). Less ceremony, less code.

**DEC-010 — `cli/init.py` scaffold keeps `"grading_model": "claude-sonnet-4-6"` (DATA-1=A).**
*Rationale:* `clauditor init` produces an Anthropic-first scaffold (the bundled
default has been Anthropic since the project's inception). Keeping the explicit
`grading_model` in the scaffold means new users see a working spec out of the
box; an operator who wants OpenAI grading edits the scaffold to set
`"grading_provider": "openai"` and `"grading_model": "gpt-5.4"`. Removing the
scaffold default would force every new user through that edit. Documented in
the scaffold comment.

---

## Detailed Breakdown

### Story ordering rationale

Foundation (pure helpers) → schema migration → CLI wiring → orchestrator
normalization → fixtures → end-to-end → quality gate → docs.

| # | Title | Depends on |
|---|---|---|
| US-001 | Pure helpers: `infer_provider_from_model` + `resolve_grading_provider` + `resolve_grading_model` | none |
| US-002 | `EvalSpec.grading_provider` field migration (literal + auto default + null coercion) | none |
| US-003 | `EvalSpec.grading_model` migration to nullable + retire `_validate_provider_model` | US-001, US-002 |
| US-004 | CLI helper `_resolve_grading_provider` + `_provider_choice` argparse type | US-001 |
| US-005 | Wire `--grading-provider` flag on all 6 CLI commands | US-002, US-003, US-004 |
| US-006 | Normalize grader call sites to accept `provider` kwarg from CLI seam | US-005 |
| US-007 | Pytest fixtures dispatch via `check_provider_auth` | US-002, US-004 |
| US-008 | End-to-end tests for four-layer precedence + auto-inference | US-006, US-007 |
| US-009 | Quality Gate — code review × 4 + CodeRabbit + project validation | US-001..US-008 |
| US-010 | Patterns & Memory — update `.claude/rules/spec-cli-precedence.md` + `.claude/rules/multi-provider-dispatch.md` + docs | US-009 |

---

### US-001 — Pure helpers: `infer_provider_from_model`, `resolve_grading_provider`, `resolve_grading_model`

**Description.** Add three pure helpers to `src/clauditor/_providers/__init__.py`
(or a new `_providers/_resolve.py` if that module gets crowded — author's call).
All side-effect-free per `.claude/rules/pure-compute-vs-io-split.md`; raise
`ValueError` on bad inputs.

**Traces to:** DEC-001, DEC-003, DEC-004.

**Acceptance criteria:**
- `infer_provider_from_model(model: str | None) -> str` — strict prefix match
  per DEC-003. Returns `"anthropic"` for `claude-*`, `"openai"` for `gpt-*` /
  `o[0-9]+*` / `o[0-9]+-*`. Raises `ValueError` for any other non-empty
  string. Returns `"anthropic"` (the default-default) when `model is None` —
  this branch is reached only when the caller has no model AND provider is
  `"auto"`, in which case the caller should also have provided `provider != "auto"`
  via CLI/env/spec.
- `resolve_grading_provider(cli_override, env_override, spec_value, model) -> str` —
  four-layer precedence: first non-None of cli/env/spec wins; otherwise default
  `"auto"`; if resolved value is `"auto"`, delegate to
  `infer_provider_from_model(model)`. Validates each layer's value against
  `{"anthropic","openai","auto"}` and raises `ValueError` naming the layer
  (`CLI --grading-provider`, `CLAUDITOR_GRADING_PROVIDER`, or
  `EvalSpec.grading_provider`) so the CLI seam can route to exit 2.
- `resolve_grading_model(eval_spec, provider) -> str` — provider-aware
  default-picker per DEC-004. Returns
  `eval_spec.grading_model` when non-None; otherwise returns
  `"claude-sonnet-4-6"` for `provider="anthropic"` or
  `_providers._openai.DEFAULT_MODEL_L3` (currently `"gpt-5.4"`) for
  `provider="openai"`. Raises `ValueError` for unknown provider.
- `_providers/__init__.py` adds all three to `__all__`.
- Coverage of all three helpers ≥95%.

**Done when:** Helpers exist, all unit tests green, ruff clean.

**Files:**
- `src/clauditor/_providers/__init__.py` (add helpers + export in `__all__`)
- `tests/test_providers_resolve.py` (NEW — `TestInferProviderFromModel`,
  `TestResolveGradingProvider`, `TestResolveGradingModel`)

**TDD:**
- `test_infer_anthropic_for_claude_prefix`, `test_infer_openai_for_gpt_prefix`,
  `test_infer_openai_for_o_prefix`, `test_infer_raises_for_unknown_prefix`,
  `test_infer_returns_anthropic_for_none`.
- `test_resolve_cli_wins_over_env_spec_default`,
  `test_resolve_env_wins_over_spec_default`,
  `test_resolve_spec_wins_over_default`, `test_resolve_default_is_auto`,
  `test_resolve_auto_delegates_to_inference`,
  `test_resolve_invalid_value_raises_naming_layer` (parametrized).
- `test_resolve_grading_model_returns_explicit_when_set`,
  `test_resolve_grading_model_anthropic_default`,
  `test_resolve_grading_model_openai_default`,
  `test_resolve_grading_model_unknown_provider_raises`.

---

### US-002 — `EvalSpec.grading_provider` field migration

**Description.** Promote the field from `str | None = None` (post-#145) to
`Literal["anthropic","openai","auto"] = "auto"`. Update `from_dict` to silently
coerce legacy `null` (post-JSON-decode `None`) to `"auto"` per DEC-008. Update
`to_dict` to emit the field unconditionally (it's now always a real string, not
`None`).

**Traces to:** DEC-001, DEC-008.

**Acceptance criteria:**
- `schemas.py` line 310 changes: `grading_provider: str | None = None` →
  `grading_provider: Literal["anthropic", "openai", "auto"] = "auto"`. (Use
  `Literal` from `typing`.)
- `from_dict` validator (lines 752-775) accepts the literal set
  `{"anthropic", "openai", "auto"}` AND legacy `None` (silently coerced to
  `"auto"`). Rejects other values with `ValueError` naming the literal set.
- `to_dict` (lines 893-898) drops the conditional `if non-None` emit; always
  emits the field.
- Existing tests in `test_schemas.py::TestEvalSpecGradingProvider` (lines
  2150-2229) updated: legacy `null` test asserts coercion to `"auto"`; new
  test for the `"auto"` value; new test for round-trip stability of `"auto"`.

**Done when:** `EvalSpec.grading_provider` is `"auto"` by default; null-bearing
specs load to `"auto"`; round-trip stable.

**Files:**
- `src/clauditor/schemas.py` (field + `from_dict` + `to_dict`)
- `tests/test_schemas.py::TestEvalSpecGradingProvider` (extend)

**TDD:**
- `test_grading_provider_default_is_auto`,
  `test_grading_provider_accepts_auto`,
  `test_grading_provider_legacy_null_coerced_to_auto`,
  `test_grading_provider_to_dict_emits_auto_unconditionally`.
- Existing rejection tests for `"foo"`, integers, etc. should still pass.

---

### US-003 — `EvalSpec.grading_model` migration to nullable + retire `_validate_provider_model`

**Description.** Promote `EvalSpec.grading_model: str = "claude-sonnet-4-6"` →
`grading_model: str | None = None`. Update `from_dict` (line 811) to read
`data.get("grading_model")` (None when unset). Update `to_dict` (line 879) to
emit only when non-None. Remove the `_validate_provider_model` runtime guard
from `quality_grader.py:34-63` (its TODO at line 24 explicitly names #146 as
the owner). Remove all call sites of the guard.

**Traces to:** DEC-004.

**Acceptance criteria:**
- `schemas.py` line 261 changes: `grading_model: str = "claude-sonnet-4-6"` →
  `grading_model: str | None = None`.
- `from_dict` (line 811): `grading_model=data.get("grading_model")` (drops the
  `, "claude-sonnet-4-6"` default).
- `to_dict` (line 879) emits `grading_model` only when `self.grading_model is
  not None`.
- `quality_grader.py`: remove `_validate_provider_model` (lines 34-63) and
  every call site to it. The TODO comment block (lines 24-31) and the
  `DEFAULT_GRADING_MODEL = "claude-sonnet-4-6"` constant (line 31) stay; the
  constant becomes the Anthropic-default value used internally by
  `resolve_grading_model` (so the canonical default value lives in one place
  shared with `_providers/__init__.py::resolve_grading_model`).
- Update existing test fixtures that construct `EvalSpec(...)` directly to
  either set `grading_model` explicitly or accept the new `None` default.
  Audit ~20 sites in `tests/test_*.py`.

**Done when:** Schema-level field is nullable; runtime guard removed; all
existing tests still pass (with adjusted fixture values where needed).

**Files:**
- `src/clauditor/schemas.py` (field + `from_dict` + `to_dict`)
- `src/clauditor/quality_grader.py` (remove guard)
- `tests/test_schemas.py` (update tests asserting on default; ~6 tests)
- `tests/test_quality_grader.py` (~5 fixture updates)
- `tests/test_baseline.py`, `tests/test_triggers.py`,
  `tests/test_cli_transcript_slice.py`, `tests/test_pytest_plugin.py` (audit
  direct `EvalSpec(...)` constructions; either set `grading_model` or accept
  None default; ~10 sites)

**TDD:**
- `test_grading_model_default_is_none`, `test_grading_model_to_dict_omits_when_none`,
  `test_validate_provider_model_removed` (assert no such symbol on
  `quality_grader`).

---

### US-004 — CLI helper `_resolve_grading_provider` + `_provider_choice` argparse type

**Description.** Add `_provider_choice` argparse type (parallel to
`_transport_choice` at `cli/__init__.py:43-50`) and `_resolve_grading_provider`
helper (parallel to `_resolve_grader_transport` at lines 58-88). The helper
reads `args.grading_provider`, `os.environ["CLAUDITOR_GRADING_PROVIDER"]` (with
whitespace normalization to `None`), and `eval_spec.grading_provider`, plus
the resolved or fallback model for auto-inference. Raises `SystemExit(2)` on
invalid env values, printing to stderr — matching the exit-2 routing of
`_resolve_grader_transport`.

**Traces to:** DEC-001, DEC-003, DEC-007, US-001.

**Acceptance criteria:**
- `cli/__init__.py::_provider_choice(value: str) -> str` — validates value in
  `{"anthropic","openai","auto"}`; raises `argparse.ArgumentTypeError` on
  invalid (argparse maps to exit 2 automatically).
- `cli/__init__.py::_resolve_grading_provider(args, eval_spec=None) -> str` —
  reads `getattr(args, "grading_provider", None)`, normalizes
  `CLAUDITOR_GRADING_PROVIDER` env (whitespace-only → `None`), resolves the
  effective model (via `eval_spec.grading_model` if set; else `args.model` if
  CLI-provided; else `None` — the auto-inference layer raises if all are None
  AND provider can't be determined from another layer). Calls
  `clauditor._providers.resolve_grading_provider(cli, env, spec_value, model)`.
  Catches `ValueError` and re-raises as `SystemExit(2)` with stderr message.
- `cli/__init__.py` `__all__` (or equivalent re-exports) lists the new helper
  if other modules import from there.
- Test class: `tests/test_cli_init.py::TestResolveGradingProvider` (or extend
  existing) — at minimum 8 tests: CLI wins, env wins, spec wins, default
  auto, auto delegates to inference, invalid env → SystemExit(2), whitespace
  env → fallthrough, model used for auto-inference.

**Done when:** Helper exists, tests green; mirrors `_resolve_grader_transport`
shape and error semantics.

**Files:**
- `src/clauditor/cli/__init__.py` (add `_provider_choice`,
  `_resolve_grading_provider`)
- `tests/test_cli_init.py` (NEW or extend) — `TestResolveGradingProvider`,
  `TestProviderChoice`.

**TDD:** Write the 8 precedence tests first, then implement helper.

---

### US-005 — Wire `--grading-provider` flag on all 6 CLI commands

**Description.** Add `--grading-provider {anthropic,openai,auto}` argparse flag
to each of the six LLM-mediated CLI commands: `grade`, `extract`, `triggers`,
`compare`, `propose-eval`, `suggest`. Replace inline
`spec.eval_spec.grading_provider or "anthropic"` resolution with a call to
`_resolve_grading_provider(args, spec.eval_spec)` (or `_resolve_grading_provider(args, None)` for the
no-eval-spec commands). Per DEC-005, `propose-eval` and `suggest` also gain
this wiring, replacing their hardcoded `check_any_auth_available` /
`check_provider_auth("anthropic", ...)` calls with the resolved value.

**Traces to:** DEC-005, DEC-007, US-004.

**Acceptance criteria:**
- Each of the six command files gains a `--grading-provider` argparse argument
  with `type=_provider_choice`, `default=None`, `choices=("anthropic","openai","auto")`,
  and a help string referencing the four-layer precedence.
- Each command's `cmd_<name>` function:
  1. Resolves provider via `provider = _resolve_grading_provider(args, eval_spec_or_none)`
     AFTER `--dry-run` early-return, BEFORE `check_provider_auth`.
  2. Passes `provider` to `check_provider_auth(provider, cmd_name)`.
  3. Threads `provider` to all downstream orchestrator calls.
- The `_validate_provider_model` runtime guard removal from US-003 is
  externally visible: removing the import + call sites in CLI command files.
- `propose-eval` and `suggest` lose their hardcoded provider strings.
- Existing CLI tests for each command still pass; new tests exercise the
  flag's existence and that resolved provider flows through to
  `check_provider_auth`.

**Done when:** All six commands accept `--grading-provider`; six call sites
replaced with the helper; no inline `or "anthropic"` resolution remains.

**Files:**
- `src/clauditor/cli/grade.py`
- `src/clauditor/cli/extract.py`
- `src/clauditor/cli/triggers.py`
- `src/clauditor/cli/compare.py`
- `src/clauditor/cli/propose_eval.py`
- `src/clauditor/cli/suggest.py`
- `tests/test_cli_grade.py`, `tests/test_cli_extract.py`,
  `tests/test_cli_triggers.py`, `tests/test_cli_compare.py`,
  `tests/test_cli_propose_eval.py`, `tests/test_cli_suggest.py` — one
  per-command precedence + flag-existence test each (~3 tests × 6 = 18
  tests; many parametrizable).

**TDD:** Write `test_<cmd>_grading_provider_flag_overrides_spec` first (per
command); implement until green.

---

### US-006 — Normalize grader call sites to accept `provider` kwarg

**Description.** The six grader orchestrator entry points
(`extract_and_grade`, `extract_and_report`, `grade_quality`, `blind_compare`,
`test_triggers`, `propose_eval`) currently have inconsistent provider-handling
patterns (some read `eval_spec.grading_provider` internally, one takes
`provider` as kwarg, one hardcodes `"anthropic"`). Normalize: every
orchestrator accepts `provider: str` as a required keyword argument. The CLI
seam owns resolution and passes the resolved value. Internal reads of
`eval_spec.grading_provider` are removed.

**Traces to:** DEC-005, `.claude/rules/multi-provider-dispatch.md`.

**Acceptance criteria:**
- `grader.py::extract_and_grade(*, provider: str, ...)` — explicit kwarg.
- `grader.py::extract_and_report(*, provider: str, ...)` — same.
- `quality_grader.py::grade_quality(*, provider: str, ...)` — same; remove
  inline `provider = eval_spec.grading_provider or "anthropic"` at line 1197.
- `quality_grader.py::blind_compare(*, provider: str, ...)` — already accepts
  `provider`; remove the default `"anthropic"` (now mandatory kwarg). Also
  applies to `blind_compare_from_spec`.
- `triggers.py::test_triggers(*, provider: str, ...)` — explicit kwarg;
  remove internal `eval_spec.grading_provider` read.
- `propose_eval.py::propose_eval(*, provider: str, ...)` — explicit kwarg;
  removes the `provider="anthropic"` hardcode.
- `suggest.py::propose_edits` — same shape if it didn't already accept
  `provider`.
- All callers pass `provider=` from the CLI-resolved value (US-005) or
  fixture-resolved value (US-007).
- `resolve_grading_model(eval_spec, provider)` (from US-001) is called inside
  each orchestrator (or at the CLI seam) to pick the right default model.
- All existing orchestrator tests still pass; signature changes propagate
  through ~15-20 test call sites.

**Done when:** Every orchestrator entry point declares `provider` as a
required keyword argument; no orchestrator reads `eval_spec.grading_provider`
internally.

**Files:**
- `src/clauditor/grader.py`
- `src/clauditor/quality_grader.py`
- `src/clauditor/triggers.py`
- `src/clauditor/propose_eval.py`
- `src/clauditor/suggest.py`
- ~15-20 test call sites across `tests/test_grader.py`,
  `tests/test_quality_grader.py`, `tests/test_triggers.py`,
  `tests/test_propose_eval.py`, `tests/test_suggest.py`.

**TDD:** Run existing tests, fix signature failures, add new test asserting
each orchestrator raises `TypeError` (or rejects) when `provider` is omitted
positionally.

---

### US-007 — Pytest fixtures dispatch via `check_provider_auth`

**Description.** Extend the three pytest plugin fixtures
(`clauditor_grader`, `clauditor_blind_compare`, `clauditor_triggers`) so each
resolves the provider from the eval_spec and calls
`check_provider_auth(resolved_provider, fixture_name)` instead of the
hardcoded `check_any_auth_available` / `check_api_key_only`. The strict
default vs `CLAUDITOR_FIXTURE_ALLOW_CLI=1` opt-in is preserved for Anthropic;
OpenAI is always strict (`check_openai_auth`).

**Traces to:** DEC-006.

**Acceptance criteria:**
- `pytest_plugin.py` adds `_resolve_fixture_provider(eval_spec) -> str` helper
  that calls `_resolve_grading_provider(None, eval_spec)` (no argparse args
  at fixture time; env-var still applies).
- Each of the three fixtures calls
  `check_provider_auth(resolved_provider, "<fixture_name>")` after
  resolving. For `provider="anthropic"`, the existing strict-vs-relaxed
  branching via `CLAUDITOR_FIXTURE_ALLOW_CLI` is preserved (the dispatcher
  routes to the right helper). For `provider="openai"`, the dispatcher routes
  to `check_openai_auth` always.
- New test class `tests/test_pytest_plugin.py::TestProviderAuthDispatch`
  covers: anthropic-default, anthropic-strict, openai-strict, missing-
  OPENAI_API_KEY raises during fixture invocation. ~6 tests.
- Existing fixture tests still pass.

**Done when:** Three fixtures dispatch via `check_provider_auth`; tests green.

**Files:**
- `src/clauditor/pytest_plugin.py`
- `tests/test_pytest_plugin.py` (extend)

**TDD:** Write fixture-dispatch tests first (parametrized on provider × auth
state); implement helper.

---

### US-008 — End-to-end tests for four-layer precedence + auto-inference

**Description.** A focused integration test pass exercising the full
precedence chain end-to-end through one CLI command (likely `grade`, the
densest seam). Tests use mocked `call_model` to avoid real API spend; assert
that the right `provider=` value reaches the dispatcher under each
precedence configuration.

**Traces to:** All DECs.

**Acceptance criteria:**
- `tests/test_cli_grading_provider_e2e.py` (NEW or merged into existing).
- Test cases (parametrized on the precedence axis):
  - `--grading-provider openai` flag wins over spec="anthropic" → call_model
    receives `provider="openai"`.
  - `CLAUDITOR_GRADING_PROVIDER=openai` env wins over spec="anthropic" →
    call_model receives `provider="openai"`.
  - `EvalSpec.grading_provider="openai"` wins over default "auto" →
    call_model receives `provider="openai"`.
  - `EvalSpec.grading_provider="auto"` + `grading_model="gpt-5.4"` → infers
    openai → call_model receives `provider="openai"`.
  - `EvalSpec.grading_provider="auto"` + `grading_model="claude-sonnet-4-6"`
    → infers anthropic → call_model receives `provider="anthropic"`.
  - `EvalSpec.grading_provider="auto"` + `grading_model="unknown-model"` →
    raises ValueError → CLI exits 2.
  - Invalid `CLAUDITOR_GRADING_PROVIDER=foo` → CLI exits 2 with stderr.
  - Whitespace `CLAUDITOR_GRADING_PROVIDER="   "` falls through to spec.
  - Auth guard fires correctly per resolved provider (anthropic vs openai
    auth-missing tests).
- ≥9 parametrized test cases.

**Done when:** All precedence + inference paths exercised end-to-end; full
test suite passes with ≥80% coverage on the new modules.

**Files:**
- `tests/test_cli_grading_provider_e2e.py` (NEW)

**TDD:** Pick one test case (e.g. `--grading-provider` flag override),
implement until green, parametrize from there.

---

### US-009 — Quality Gate

**Description.** Run `code-reviewer` agent four times across the full
changeset, fixing every real bug found each pass. Run CodeRabbit review if
available. Project validation must pass after all fixes:
`uv run ruff check src/ tests/` clean and
`uv run pytest --cov=clauditor --cov-report=term-missing` passes (80%
coverage gate enforced).

**Traces to:** All US-001..US-008.

**Acceptance criteria:**
- 4 code-reviewer passes complete; all real findings fixed (false positives
  documented in the bead's notes).
- Coverage report shows ≥80% line coverage across changed files.
- Ruff clean.
- All tests green (incl. existing tests not changed by this work).

**Done when:** Project validation passes; all review findings addressed.

**Files:** Any file modified during US-001..US-008 may need fixes.

---

### US-010 — Patterns & Memory

**Description.** Update `.claude/rules/spec-cli-precedence.md` to add
`grading_provider` as the fifth four-layer-precedence canonical anchor (after
`timeout`, `transport`, `skill_runner_transport`, `allow_hang_heuristic`).
Update `.claude/rules/multi-provider-dispatch.md` to reflect the
`check_provider_auth` integration in pytest fixtures. Update
`docs/cli-reference.md` to document the new `--grading-provider` flag and
`CLAUDITOR_GRADING_PROVIDER` env var on each of the six commands. Update the
`docs/eval-spec-reference.md` field table for `grading_provider` and
`grading_model` to reflect the new defaults / nullability.

**Traces to:** All DECs; `.claude/rules/spec-cli-precedence.md` requires the
canonical-implementations section update per the rule's discipline.

**Acceptance criteria:**
- `.claude/rules/spec-cli-precedence.md` "Canonical implementations" section
  gains a "fifth four-layer-precedence anchor" entry for `grading_provider`,
  documenting the auto-inference layer (which is novel — none of the prior
  four anchors have an inference fallback).
- `.claude/rules/multi-provider-dispatch.md` "Canonical implementation"
  section pytest-fixtures bullet updates from "currently route through
  `check_api_key_only` / `check_any_auth_available` directly rather than
  `check_provider_auth`; per-provider fixture dispatch is forward-compat work
  for a future ticket" → "extended in #146 to dispatch via
  `check_provider_auth`".
- `docs/cli-reference.md` per-command sections gain a `--grading-provider`
  row in the flags table.
- `docs/eval-spec-reference.md` field table updates for `grading_provider`
  (default `"auto"`) and `grading_model` (default `null`, with provider-aware
  resolution explanation).
- README.md mentions multi-provider grading at a high level (one-sentence
  addition to the existing brief).

**Done when:** Docs in sync with implementation.

**Files:**
- `.claude/rules/spec-cli-precedence.md`
- `.claude/rules/multi-provider-dispatch.md`
- `docs/cli-reference.md`
- `docs/eval-spec-reference.md`
- `README.md` (small touch)

---

## Beads Manifest

**Epic:** `clauditor-apy` — #146: EvalSpec.grading_provider four-layer precedence

**Tasks (in dependency order):**

| Bead | Story | Depends on |
|---|---|---|
| `clauditor-apy.1` | US-001 — Pure helpers (`infer_provider_from_model` + `resolve_grading_provider` + `resolve_grading_model`) | none |
| `clauditor-apy.2` | US-002 — `EvalSpec.grading_provider` field migration | none |
| `clauditor-apy.3` | US-003 — `EvalSpec.grading_model` nullable + retire `_validate_provider_model` | `.1`, `.2` |
| `clauditor-apy.4` | US-004 — CLI helper `_resolve_grading_provider` + `_provider_choice` | `.1` |
| `clauditor-apy.5` | US-005 — Wire `--grading-provider` flag on all 6 CLI commands | `.2`, `.3`, `.4` |
| `clauditor-apy.6` | US-006 — Normalize 6 grader call sites to accept `provider` kwarg | `.5` |
| `clauditor-apy.7` | US-007 — Pytest fixtures dispatch via `check_provider_auth` | `.2`, `.4` |
| `clauditor-apy.8` | US-008 — End-to-end tests for precedence + auto-inference | `.6`, `.7` |
| `clauditor-apy.9` | US-009 — Quality Gate (code review × 4 + CodeRabbit) | `.8` |
| `clauditor-apy.10` | US-010 — Patterns & Memory (rules + docs) | `.9` |

**Ready tasks at devolution time:** `clauditor-apy.1` and `clauditor-apy.2`
(no blockers; can run in parallel).

---

## Detailed Breakdown

*(Pending — Phase 4.)*

---

## Beads Manifest

*(Pending — Phase 7.)*
