# Rule: Route every model SDK call through the centralized helper

When any clauditor module needs to call a model provider's API, it
must go through the centralized `clauditor._providers.call_model`
dispatcher rather than constructing its own `AsyncAnthropic` /
`AsyncOpenAI` client or calling `messages.create` /
`responses.create` directly. The dispatcher routes to the backend
selected by `provider=` — today `"anthropic"` (delegating to
`clauditor._providers._anthropic.call_anthropic`) and `"openai"`
(delegating to `clauditor._providers._openai.call_openai`, #145).
Both layers together own retry policy, error categorization, token
accounting, transport routing, and the `AnthropicHelperError` /
`OpenAIHelperError` user-facing error envelopes. Bypassing them
means each new caller re-implements (and drifts on) the
retry/back-off/auth-error-message logic the rest of clauditor
depends on.

For one-release back-compat, `clauditor._anthropic` re-exports the
moved Anthropic public surface (`call_anthropic`,
`AnthropicHelperError`, `ClaudeCLIError`,
`ModelResult`/`AnthropicResult`, the auth helpers, etc.) so existing
call sites keep working unmodified — but new code should target
`clauditor._providers` directly.

## The pattern

```python
# At the call site:
from clauditor._providers import (
    AnthropicHelperError,
    OpenAIHelperError,
    call_model,
)

try:
    result = await call_model(
        prompt,
        provider="anthropic",   # or "openai"
        model=model,
        transport="auto",
        max_tokens=4096,
    )
except AnthropicHelperError as exc:
    # User-facing message already formatted (auth hint, status code,
    # body excerpt). Surface to stderr, set exit code, do NOT retry.
    print(f"ERROR: {exc}", file=sys.stderr)
    raise
except OpenAIHelperError as exc:
    # Same shape — pre-formatted user-facing message. Distinct
    # exception class keeps the CLI's ``except`` ladder structural.
    print(f"ERROR: {exc}", file=sys.stderr)
    raise
response_text = result.text_blocks[0] if result.text_blocks else ""
# result.input_tokens / result.output_tokens for metrics
# result.raw_message for refusal / tool-use inspection
# result.provider in {"anthropic", "openai"}; result.source in
# {"api", "cli"} (always "api" for openai per DEC-002 of #145)
```

Inside `_providers/__init__.py` (the dispatcher):

```python
async def call_model(
    prompt: str,
    *,
    provider: Literal["anthropic", "openai"],
    model: str,
    transport: Literal["api", "cli", "auto"] = "auto",
    max_tokens: int = 4096,
) -> ModelResult:
    if provider == "anthropic":
        # Deferred per-call import per
        # ``.claude/rules/back-compat-shim-discipline.md`` Pattern 3,
        # so test patches that target the canonical seam
        # ``clauditor._providers._anthropic.call_anthropic`` still fire.
        from clauditor._providers import _anthropic as _anthropic_mod
        return await _anthropic_mod.call_anthropic(
            prompt, model=model, transport=transport, max_tokens=max_tokens
        )
    if provider == "openai":
        # Same deferred-import shape; openai backend ignores
        # ``transport=`` (always source="api" per DEC-002 of #145).
        from clauditor._providers import _openai as _openai_mod
        return await _openai_mod.call_openai(
            prompt, model=model, transport=transport, max_tokens=max_tokens
        )
    raise ValueError(f"unknown provider: {provider!r}")
```

Inside `_providers/_anthropic.py` (the anthropic backend body):

```python
# Module-level aliases per .claude/rules/monotonic-time-indirection.md.
# Tests patch these so asyncio's own scheduler calls are not clobbered.
_sleep = asyncio.sleep


def _rand_uniform(lo: float, hi: float) -> float:
    """Indirected so tests can pin jitter deterministically."""
    return _rng.uniform(lo, hi)


async def call_anthropic(
    prompt: str,
    *,
    model: str,
    max_tokens: int = 4096,
) -> AnthropicResult:
    # ... build AsyncAnthropic(), loop with per-exception retry caps,
    # compute exponential backoff with ±25% jitter (via shared
    # _providers/_retry.py helpers), raise AnthropicHelperError on
    # non-retriable or exhausted failures ...
```

Inside `_providers/_openai.py` (the openai backend body):

```python
# Same module-level aliases — tests patch _sleep / _monotonic on the
# openai module specifically so anthropic tests' patches don't leak.
_sleep = asyncio.sleep
_monotonic = time.monotonic


async def call_openai(
    prompt: str,
    *,
    model: str,
    transport: str = "auto",  # accepted for signature parity; ignored
    max_tokens: int = 4096,
) -> ModelResult:
    # Defense-in-depth: catch (TypeError, OpenAIError) at AsyncOpenAI()
    # construction. OpenAI's SDK raises OpenAIError immediately when
    # OPENAI_API_KEY is missing (Anthropic raises TypeError later, at
    # messages.create()), so the construction wrap covers both shapes.
    try:
        client = AsyncOpenAI()
    except ImportError:
        raise
    except (TypeError, OpenAIError) as exc:
        raise OpenAIHelperError(
            "OpenAI SDK client initialization failed — "
            "verify OPENAI_API_KEY is set."
        ) from exc
    # ... loop with per-exception retry caps shared via
    # _providers/_retry.py (same constants + compute_backoff). Stamps
    # ModelResult.source="api" unconditionally per DEC-002 of #145.
```

## Why this shape

- **One retry taxonomy, consistently applied across providers**:
  - `RateLimitError` (HTTP 429) → up to 3 retries (4 attempts total).
  - `APIStatusError` with `status_code >= 500` → 1 retry then raise.
  - `APIStatusError` 4xx (other than 401/403) → no retry; raise
    immediately (bad request, not found, conflict, etc).
  - `AuthenticationError` (401) / `PermissionDeniedError` (403) → no
    retry; raise immediately with a message pointing at the
    provider's API-key env var.
  - `APIConnectionError` → 1 retry then raise.
  Both Anthropic and OpenAI backends share the same per-category
  retry caps (`RATE_LIMIT_MAX_RETRIES=3`, `SERVER_MAX_RETRIES=1`,
  `CONN_MAX_RETRIES=1`) and the same exponential-backoff formula
  via the shared `clauditor._providers._retry` module (DEC-007 of
  `plans/super/145-openai-provider.md`). Per-category ladder
  decisions live in `compute_retry_decision(category, retry_index)`,
  the policy-level helper. If every call site rolled its own, one
  module would treat 500s as permanent and another as transient;
  hoisting the policy to one shared module keeps every provider
  in lockstep when the ladder evolves.
- **Exponential backoff with ±25% jitter** via
  `_providers/_retry.compute_backoff(retry_index)`: the delay for
  retry index `i` is `2 ** i` seconds (i.e. 1 s, 2 s, 4 s)
  multiplied by a uniform random factor in `[0.75, 1.25]`. Jitter
  avoids the stampede failure mode where two concurrent callers
  retry on the same wall-clock tick and both hit the same
  throttling window again.
- **Auth errors include the env-var hint**: an operator-friendly
  `"check the ANTHROPIC_API_KEY environment variable"` (or
  `OPENAI_API_KEY` for the OpenAI side) is attached to every
  401/403 message. A scattered SDK-call style would leave this
  up to each call site and at least one would forget.
- **Per-provider helper-error classes wrap the original exception**:
  non-retriable SDK errors are re-raised as
  `AnthropicHelperError(message) from exc` or
  `OpenAIHelperError(message) from exc`, so `__cause__` preserves
  the original exception for callers that want to introspect
  (e.g. for status code), while new callers can just
  `except AnthropicHelperError` / `except OpenAIHelperError` for
  the pre-formatted user-facing message. The two classes are
  siblings of `Exception`, NOT a shared parent class — preserving
  the structural-routing invariant per
  `.claude/rules/llm-cli-exit-code-taxonomy.md`.
- **Per-provider `_sleep` / `_monotonic` / `_rand_uniform` aliases**
  per `.claude/rules/monotonic-time-indirection.md`: tests patch
  `clauditor._providers._anthropic._sleep` (or
  `_providers._openai._sleep`) rather than `asyncio.sleep`.
  Patching the stdlib originals under asyncio corrupts the event
  loop's own scheduler ticks; the alias indirection keeps the
  test scope tight. The shared retry module
  `_providers/_retry.py` exposes its own `_rand_uniform` for
  isolated-jitter testing.
- **`ModelResult` bundles what all callers need**: joined
  `response_text`, per-block `text_blocks`, `input_tokens`,
  `output_tokens`, `raw_message`, plus `provider` (`"anthropic"`
  or `"openai"`) and `source` (`"api"` or `"cli"`) so callers can
  stamp per-call provenance on their reports. Callers that want
  to distinguish "no text" from "empty text" check `text_blocks`;
  callers that want refusal handling read `raw_message`; metrics
  consumers read the token fields. One return type covers every
  current consumer without forcing them to dig through the raw
  SDK response. The legacy alias `AnthropicResult = ModelResult`
  is preserved so existing test fixtures and docstrings keep
  working.
- **Asymmetric transport: anthropic supports `{api,cli,auto}`,
  openai always `source="api"`.** Per DEC-002 of
  `plans/super/145-openai-provider.md`, OpenAI has no CLI
  transport axis. `call_openai` accepts the `transport=` kwarg at
  the signature level so the dispatcher can pass it uniformly,
  but the OpenAI backend ignores the value and unconditionally
  stamps `ModelResult.source = "api"`. Callers that want
  per-provider behavior branch on `result.provider`, not
  `result.source`.
- **`ImportError` is raised un-wrapped**: when the `anthropic` or
  `openai` SDK is not installed, the helper raises `ImportError`
  directly (not `AnthropicHelperError` / `OpenAIHelperError`).
  This preserves the existing `pip install clauditor[grader]`
  install-hint path every grader entry point already produces
  when users run the tool without the optional extra.

## Canonical implementation

`src/clauditor/_providers/__init__.py::call_model` — thin async
dispatcher. Validates `provider`, delegates `provider="anthropic"`
to `clauditor._providers._anthropic.call_anthropic` and
`provider="openai"` to `clauditor._providers._openai.call_openai`
via deferred per-call imports (per
`.claude/rules/back-compat-shim-discipline.md` Pattern 3, so test
patches on the canonical module path fire). Raises `ValueError`
for unknown values.

`src/clauditor/_providers/_anthropic.py::call_anthropic` — the
anthropic backend body, single async helper, exhaustive
unit-tested retry branches in `tests/test_providers_anthropic.py`.
The dataclass `ModelResult` (with back-compat alias
`AnthropicResult`) and the exception types `AnthropicHelperError`
/ `ClaudeCLIError` are part of the public surface for callers;
everything prefixed with `_` (`_body_excerpt`, `_extract_result`,
`_sleep`, `_rand_uniform`, `_rng`) is internal.

`src/clauditor/_providers/_openai.py::call_openai` — the openai
backend body (US-002+ of #145). Mirrors the anthropic seam's
structural shape — module-level test-indirection aliases,
sibling `OpenAIHelperError` exception class, deferred SDK import
where appropriate. Pins module-level constants
`DEFAULT_MODEL_L3 = "gpt-5.4"` and `DEFAULT_MODEL_L2 = "gpt-5.4-mini"`
per DEC-001 of `plans/super/145-openai-provider.md`. Catches
`(TypeError, OpenAIError)` at `AsyncOpenAI()` construction
because OpenAI's SDK raises its base `OpenAIError` immediately
when `OPENAI_API_KEY` is missing, whereas Anthropic raises
`TypeError` later at `messages.create()` — the `OpenAIError`
arm of the tuple covers OpenAI's earlier failure point.
Unit-tested retry branches in `tests/test_providers_openai.py`.

`src/clauditor/_providers/_retry.py` — shared retry policy module
(DEC-007 of `plans/super/145-openai-provider.md`). Hosts:

- `RATE_LIMIT_MAX_RETRIES = 3` / `SERVER_MAX_RETRIES = 1` /
  `CONN_MAX_RETRIES = 1` — the per-category retry caps both
  providers honor.
- `compute_backoff(retry_index)` — pure helper returning the
  delay for the `retry_index`-th retry (`2 ** retry_index` plus
  uniform `±25%` jitter, floored at 0).
- `compute_retry_decision(category, retry_index)` — pure helper
  returning `"retry"` or `"raise"` per the shared ladder. Each
  provider's `except` arms call this rather than open-coding
  the decision.

Per-provider concerns (the `_sleep` / `_monotonic` /
`_rand_uniform` / `_rng` test-indirection aliases) stay
per-provider — patching `_providers._openai._sleep` does not
clobber Anthropic's sleeping. The retry module owns the *policy*
(constants + decision logic); each provider owns its own clock
/ RNG indirection. Unit tests in `tests/test_providers_retry.py`.

`src/clauditor/_providers/_auth.py` — auth sub-seam shared
across providers. Hosts the per-provider auth helpers
(`check_any_auth_available`, `check_openai_auth`), the
multi-provider dispatcher `check_provider_auth(provider,
cmd_name)` (DEC-006 of #145; see also
`.claude/rules/multi-provider-dispatch.md`), the env-key probes
(`_api_key_is_set` / `_openai_api_key_is_set`), and the
implicit-coupling announcement family (see "Implicit-coupling
announcements" below).

`src/clauditor/_anthropic.py` — back-compat shim. Re-exports the
public Anthropic surface so existing call sites still importing
`from clauditor._anthropic` keep working unmodified for one
release. New code targets `clauditor._providers` directly. There
is no analogous `clauditor._openai.py` — the OpenAI surface is
new and ships in `_providers` from day one.

Call sites (six consumers — paths unchanged from #144; the
provider parameter resolves from
`eval_spec.grading_provider or "anthropic"` per
`.claude/rules/multi-provider-dispatch.md`):

- `src/clauditor/grader.py` — `extract_and_grade`,
  `extract_and_report` (Layer 2 schema extraction).
- `src/clauditor/quality_grader.py` — `grade_quality`,
  `blind_compare` (Layer 3 rubric grading + blind A/B).
- `src/clauditor/suggest.py` — the `clauditor suggest` command's
  edit-proposal call.
- `src/clauditor/triggers.py` — trigger precision judge.

Each call site's shape is the same: build a prompt with a pure
helper, `await call_model(prompt, provider=<resolved>, model=...,
transport=..., max_tokens=...)`, hand the resulting
`text_blocks[0]` to a pure parser/builder. See
`.claude/rules/pure-compute-vs-io-split.md` (LLM grader pure split
anchor) for how the pure layer that surrounds this seam is
structured.

### Multi-transport routing (CLI + SDK, #86) — Anthropic only

`call_anthropic` accepts a `transport: str = "auto"` keyword argument
(DEC-003 of `plans/super/86-claude-cli-transport.md`). The centralized
seam owns transport selection; every future caller inherits both the
SDK and CLI backends for free.

- **`"api"`** — HTTP SDK path via `AsyncAnthropic()`. Default before #86.
- **`"cli"`** — subprocess path via `harness.invoke()` (default harness is
  `ClaudeCodeHarness` in `src/clauditor/_harnesses/_claude_code.py`; reuses the
  same `InvokeResult` projection that `SkillRunner` uses).
- **`"auto"`** — prefers CLI when `shutil.which("claude")` is non-None;
  falls back to SDK otherwise. Emits a one-time stderr announcement on
  first auto→CLI resolution per process so operators are not surprised.

Transport resolution follows a four-layer precedence (see
`.claude/rules/spec-cli-precedence.md`): CLI flag > `CLAUDITOR_TRANSPORT`
env var > `EvalSpec.transport` > default `"auto"`. The shared helper
`clauditor.cli._resolve_grader_transport(args, eval_spec)` centralizes
the precedence logic for all six LLM-mediated CLI commands so whitespace
normalization and env stripping are applied uniformly.

`ModelResult.source` (`"api"` or `"cli"`) records which Anthropic
backend handled each call (legacy alias `AnthropicResult.source` is
preserved). For OpenAI calls, `source` is always `"api"` per DEC-002
of `plans/super/145-openai-provider.md` — there is no OpenAI CLI
transport to record. `BlindReport.transport_source` propagates this
through blind-compare; when the two parallel calls disagree
(unlikely in practice), the report stamps `"mixed"` (DEC-018).

### Implicit-coupling announcements — an emerging family

One-time-per-process stderr notices are an emerging family co-located
in the `clauditor._providers` package. Each member pairs a
module-level bool flag with an announcement-text constant; the
print-and-flip logic either lives inline at the call site (the first
member, from #86) or is factored into a public helper (the second
and third members, from #95 and #144 respectively, and the target
shape going forward). Three members today:

- `_announced_cli_transport` (bool flag) + `_CLI_AUTO_ANNOUNCEMENT`
  (plain `str` constant) in `src/clauditor/_providers/_anthropic.py`
  — from #86. Fires on auto→cli transport resolution. Print-and-flip
  is **inlined** inside `call_anthropic` (see the
  `if not _announced_cli_transport:` block near the bottom of the
  function). No standalone emitter helper.
- `_announced_implicit_no_api_key` (bool flag) +
  `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT` (`Final[str]` constant) in
  `src/clauditor/_providers/_auth.py` — from #95 US-002. Fires when
  `--transport cli` (or `CLAUDITOR_TRANSPORT=cli`) implicitly strips
  `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` from a skill subprocess
  env. Print-and-flip lives in the **public helper**
  `announce_implicit_no_api_key()`, called from the `env_override`
  computation in `cli/grade.py::cmd_grade` (see
  `.claude/rules/spec-cli-precedence.md` "Implicit coupling at the
  operator-intent layers" for the call-site contract).
- `_announced_call_anthropic_deprecation` (bool flag) +
  `_CALL_ANTHROPIC_DEPRECATION_NOTICE` (`Final[str]` constant) in
  `src/clauditor/_providers/_auth.py` — from #144 US-007. Fires on
  the first invocation per Python process of the back-compat shim
  `clauditor._anthropic.call_anthropic`. Print-and-flip lives in the
  **public helper** `announce_call_anthropic_deprecation()`, called
  from the shim's `call_anthropic` wrapper before each delegation to
  `call_model(provider="anthropic", ...)`. Three durable substrings
  pinned by tests: `"clauditor._anthropic"` (deprecated path),
  `"clauditor._providers"` (canonical replacement),
  `"will be removed"` (future-removal hint).

Per #145 the OpenAI backend deliberately did **not** introduce a
fourth announcement family. OpenAI's auth posture is strict
(`OPENAI_API_KEY` only — no CLI fallback, no env-stripping
implicit-coupling, no shim deprecation surface), so none of the
three existing announcement triggers map to OpenAI. Adding a
no-op flag for symmetry would create an empty member of the
family that future maintainers would have to defend against.
The empty result is itself the design decision: a future provider
that introduces a genuinely new implicit-coupling failure mode
(e.g. a Vertex auth-method-vs-credentials-file precedence
ambiguity) earns its own announcement family member; OpenAI as
shipped did not.

The #95 / #144 shape (`Final[str]` constant + public helper) is the target
pattern for new members — it makes the notice independently testable
without reaching into `call_anthropic` internals. New announcement
flags belong in the `_providers` package (DEC-009 of
`plans/super/95-subscription-auth-flag.md`); auth-coupled and
deprecation-coupled notices in `_providers/_auth.py`, transport-
coupled notices in `_providers/_anthropic.py`. Reset mechanism for
tests is the `monkeypatch.setattr(..., False)` autouse fixture
pattern — see
`tests/test_providers_anthropic.py::TestStderrAnnouncement`,
`tests/test_providers_auth.py::TestAnnounceImplicitNoApiKey`, and
`tests/test_providers_auth.py::TestCallAnthropicDeprecationAnnouncement`
for the shape.

## When this rule applies

Any new clauditor feature that needs to call a model provider — a
new grader tier, an auto-triage judge, a rubric critic, a
regeneration-on-low-score loop. Import `call_model` from
`clauditor._providers` and call it with the resolved `provider=`
string. Do NOT construct `AsyncAnthropic()` / `AsyncOpenAI()`
directly in the new module; do NOT catch `RateLimitError` /
`APIStatusError` at the call site to implement a bespoke retry
loop. If the centralized retry policy is genuinely wrong for a new
use case, change the policy in `_providers/_retry.py` (with tests
covering the new branches) so every provider inherits the fix.

## When this rule does NOT apply

- Non-Anthropic / non-OpenAI API clients (Vertex via direct REST,
  local Ollama, a third-party judge service). The centralized
  helper today supports only the two providers; a sibling helper
  per provider is fine, and the rule's shape (centralize retry +
  error categorization in one seam) should be replicated for
  each — typically as a new `_providers/_<name>.py` module with
  a matching `call_<name>` function and a sibling
  `<Name>HelperError` class, plus a new branch in
  `call_model`'s dispatcher.
- Synchronous one-off scripts in `scripts/` that want to hit an
  SDK directly for a diagnostic. They can construct a client
  inline — but production paths loaded via `import clauditor.*`
  must go through the helper.
- Tests that mock a provider entirely. The canonical mock
  targets are
  `clauditor._providers._anthropic.call_anthropic` and
  `clauditor._providers._openai.call_openai` (or a per-call-site
  patch of the same symbols), never the `anthropic` / `openai`
  SDKs directly. Tests that patched the legacy path
  `clauditor._anthropic.X` need to follow the symbol to its new
  location — Python's `monkeypatch.setattr` operates on the
  module where the symbol lives, so re-exports through the
  back-compat shim do not propagate patches.
