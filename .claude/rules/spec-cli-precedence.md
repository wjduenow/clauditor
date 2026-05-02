# Rule: CLI > spec > default precedence for per-invocation runner config

When a runner-adjacent knob (timeout, interactive-hang heuristic, a
future retry cap, rate-limit budget, …) needs to be settable at
*three* different layers — a CLI flag for the operator running one
command, a spec field for the skill author expressing a per-skill
preference, and a hardcoded default for everyone else — resolve the
effective value **inside `SkillSpec.run`** and thread it to
`SkillRunner.run` as a keyword-only kwarg. The CLI wins when
explicitly passed; otherwise the `EvalSpec` field wins when set;
otherwise `None` falls through to the runner's own `__init__`
default.

The shape is small, but the precedence direction is the load-bearing
piece: **operator override > author preference > library default**.
Flipping that order — e.g., letting the spec field win over the CLI
— silently defeats the operator's explicit flag on the spot where it
is most needed (e.g. a CI pipeline forcing `--timeout 30` to fail
fast is silently overruled by `eval.json` declaring `"timeout":
600`). Getting the direction right once is cheap; getting it wrong
and auditing every call site is expensive.

## The pattern

### Layer 1 — spec field (EvalSpec)

Carries the author's per-skill preference. Optional, `None` default,
load-time validated:

```python
# schemas.py
@dataclass
class EvalSpec:
    # ... other fields ...
    timeout: int | None = None  # None means "unset"

# from_dict validation block:
if "timeout" in data and data["timeout"] is not None:
    raw = data["timeout"]
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(
            f"EvalSpec(skill_name={skill_name!r}): "
            f"'timeout' must be an int, got {type(raw).__name__} {raw!r}"
        )
    if raw <= 0:
        raise ValueError(
            f"EvalSpec(skill_name={skill_name!r}): "
            f"'timeout' must be > 0, got {raw}"
        )
    timeout = raw
```

### Layer 2 — `SkillSpec.run` resolution + thread

Keyword-only `<field>_override` kwarg; `None` default. Resolve
inside `run` just before calling `self.runner.run`:

```python
# spec.py
def run(
    self,
    args: str | None = None,
    *,
    run_dir: Path | None = None,
    timeout_override: int | None = None,
    env_override: dict[str, str] | None = None,
) -> SkillResult:
    # ... args / run_dir resolution above ...

    # DEC-002: timeout precedence is CLI > spec > default. ``None``
    # falls through to ``SkillRunner.run``, which then uses its own
    # ``self.timeout`` default.
    effective_timeout = (
        timeout_override
        if timeout_override is not None
        else (
            self.eval_spec.timeout
            if self.eval_spec is not None
            else None
        )
    )
    result = self.runner.run(
        self.skill_name,
        run_args,
        cwd=effective_cwd,
        allow_hang_heuristic=allow_hang_heuristic,
        timeout=effective_timeout,
        env=env_override,
    )
```

### Layer 3 — `SkillRunner.run` fallback

Keyword-only, `None` default that falls back to the constructor's
`self.<field>`. Matches `.claude/rules/subprocess-cwd.md` (the
sibling pattern for the per-invocation `cwd` kwarg):

```python
# runner.py
class SkillRunner:
    def __init__(self, ..., timeout: int = 180, ...):
        self.timeout = timeout

    def run(
        self,
        skill_name: str,
        args: str = "",
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        ...,
    ) -> SkillResult:
        effective_timeout = timeout if timeout is not None else self.timeout
        # ... pass effective_timeout into the watchdog / Popen ...
```

### Layer 4 — CLI flag

`argparse` type callable for input validation; default `None` (so
the sentinel path through `SkillSpec.run` kicks in); pass explicitly
to `spec.run(..._override=args.<field>)`:

```python
# cli/validate.py (same shape on grade/capture/run)
p_validate.add_argument(
    "--timeout",
    type=_positive_int,    # exit 2 on <= 0 / non-int
    default=None,          # None = "not set"
    metavar="SECONDS",
    help="Override the runner timeout; defaults to EvalSpec.timeout or 180s.",
)

# cmd_validate:
skill_result = spec.run(
    run_dir=workspace.tmp_path / "run-0",
    timeout_override=getattr(args, "timeout", None),
    env_override=env_override,
)
```

## Why this shape

- **Operator intent wins over author intent wins over library
  default.** The CI operator who typed `--timeout 30` knows
  something the spec author did not — that this run, in this
  pipeline, at this moment, should fail fast. A reverse
  precedence (spec > CLI) silently ignores their flag; an
  all-equal precedence (whoever was evaluated last) is
  order-dependent and fragile. This direction is the one
  direction that maps to user expectations at every layer.
- **Resolution at `SkillSpec.run`, not at the CLI layer.** The
  CLI can't see `eval_spec.timeout` without loading the spec,
  and the runner can't see the CLI flag without plumbing it.
  `SkillSpec` is the one layer that already holds both the
  `eval_spec` (for the author's preference) and is called from
  the CLI (which passes the operator's override) — it is the
  natural aggregation seam. Centralizing the resolution there
  means every future caller (pytest plugin fixture, baseline
  phase, variance reps) inherits the same precedence with no
  per-caller drift.
- **`None` as "unset" sentinel, no magic numbers.** Every layer
  defaults to `None` to mean "I have no preference, fall
  through." This is how a CLI flag without `--timeout` (default
  `None`), a spec without `"timeout"` (field default `None`),
  and a direct constructor call (runner's own `self.timeout`
  fallback) all compose cleanly. A default-to-180 at the CLI
  layer would silently shadow the spec field; a default-to-180
  on the spec field would silently shadow the library's choice
  of default.
- **Keyword-only overrides at every boundary.** `SkillSpec.run(*,
  timeout_override=...)` and `SkillRunner.run(*, timeout=...)`
  both use `*,` to force call sites to be explicit. This is
  directly inherited from `.claude/rules/subprocess-cwd.md` —
  per-invocation config must be keyword-only so a future
  positional-arg addition doesn't silently shadow it.
- **Load-time validation on the spec field is mandatory.** The
  `EvalSpec` dataclass validator rejects non-int, bool (which is
  an `int` subclass in Python, per
  `.claude/rules/constant-with-type-info.md`), zero, and
  negative values at load time. If the spec field is garbage,
  the precedence chain never runs — exit 2 at load surfaces the
  error where the author can fix it, rather than passing it
  down to a confused watchdog.
- **CLI-side validation via an argparse `type=` callable.** A
  shared `_positive_int` helper in `clauditor.cli.__init__`
  rejects `--timeout 0 | -5 | foo` with an
  `argparse.ArgumentTypeError`, which argparse surfaces as exit
  2 per `.claude/rules/llm-cli-exit-code-taxonomy.md`. The
  `EvalSpec` validator and the argparse helper enforce the
  *same* invariant (`> 0`, int, not bool) — two validators, one
  contract.
- **Pass-through for knobs that are CLI-only** (no spec field).
  The `env_override` kwarg in #64 demonstrates the shape:
  `SkillSpec.run` threads `env_override` to
  `SkillRunner.run(env=env_override)` unchanged, with no
  resolution or merge. A 1-level pass-through shares the
  `*_override` keyword-only contract but skips the
  `if ... is not None else eval_spec.<field>` resolution block.
  This keeps the surface uniform for future callers even when a
  particular knob has no spec-field counterpart yet. (Auth
  source is environmental, not skill-intrinsic — DEC-004 of
  `plans/super/64-runner-auth-timeout.md`.)

## What NOT to do

- Do NOT flip the precedence to "spec wins over CLI." That
  silently defeats operator intent.
- Do NOT default the CLI flag to the library default value
  (e.g. `--timeout` defaulting to `180`). That makes the flag
  indistinguishable from "not passed," so the `SkillSpec.run`
  resolution sees `180` always and the spec field can never
  win. The CLI default MUST be `None`.
- Do NOT resolve precedence inside the CLI command function
  (`cmd_validate`, `cmd_grade`, …). Every CLI entry point
  would then need to duplicate the resolution, and a future
  caller (pytest fixture, baseline phase) would silently miss
  it. The resolution belongs on `SkillSpec.run`.
- Do NOT forget the `bool` guard on any int-typed spec field.
  `isinstance(True, int)` is `True` in Python; without
  `and not isinstance(val, bool)`, a spec with `"timeout":
  true` loads as `timeout=1`. See
  `.claude/rules/constant-with-type-info.md` for the
  canonical shape.
- Do NOT thread the CLI value directly to `SkillRunner.run`
  (bypassing `SkillSpec`). The spec-field layer would be
  skipped, and a CLI-less caller (the pytest plugin factory
  fixture, a future batch runner) would have no way to pick
  up the author's preference.

## Canonical implementations

### Three-level precedence (this rule's anchor)

`timeout`, introduced in #64:

- **Spec field**: `src/clauditor/schemas.py::EvalSpec.timeout` (last
  field) + the `from_dict` validator block. Rejects non-int,
  `bool`, and `<= 0` at load time per
  `.claude/rules/constant-with-type-info.md`.
- **Resolution**: `src/clauditor/spec.py::SkillSpec.run` — the
  `effective_timeout = timeout_override if ... else (eval_spec.timeout ...)`
  block (~lines 147-159). Threaded to
  `SkillRunner.run(timeout=effective_timeout)`.
- **Runner fallback**: `src/clauditor/runner.py::SkillRunner.run`
  — the `effective_timeout = timeout if timeout is not None else
  self.timeout` block (top of `_invoke`). The 180s default lives
  on `__init__`.
- **CLI flag**: `src/clauditor/cli/validate.py`, `cli/grade.py`,
  `cli/capture.py`, `cli/run.py` — each adds `--timeout
  SECONDS` with `type=_positive_int`, `default=None`. The shared
  `_positive_int` helper lives at
  `src/clauditor/cli/__init__.py::_positive_int`. Each CLI command
  calls `spec.run(..., timeout_override=args.timeout)`.
- **Pytest plugin**: `src/clauditor/pytest_plugin.py::clauditor_spec`
  — the factory wraps `SkillSpec.run` so callers can pass
  `timeout_override=` and `env_override=` to the fixture too.

### Two-level (spec > default; no CLI yet)

`allow_hang_heuristic`, introduced in #63:

- **Spec field**: `src/clauditor/schemas.py::EvalSpec.allow_hang_heuristic`
  + `from_dict` validator (bool, default `True`).
- **Resolution**: `src/clauditor/spec.py::SkillSpec.run` —
  `allow_hang_heuristic = self.eval_spec.allow_hang_heuristic if
  self.eval_spec else True` (~lines 142-146). Threaded to
  `SkillRunner.run(allow_hang_heuristic=allow_hang_heuristic)`.
- **Runner**: `src/clauditor/runner.py::SkillRunner.run(*,
  allow_hang_heuristic=True, ...)` — keyword-only.

No CLI flag today. If a future ticket adds `--hang-heuristic /
--no-hang-heuristic`, follow the three-level shape above — adding
the CLI layer is an extend, not a redesign.

### One-level pass-through (CLI-only; no spec field)

`env_override`, introduced in #64:

- **CLI**: `--no-api-key` flag on `validate`, `grade`, `capture`,
  `run`. When set, constructs the env dict via
  `env_without_api_key()` in `_harnesses/_claude_code.py`; passes as
  `spec.run(env_override=env)`.
- **Spec field**: NONE. Auth source is environmental, not
  skill-intrinsic (DEC-004 of
  `plans/super/64-runner-auth-timeout.md`).
- **Resolution**: `src/clauditor/spec.py::SkillSpec.run` —
  pass-through, no merge, no fallback (~line 166). Threaded as
  `self.runner.run(env=env_override)`.
- **Runner**: `src/clauditor/runner.py::SkillRunner.run(*, env=None,
  ...)` — forwarded verbatim to `subprocess.Popen(env=...)`. The
  `None` default preserves today's inherit-os-environ behavior.

Traces to DEC-002, DEC-004, DEC-010, DEC-013, DEC-014 of
`plans/super/64-runner-auth-timeout.md`. Companion rules:
`.claude/rules/subprocess-cwd.md` (per-invocation `cwd` kwarg
shape, the original anchor for keyword-only runner config),
`.claude/rules/constant-with-type-info.md` (load-time int /
bool-guard validation for the spec field),
`.claude/rules/llm-cli-exit-code-taxonomy.md` (CLI input-error
exit-code routing for the argparse-type validator).

### Four-layer precedence — grader transport (#86)

`transport` for the LLM-grader calls (Layer 2/3 Anthropic calls),
introduced in #86:

- **CLI flag**: `--transport {api,cli,auto}` on all six LLM-mediated
  commands (`grade`, `extract`, `propose-eval`, `suggest`,
  `triggers`, `compare --blind`). Validated by the shared
  `_transport_choice` argparse type helper in
  `src/clauditor/cli/__init__.py`.
- **Env var**: `CLAUDITOR_TRANSPORT={api,cli,auto}`. Whitespace-only
  values are normalized to `None` (treated as unset) so an accidental
  `export CLAUDITOR_TRANSPORT=" "` does not override everything.
- **Spec field**: `EvalSpec.transport` — per-skill preference set by the
  skill author in `eval.json`. Validated at load time (must be one of
  `"api"`, `"cli"`, `"auto"`).
- **Default**: `"auto"` — prefers CLI when `shutil.which("claude")` is
  non-None, falls back to SDK otherwise.

Resolution lives in `src/clauditor/cli/__init__.py::_resolve_grader_transport`.
Unlike the runner-config knobs above (resolved inside `SkillSpec.run`),
transport resolution for grader calls is centralized at the CLI layer
because the LLM grader calls are not routed through `SkillSpec.run` —
they are direct `await call_anthropic(...)` invocations from the six
grader orchestrators. The centralized helper keeps whitespace
normalization and env stripping consistent across all six commands.

`EvalSpec.transport` and `EvalSpec.skill_runner_transport` are both
spec-field knobs: the former controls Anthropic grader calls; the latter
controls the `claude` CLI subprocess used to *run* the skill. They share
the same `{api,cli,auto}` vocabulary but thread to different seams.

Traces to DEC-003, DEC-008, DEC-012, DEC-017 of
`plans/super/86-claude-cli-transport.md`.

### Four-layer precedence — grading provider (#146)

`grading_provider` for the LLM-grader calls (which provider's SDK
handles the Layer 2/3 grading call), introduced in #146:

- **CLI flag**: `--grading-provider {anthropic,openai,auto}` on all
  six LLM-mediated commands (`grade`, `extract`, `triggers`,
  `compare --blind`, `propose-eval`, `suggest`). Validated by the
  shared `_provider_choice` argparse type helper in
  `src/clauditor/cli/__init__.py`.
- **Env var**: `CLAUDITOR_GRADING_PROVIDER={anthropic,openai,auto}`.
  Whitespace-only values are normalized to `None` (treated as unset)
  so an accidental `export CLAUDITOR_GRADING_PROVIDER=" "` does not
  override everything.
- **Spec field**: `EvalSpec.grading_provider` — per-skill preference
  set by the skill author in `eval.json`. Validated at load time
  against `{"anthropic", "openai", "auto"}`. Legacy `null` (post-
  JSON-decode `None`) is silently treated as "unset" so #145-vintage
  specs round-trip unchanged (DEC-008).
- **Default**: `"auto"` — the auto-inference layer (see below)
  decides the concrete provider from `grading_model`.

Resolution lives in
`src/clauditor/cli/__init__.py::_resolve_grading_provider` (the thin
CLI wrapper) which delegates to the pure
`clauditor._providers.resolve_grading_provider(cli, env, spec, model)`
helper. Like `_resolve_grader_transport`, the resolution is
centralized at the CLI layer because grader calls are direct
`await call_model(...)` invocations from the six grader
orchestrators — they are not routed through `SkillSpec.run`. The
centralized helper keeps whitespace normalization, layer
validation, and exit-2 routing on `ValueError` consistent across
all six commands.

**Novel: auto-inference layer.** Unlike the prior four anchors
(`timeout`, `transport`, `skill_runner_transport`,
`allow_hang_heuristic`), `grading_provider` adds a fifth resolution
step that fires when the winning value is `"auto"` (or unset and
falls through to the default `"auto"`):

- `claude-*` model prefix → `"anthropic"`.
- `gpt-*` or `o[0-9]+*` model prefix → `"openai"` (the o-series
  branch forward-compats reasoning models per #145 DEC-005).
- Any other non-empty model string → `ValueError` ("cannot infer
  provider from unknown model prefix … — set `--grading-provider`
  explicitly"). DEC-003 chose strict prefix-match over silent
  fallback so a typo like `gtp-5.4` raises a crisp actionable
  error at resolve time rather than silently routing the wrong
  provider's SDK and surfacing as an opaque 400.
- `model is None` AND every precedence layer is `"auto"` →
  `ValueError` ("provide grading_provider or grading_model").

The auto-inference layer lives in
`clauditor._providers.infer_provider_from_model` and is invoked by
`resolve_grading_provider` only when the winning precedence value
is `"auto"`. The effective model used for inference follows the
operator-intent direction at the CLI seam: `args.model` (when the
command exposes a `--model` flag) wins over `eval_spec.grading_model`
so an operator passing `--model gpt-5.4 --grading-provider auto`
gets OpenAI even when the spec author wrote `claude-sonnet-4-6`.

**Companion knob — `grading_model` (nullable migration).** #146
also promoted `EvalSpec.grading_model` from `str` to `str | None`
(DEC-004a, partial — see "Deferred default-flip" below). The
provider-aware default-picker
`clauditor._providers.resolve_grading_model(eval_spec, provider)`
returns `eval_spec.grading_model` when non-`None`, else the
Anthropic-default (`"claude-sonnet-4-6"`) for `provider="anthropic"`
or the OpenAI-default (`_providers._openai.DEFAULT_MODEL_L3` —
currently `"gpt-5.4"`) for `provider="openai"`. Each grader
orchestrator calls `resolve_grading_model(...)` rather than reading
`eval_spec.grading_model` directly so the right per-provider
default fires.

**Deferred default-flip — DEC-001a / DEC-004a partial migration.**
The plan called for flipping the dataclass defaults — `grading_provider`
to `"auto"` and `grading_model` to `None` — in lockstep with the
field-shape changes. Mid-implementation it surfaced that doing so
would have broken downstream tests because six CLI files and
several orchestrators still resolve provider via the falsy-`None`
short-circuit pattern `eval_spec.grading_provider or "anthropic"`
that pre-dates #146. With `"auto"` being truthy (and `None` no
longer the sentinel), the short-circuit fails and
`check_provider_auth("auto", ...)` raises because the auth
dispatcher only knows `anthropic`/`openai`.

Per `.claude/rules/plan-contradiction-stop.md`, the worker
surfaced the gap and split each DEC into two parts:

- **DEC-001a (this story)** — accept `"auto"` as a literal value
  alongside `anthropic`/`openai`; keep dataclass default
  `grading_provider: str | None = None`. `to_dict` still emits
  conditionally so #145 round-trip stays byte-identical. Runtime
  semantics unchanged from #145.
- **DEC-001b (deferred)** — flip default `None` → `"auto"` once a
  follow-up sweep eliminates every falsy-`None` short-circuit
  call site.
- **DEC-004a (this story)** — promote `grading_model` field type
  from `str` to `str | None`; accept explicit JSON `null` in
  `from_dict` (previously coerced to the default); keep dataclass
  default `"claude-sonnet-4-6"` AND the `_validate_provider_model`
  runtime guard. Runtime semantics for unset / set specs are
  byte-identical to #145; the new capability is "explicit `null`
  no longer silently coerced."
- **DEC-004b (deferred)** — flip default to `None` and retire the
  `_validate_provider_model` guard once the sweep above completes.

Net effect on this rule: the precedence machinery, the auto-
inference layer, and `resolve_grading_model`'s provider-aware
default-picker are all live in production. The dataclass defaults
will flip in a follow-up ticket once the falsy-short-circuit call
sites are migrated.

Canonical implementation paths:

- Pure helpers (no I/O):
  `clauditor._providers.infer_provider_from_model`,
  `clauditor._providers.resolve_grading_provider`,
  `clauditor._providers.resolve_grading_model`.
- CLI wrappers (own stderr + `SystemExit(2)` routing):
  `clauditor.cli._resolve_grading_provider`,
  `clauditor.cli._provider_choice` (argparse `type=` validator).
- Pytest fixture dispatcher (mirrors the CLI seam in fixture-
  land): `clauditor.pytest_plugin._dispatch_fixture_auth_guard` +
  `_resolve_fixture_provider`.

Traces to DEC-001a, DEC-003, DEC-004a, DEC-005, DEC-006, DEC-007,
DEC-008 of `plans/super/146-grading-provider-precedence.md`.
Companion rules: `.claude/rules/multi-provider-dispatch.md` (the
`check_provider_auth` dispatcher this resolver feeds),
`.claude/rules/centralized-sdk-call.md` (the `call_model(provider=...)`
seam this resolver targets),
`.claude/rules/precall-env-validation.md` (the per-provider
auth-missing-exception shape that fires after this resolver
picks the provider),
`.claude/rules/plan-contradiction-stop.md` (the deferred-default-
flip migration discipline).

### Implicit coupling at the operator-intent layers

An adjacent pattern to the precedence rule: when a CLI flag (or its
env-var sibling) implicitly sets a *related* runner-config flag, the
coupling must fire only on the **operator-intent** precedence layers
(CLI flag + env var). It must NOT fire on `EvalSpec.*` (author-intent:
the skill author does not know the user's env and cannot make the
coupling decision correctly) and must NOT fire on auto-resolution
(would surprise users who happen to have the underlying tool on PATH
but maintain an API key for production).

Tie-breaker: the explicit user flag always wins over the implicit
coupling. If the user passed the explicit equivalent themselves, the
implicit path does NOT re-fire its one-time stderr notice — the notice
exists to surface the *implicit* decision to surprised users, not to
re-announce what the user just typed.

Canonical implementation: `should_strip_api_key_for_skill_subprocess`
in `src/clauditor/cli/__init__.py` — a pure sibling helper next to
`_resolve_grader_transport` that reads `args.transport == "cli"` and
`os.environ.get("CLAUDITOR_TRANSPORT", "").strip() == "cli"` and
returns a bool. Does NOT consult `eval_spec`; does NOT resolve auto.

Call site: the `env_override` computation in
`src/clauditor/cli/grade.py::cmd_grade` (and the parallel computation
in the `--baseline` arm, so both sides of the delta share the same
auth posture). The site reads the helper, combines with an
`explicit_strip = args.no_api_key` check for the tie-breaker, and
calls `announce_implicit_no_api_key()` from `clauditor._anthropic`
when the implicit path fired AND a key was actually present.

Traces to DEC-001, DEC-002, DEC-006, DEC-007, DEC-008, DEC-009,
DEC-011 of `plans/super/95-subscription-auth-flag.md`. Companion
rules: `.claude/rules/centralized-sdk-call.md` (the announcement-
family seam these notices live on) and
`.claude/rules/pure-compute-vs-io-split.md` (the pure-helper shape
the coupling decision follows).

## When this rule applies

Any future runner-adjacent knob that an operator may want to
override on the CLI AND a skill author may want to declare on a
per-skill basis AND a library default exists. Plausible future
callers:

- **Per-spec retry cap** (`EvalSpec.max_retries`) — operator
  forces `--retries 0` for a dry-run CI check; spec author
  declares `"max_retries": 3` for a flaky skill; library
  default is `1`.
- **Per-spec stdout line budget** (`EvalSpec.max_stdout_bytes`)
  — operator caps to 1 MB on a constrained runner; spec author
  allows 100 MB for a research skill; library default is 10 MB.
- **Per-spec concurrency** (`EvalSpec.max_parallel`) for
  variance or trigger-test runs.
- Any other "operator has final say, author has strong
  preference, library ships sane default" knob.

The rule also applies retroactively: an existing knob that is
currently resolved inside a CLI command function (rather than
`SkillSpec.run`) is a latent drift site — migrate the
resolution up to `SkillSpec.run` the next time the knob is
touched.

## When this rule does NOT apply

- Knobs that are *exclusively* environmental and have no
  skill-intrinsic meaning (auth source, proxy URL, working
  directory). Those are CLI-only pass-through — the one-level
  shape above. Do not invent a spec field just to satisfy the
  three-level pattern.
- Knobs that are *exclusively* library-internal (e.g. retry
  back-off curves, stream buffering sizes). Those have no
  operator or author use case and should stay on the
  constructor or as module-level constants.
- Construction-time config that does not vary per-invocation
  (e.g. `claude_bin`, `project_dir`). Those live on the
  runner's `__init__` signature, not on `run()`. See
  `.claude/rules/subprocess-cwd.md` for the per-invocation vs
  per-construction distinction.
- One-shot diagnostic scripts in `scripts/` that hit the
  runner directly with inline kwargs. They can skip the
  precedence chain — but production paths (CLI, pytest plugin,
  any `import clauditor.*` caller) must go through
  `SkillSpec.run`.
