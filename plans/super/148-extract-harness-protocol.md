# Super Plan: #148 — Extract `Harness` protocol from `_invoke_claude_cli` (refactor)

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/148
- **Parent epic:** https://github.com/wjduenow/clauditor/issues/143 (Multi-provider / multi-harness, Epic B "first issue")
- **Branch:** `feature/148-extract-harness-protocol`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/148-extract-harness-protocol`
- **Base branch:** `dev`
- **Phase:** `detailing`
- **Sessions:** 1
- **Last session:** 2026-04-28
- **Total decisions:** 9
- **PR URL:** (pending)

---

## Discovery

### Ticket Summary

**What:** Pure refactor — zero behavior change. Extract the subprocess-invocation seam from `runner.py::_invoke_claude_cli` into a `Harness` protocol so additional harnesses (Codex per #149, future raw-API loop) can plug into `SkillRunner` without rewriting it. Move the Claude-Code-specific parser helpers (`_classify_result_message`, `_detect_interactive_hang`) alongside the moved invoker.

**Why:** Prerequisite for all of Epic B. Today the `claude -p` subprocess body and stream-json parser are inlined in `runner.py`. To swap in `codex exec --json` later, the seam has to be a protocol, not a function.

**Who benefits:** clauditor maintainers (unblocks #149/#150/#151/#152) and downstream users who want to evaluate skills against multiple harnesses.

**Done when (from ticket):**
1. All existing tests pass without modification
2. No behavior change visible to callers — `SkillRunner.run()` signature unchanged
3. `runner.py` shrinks; `_harnesses/_claude_code.py` carries the Claude-specific parser
4. A trivial `MockHarness` can be substituted in tests
5. Coverage stays ≥80%; ruff passes

**Out of scope (explicit):**
- `CodexHarness` implementation (#149)
- Skill-name-to-prompt resolver changes (#150) — the `f"/{skill_name}"` synthesis at `runner.py:987` stays put
- Any new CLI flag (#151)

### Codebase Findings

#### `_invoke_claude_cli` and its Claude-Code-specific helpers

| Element | Lines | Role | Migrates to |
|---|---|---|---|
| `_invoke_claude_cli()` | 472–939 | Subprocess + stream-json parse + heuristics | `_harnesses/_claude_code.py::ClaudeCodeHarness.invoke` |
| `_classify_result_message()` | 359–422 | Pure classifier: `is_error: true` → `(error_text, error_category)`. Categories: `rate_limit`, `auth`, `api`. | Move with parser |
| `_detect_interactive_hang()` | 167–248 | Pure detector: 1-turn hang on trailing `?` / `AskUserQuestion` + `end_turn` | Move with parser |
| `_detect_background_task_noncompletion()` | 307–357 | Pure detector: background-task launches without polling | Move with parser (Claude-Code-specific) |
| `_count_background_task_launches()` | 272–304 | Helper for ↑ | Move with parser |
| `_BACKGROUND_TASK_*` constants + regex | 256–269 | Heuristic warning text + matcher | Move with parser |
| `_INTERACTIVE_HANG_WARNING*` constants | 160–164 | Heuristic warning text | Move with parser |
| `_RESULT_TEXT_MAX_CHARS` | 153 | Truncate cap (4096) for error text | Move with classifier |

#### What stays in `runner.py`

| Element | Lines | Reason |
|---|---|---|
| `InvokeResult` dataclass | 425–470 | Transport-level result type; the protocol's return type. Shared between harnesses. |
| `SkillRunner` class | 941–1093 | Orchestrator; gains `harness: Harness \| None` param |
| `SkillRunner.run()` slash-command synthesis | 987 (`f"/{skill_name}"`) | Explicitly deferred to #150 |
| `env_without_api_key()` | 43–56 | Public utility (used by `_anthropic.py`, scripts); see scoping question Q3 |
| `env_with_sync_tasks()` | 59–75 | Public utility; not Claude-Code-specific |
| `_API_KEY_ENV_VARS` | 31 | Auth env var names; see scoping question Q3 |
| `_SYNC_TASKS_ENV_VAR` | 40 | Threaded into the harness via env, not harness-specific |

#### Callers of the to-be-moved code

`_invoke_claude_cli` is called from exactly **two** internal sites plus tests:

1. **`SkillRunner._invoke()`** at `runner.py:1070` — main path, threads `claude_bin` (line 1075).
2. **`call_anthropic()`** at `_anthropic.py:881–882` via `asyncio.to_thread(_invoke_claude_cli, ...)` — the CLI-transport path shipped in #86.
3. (External) `scripts/repro_cli_truncation.py:85` — repro script, not load-bearing.
4. **Tests** at `tests/test_runner.py:2517, 2549, 2580, 2622, 3080, 3100, 3126, 3146, 3163, 3181`.

The two internal callers are the load-bearing constraint: both must keep working. `call_anthropic` and `SkillRunner` need to either share a default `ClaudeCodeHarness` instance, both construct their own, or call a back-compat shim. (See Q1.)

#### Test seam

`tests/test_runner.py` (3679 lines) already mocks `subprocess.Popen` via `_FakePopen` (conftest.py:29–74) and synthesizes stream-json with `make_fake_skill_stream()` etc. (conftest.py:77–240). The acceptance criterion **"all existing tests pass without modification"** means:
- The `subprocess.Popen` patch target stays valid (whether the harness module patches `clauditor.runner.subprocess.Popen` or `clauditor._harnesses._claude_code.subprocess.Popen` matters — the tests presumably patch `clauditor.runner.subprocess.Popen`, so we need to verify and decide).
- Direct unit tests for `_classify_result_message` (1555–1710) and `_detect_interactive_hang` (2077–2210) currently import from `clauditor.runner` — if those tests must work without modification, the helpers stay re-exported from `runner.py`. (See Q4.)

#### Project validation

- **Test runner:** `pytest tests/ --cov=clauditor --cov-fail-under=80`
- **Lint:** `ruff check src/ tests/` and `ruff format --check src/ tests/`
- **Python:** `>=3.11` (PEP 604 unions required by ruff `UP` rules)

### Convention Checker Findings (rules that bind this refactor)

Direct hits:

- **`.claude/rules/centralized-sdk-call.md`** — Anthropic SDK calls go through `_anthropic.py::call_anthropic`. `call_anthropic`'s CLI-transport branch currently calls `_invoke_claude_cli`; after the refactor it must call into the harness without losing the `AnthropicHelperError` wrap.
- **`.claude/rules/pure-compute-vs-io-split.md`** — The protocol defines an interface contract; subprocess I/O lives only in the implementation method. Pure helpers (`_classify_result_message`, `_detect_interactive_hang`, `_detect_background_task_noncompletion`) stay pure when moved.
- **`.claude/rules/stream-json-schema.md`** — Defensive parser contract is preserved verbatim by the move. No structural changes to the parser loop.
- **`.claude/rules/non-mutating-scrub.md`** — `strip_auth_keys(env)` returns a new dict; never mutates the input. Mirrors today's `env_without_api_key` semantics.
- **`.claude/rules/monotonic-time-indirection.md`** — `_invoke_claude_cli` measures its own duration via `time.monotonic`. After the move, the harness module needs the `_monotonic` indirection alias if any test patches it (currently the runner module aliases `time.monotonic` for testing — verify which tests rely on this).
- **`.claude/rules/llm-cli-exit-code-taxonomy.md`** — No exit-code changes (refactor-only). The `AnthropicHelperError` wrap in `call_anthropic` stays unchanged.
- **`.claude/rules/rule-refresh-vs-delete.md`** — Any rule file that names `runner.py::_invoke_claude_cli` or `_classify_result_message` by path is a refresh candidate (must update file paths, not delete).

Style/structure:

- Type hints use **PEP 604** (`X | None`, not `Optional[X]`) — `pyproject.toml` ruff `target = "py311"` and `UP` rule enforces this.
- `Protocol` classes: no clear precedent yet in this repo. The ticket says `_harnesses/__init__.py` for the protocol; given `_harnesses/` is a private package, this is consistent with project's "private helpers live in `_*` modules" convention (`_anthropic.py`, `_providers/` planned in #144).
- `field(default_factory=dict)` for mutable dataclass defaults.
- Tests in `tests/test_<module>.py`, class-based (`TestClassName`).

### `workflow-project.md`

Not present in this repo. No project-specific scoping questions, additional review areas, or chunking patterns to layer in.

### Predecessor plans (worth a glance during refinement)

- `plans/super/86-claude-cli-transport.md` — shipped the CLI transport path that `call_anthropic` currently uses. Its "critical reusability fact" (line 110) is the genesis of the seam this ticket extracts.

---

## Scoping Decisions (Phase 1 sign-off)

### DEC-001 — `call_anthropic` calls the harness directly

**Decision:** `_anthropic.py::call_anthropic` instantiates (or imports) a module-level default `ClaudeCodeHarness` and calls `.invoke(...)` directly. The `_invoke_claude_cli` function name does not survive — the harness `.invoke()` is the only callable.

**Rationale:** Both internal callers go through the same protocol surface. Q1 → A. No compatibility shim, no re-export — a clean end state. The trade-off (touching `_anthropic.py:881–885`) is small and one-time.

**Affects:** `_anthropic.py:881–885`, `scripts/repro_cli_truncation.py:72,85`.

### DEC-002 — `claude_bin` deprecation on `SkillRunner.__init__`

**Decision:** `SkillRunner.__init__(self, project_dir=None, timeout=300, claude_bin="claude", harness=None)`. When `harness` is provided **and** `claude_bin != "claude"` (the default), emit `DeprecationWarning("Pass claude_bin via ClaudeCodeHarness(claude_bin=...) instead; SkillRunner.claude_bin will be removed in a future release.")` and ignore `claude_bin`. When `harness=None`, construct `ClaudeCodeHarness(claude_bin=claude_bin)` silently (back-compat path).

**Rationale:** Q2 → C. Three-step retirement; preserves all current call sites; surfaces the migration path without breaking anyone.

**Affects:** `runner.py::SkillRunner.__init__`. New test for the deprecation warning (assert via `pytest.warns(DeprecationWarning)`).

### DEC-003 — Auth-strip lives entirely in the harness module

**Decision:** Move `_API_KEY_ENV_VARS` (currently `runner.py:31`) and `env_without_api_key` (currently `runner.py:43–56`) into `src/clauditor/_harnesses/_claude_code.py`. Update `_anthropic.py:885` and `scripts/repro_cli_truncation.py:72` to import from the new location. The `Harness` protocol's `strip_auth_keys(env)` method on `ClaudeCodeHarness` calls into the same `env_without_api_key` helper (or the constant directly).

**Rationale:** Q3 → B. The acceptance criterion explicitly says "`_API_KEY_ENV_VARS` becomes per-harness via `strip_auth_keys`." Cleanest end state. Two import-line touches in external callers is acceptable churn; both are private/internal.

**Affects:** `runner.py` (deletes constant + helper), `_harnesses/_claude_code.py` (defines them), `_anthropic.py:885`, `scripts/repro_cli_truncation.py:72`.

**Caveat:** The `env_without_api_key` symbol is part of the public-ish surface of `clauditor.runner` (no leading underscore). External users who imported it will need to update. Phase 2 should rate the back-compat impact (likely a `concern` not a `blocker`, since clauditor is pre-1.0).

### DEC-004 — Pure helpers move; tests update their imports

**Decision:** `_classify_result_message` and `_detect_interactive_hang` move into `_harnesses/_claude_code.py` and are NOT re-exported from `runner.py`. The two test classes (`TestClassifyResultMessage` at `test_runner.py:1555–1710`, `TestDetectInteractiveHang` at `test_runner.py:2077–2210`) update their `from clauditor.runner import` lines to `from clauditor._harnesses._claude_code import`. Same for `_detect_background_task_noncompletion` and `_count_background_task_launches`.

**Rationale:** Q4 → B. Cleanest end state; the test diff is mechanical and small (~3 import lines). The "no test modifications" acceptance criterion is interpreted as "no behavior assertions change" (confirmed by Q6 → B below).

**Affects:** `tests/test_runner.py` (≤4 import-line updates).

### DEC-005 — `MockHarness` ships in the private package

**Decision:** Ship `class MockHarness(Harness)` in `src/clauditor/_harnesses/_mock.py`. Trivial — records the last `invoke(...)` call and returns a configurable `InvokeResult`. Used by at least one new test in `tests/test_runner.py` that demonstrates substitution: `SkillRunner(harness=MockHarness(result=...))`.

**Rationale:** Q5 → B. Acceptance criterion 4 says "a trivial MockHarness **can be substituted**". Shipping it in the package (rather than tests/conftest) means external pytest-plugin consumers can use it; it's tiny code and the `_harnesses` package already houses harness-related code. Privacy via the underscore-prefixed module is sufficient.

**Affects:** New file `_harnesses/_mock.py`. New test class in `tests/test_runner.py` (e.g. `TestSkillRunnerHarnessSubstitution`).

### DEC-006 — `subprocess.Popen` patch target moves

**Decision:** Existing tests that patch `clauditor.runner.subprocess.Popen` are updated to patch `clauditor._harnesses._claude_code.subprocess.Popen`. The "all existing tests pass without modification" acceptance is interpreted semantically (no behavior assertions change), not literally (zero-diff).

**Rationale:** Q6 → B. Required for the patch to take effect on the moved code path. The diff is mechanical — likely one search-and-replace across `tests/test_runner.py` plus matching imports in `conftest.py`.

**Affects:** `tests/test_runner.py` (and possibly `tests/conftest.py` if `_FakePopen` injection patches the runner module).

---

## Architecture Review

Four parallel reviews ran (protocol design, refactor safety, test impact, convention compliance). Standard performance/data-model passes were skipped — refactor with no behavior change has no signal there. Summary:

| Area | Rating | Headline finding |
|---|---|---|
| Protocol design — `name`, location, `model`, `subject`, `strip_auth_keys`, `validate_env`? | pass | `name: str` class attr, `_harnesses/__init__.py` location, `model` on `.invoke`, `subject` skill-runner-internal, strict `strip_auth_keys` signature, no `validate_env` — all good. |
| Protocol design — `InvokeResult` field set | **blocker** | `raw_messages`/`stream_events` are Claude-stream-json-shaped. Codex's NDJSON has `reasoning` items; raw-API has no stream events. Forcing future harnesses to return `[]` discards information. Add `harness_metadata: dict[str, Any] = field(default_factory=dict)` **now** so #149 doesn't have to widen `InvokeResult` (which is a breaking change for sidecar consumers). |
| Protocol design — `allow_hang_heuristic` on `.invoke` signature | **blocker** | The ticket-proposed signature includes `allow_hang_heuristic: bool` on `.invoke(...)`, but this is Claude-Code-only (the heuristic is hard-wired to `_detect_interactive_hang` / `_detect_background_task_noncompletion`). Codex/raw-API would receive a meaningless flag. Recommendation: move to `ClaudeCodeHarness(allow_hang_heuristic=True)` construction; drop from the protocol. |
| Refactor safety — module-level state | pass | `_INTERACTIVE_HANG_WARNING_PREFIX` and `_BACKGROUND_TASK_WARNING_PREFIX` (runner.py:160, 256) must STAY in runner.py because `SkillResult.succeeded_cleanly` (runner.py:141, 143) prefix-matches against them. Harness module imports them back. The full warning *bodies* (`_INTERACTIVE_HANG_WARNING`, `_BACKGROUND_TASK_WARNING`) move with the heuristic. |
| Refactor safety — `time.monotonic` indirection | concern → mitigated | Today `runner.py` calls `time.monotonic()` directly (no `_monotonic` alias). After the move, `_harnesses/_claude_code.py` should add `_monotonic = time.monotonic` per `.claude/rules/monotonic-time-indirection.md` so future tests can patch duration without touching `time.monotonic` globally. Net cost: 2 lines. |
| Refactor safety — `subprocess.Popen` patch targets | concern → mitigated | 51 patch sites in `tests/test_runner.py` + 23 in `tests/test_anthropic.py` (74 total) currently target `clauditor.runner.subprocess.Popen`. After the move, all must target `clauditor._harnesses._claude_code.subprocess.Popen`. Mechanical search-replace; included in DEC-006 scope. |
| Refactor safety — `asyncio.to_thread(harness.invoke, ...)` | pass | Bound-method form is safe under `asyncio.to_thread`; `ClaudeCodeHarness` is stateless beyond immutable `claude_bin`/`model` config. |
| Refactor safety — coverage redistribution under `--cov-fail-under=80` | concern | New modules will inherit existing tests' coverage; risk is small but not zero. Quality Gate story will run coverage before PR. |
| Refactor safety — public API churn (`env_without_api_key`) | **concern** | `env_without_api_key` is imported from `clauditor.runner` by **4 sites**, not 2: `_anthropic.py:885`, `pytest_plugin.py:27`, `cli/validate.py:12`, `scripts/repro_cli_truncation.py:72`. Q3 → B says move it cleanly; the safety reviewer recommends a deprecation re-export in `runner.py` (single line: `from ._harnesses._claude_code import env_without_api_key`) to keep external consumers (`pytest_plugin` is part of the **public** pytest plugin surface) working without churn. **Decision needed — see Q7.** |
| Refactor safety — slash-command synthesis at runner.py:987 | pass | `f"/{skill_name}"` is composed BEFORE `_invoke()` is called; `skill_name` never threads into `_invoke_claude_cli`. Move is orphan-free. |
| Test impact — TestInvokeClaudeCli location | pass | Stays in `tests/test_runner.py` with updated imports + patch targets; do NOT relocate to `test_harnesses_claude_code.py` (would fragment integration coverage). Same for `TestClassifyResultMessage`, `TestDetectInteractiveHang`, `TestEnvWithoutApiKey`. |
| Test impact — new tests required | pass | (a) `TestSkillRunnerHarnessSubstitution` exercising `MockHarness`, (b) `TestSkillRunnerClaudeBinDeprecation` asserting `pytest.warns(DeprecationWarning)` when both `harness=` and `claude_bin=` are passed. |
| Test impact — live-mode tests | pass | Only `tests/test_bundled_review_skill.py` is `@pytest.mark.live`; it imports `SkillRunner` from `clauditor.runner` (stays valid). No patches. |
| Test impact — `tests/test_spec.py`, `tests/test_cli.py` references to `_INTERACTIVE_HANG_WARNING` | pass | These reference the prefix constant (stays in `runner.py` per safety finding above). Imports keep working. |
| Convention compliance — applies | pass | `pure-compute-vs-io-split.md`, `centralized-sdk-call.md`, `non-mutating-scrub.md`, `stream-json-schema.md`, `monotonic-time-indirection.md`, `rule-refresh-vs-delete.md`, `spec-cli-precedence.md` — all respected by the plan as designed. |
| Convention compliance — rules needing **text refresh** (per `rule-refresh-vs-delete.md`) | concern → tracked | 4 rule files have line-level references to moving symbols and need find-replace edits in this PR: `pure-compute-vs-io-split.md` (lines 267, 273, 299–300), `centralized-sdk-call.md` (line 137), `stream-json-schema.md` (lines 47, 119), `spec-cli-precedence.md` (line 282). Becomes a refinement / story task. |

### Blockers requiring user input

Two protocol-design blockers (B1: `harness_metadata` field, B2: `allow_hang_heuristic` location) and one back-compat concern (env_without_api_key) need decisions before Phase 3 closes.

## Refinement Log

### DEC-007 — Add `harness_metadata` to `InvokeResult` now

**Decision:** Add `harness_metadata: dict[str, Any] = field(default_factory=dict)` to `InvokeResult` (`runner.py:425–470`) in this PR. Document in the docstring that `raw_messages` and `stream_events` are Claude-Code-stream-json best-effort; harnesses with different transport shapes (Codex NDJSON, raw API) populate `harness_metadata` with their native shape.

**Rationale:** Forecloses a sidecar-schema breaking change in #149. Cost is ~3 lines; the field is purely additive (defaults to empty), so zero behavior change for the Claude-Code path.

**Affects:** `runner.py::InvokeResult` + docstring.

### DEC-008 — `allow_hang_heuristic` is per-instance, not per-call

**Decision:** Drop `allow_hang_heuristic: bool` from the `Harness.invoke(...)` protocol signature. Move it to `ClaudeCodeHarness.__init__(self, *, claude_bin: str = "claude", model: str | None = None, allow_hang_heuristic: bool = True)`. The flag is hard-wired to Claude-Code-stream-json detectors; other harnesses don't need a knob for it. `SkillRunner._invoke` and `call_anthropic` either accept the harness's configured value or construct a custom harness when they want a different setting.

**Rationale:** Keeps the protocol's `.invoke(...)` signature harness-agnostic. The two existing callers (`SkillRunner._invoke`, `call_anthropic`) pass the same value today; making it per-instance loses no flexibility.

**Affects:** `Harness` protocol signature, `ClaudeCodeHarness.__init__`, the migrated `_invoke_claude_cli` body (reads `self.allow_hang_heuristic` instead of a parameter), the two callers (no longer pass the flag).

### DEC-009 — Update all 4 `env_without_api_key` callers; no deprecation alias

**Decision:** Move `env_without_api_key` cleanly to `_harnesses/_claude_code.py` and update all four call sites: `_anthropic.py:885`, `pytest_plugin.py:27`, `cli/validate.py:12`, `scripts/repro_cli_truncation.py:72`. No re-export alias in `runner.py`.

**Rationale:** Q7 → A. Clauditor is pre-1.0 and the `pytest_plugin.py` import is one line; downstream test suites that depend on the plugin already pin a clauditor version, so the import update lands cleanly with the next release. Cleanest end state.

**Affects:** All four files listed; `runner.py` (deletes the function and the constant).

### Refinement notes (concerns absorbed without further question)

- **`_INTERACTIVE_HANG_WARNING_PREFIX` and `_BACKGROUND_TASK_WARNING_PREFIX` stay in `runner.py`** — `SkillResult.succeeded_cleanly` (runner.py:141, 143) prefix-matches the warnings list against them. The harness module imports them back from `runner` to compose the warning bodies.
- **`_INTERACTIVE_HANG_WARNING` and `_BACKGROUND_TASK_WARNING` (the full body strings) move with the heuristic** — they are produced where the heuristic fires.
- **`_monotonic = time.monotonic` alias** must be added to `_harnesses/_claude_code.py` per `.claude/rules/monotonic-time-indirection.md`. The migrated `_invoke_claude_cli` body uses `_monotonic()` instead of `time.monotonic()`.
- **74 `subprocess.Popen` patch targets** (51 in `tests/test_runner.py`, 23 in `tests/test_anthropic.py`) update from `clauditor.runner.subprocess.Popen` to `clauditor._harnesses._claude_code.subprocess.Popen` — mechanical search-replace inside US-004.
- **4 rule files** (`pure-compute-vs-io-split.md`, `centralized-sdk-call.md`, `stream-json-schema.md`, `spec-cli-precedence.md`) need text refresh per `.claude/rules/rule-refresh-vs-delete.md` because they reference moved file paths/symbols by name. Refreshes are mechanical line edits — single dedicated story.
- **Coverage** (`--cov-fail-under=80`) is verified during the Quality Gate story; no expected drop because every moved line keeps its existing test, only the import path changes.

---

## Detailed Breakdown

Story ordering follows architecture-layer-first: protocol skeleton → pure helpers → auth utility → main migration → test helpers/deprecation tests → rule refresh → quality gate → patterns & memory. Every story leaves the test suite green at the end. Validation command for every story:

```
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/
```

(Coverage is checked at the Quality Gate story; per-story `--cov-fail-under=80` should still pass since no production code is deleted, only relocated.)

### US-001 — Create `_harnesses/` package; define `Harness` Protocol; widen `InvokeResult`

**Description.** Bootstrap the new private package `src/clauditor/_harnesses/`. Define the `Harness` Protocol with `name: ClassVar[str]`, `invoke(self, prompt, *, cwd, env, timeout, model=None) -> InvokeResult`, and `strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]`. Add `harness_metadata: dict[str, Any] = field(default_factory=dict)` to `InvokeResult` in `runner.py`.

**Traces to:** DEC-007 (`harness_metadata`), DEC-008 (no `allow_hang_heuristic` on protocol).

**Files:**
- `src/clauditor/_harnesses/__init__.py` (new) — defines `Harness` Protocol + re-exports `InvokeResult` from runner for typing convenience.
- `src/clauditor/runner.py` — `InvokeResult` gains `harness_metadata` field.
- `tests/test_runner.py` — extend `TestInvokeResult` (or create) with one assertion that `harness_metadata` defaults to `{}` and is mutable per instance.

**Acceptance criteria:**
- `from clauditor._harnesses import Harness` resolves.
- `Harness` is a `typing.Protocol` (not a runtime base class). Its method signatures match the spec verbatim.
- `InvokeResult().harness_metadata == {}`; assigning `r.harness_metadata["key"] = "val"` does not mutate other instances.
- Validation command passes.

**Done when:** Protocol exists, `InvokeResult` widened, one new test for the new field passes.

**Depends on:** none.

**TDD:**
- Write a test that constructs a stub class with the protocol's three members and asserts it is structurally compatible with `Harness` (using `isinstance()` if `@runtime_checkable`, or simply `def takes_harness(h: Harness): ...; takes_harness(StubHarness())` to exercise type-hint shape).
- Write a test that `InvokeResult()` gives `harness_metadata == {}` and that two instances have independent dicts (proves `default_factory=dict`, not a shared default).

### US-002 — Move pure Claude-Code-specific helpers and their constants

**Description.** Relocate the four pure helpers and their associated constants into `src/clauditor/_harnesses/_claude_code.py`. Keep the warning-prefix constants in `runner.py` (load-bearing for `SkillResult.succeeded_cleanly`); move only the warning bodies. Update unit-test imports.

**Traces to:** DEC-004 (helpers move; tests update imports).

**Moves into `_harnesses/_claude_code.py`:**
- `_classify_result_message` (runner.py:359–422)
- `_detect_interactive_hang` (runner.py:167–248)
- `_detect_background_task_noncompletion` (runner.py:307–357)
- `_count_background_task_launches` (runner.py:272–304)
- `_RESULT_TEXT_MAX_CHARS` (runner.py:153)
- `_BACKGROUND_TASK_WAITING_RE` (runner.py:266–269)
- `_INTERACTIVE_HANG_WARNING` body (runner.py:161–164)
- `_BACKGROUND_TASK_WARNING` body (runner.py:257–261)

**Stays in `runner.py`:**
- `_INTERACTIVE_HANG_WARNING_PREFIX` (line 160) — referenced by `SkillResult.succeeded_cleanly` line 141
- `_BACKGROUND_TASK_WARNING_PREFIX` (line 256) — referenced by `SkillResult.succeeded_cleanly` line 143

**Bridge imports during transition:**
- `runner.py::_invoke_claude_cli` continues to call the helpers; add `from ._harnesses._claude_code import _classify_result_message, _detect_interactive_hang, _detect_background_task_noncompletion` at the top of `runner.py`. (These bridge imports are deleted in US-004 along with `_invoke_claude_cli`.)
- `_harnesses/_claude_code.py` imports the two prefix constants back from runner: `from clauditor.runner import _INTERACTIVE_HANG_WARNING_PREFIX, _BACKGROUND_TASK_WARNING_PREFIX`.

**Test imports updated:**
- `tests/test_runner.py::TestClassifyResultMessage` (lines 1555–1710) — `from clauditor.runner import _classify_result_message` → `from clauditor._harnesses._claude_code import _classify_result_message`
- `tests/test_runner.py::TestDetectInteractiveHang` (lines 2077–2210) — analogous

**Acceptance:**
- All previously-passing unit tests for these four helpers still pass after the import update (no behavior assertions change).
- `runner.py` no longer contains the four helper definitions.
- `clauditor.runner._INTERACTIVE_HANG_WARNING_PREFIX` and `_BACKGROUND_TASK_WARNING_PREFIX` still resolve.
- Validation command passes.

**Done when:** Helpers live only in `_harnesses/_claude_code.py`; tests pass; no duplicate symbols.

**Depends on:** US-001.

**TDD:** No new tests. The existing `TestClassifyResultMessage` and `TestDetectInteractiveHang` are the regression net.

### US-003 — Move `_API_KEY_ENV_VARS` and `env_without_api_key`; update 4 callers

**Description.** Relocate `_API_KEY_ENV_VARS` (`runner.py:31`) and `env_without_api_key` (`runner.py:43–56`) into `src/clauditor/_harnesses/_claude_code.py`. Update all four call sites and the relevant unit test.

**Traces to:** DEC-003, DEC-009.

**Files:**
- `src/clauditor/_harnesses/_claude_code.py` — adds the constant + function.
- `src/clauditor/runner.py` — deletes both.
- `src/clauditor/_anthropic.py:885` — import `env_without_api_key` from new location.
- `src/clauditor/pytest_plugin.py:27` — import path update.
- `src/clauditor/cli/validate.py:12` — import path update.
- `scripts/repro_cli_truncation.py:72` — import path update.
- `tests/test_runner.py::TestEnvWithoutApiKey` (lines 2681–2743) — import path update.

**Acceptance:**
- `clauditor.runner` no longer exports `env_without_api_key` or `_API_KEY_ENV_VARS`.
- All four production call sites import from the new path and behave identically.
- Existing `TestEnvWithoutApiKey` tests pass with updated import.
- Validation command passes.

**Done when:** symbols moved; 4 callers updated; tests green.

**Depends on:** US-001.

**TDD:** No new tests. `TestEnvWithoutApiKey` is the regression net.

### US-004 — Implement `ClaudeCodeHarness`; migrate `_invoke_claude_cli` body; rewire callers

**Description.** This is the load-bearing story. Implement `ClaudeCodeHarness` as the protocol's first concrete class. Migrate the entire body of `_invoke_claude_cli` (runner.py:472–939) into `ClaudeCodeHarness.invoke`. Modify `SkillRunner.__init__` to accept `harness: Harness | None = None` with `claude_bin` deprecation per DEC-002. Wire `SkillRunner._invoke` to call `self.harness.invoke(...)`. Update `_anthropic.py:881` to call a module-level default `ClaudeCodeHarness().invoke` via `asyncio.to_thread`. Add the `_monotonic` alias. Update 74 `subprocess.Popen` patch targets across two test files. Update `TestInvokeClaudeCli` to call `ClaudeCodeHarness().invoke(...)` instead of `_invoke_claude_cli(...)`. Delete `_invoke_claude_cli` (no compatibility shim).

**Traces to:** DEC-001, DEC-002, DEC-006, DEC-008.

**Files:**
- `src/clauditor/_harnesses/_claude_code.py`:
  - `_monotonic = time.monotonic` (module level).
  - `class ClaudeCodeHarness:` with `name: ClassVar[str] = "claude-code"`, `__init__(self, *, claude_bin: str = "claude", model: str | None = None, allow_hang_heuristic: bool = True)` storing the three to `self`, `invoke(self, prompt, *, cwd, env, timeout, model=None) -> InvokeResult` (body migrated; `model` parameter falls back to `self.model`; reads `self.allow_hang_heuristic`), `strip_auth_keys(self, env)` calling `env_without_api_key`.
- `src/clauditor/runner.py`:
  - Delete `_invoke_claude_cli` entirely (lines 472–939).
  - Delete bridge imports added in US-002.
  - `SkillRunner.__init__(self, project_dir=None, timeout=300, claude_bin: str = "claude", harness: Harness | None = None)`. If `harness is not None and claude_bin != "claude"`, emit `DeprecationWarning("Pass claude_bin via ClaudeCodeHarness(claude_bin=...) instead; SkillRunner.claude_bin will be removed in a future release.", DeprecationWarning, stacklevel=2)` and ignore `claude_bin`. If `harness is None`, set `self.harness = ClaudeCodeHarness(claude_bin=claude_bin)`.
  - `SkillRunner._invoke` (current lines 1043–1092): replace the `_invoke_claude_cli(...)` call (line 1070) with `self.harness.invoke(...)`. Field-copy projection (lines 1078–1092) is unchanged but now also copies `harness_metadata`.
- `src/clauditor/_anthropic.py:881–885`: replace `asyncio.to_thread(_invoke_claude_cli, prompt, ...)` with `asyncio.to_thread(_default_harness.invoke, prompt, ...)` where `_default_harness = ClaudeCodeHarness()` is a module-level constant near the top of `_anthropic.py`. Remove the `_invoke_claude_cli` import.
- `tests/test_runner.py`: update 51 `subprocess.Popen` patch targets to `clauditor._harnesses._claude_code.subprocess.Popen`. Update `TestInvokeClaudeCli` (lines 2900+) to construct `ClaudeCodeHarness()` and call `.invoke(...)` instead of `_invoke_claude_cli(...)`. Same for `TestSkillRunnerInvokeRegressionSmoke` if it patches Popen.
- `tests/test_anthropic.py`: update 23 `subprocess.Popen` patch targets to `clauditor._harnesses._claude_code.subprocess.Popen`.
- `tests/conftest.py`: if any fixture-level patch targets `clauditor.runner.subprocess`, update to the new path.
- `scripts/repro_cli_truncation.py:85`: replace direct `_invoke_claude_cli` call with `ClaudeCodeHarness().invoke(...)`.

**Acceptance:**
- `clauditor.runner._invoke_claude_cli` is undefined (`ImportError` if anyone tries).
- `SkillRunner(project_dir="...")` constructs a working runner with the default `ClaudeCodeHarness`.
- `SkillRunner(harness=custom_harness)` uses the custom harness; Popen is never called via `clauditor.runner`.
- `call_anthropic(...)` under the CLI transport path works identically (existing `tests/test_anthropic.py` integration tests pass).
- `SkillRunner(harness=harness, claude_bin="custom")` emits a `DeprecationWarning`. (Verified by US-005's deprecation test, but the implementation lives here.)
- Coverage stays ≥80%.
- Validation command passes.

**Done when:** Everything compiles, all existing tests pass with updated patch targets, `_invoke_claude_cli` is gone.

**Depends on:** US-001, US-002, US-003.

**TDD:** No new test cases (US-005 carries the new ones). Existing `TestInvokeClaudeCli`, `TestSkillRunnerRun`, `TestRunRaw`, `TestSkillRunnerCwd`, `TestSkillRunnerEnvAndTimeout` and the 23 test_anthropic.py CLI-transport tests are the regression net. **Make sure to run the suite at every checkpoint.**

### US-005 — `MockHarness` + substitution test + `claude_bin` deprecation test

**Description.** Ship `MockHarness` as a simple test helper in `src/clauditor/_harnesses/_mock.py`. Add `TestSkillRunnerHarnessSubstitution` (proves substitutability) and `TestSkillRunnerClaudeBinDeprecation` (proves DEC-002 emits the warning).

**Traces to:** DEC-002, DEC-005.

**Files:**
- `src/clauditor/_harnesses/_mock.py` (new):
  ```python
  from dataclasses import dataclass, field
  from pathlib import Path
  from typing import ClassVar

  from clauditor.runner import InvokeResult


  @dataclass
  class MockHarness:
      """Records every invoke() call; returns a configurable InvokeResult."""

      name: ClassVar[str] = "mock"
      result: InvokeResult = field(default_factory=lambda: InvokeResult(output="", exit_code=0, duration_seconds=0.0))
      invoke_calls: list[dict] = field(default_factory=list)

      def invoke(
          self,
          prompt: str,
          *,
          cwd: Path | None,
          env: dict[str, str] | None,
          timeout: int,
          model: str | None = None,
      ) -> InvokeResult:
          self.invoke_calls.append(
              {"prompt": prompt, "cwd": cwd, "env": env, "timeout": timeout, "model": model}
          )
          return self.result

      def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]:
          return dict(env)
  ```
- `tests/test_runner.py`:
  - New `class TestSkillRunnerHarnessSubstitution` with at least: (a) `SkillRunner(harness=MockHarness(...)).run("foo")` returns a `SkillResult` projected from the MockHarness's configured result; (b) the recorded `invoke_calls` contains the expected prompt, cwd, env shape; (c) constructing `SkillRunner()` with no arg yields `isinstance(runner.harness, ClaudeCodeHarness)`.
  - New `class TestSkillRunnerClaudeBinDeprecation` with at least: (a) `SkillRunner(harness=MockHarness(), claude_bin="custom")` emits `DeprecationWarning` matching `r"claude_bin via ClaudeCodeHarness"` (use `pytest.warns`); (b) `SkillRunner(claude_bin="custom")` (no `harness`) emits NO warning and constructs a `ClaudeCodeHarness(claude_bin="custom")`.

**Acceptance:**
- `MockHarness` satisfies the `Harness` protocol structurally.
- Both new test classes pass.
- Validation command passes.

**Done when:** `MockHarness` shipped, two new test classes pass.

**Depends on:** US-004.

**TDD:**
- Write `TestSkillRunnerHarnessSubstitution` (assertions a, b, c) FIRST against an empty `_mock.py`, see it fail with `ImportError`, then implement `MockHarness` minimally to pass.
- Write `TestSkillRunnerClaudeBinDeprecation` (assertions a, b) — assertion (a) requires the `DeprecationWarning` plumbing already done in US-004; this test is the regression net for it.

### US-006 — Refresh rule files for moved symbols

**Description.** Mechanical text edits to four `.claude/rules/*.md` files that name moved file paths or symbols. Per `.claude/rules/rule-refresh-vs-delete.md`, these are refreshes (not deletions); the patterns remain load-bearing.

**Files:**
- `.claude/rules/pure-compute-vs-io-split.md` — line 267 (`_classify_result_message` path), line 273 (`_detect_interactive_hang` path), lines 299–300 (context reference to `_invoke`'s streaming loop → `ClaudeCodeHarness.invoke`'s streaming loop).
- `.claude/rules/centralized-sdk-call.md` — line 137 (`_invoke_claude_cli` reference → `Harness.invoke` / default `ClaudeCodeHarness`).
- `.claude/rules/stream-json-schema.md` — lines 47 and 119 (`_classify_result_message` path).
- `.claude/rules/spec-cli-precedence.md` — line 282 (`env_without_api_key in runner.py` → `env_without_api_key in _harnesses/_claude_code.py::ClaudeCodeHarness`).

**Acceptance:**
- Every rule file's symbol references resolve to actual files/symbols in the codebase as of this PR (verifiable with grep).
- No rule's "Why this rule exists" / historical-validation paragraphs are altered (per `rule-refresh-vs-delete.md`).
- Validation command passes (`ruff` and `pytest` are unaffected by `.md` edits, but run anyway).

**Done when:** Four rule files updated; grep proves the new references resolve.

**Depends on:** US-002, US-003, US-004.

**TDD:** N/A (documentation edits).

### US-007 — Quality Gate

**Description.** Run code reviewer 4 times across the full changeset, fixing every real bug each pass. Run CodeRabbit if available. Run coverage and confirm ≥80%.

**Files:** any file flagged by the reviewers.

**Acceptance:**
- 4 sequential code-review passes complete; all real bugs resolved before the next pass.
- CodeRabbit (if available) findings resolved or explicitly accepted.
- `pytest tests/ --cov=clauditor --cov-fail-under=80` passes.
- `ruff check src/ tests/` and `ruff format --check src/ tests/` pass.

**Done when:** All review passes complete with no remaining bugs; coverage gate green.

**Depends on:** US-001 through US-006.

### US-008 — Patterns & Memory

**Description.** Capture any new pattern discovered during this refactor in `.claude/rules/` or memory. Likely candidates:
- A `harness-protocol-shape.md` rule documenting the `Harness` protocol contract for #149/#150 implementers.
- An update to `centralized-sdk-call.md` to reflect that the CLI transport now goes through `harness.invoke` (already partly covered by US-006, but the architectural note belongs here).
- Memory updates if any cross-cutting decision emerged (e.g., "harness sidecars use `harness_metadata` for transport-specific shape").

**Files:** `.claude/rules/*.md` (new and/or updated), or memory writes.

**Acceptance:** New pattern documented or explicit "no new pattern emerged" note in the closeout.

**Done when:** Pattern captured (or explicitly waived).

**Depends on:** US-007.

---

## Beads Manifest

(Phase 7 — pending devolve.)

## Beads Manifest

(Phase 7 — pending devolve.)
