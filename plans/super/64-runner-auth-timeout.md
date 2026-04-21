# Super Plan: #64 — Runner auth-source control + configurable timeout

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/64
- **Branch:** `feature/64-runner-auth-timeout`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/64-runner-auth-timeout`
- **Phase:** `detailing`
- **Sessions:** 1
- **Last session:** 2026-04-20

---

## Discovery

### Ticket summary

**What:** Two runner defects plus one observability miss, all
rooted in `src/clauditor/runner.py`:

1. **Auth source is uncontrollable.** `SkillRunner._invoke` spawns
   `subprocess.Popen` with no `env=` argument (runner.py:367–380).
   The child inherits the full parent environment, which means a
   dev shell that has `ANTHROPIC_API_KEY` set (most of them, for
   unrelated tools) forces `claude -p` onto the lowest API tier
   (30k input tokens/min). There is no flag to opt into
   subscription (Pro/Max) auth, which has much higher throughput.
2. **Timeout is hardcoded.** `SkillRunner.__init__` defaults
   `timeout=180` (runner.py:255). No CLI flag, no per-spec
   override. Research-heavy skills (multi-agent, deep-research)
   legitimately need 5–15 minutes and get killed at 180.05s by the
   watchdog.
3. **`apiKeySource` observability gap.** The stream-json `init`
   event already reports which auth source the CLI ran against
   (`"ANTHROPIC_API_KEY"` / `"claude.ai"` / `"none"`). Clauditor
   currently ignores it. Surfacing it tells users which tier just
   validated their assertions.

**Why:** Anyone testing a non-trivial skill on a Pro/Max plan is
blocked: the env-var path forces API-tier rate limits, and even
after stripping `ANTHROPIC_API_KEY` manually the 180s wall kills
the legitimate run. This is a specific class of user (Pro/Max
subscribers iterating on research skills) who currently cannot use
clauditor at all without editing the source.

**Done when:**
- A CLI flag exists on `validate` / `grade` / `capture` / `run`
  that, when set, strips `ANTHROPIC_API_KEY` from the subprocess
  environment so `claude -p` uses whatever auth is cached in
  `~/.claude/` (typically subscription).
- A CLI flag exists on the same commands to override the runner
  timeout; a spec-level field overrides the default for a
  specific skill.
- The stream-json `init` event's `apiKeySource` is surfaced — at
  minimum as a stderr info line, ideally as a `SkillResult` field
  accessible to tests.
- Coverage stays ≥80%; ruff passes.

### Key findings — codebase scout

#### Bug site 1: `src/clauditor/runner.py::_invoke` (lines 367–380)

```python
proc = subprocess.Popen(
    [
        self.claude_bin,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    cwd=str(cwd) if cwd is not None else str(self.project_dir),
)
```

No `env=` argument — child inherits full parent environment.

#### Bug site 2: `src/clauditor/runner.py::SkillRunner.__init__` (252–260)

```python
def __init__(
    self,
    project_dir: str | Path | None = None,
    timeout: int = 180,
    claude_bin: str = "claude",
):
    self.project_dir = Path(project_dir) if project_dir else Path.cwd()
    self.timeout = timeout
    self.claude_bin = claude_bin
```

Hardcoded `timeout=180`. Watchdog enforcement lives at
`runner.py:446–448` (a `threading.Timer(self.timeout, _on_timeout)`
that calls `proc.kill()` on expiry).

#### Observability miss: stream-json `init` parsing (runner.py:~452–480)

The for-loop reads `mtype = msg.get("type")` and handles only
`"assistant"` and `"result"`. The `"init"` message type — which
carries `apiKeySource`, `model`, and other init metadata — is
appended to `raw_messages` / `stream_events` unchanged but never
inspected. `SkillResult` has no `api_key_source` field.

#### Call sites of `SkillRunner(...)` (six total)

1. `src/clauditor/cli/capture.py:69` — bare constructor.
2. `src/clauditor/cli/run.py:28–31` — `project_dir`, `timeout`
   (already parses `--timeout` CLI arg).
3. `src/clauditor/pytest_plugin.py:90–93` — fixture, threads
   `timeout` from the `--clauditor-timeout` pytest option.
4. `src/clauditor/pytest_plugin.py:130–133` — factory fixture,
   threads `timeout` from the same option.
5. `src/clauditor/spec.py:61` — conditional:
   `runner or SkillRunner(project_dir=...)`.
6. `tests/test_runner.py` — ~30 direct test instantiations.

#### EvalSpec today (`src/clauditor/schemas.py:236–269`)

```python
@dataclass
class EvalSpec:
    skill_name: str
    description: str = ""
    test_args: str = ""
    user_prompt: str | None = None
    input_files: list[str] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    sections: list[SectionRequirement] = field(default_factory=list)
    grading_criteria: list = field(default_factory=list)
    grading_model: str = "claude-sonnet-4-6"
    output_file: str | None = None
    output_files: list[str] = field(default_factory=list)
    trigger_tests: TriggerTests | None = None
    variance: VarianceConfig | None = None
    grade_thresholds: GradeThresholds | None = None
    allow_hang_heuristic: bool = True
```

No `timeout` field. `allow_hang_heuristic` (#63's precedent) is
the last field added — it's threaded from spec → `SkillSpec.run`
→ `SkillRunner.run(..., allow_hang_heuristic=...)` in
`src/clauditor/spec.py:140–150`. That is the parallel shape a
spec-level `timeout` would follow.

### Key findings — convention checker

**Directly load-bearing rules:**

- **`.claude/rules/subprocess-cwd.md`** — the `env=` kwarg on
  `SkillRunner.run` (and inside `_invoke`'s `Popen` call) must
  follow the same keyword-only, `None`-default, resolve-at-call-
  time pattern as `cwd`. This is the canonical shape for threading
  optional subprocess params without disturbing existing call
  sites.
- **`.claude/rules/stream-json-schema.md`** — reading `apiKeySource`
  from the `init` message must be defensive: `.get()` with falsy-
  safe defaults, `isinstance` guards, skip-and-warn on malformed
  lines. Never abort on a missing field. The rule also mandates
  documenting any newly-read field in `docs/stream-json-schema.md`
  as a companion update.
- **`.claude/rules/pre-llm-contract-hard-validate.md`** — CLI flag
  values (`--timeout`) and spec-level `timeout` must hard-validate
  at parse/load time with a clear error. Negative or zero timeouts
  reject before any runner spins up.
- **`.claude/rules/llm-cli-exit-code-taxonomy.md`** — a malformed
  `--timeout` routes to exit 2 (input-validation); this is a pre-
  call input check regardless of whether the command wraps an LLM.

**Tangential rules:**

- **`.claude/rules/json-schema-version.md`** — only fires if we
  persist `api_key_source` into an already-versioned sidecar.
  Adding `timeout` to in-memory `EvalSpec` does not require a
  version bump by itself.
- **`.claude/rules/eval-spec-stable-ids.md`** — no new per-entry
  id fields. N/A.
- **`.claude/rules/pure-compute-vs-io-split.md`** — small helper
  for "resolve effective env dict from auth-source choice + os
  environ" could be a pure function worth factoring out.

**Project-wide conventions:**

- CLI flags are kebab-case. Binary flags use `--no-foo` style
  (`--no-transcript`, `--no-verify`). Int-valued flags use
  positional int with a metavar (`--iteration N`, `--timeout
  SECONDS`).
- Existing pytest plugin already carries `--clauditor-timeout`
  (no auth-source equivalent yet).
- No `.claude/workflow-project.md` file — no project-specific
  scoping questions or review areas.

### Precedent plans

- **#63 runner-error-surfacing (shipped)** — added
  `error_category` to `SkillResult`, introduced
  `_classify_result_message` pure helper, threaded
  `allow_hang_heuristic` from EvalSpec → runner. This is the
  closest-shape precedent: same file (`runner.py`), same
  cross-layer threading (spec → runner kwarg), same
  `docs/stream-json-schema.md` companion update contract.
- **#22 iteration-workspace** — atomic publication pattern;
  tangential.
- **#26 execution-transcripts** — stream-json persistence;
  tangential.

### Proposed scope

Three concurrent tracks that share `runner.py` but are
architecturally independent:

1. **Auth source control.** Add a keyword-only `env` override to
   `SkillRunner.run` (defaulting to `None` → `os.environ.copy()`),
   threaded from a new `--no-api-key` CLI flag that, when set,
   constructs the env dict with `ANTHROPIC_API_KEY` stripped.
2. **Timeout configurability.** Add `--timeout <seconds>` to CLI
   commands that lack it, and a top-level `timeout` field on
   `EvalSpec`. Precedence: CLI flag > spec field > default
   (180 preserved).
3. **`apiKeySource` observability.** Extend the stream-json parser
   to capture `apiKeySource` from the `init` event; add
   `api_key_source: str | None = None` to `SkillResult`; surface
   it in stderr / CLI summary. Document the field in
   `docs/stream-json-schema.md`.

### Scoping decisions

- **Q1 — Auth-source flag shape.** `--no-api-key` (boolean).
  Smallest surface, mirrors `--no-transcript`.
- **Q2 — Timeout placement.** Both CLI flag and `EvalSpec.timeout`
  field. Precedence: CLI flag > spec field > default.
- **Q3 — Default timeout value.** Keep 180s. Additive change;
  authors opt in.
- **Q4 — Auth source in EvalSpec.** No — CLI-only. Auth source is
  environmental, not skill-intrinsic.
- **Q5 — `apiKeySource` surfacing.** Stderr info line **plus**
  `SkillResult.api_key_source` field (`str | None`, defensive).
- **Q6 — Commands.** `validate`, `grade`, `capture`, `run`, plus
  pytest plugin option (`--clauditor-no-api-key` alongside the
  existing `--clauditor-timeout`).

---

## Architecture Review

Six baseline review areas run in parallel. Ratings:

| Area | Rating | Summary |
|------|--------|---------|
| Security | **blocker** | `ANTHROPIC_AUTH_TOKEN` is a second Anthropic env-auth path the plan does not account for. |
| Performance | **pass** | Trivial: one env copy / one parse branch / one dataclass field per run. |
| Data Model | **concern** | Bool-guard needed on `EvalSpec.timeout`; `docs/stream-json-schema.md` companion update is mandatory. |
| API Design | **concern** | Move `timeout` to keyword-only `run()` kwarg; extract pure `_env_without_api_key()` helper. |
| Observability | **concern** | Suppress stderr line when `api_key_source is None` (don't print "unavailable"). |
| Testing Strategy | **pass** | ~25–30 new tests across six files; no new test files needed. |

### Blockers

**BL-1 — `ANTHROPIC_AUTH_TOKEN` second env-auth path.**
The Anthropic SDK recognizes both `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` as env-based auth paths. A user who has
`ANTHROPIC_AUTH_TOKEN` set and expects `--no-api-key` to force
subscription auth will silently still use token-based API auth.
Two options:

- **BL-1a. `--no-api-key` strips both `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN`.** Flag name means "no env-based API
  auth." Simple and matches the user intent.
- **BL-1b. Rename the flag to something precise.** E.g.
  `--strip-api-auth` or `--use-subscription`. The flag then
  unambiguously strips all env-based auth paths.

### Concerns

**C-1 — Interactive hang on missing subscription auth
(security).** If a user sets `--no-api-key` but has no cached
`~/.claude/` subscription auth, `claude -p` will block on stdin
waiting for interactive login. The existing interactive-hang
heuristic from #63 fires on `num_turns==1 + trailing "?"`
patterns, not on pre-stream-json stdin blocking. Failure mode:
watchdog timeout → ambiguous "timeout" error. Acceptable cost
for v1: the user sees a timeout and a stderr line telling them
`apiKeySource=` is absent, which is a strong signal. Not a
blocker.

**C-2 — `EvalSpec.timeout` validation must exclude `bool`
(data model).** Python `isinstance(True, int)` returns `True`
because `bool` is an `int` subclass. The validator must do
`isinstance(val, int) and not isinstance(val, bool)` per the
precedent in `.claude/rules/constant-with-type-info.md`.

**C-3 — `docs/stream-json-schema.md` companion update (data
model).** Per `.claude/rules/stream-json-schema.md`, every new
parser field requires a doc update in the same PR. Add a row
for `apiKeySource` under the `type: "system"` / `subtype:
"init"` section. Note the label-not-secret observation from
Observability review.

**C-4 — Move `timeout` from `__init__` to keyword-only
`run()` kwarg (API design).** Today
`SkillRunner(timeout=180).__init__` stores `self.timeout`. Per
`.claude/rules/subprocess-cwd.md`, per-invocation config
belongs on `run()` kwargs, defaulting to `self.timeout` for
back-compat with the 6 existing constructor call sites. Add
keyword-only `timeout: int | None = None` on `run()`; resolve
as `effective_timeout = timeout if timeout is not None else
self.timeout`. No caller changes required.

**C-5 — Extract `_env_without_api_key()` pure helper (API
design).** Per `.claude/rules/pure-compute-vs-io-split.md`,
lift the "copy env and strip the two auth vars" logic into a
pure helper in `runner.py`. Call sites (CLI commands, pytest
plugin) then call one function instead of re-rolling the
comprehension. Testable without subprocess mocks.

**C-6 — Suppress stderr info line when `api_key_source is
None` (observability).** When the `init` message doesn't
carry `apiKeySource` (older `claude` CLI builds), emit
nothing — don't print `apiKeySource=unavailable`. The absence
is already the signal.

### Accepted as-is

- Performance: one env copy per run is trivial; one extra
  branch in the stream-json parser is constant-time.
- Testing: the proposed test plan (~25–30 new methods across
  existing files) is comprehensive. No new test files needed.
  Coverage gate (80%) stays green by construction.
- Secret redaction: `apiKeySource` values are labels
  (`"ANTHROPIC_API_KEY"`, `"claude.ai"`, `"none"`), not
  secrets. Existing `transcripts.py::redact` handles any
  unexpected shape regressions.
- CLI arg validation: `argparse type=int` syntax check is
  fine; range check (`> 0`) lives in the `EvalSpec` validator
  per C-2 and the CLI-side reject-early pattern per
  `.claude/rules/llm-cli-exit-code-taxonomy.md`.

---

## Refinement Log

### Decisions

**DEC-001 — Auth flag shape: `--no-api-key` (boolean).** One
binary flag matches the project's `--no-<feature>` style
(`--no-transcript`). (Q1 answer A.)

**DEC-002 — Timeout precedence: CLI > spec > default.** The CLI
flag wins when passed explicitly; otherwise the `EvalSpec.timeout`
field wins; otherwise the hardcoded default (180s). Resolution
happens in `SkillSpec.run` per the `allow_hang_heuristic`
precedent from #63. (Q2 answer A.)

**DEC-003 — Default timeout stays at 180s.** Additive change;
authors opt in to higher timeouts. Changing the default would
silently alter behavior for every existing spec. (Q3 answer A.)

**DEC-004 — Auth source is CLI-only; not an `EvalSpec` field.**
Auth source is environmental, not skill-intrinsic. A spec author
should not embed "I expect subscription auth" in the spec — that
is a quota / tier preference of the operator running the eval.
(Q4 answer A.)

**DEC-005 — `apiKeySource` surfaces as both stderr info line AND
`SkillResult.api_key_source` field.** Humans see the stderr line;
tests and downstream consumers read the field. Field type is
`str | None`. (Q5 answer B.)

**DEC-006 — Flags land on `validate`, `grade`, `capture`, `run`;
pytest plugin gets `--clauditor-no-api-key`.** All four
skill-invoking CLI commands plus the pytest fixture factory
config. (Q6 answer A + B.)

**DEC-007 — `--no-api-key` strips BOTH `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN`.** Both are documented Anthropic SDK
env-auth paths. Stripping only one leaves the flag name
misleading. Flag-name interpretation: "no env-based API auth
credential." (BL-1 resolved as BL-1a.)

**DEC-008 — `EvalSpec.timeout` validator excludes `bool`.**
`isinstance(True, int)` returns `True` because `bool` is an `int`
subclass. Validator is
`isinstance(val, int) and not isinstance(val, bool)` per the
canonical pattern in `.claude/rules/constant-with-type-info.md`.
Error message follows the existing style
(`"EvalSpec(skill_name=…): 'timeout' must be an int, got …"`).
(C-2.)

**DEC-009 — `docs/stream-json-schema.md` companion update in the
same PR.** Add a row for `apiKeySource` under the
`type: "system"` / `subtype: "init"` section with values
(`"ANTHROPIC_API_KEY"`, `"claude.ai"`, `"none"`) and a
"label, not secret" note to preempt future redaction questions.
Two-step recipe per `.claude/rules/stream-json-schema.md`. (C-3.)

**DEC-010 — Move `timeout` from `SkillRunner.__init__` to a
keyword-only `run()` kwarg; keep `self.timeout` as fallback.**
Per `.claude/rules/subprocess-cwd.md`, per-invocation config
belongs on `run()`. `__init__` keeps `timeout: int = 180` for
back-compat with the 6 existing constructor call sites (no
changes needed to any caller). `run()` adds
`timeout: int | None = None`; `_invoke` uses
`effective_timeout = timeout if timeout is not None else self.timeout`.
(C-4.)

**DEC-011 — Pure helper `_env_without_api_key(base_env=None) ->
dict[str, str]` in `runner.py`.** Returns a new dict, always
non-mutating (per `.claude/rules/non-mutating-scrub.md`).
Stripped keys: `{"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}`.
Reused by CLI entrypoints, pytest plugin, and any future caller
that needs the same transformation. Testable without subprocess
mocks. (C-5.)

**DEC-012 — When the `init` message omits `apiKeySource`,
`SkillResult.api_key_source` stays `None` and the stderr info
line is suppressed.** Absence is the signal; don't print
"apiKeySource=unavailable". (C-6.)

**DEC-013 — `SkillRunner.run(env=dict | None)` kwarg shape;
mirrors `cwd`.** `None` default → `subprocess.Popen(env=None)`
inherits `os.environ` (today's behavior). A dict → `Popen`
receives it unchanged. CLI/plugin callers compute the dict via
`_env_without_api_key()` when `--no-api-key` is set. Flexibility
doubles as "tests can inject arbitrary env without monkeypatching
`os.environ`."

**DEC-014 — CLI `--timeout SECONDS` validates at argparse AND at
load time.** argparse uses `type=int` for syntax. A small custom
type function (`_positive_int`) rejects `<= 0` with a clear
message; argparse surfaces it as exit 2 (per
`.claude/rules/llm-cli-exit-code-taxonomy.md`). `EvalSpec.timeout`
reuses the same `> 0` check at load time per DEC-008.

**DEC-015 — First `init` message wins; subsequent `init` events
are ignored.** Stream-json spec allows at most one init per run;
defensive-parse pattern means we tolerate duplicates by taking
the first and skipping others. Per
`.claude/rules/stream-json-schema.md`.

**DEC-016 — `_env_without_api_key` preserves non-auth Anthropic
env vars (`ANTHROPIC_BASE_URL` etc.).** Only the two credential
vars are stripped. A user who sets `ANTHROPIC_BASE_URL` to point
at a proxy still gets their proxy; they just lose the API key.

**DEC-017 — Parser match on `type == "system"` AND `subtype ==
"init"` for `apiKeySource` extraction.** Today the parser only
handles `type == "assistant"` and `type == "result"`. Add a third
branch that matches the compound shape and reads `apiKeySource`
with `.get(...)`. Defensive per
`.claude/rules/stream-json-schema.md`.

### Open questions

None. Architecture concerns resolved; decisions trace every
acceptance criterion in Discovery "Done when."

---

## Detailed Breakdown

Seven implementation stories + Quality Gate + Patterns & Memory.
Ordering is bottom-up: foundational pure layers first
(`EvalSpec.timeout`, `_env_without_api_key`, stream-json parser),
runtime plumbing next (`SkillRunner.run` kwargs, `SkillSpec.run`
precedence), user-facing surfaces last (CLI flags, pytest plugin
option). Each story targets ≤~150 lines of diff so a single Ralph
context window can complete it. Every story's acceptance
includes the shared validation gate: **`uv run ruff check src/
tests/` passes AND `uv run pytest --cov=clauditor
--cov-fail-under=80` passes**.

---

### US-001 — Add `EvalSpec.timeout` field with load-time validation

**Description:** Add an optional `timeout: int | None = None`
field on `EvalSpec`. Validate at `from_dict` load time — reject
non-int (including `bool`), reject `<= 0`, accept `None` /
missing as "unset."

**Traces to:** DEC-002, DEC-003, DEC-008, DEC-014.

**Acceptance Criteria:**
- `EvalSpec` carries `timeout: int | None = None` as the last
  field (after `allow_hang_heuristic`).
- `{"timeout": 300}` loads → `spec.timeout == 300`.
- `{"timeout": 0}` or `{"timeout": -5}` raises `ValueError` with
  message matching `"EvalSpec(skill_name=…): 'timeout' must be >
  0, got …"`.
- `{"timeout": "300"}` raises `ValueError` with message matching
  `"'timeout' must be an int, got str '300'"`.
- `{"timeout": True}` (bool guard) raises `ValueError` —
  `isinstance(True, int)` is True in Python but `bool`
  rejection is explicit.
- `{"timeout": None}` loads → `spec.timeout is None`.
- Missing `timeout` key → `spec.timeout is None`.
- Shared validation gate passes.

**Done when:** Six new test methods pass in
`tests/test_schemas.py::TestFromDict`; coverage ≥ 80%; ruff
clean.

**Files:**
- `src/clauditor/schemas.py` — add the field on `EvalSpec` and
  the validation block inside `from_dict` (pattern matches the
  existing `allow_hang_heuristic` validation).
- `tests/test_schemas.py` — six new tests in `TestFromDict`.

**Depends on:** none.

**TDD:**
- `test_timeout_int_300` — positive int loads.
- `test_timeout_zero_raises` — `<= 0` rejection.
- `test_timeout_negative_raises` — `<= 0` rejection.
- `test_timeout_string_raises` — non-int rejection.
- `test_timeout_bool_raises` — bool guard.
- `test_timeout_null_is_none` — null maps to None.
- `test_timeout_missing_is_none` — absence maps to None.

---

### US-002 — Add `_env_without_api_key()` pure helper in runner.py

**Description:** Add a pure, non-mutating helper that returns a
new env dict with both `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` removed. Preserves all other env vars
(including `ANTHROPIC_BASE_URL`).

**Traces to:** DEC-007, DEC-011, DEC-016.

**Acceptance Criteria:**
- `_env_without_api_key(base_env: dict[str, str] | None = None)
  -> dict[str, str]` lives in `src/clauditor/runner.py`.
- When `base_env is None`, uses `os.environ` as the source.
- Always returns a **new** dict (never mutates the input).
- Strips both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`.
- Preserves every other key (including `ANTHROPIC_BASE_URL`,
  `PATH`, etc.).
- Is a no-op (but still returns a new dict) when neither
  auth key is present.
- Unit tests require no subprocess mocks (the helper is pure).
- Shared validation gate passes.

**Done when:** Four new tests pass in
`tests/test_runner.py::TestEnvWithoutApiKey`; coverage ≥ 80%;
ruff clean.

**Files:**
- `src/clauditor/runner.py` — new module-level function near
  the top of the file, above `SkillRunner`.
- `tests/test_runner.py` — new `TestEnvWithoutApiKey` class.

**Depends on:** none.

**TDD:**
- `test_strips_both_auth_vars` — input has both; output has
  neither.
- `test_preserves_other_vars` — `ANTHROPIC_BASE_URL`, `PATH`,
  unrelated key preserved.
- `test_default_reads_os_environ` — `base_env=None` path.
- `test_is_non_mutating` — input dict unchanged after call.
- `test_no_auth_vars_present` — no-op returns a new dict equal
  in content to the source.

---

### US-003 — Add keyword-only `env=` and `timeout=` kwargs on `SkillRunner.run`

**Description:** Thread the `env` dict and the per-invocation
`timeout` override through `SkillRunner.run` → `_invoke` to
`subprocess.Popen`. Defaults preserve back-compat: `env=None` →
`Popen(env=None)` inherits `os.environ` (today's behavior);
`timeout=None` → falls back to `self.timeout`.

**Traces to:** DEC-010, DEC-013.

**Acceptance Criteria:**
- `SkillRunner.run(..., *, env: dict[str, str] | None = None,
  timeout: int | None = None, ...)` — new keyword-only kwargs.
- `_invoke` receives and uses both. `subprocess.Popen` is
  invoked with `env=env` (which may be `None`, unchanged
  behavior, or a dict).
- The watchdog (`threading.Timer`) uses
  `effective_timeout = timeout if timeout is not None else
  self.timeout`.
- `SkillRunner.__init__(timeout=180, ...)` signature unchanged;
  existing six constructor call sites continue to work without
  modification.
- Shared validation gate passes.

**Done when:** Six new tests pass in
`tests/test_runner.py`; all existing runner tests still pass;
coverage ≥ 80%; ruff clean.

**Files:**
- `src/clauditor/runner.py` — `SkillRunner.run` signature,
  `_invoke` `env` parameter, Popen call site (lines 367–380),
  watchdog timer (lines 446–448).
- `tests/test_runner.py` — new test class covering both kwargs.

**Depends on:** US-002 (the helper is the canonical env-dict
producer, though `run()` itself doesn't import it).

**TDD:**
- `test_run_env_none_popen_receives_none` — default path.
- `test_run_env_dict_popen_receives_dict` — threading.
- `test_run_timeout_override_used_in_watchdog` — override
  wins.
- `test_run_timeout_none_falls_back_to_self_timeout` — back-
  compat.
- `test_init_timeout_default_180_unchanged` — constructor
  default preserved.
- `test_existing_call_sites_unaffected` — a representative
  call with neither new kwarg still works.

---

### US-004 — Parse `apiKeySource` from stream-json init; add `SkillResult.api_key_source`; stderr line; docs update

**Description:** Extend the stream-json parser to match on
`type == "system" AND subtype == "init"`, read `apiKeySource`
defensively via `.get()`, and populate a new
`SkillResult.api_key_source: str | None = None` field. Emit one
stderr info line per run when the field is present. Suppress
the line when the field is absent. Document the new field in
`docs/stream-json-schema.md`.

**Traces to:** DEC-005, DEC-009, DEC-012, DEC-015, DEC-017.

**Acceptance Criteria:**
- `SkillResult.api_key_source: str | None = None` is the last
  field on the dataclass.
- Parser in `_invoke` reads `apiKeySource` defensively from
  the first `type=="system"`/`subtype=="init"` message; later
  init messages are ignored.
- Stderr line format: `clauditor.runner: apiKeySource=<value>
  model=<value>` printed exactly once per run when
  `api_key_source is not None`.
- No stderr line when `api_key_source is None` (older CLI
  builds missing the field).
- `tests/conftest.py::make_fake_skill_stream` accepts an
  optional `init_message` kwarg that injects a `system/init`
  event at the head of the stream.
- `docs/stream-json-schema.md` has a new row documenting
  `apiKeySource` under the `type: "system"` / `subtype:
  "init"` section, including example values and a "label,
  not secret" note.
- Shared validation gate passes.

**Done when:** Five new parser tests + stderr-line test pass
in `tests/test_runner.py`; docs file updated; coverage ≥ 80%;
ruff clean.

**Files:**
- `src/clauditor/runner.py` — `SkillResult` dataclass (lines
  25–53); parser loop `init`-branch (add alongside `assistant`
  / `result` branches, ~line 481+); stderr print (near parser,
  after init is captured).
- `tests/conftest.py` — extend `make_fake_skill_stream`
  signature.
- `tests/test_runner.py` — new `TestApiKeySourceParsing` class.
- `docs/stream-json-schema.md` — new row under `type:
  "system"` section.

**Depends on:** none (parser change is independent of
env/timeout work; could run in parallel with US-003 but is
sequenced after for simplicity).

**TDD:**
- `test_init_apikeysource_none` — `"none"` value captured.
- `test_init_apikeysource_env_var` —
  `"ANTHROPIC_API_KEY"` value captured.
- `test_init_apikeysource_missing` — no field → None.
- `test_no_init_message` — malformed stream → None, no crash.
- `test_first_init_wins` — duplicate init events → first
  wins.
- `test_stderr_line_emitted` — stderr contains the info line
  when source is present.
- `test_stderr_line_suppressed_on_none` — no line when field
  absent.

---

### US-005 — Implement `SkillSpec.run` precedence resolution (CLI > spec > default)

**Description:** In `SkillSpec.run`, resolve the effective
`timeout` from the CLI override param, the `EvalSpec.timeout`
field, and the default (None → fall through to
`SkillRunner.run`'s own default which uses `self.timeout`).
Thread the result to `SkillRunner.run(timeout=…)`. Mirror the
`env` pass-through for `--no-api-key`.

**Traces to:** DEC-002, DEC-013.

**Acceptance Criteria:**
- `SkillSpec.run(..., *, timeout_override: int | None = None,
  env_override: dict[str, str] | None = None, ...)` — new
  keyword-only kwargs.
- Precedence: `timeout_override` wins when not None; else
  `spec.eval_spec.timeout` (if set); else `None` (let runner
  fall back to `self.timeout`).
- `env_override` (when not None) forwarded as
  `SkillRunner.run(env=env_override)`. No precedence merge —
  CLI is the only producer.
- Mirror shape of the existing `allow_hang_heuristic`
  threading (spec.py:142–150).
- Shared validation gate passes.

**Done when:** Three new precedence tests + one env-threading
test pass in `tests/test_spec.py::TestTimeoutPrecedence` (new
class); coverage ≥ 80%; ruff clean.

**Files:**
- `src/clauditor/spec.py` — `SkillSpec.run` signature +
  resolution block before the `self.runner.run(...)` call.
- `tests/test_spec.py` — new `TestTimeoutPrecedence` class.

**Depends on:** US-001 (`EvalSpec.timeout`), US-003
(`SkillRunner.run(timeout=, env=)` kwargs).

**TDD:**
- `test_cli_override_wins` — `timeout_override=60` with
  `spec.timeout=300` → runner gets 60.
- `test_spec_wins_when_no_cli_override` —
  `timeout_override=None`, `spec.timeout=300` → runner gets
  300.
- `test_default_when_neither_set` — both None → runner gets
  None (i.e. its own `self.timeout`).
- `test_env_override_threaded_through` — `env_override=dict`
  → runner gets `env=dict`.

---

### US-006 — Add `--no-api-key` and `--timeout` CLI flags on validate, grade, capture, run

**Description:** Add the two new flags on four CLI entry
points. `--no-api-key` computes
`env = _env_without_api_key() if args.no_api_key else None`
and passes to `SkillSpec.run(env_override=env)`. `--timeout`
uses a custom argparse type (`_positive_int`) that rejects
`<= 0` at parse time with exit 2; result threads to
`SkillSpec.run(timeout_override=args.timeout)`. `run.py`
already has `--timeout`; align its shape and add `--no-api-key`.

**Traces to:** DEC-001, DEC-006, DEC-014.

**Acceptance Criteria:**
- `validate`, `grade`, `capture`, `run` each expose
  `--no-api-key` (bool, default False) and `--timeout
  SECONDS` (int, default None → means "not set").
- `_positive_int` argparse type is defined once (in a shared
  util, or per-CLI as a small module-level helper — pick one
  and do it consistently).
- `--timeout 0` / `--timeout -5` / `--timeout foo` exits with
  code 2 and a clear stderr message.
- Valid `--timeout 300` reaches the runner via
  `SkillSpec.run(timeout_override=300)`.
- `--no-api-key` on any of the four commands results in
  `SkillSpec.run(env_override=<dict without both auth vars>)`.
- `run.py`'s existing `--timeout` stays functional (if it
  already uses `type=int` with `default=180`, align the
  semantics with the new precedence pattern: the CLI default
  should become `None`, not `180`, so the spec/runner defaults
  can kick in).
- Shared validation gate passes.

**Done when:** Seven new tests pass in `tests/test_cli.py`
(one per flag per command + one argparse-reject case);
coverage ≥ 80%; ruff clean.

**Files:**
- `src/clauditor/cli/validate.py`, `cli/grade.py`,
  `cli/capture.py`, `cli/run.py` — argparse additions, kwarg
  pass-through to `SkillSpec.run`.
- `tests/test_cli.py` — new `TestNoApiKeyFlag` and
  `TestTimeoutFlag` classes (or extend existing per-command
  test classes).

**Depends on:** US-002 (`_env_without_api_key`), US-005
(`SkillSpec.run(timeout_override=, env_override=)`).

**TDD:**
- `test_validate_no_api_key_strips_both_vars`
- `test_grade_no_api_key_strips_both_vars`
- `test_capture_no_api_key_strips_both_vars`
- `test_run_no_api_key_strips_both_vars`
- `test_validate_timeout_300_reaches_runner`
- `test_validate_timeout_zero_exits_2`
- `test_validate_timeout_non_int_exits_2`

---

### US-007 — Add `--clauditor-no-api-key` pytest plugin option

**Description:** Add a pytest plugin option parallel to the
existing `--clauditor-timeout`. The option controls the
`env_override` threaded through the `clauditor_runner` /
`clauditor_spec` fixtures to `SkillSpec.run(env_override=…)` at
skill-invocation time (not at runner-construction time; the
runner stays constructed with a default env).

**Traces to:** DEC-006.

**Acceptance Criteria:**
- `--clauditor-no-api-key` pytest option registered in
  `pytest_addoption`.
- The `clauditor_spec` fixture (or whatever layer calls
  `SkillSpec.run`) reads the option and threads
  `env_override=_env_without_api_key() if option else None`.
- `--clauditor-timeout` stays functional; the new option does
  not disrupt its wiring.
- Shared validation gate passes.

**Done when:** Two new tests pass in
`tests/test_pytest_plugin.py`; coverage ≥ 80%; ruff clean.

**Files:**
- `src/clauditor/pytest_plugin.py` — `pytest_addoption` and
  the fixture wrapping skill invocation.
- `tests/test_pytest_plugin.py` — new tests following the
  existing `--clauditor-timeout` test pattern.

**Depends on:** US-002 (`_env_without_api_key`), US-005
(`SkillSpec.run(env_override=)`).

**TDD:**
- `test_no_api_key_option_reaches_spec_run` — fixture
  threads `env_override` to the spec.
- `test_timeout_option_still_works` — regression guard on
  existing option.

---

### US-008 — Quality Gate

**Description:** Run `code-reviewer` Task 4x on the full
changeset, fixing every real bug found each pass. Run
CodeRabbit review on the PR. Resolve all findings (no
deferrals) and re-run the shared validation gate.

**Traces to:** project quality standard; no specific DEC-###.

**Acceptance Criteria:**
- `code-reviewer` Task agent has run four separate passes on
  `git diff dev..HEAD`. Every real finding is fixed; false
  positives are documented in a brief note.
- CodeRabbit review on the PR has zero unresolved comments
  (fix or reply-with-justification on each).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-fail-under=80`
  passes.

**Done when:** four review passes complete, all findings
addressed, validation gate green.

**Files:** all of the above, as needed.

**Depends on:** US-001, US-002, US-003, US-004, US-005,
US-006, US-007 (all implementation stories complete).

---

### US-009 — Patterns & Memory

**Description:** Review this epic's changes for new patterns
worth codifying in `.claude/rules/` or `docs/`. Candidate
patterns:

- **Auth-env stripping helper** — if `_env_without_api_key`
  becomes a reusable pattern (e.g. future `_env_without_aws_auth`
  for a Bedrock path), codify the "pure env-scrub helper in
  runner.py" shape as a rule.
- **CLI > spec > default precedence** — the pattern is now
  used twice (`allow_hang_heuristic`, `timeout`). A new rule
  `.claude/rules/spec-cli-precedence.md` documenting the
  "resolve in `SkillSpec.run`, thread as keyword-only kwarg"
  shape is warranted.
- **Positive-int argparse type** — if `_positive_int` is
  reused, codify as a small rule anchor; otherwise leave as
  an inline helper.

Update `docs/cli-reference.md` to document the new flags.
Update `README.md` and/or `docs/eval-spec-reference.md` to
document `EvalSpec.timeout`.

**Traces to:** broader project convention maintenance.

**Acceptance Criteria:**
- Any new pattern identified is codified as a
  `.claude/rules/<name>.md` file with canonical-implementation
  pointers (or explicitly declared "not yet; wait for third
  occurrence").
- `docs/cli-reference.md` documents `--no-api-key` and
  `--timeout` on validate / grade / capture / run.
- `docs/eval-spec-reference.md` (or equivalent) documents the
  new `EvalSpec.timeout` field with validation rules.
- README changes, if any, follow
  `.claude/rules/readme-promotion-recipe.md`.

**Done when:** rules / docs updated and committed.

**Files:**
- `.claude/rules/<new-rule>.md` (zero or more).
- `docs/cli-reference.md`.
- `docs/eval-spec-reference.md` (or whichever file documents
  `EvalSpec` fields).
- `README.md` (only if a teaser section changed shape per the
  promotion recipe).

**Depends on:** US-008 (Quality Gate).

---

## Beads Manifest

_(pending)_

---

## Session Notes

**2026-04-20** — Discovery opened. Fetched ticket, spawned parallel
codebase-scout and convention-checker subagents, assembled findings.
Worktree created at
`/home/wesd/dev/worktrees/clauditor/64-runner-auth-timeout` on branch
`feature/64-runner-auth-timeout`. Six scoping questions presented.

**2026-04-20 (2)** — Scoping answers locked: A/A/A/A/B/A+B. Moving
to architecture review: six baseline areas (security, performance,
data model, API design, observability, testing strategy) spawned
in parallel.

**2026-04-20 (3)** — Architecture review complete. 1 blocker
(`ANTHROPIC_AUTH_TOKEN` second env-auth path), 5 concerns (hang
heuristic gap, bool-guard on `EvalSpec.timeout`, docs companion
update, `timeout` kwarg placement, pure `_env_without_api_key`
helper, suppress-stderr-when-None). Performance and Testing
reviews pass cleanly.

**2026-04-20 (4)** — Refinement locked. BL-1a (strip both env
vars, keep `--no-api-key` name) and C-1…C-6 all accepted.
17 decisions recorded. No open questions. Ready to generate
stories.

**2026-04-20 (5)** — Detailing complete. Seven implementation
stories (US-001…007) + Quality Gate (US-008) + Patterns &
Memory (US-009). Ordering: foundational layers first
(EvalSpec field, pure env helper, runner kwargs, stream-json
parser) → runtime plumbing (SkillSpec precedence) →
user-facing surfaces (CLI flags, pytest plugin option).
