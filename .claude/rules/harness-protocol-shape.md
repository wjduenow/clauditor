# Harness protocol shape

When adding a new harness implementation (Codex per #149, future raw-API
agent loop, etc.), conform to the `Harness` protocol surface defined in
`src/clauditor/_harnesses/__init__.py`. The protocol is **structurally
typed** and decorated `@runtime_checkable`, so `isinstance(obj, Harness)`
works as a duck-typed drift-guard — but `runtime_checkable` only checks
member presence, not signature shape; sibling tests in
`tests/test_runner.py::TestHarnessProtocol` use `inspect.signature` to
lock the parameter set. A harness is "in" the moment it provides four
members (`name`, `invoke`, `strip_auth_keys`, `build_prompt`) with
matching signatures.

## The pattern

```python
from typing import ClassVar
from clauditor.runner import InvokeResult


class MyHarness:
    name: ClassVar[str] = "my-harness"

    def __init__(
        self,
        *,
        # Whatever construction kwargs YOUR harness needs.
        # Per-harness knobs (e.g. ClaudeCodeHarness's
        # ``allow_hang_heuristic``) live here, NOT on ``invoke``.
        my_bin: str = "my-cli",
        model: str | None = None,
    ) -> None:
        self.my_bin = my_bin
        self.model = model

    def invoke(
        self,
        prompt: str,
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
        timeout: int,
        model: str | None = None,
        subject: str | None = None,
    ) -> InvokeResult:
        # Run your CLI / API loop. Return a fully populated
        # ``InvokeResult`` per the docstring on that dataclass.
        ...

    def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]:
        # Return a NEW dict with your harness's auth env vars removed.
        # Pure, non-mutating per non-mutating-scrub.md.
        ...

    def build_prompt(
        self,
        skill_name: str,
        args: str,
        *,
        system_prompt: str | None,
    ) -> str:
        # Compose the wire-shape prompt this harness's ``invoke``
        # expects. Pure compute (no I/O, no global state) per
        # pure-compute-vs-io-split.md. Each harness owns its own
        # rendering — Claude Code uses ``"/{skill_name} {args}"``;
        # raw-API harnesses embed ``system_prompt`` in a structured
        # message body. Harnesses with no notion of a separate system
        # prompt MUST still accept and ignore the kwarg.
        ...
```

## Rationale

Four members keep the protocol minimal: identity (`name`), the work
itself (`invoke`), a non-mutating env scrub (`strip_auth_keys`), and the
pure prompt composer (`build_prompt`). Construction parameters are
entirely per-harness — they don't appear on the protocol surface — so
harness-specific knobs cannot leak into cross-harness code.

### Why `name: ClassVar[str]`

Sidecar comparability (`audit`, `trend`, `compare`) groups by harness
identity. Class-level so the value is immutable per harness type — no
instance variance. `ClaudeCodeHarness.name = "claude-code"`,
`MockHarness.name = "mock"`. Future: `CodexHarness.name = "codex"`.

### Why `invoke` accepts `subject: str | None = None`

`subject` is an optional human-readable label (e.g. `"L2 extraction"`)
that harnesses MAY use to enrich observability output (logs, warning
suffixes). Harnesses that have no equivalent should still accept and
ignore the kwarg — analogous to how all harnesses accept `model` even
if they bind it to a fixed value internally. This keeps `_anthropic.py`'s
call site (`_default_harness.invoke(prompt, ..., subject=subject)`)
substitutable for any conforming harness.

### Why `model` is on `invoke`, not construction

Two reasons: (1) per-call overrides matter for provider-axis evaluation
(grading the same prompt against multiple models); (2) parity with the
`subject` kwarg keeps the per-call surface uniform. A harness MAY ignore
`model` (Codex pinned to `gpt-5-mini` ignores it; raw-API uses it as a
default override).

### Why `allow_hang_heuristic` is NOT on `invoke`

Per DEC-008 of `plans/super/148-extract-harness-protocol.md`, the
`allow_hang_heuristic` knob is Claude-Code-specific (it gates
`_detect_interactive_hang` and `_detect_background_task_noncompletion`,
both stream-json-shaped). Putting it on the cross-harness protocol
would force Codex/raw-API implementers to accept a meaningless flag.
Per-harness knobs live in `__init__`.

### `harness_metadata` as forward-compat surface

`InvokeResult` and `SkillResult` both carry `harness_metadata: dict[str,
Any] = field(default_factory=dict)` per DEC-007. Harnesses with native
shape that doesn't fit `raw_messages` / `stream_events` (Codex's
`reasoning` items, a raw-API harness's reasoning tokens, anthropic-CLI
hidden system prompts) populate `harness_metadata` with their own keys.
`SkillRunner._invoke` projects this field through verbatim so future
sidecars can surface harness-specific observability without bumping a
schema version.

### Why `build_prompt` is on the protocol (not a free function)

Each harness's wire-shape is structurally different: Claude Code consumes
`"/{skill_name} {args}"` slash-commands resolved by `claude -p`; future
Codex / raw-API harnesses prepend `system_prompt` and append `args` as
distinct message-body components. Putting `build_prompt` on the protocol
lets every harness own its own rendering without forcing
`SkillRunner.run` to branch on harness type. Per US-001 of issue #150,
the method is **pure compute** — no file I/O, no global state — so
auto-derive of `system_prompt` from `SKILL.md` body lives one layer up
in `SkillSpec.run` (which wraps read/parse failures in a `RuntimeError`
naming the skill + path with `__cause__` chained, per
`.claude/rules/pure-compute-vs-io-split.md`).

### Why `system_prompt` on `build_prompt` is keyword-only

Per US-001 of issue #150, `system_prompt` is a positional-swap risk
against `args` (both are strings; transposing them silently sends the
wrong text to the LLM). Marking it keyword-only after `*` makes the
mistake unrepresentable: `harness.build_prompt("foo", "bar", "baz")`
would `TypeError` instead of binding `"baz"` to `system_prompt`.
Harnesses with no notion of a separate system prompt
(`ClaudeCodeHarness`) MUST still accept and ignore the kwarg — analogous
to how all harnesses accept `model` on `invoke` even if they pin a
fixed value internally.

## Canonical implementations

- `src/clauditor/_harnesses/_claude_code.py::ClaudeCodeHarness` — the
  primary implementation; subprocess + stream-json parser.
  `build_prompt` returns `f"/{skill_name}"` when `args == ""`, else
  `f"/{skill_name} {args}"`; ignores `system_prompt`.
- `src/clauditor/_harnesses/_mock.py::MockHarness` — production-grade
  protocol implementation (NOT just a test fake). Records every
  `invoke(...)` and `build_prompt(...)` call on `invoke_calls` and
  `build_prompt_calls` respectively, returns a configurable
  `InvokeResult`. Use for `SkillRunner(harness=MockHarness(...))` in
  unit tests when you don't want to mock subprocess. **Because
  `MockHarness` is a real protocol implementation, every protocol
  addition MUST update it in the same PR** — otherwise `isinstance(mock,
  Harness)` silently breaks for any test that relied on it.

## Adding a new protocol member — checklist

When extending the protocol with a new member (`build_prompt` from #150
is the first such extension since the protocol was extracted in #148):

1. Define the member on `Harness` in `src/clauditor/_harnesses/__init__.py`
   with a clear docstring noting purity and per-harness ownership.
2. Implement on **every** existing harness in the same PR — including
   `MockHarness` (it is production-grade, not a test fake; see canonical
   implementations above). A skipped implementation silently breaks
   `isinstance(harness, Harness)` for any caller relying on the
   `runtime_checkable` drift-guard.
3. Keep I/O at a higher layer. If the new member needs a side-effectful
   resolver (e.g. `build_prompt`'s `system_prompt` auto-derive), put the
   I/O in `SkillSpec.run` (or its analogue) and pass the resolved value
   into the pure protocol method, per
   `.claude/rules/pure-compute-vs-io-split.md`.
4. Update the count in this rule's opening paragraph and rationale
   header. The signature drift between rule prose and protocol code is
   real footgun; keeping the count current is the cheap audit signal.
5. Add a signature-locking test in
   `tests/test_runner.py::TestHarnessProtocol` using `inspect.signature`
   to assert the parameter set, kinds (positional vs keyword-only), and
   return annotation. Keyword-only parameters are particularly
   sensitive — `runtime_checkable` does not enforce them.

## Adding a new harness — checklist

1. Create `src/clauditor/_harnesses/_<name>.py` (private module
   convention — leading underscore).
2. Define the class with the four protocol members. Match parameter
   names and types exactly.
3. Construct an `InvokeResult` populated with semantic-equivalent
   fields (`output`, `exit_code`, `duration_seconds`, `error`,
   `error_category`, `input_tokens`, `output_tokens`, `warnings`,
   `api_key_source`, `harness_metadata`). Fields you cannot populate
   meaningfully default to empty/zero — never raise.
4. Pure helpers (parsers, classifiers) live as module-private
   functions (`_classify_*`, `_detect_*`) at the top of the file,
   parallel to `_claude_code.py`'s structure.
5. Add `_monotonic = time.monotonic` at module level per
   `monotonic-time-indirection.md` if you measure duration.
6. Cover the implementer with direct unit tests in
   `tests/test_runner.py` or a sibling test module. Patch
   `subprocess.Popen` (or your CLI's analogue) at the harness module
   path, not at `clauditor.runner`.

## When NOT to use this pattern

The protocol is for **harness invocation** — running a single LLM CLI
prompt-and-response cycle. It is not for graders, scorers, or reporting
code. Those layers above the harness already have their own seams
(`call_anthropic`, `quality_grader`, `assertions`). Do not widen the
protocol to absorb concerns that already have a home.
