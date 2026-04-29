# Rule: Route every Anthropic SDK call through the centralized helper

When any clauditor module needs to call the Anthropic API, it must go
through `clauditor._anthropic.call_anthropic` rather than constructing
its own `AsyncAnthropic` client or `messages.create` call. The helper
owns retry policy, error categorization, token accounting, and the
`AnthropicHelperError` user-facing error envelope. Bypassing it means
each new caller re-implements (and drifts on) the retry/back-off/auth-
error-message logic the rest of clauditor depends on.

## The pattern

```python
# At the call site:
from clauditor._anthropic import AnthropicHelperError, call_anthropic

try:
    result = await call_anthropic(prompt, model=model, max_tokens=4096)
except AnthropicHelperError as exc:
    # User-facing message already formatted (auth hint, status code,
    # body excerpt). Surface to stderr, set exit code, do NOT retry.
    print(f"ERROR: {exc}", file=sys.stderr)
    raise
response_text = result.text_blocks[0] if result.text_blocks else ""
# result.input_tokens / result.output_tokens for metrics
# result.raw_message for refusal / tool-use inspection
```

Inside `_anthropic.py`:

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
    # compute exponential backoff with ±25% jitter, raise
    # AnthropicHelperError on non-retriable or exhausted failures ...
```

## Why this shape

- **One retry taxonomy, consistently applied**:
  - `RateLimitError` (HTTP 429) → up to 3 retries (4 attempts total).
  - `APIStatusError` with `status_code >= 500` → 1 retry then raise.
  - `APIStatusError` 4xx (other than 401/403) → no retry; raise
    immediately (bad request, not found, conflict, etc).
  - `AuthenticationError` (401) / `PermissionDeniedError` (403) → no
    retry; raise immediately with a message pointing at
    `ANTHROPIC_API_KEY`.
  - `APIConnectionError` → 1 retry then raise.
  If every call site rolled its own, one module would treat 500s as
  permanent and another as transient; operators would see inconsistent
  failure behavior from identical API conditions.
- **Exponential backoff with ±25% jitter**: the delay for retry index
  `i` is `2 ** i` seconds (i.e. 1 s, 2 s, 4 s) multiplied by a
  uniform random factor in `[0.75, 1.25]`. Jitter avoids the stampede
  failure mode where two concurrent callers retry on the same wall-
  clock tick and both hit the same throttling window again.
- **Auth errors include the env-var hint**: an operator-friendly
  `"check the ANTHROPIC_API_KEY environment variable"` is attached to
  every 401/403 message. A scattered SDK-call style would leave this
  up to each call site and at least one would forget.
- **`AnthropicHelperError` wraps the original exception**: non-
  retriable SDK errors are re-raised as
  `AnthropicHelperError(message) from exc`, so `__cause__` preserves
  the original exception for callers that want to introspect (e.g.
  for status code), while new callers can just `except
  AnthropicHelperError` for the pre-formatted user-facing message.
- **`_sleep` / `_rand_uniform` module-level aliases** per
  `.claude/rules/monotonic-time-indirection.md`: tests patch
  `clauditor._anthropic._sleep` and `clauditor._anthropic._rand_uniform`
  rather than `asyncio.sleep` / `random.uniform`. Patching the
  stdlib originals under asyncio corrupts the event loop's own
  scheduler ticks; the alias indirection keeps the test scope tight.
- **`AnthropicResult` bundles what all callers need**: joined
  `response_text`, per-block `text_blocks`, `input_tokens`,
  `output_tokens`, `raw_message`. Callers that want to distinguish
  "no text" from "empty text" check `text_blocks`; callers that want
  refusal handling read `raw_message`; metrics consumers read the
  token fields. One return type covers every current consumer without
  forcing them to dig through the raw SDK response.
- **`ImportError` is raised un-wrapped**: when the `anthropic` SDK is
  not installed, the helper raises `ImportError` directly (not
  `AnthropicHelperError`). This preserves the existing
  `pip install clauditor[grader]` install-hint path every grader
  entry point already produces when users run the tool without the
  optional extra.

## Canonical implementation

`src/clauditor/_anthropic.py::call_anthropic` — single async helper,
~90 effective lines, exhaustive unit-tested retry branches in
`tests/test_anthropic.py`. The dataclass `AnthropicResult` and the
exception type `AnthropicHelperError` are part of the public surface
for callers; everything prefixed with `_` (`_compute_backoff`,
`_body_excerpt`, `_extract_result`, `_sleep`, `_rand_uniform`,
`_rng`) is internal.

Call sites (four consumers):

- `src/clauditor/grader.py` — `extract_and_grade`,
  `extract_and_report` (Layer 2 schema extraction).
- `src/clauditor/quality_grader.py` — `grade_quality`,
  `blind_compare` (Layer 3 rubric grading + blind A/B).
- `src/clauditor/suggest.py` — the `clauditor suggest` command's
  edit-proposal call.
- `src/clauditor/triggers.py` — trigger precision judge.

Each call site's shape is the same: build a prompt with a pure
helper, `await call_anthropic(prompt, model=..., max_tokens=...)`,
hand the resulting `text_blocks[0]` to a pure parser/builder. See
`.claude/rules/pure-compute-vs-io-split.md` (LLM grader pure split
anchor) for how the pure layer that surrounds this seam is
structured.

### Multi-transport routing (CLI + SDK, #86)

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

`AnthropicResult.source` (`"api"` or `"cli"`) records which backend
handled each call. `BlindReport.transport_source` propagates this through
blind-compare; when the two parallel calls disagree (unlikely in practice),
the report stamps `"mixed"` (DEC-018).

### Implicit-coupling announcements — an emerging family

One-time-per-process stderr notices are an emerging family co-located
in `src/clauditor/_anthropic.py`. Each member pairs a module-level
bool flag with an announcement-text constant; the print-and-flip
logic either lives inline at the call site (the first member, from
#86) or is factored into a public helper (the second member, from
#95, and the target shape going forward). Two members today:

- `_announced_cli_transport` (bool flag) + `_CLI_AUTO_ANNOUNCEMENT`
  (plain `str` constant) — from #86. Fires on auto→cli transport
  resolution. Print-and-flip is **inlined** inside `call_anthropic`
  (see the `if not _announced_cli_transport:` block near the bottom
  of the function). No standalone emitter helper.
- `_announced_implicit_no_api_key` (bool flag) +
  `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT` (`Final[str]` constant) — from
  #95 US-002. Fires when `--transport cli` (or
  `CLAUDITOR_TRANSPORT=cli`) implicitly strips `ANTHROPIC_API_KEY` /
  `ANTHROPIC_AUTH_TOKEN` from a skill subprocess env. Print-and-flip
  lives in the **public helper** `announce_implicit_no_api_key()`,
  called from the `env_override` computation in
  `cli/grade.py::cmd_grade` (see `.claude/rules/spec-cli-precedence.md`
  "Implicit coupling at the operator-intent layers" for the call-site
  contract).

The #95 shape (`Final[str]` constant + public helper) is the target
pattern for new members — it makes the notice independently testable
without reaching into `call_anthropic` internals. New announcement
flags belong in the same module (DEC-009 of
`plans/super/95-subscription-auth-flag.md`). Reset mechanism for
tests is the `monkeypatch.setattr(..., False)` autouse fixture
pattern — see `tests/test_anthropic.py::TestStderrAnnouncement` and
`TestAnnounceImplicitNoApiKey` for the shape.

## When this rule applies

Any new clauditor feature that needs to call Anthropic — a new
grader tier, an auto-triage judge, a rubric critic, a regeneration-
on-low-score loop. Import `call_anthropic` and use it. Do NOT
construct `AsyncAnthropic()` directly in the new module; do NOT
catch `RateLimitError` / `APIStatusError` at the call site to
implement a bespoke retry loop. If the centralized retry policy is
genuinely wrong for a new use case, change the policy in
`_anthropic.py` (with tests covering the new branches) so every
caller inherits the fix.

## When this rule does NOT apply

- Non-Anthropic API clients (OpenAI, local Ollama, a third-party
  judge service). The centralized helper is Anthropic-specific; a
  sibling helper per provider is fine, and the rule's shape
  (centralize retry + error categorization in one seam) should be
  replicated for each.
- Synchronous one-off scripts in `scripts/` that want to hit the SDK
  directly for a diagnostic. They can construct a client inline —
  but production paths loaded via `import clauditor.*` must go
  through the helper.
- Tests that mock Anthropic entirely. The mock target is
  `clauditor._anthropic.call_anthropic` (or a per-call-site patch of
  the same symbol), never the `anthropic` SDK directly.
