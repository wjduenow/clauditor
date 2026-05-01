# Super Plan: #149 — Multi-harness: add CodexHarness invoking codex exec --json with NDJSON parser

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/149
- **Parent epic:** https://github.com/wjduenow/clauditor/issues/143 (Multi-provider / multi-harness, Epic B)
- **Branch:** `feature/149-codex-harness`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/149-codex-harness`
- **Base branch:** `dev`
- **Phase:** `detailing → published` (awaiting approval)
- **Sessions:** 1
- **Last session:** 2026-04-30
- **Total decisions:** 18
- **PR URL:** _pending_
- **Beads epic:** _pending_

---

## Discovery

### Ticket Summary

**What:** Add a second concrete `Harness` implementation, `CodexHarness`, sibling to `ClaudeCodeHarness`. It invokes the OpenAI Codex CLI's `codex exec --json` subprocess, parses its NDJSON output, maps Codex events into `InvokeResult.stream_events` with a `harness` discriminator, strips Codex auth env vars, and classifies error events into `error_category`. New reference doc `docs/codex-stream-schema.md` parallels `docs/stream-json-schema.md`.

**Why:** Unblocks Epic B of #143 (multi-harness). After this lands, a single `EvalSpec` can run under either `ClaudeCodeHarness()` or `CodexHarness()`, enabling cross-harness skill evaluation. Direct beneficiaries: clauditor maintainers (it unblocks #151's `EvalSpec.harness` flag and #154's context sidecar) and downstream eval authors who want to compare a skill's behavior across `{Claude Code, Codex}`.

**Done when (from ticket):**
1. A skill that runs under Claude can run under Codex via `harness=CodexHarness()` (skill-name resolution per #150 lands separately and is already MERGED).
2. Token counts populate from `turn.completed.usage` correctly.
3. A `reasoning` item lands in `stream_events` with the right type — not silently dropped.
4. Unit tests cover NDJSON parse branches + error classification.

**Out of scope (explicit, from ticket):**
- `EvalSpec.harness` field / `--harness` CLI flag (deferred to #151).
- Cost / reasoning-token surfacing in a context sidecar (deferred to #154).
- Sandbox / approval-mode CLI plumbing (this plan picks ONE mode for v1; configurability deferred).

**Dependencies:** Blocked by #148 (Harness protocol — MERGED via PR #157) and #150 (prompt resolver / `Harness.build_prompt` — MERGED via PR #158). All dependencies satisfied on `dev` as of `8a3a309`.

### Codebase Findings (from Codebase Scout)

**Harness protocol surface** (`src/clauditor/_harnesses/__init__.py:33–105`):
```python
@runtime_checkable
class Harness(Protocol):
    name: ClassVar[str]
    def invoke(self, prompt: str, *, cwd: Path | None, env: dict[str, str] | None,
               timeout: int, model: str | None = None,
               subject: str | None = None) -> InvokeResult: ...
    def strip_auth_keys(self, env: dict[str, str]) -> dict[str, str]: ...
    def build_prompt(self, skill_name: str, args: str, *,
                     system_prompt: str | None) -> str: ...
```
- `@runtime_checkable` is used as a drift-guard. Sibling tests (`tests/test_runner.py:165–266`, `TestHarnessProtocol`) use `inspect.signature` for stricter conformance.
- All three methods MUST be present on `CodexHarness` with these exact signatures.

**ClaudeCodeHarness reference** (`src/clauditor/_harnesses/_claude_code.py:1–889`): the structural mirror. Patterns to lift:
- `_monotonic = time.monotonic` module-level alias for test patching (line 47).
- `subprocess.Popen(..., text=True, stdout=PIPE, stderr=PIPE, cwd=..., env=...)`.
- Stderr drainer thread with thread-safe warning collection (lines 514–539).
- `threading.Timer` watchdog that calls `proc.kill()` on timeout (lines 544–570).
- NDJSON parse loop with malformed-line skip+warn contract (lines 572–600).
- `try / finally` cleanup that always populates `duration_seconds` and reaps the child (lines 818–889).
- Pure-helper / I-O split (lines 57–350): classification helpers are pure, lifted out of `invoke()`, individually testable.

**MockHarness** (`src/clauditor/_harnesses/_mock.py:1–108`): records `invoke_calls` and `build_prompt_calls`. Per #148/#150 DEC: protocol additions update `MockHarness` in the SAME PR. No update needed for #149 (no new protocol method).

**`InvokeResult` and `SkillResult`** (`src/clauditor/runner.py:54–205`): identical shapes for harness-agnostic fields. `SkillRunner._invoke` (line 401) projects `InvokeResult → SkillResult` by direct field-copy at lines 407–421. The forward-compat hook for Codex-specific observability is `harness_metadata: dict[str, Any]` (DEC-007 of #148). `error_category` Literal is closed: `"rate_limit" | "auth" | "api" | "interactive" | "background-task" | "subprocess" | "timeout"`. Adding new categories requires touching this Literal.

**Construction call sites** (3 places that instantiate `ClaudeCodeHarness` today):
1. `src/clauditor/runner.py:245` — default factory in `SkillRunner.__init__`.
2. `src/clauditor/runner.py:394–398` — one-shot for per-call `allow_hang_heuristic` override.
3. `src/clauditor/_providers/_anthropic.py:690–692` — `_build_default_harness()` for grader internals.

These do NOT change in #149 (per ticket: no CLI flag, no default switch). `CodexHarness` is constructed by callers passing `SkillRunner(..., harness=CodexHarness(...))` until #151 lands.

**No existing Codex code anywhere** — clean slate. Forward-references at `__init__.py:4`, `runner.py:97,177,229` already mention "#149".

### Codex CLI Reference (from Domain Expert — authoritative against `openai/codex@main`)

**`codex exec` argv (corrected against the actual flag set):**
- `--json` — NDJSON to stdout (alias `--experimental-json`).
- `-o, --output-last-message <FILE>` — final `agent_message` text written here on success only.
- `-s, --sandbox <MODE>` where MODE ∈ `{read-only, workspace-write, danger-full-access}`.
- **`--full-auto` is DEPRECATED** (cli.rs:30–39 prints a warning and exits). Ticket text is stale; we MUST use `-s` instead.
- `--skip-git-repo-check` — needed when `cwd` isn't a git repo.
- `-m, --model <MODEL>` — model selection.
- `-C, --cd <DIR>` — working directory (also forwarded via Popen `cwd`).
- Trailing `-` reads prompt from stdin (per `lib.rs:1737–1771`).

**NDJSON event types (from `exec/src/exec_events.rs`):**
```ts
type ThreadEvent =
  | { type: "thread.started"; thread_id: string }
  | { type: "turn.started" }
  | { type: "turn.completed"; usage: Usage }
  | { type: "turn.failed"; error: { message: string } }
  | { type: "item.started"; item: ThreadItem }     // not in ticket
  | { type: "item.updated"; item: ThreadItem }     // not in ticket
  | { type: "item.completed"; item: ThreadItem }
  | { type: "error"; message: string };            // FATAL stream-level

interface Usage {
  input_tokens: number;
  cached_input_tokens: number;       // not in ticket
  output_tokens: number;
  reasoning_output_tokens: number;   // not in ticket
}
```

**Item types** (`item.type` values, from `exec_events.rs:96–122`):
- `agent_message` — `{ id, type, text }`. The text source for `output`.
- `reasoning` — `{ id, type, text }`. **Reasoning text IS surfaced**; per-item token count is NOT (only `Usage.reasoning_output_tokens`).
- `command_execution` — sandbox shell exec; carries `command`, `aggregated_output`, `exit_code`, `status`.
- `file_change` — **singular, not `file_changes`** as ticket says. Carries `changes: Array<{path, kind: add|delete|update}>` and `status`.
- `mcp_tool_call`, `collab_tool_call`, `web_search`, `todo_list` — **not in ticket scope** but appear in stream.
- Item-level `error` — non-fatal advisory (e.g. "model rerouted: gpt-5 -> gpt-5-mini"). Distinct from top-level `type: "error"`.

**Failure surface:**
- Exit code is 0 or 1 only. No structured failure type at the OS level.
- `turn.failed.error.message` — single string, no HTTP status, no error-type enum. Detail may be appended in parens.
- Top-level `error.message` — fatal stream error (e.g. serialization failure, dropped events from `Lagged`).
- Categorization MUST come from substring matching on the message text.

**`--output-last-message` semantics:**
- Written ONLY on `TurnStatus::Completed`. Cleared on Failed/Interrupted — file is NOT written.
- Contents: raw text of final agent message (or structured-output JSON if `--output-schema` was used). No JSON wrapping.
- Truncates+overwrites via `fs::write`.

**Auth + env:**
- Precedence: cached `$CODEX_HOME/auth.json` → `CODEX_API_KEY` → `OPENAI_API_KEY` → interactive ChatGPT login.
- Other env worth preserving: `CODEX_HOME`, `CODEX_CA_CERTIFICATE`, `SSL_CERT_FILE`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`.
- The ticket's two-key strip list is incomplete relative to "preserve everything that matters under corp networks." A scoping question covers this.

**Operational gotchas:**
- stderr is used even in `--json` mode (tracing, "Reading prompt from stdin..." notice, deprecation warnings). Capture but do NOT parse as JSON.
- Codex spawns subprocesses for `command_execution` items. On `SIGKILL` of parent these may orphan. Recommendation: run `codex exec` in its own process group (`start_new_session=True`) and on cleanup kill the group.
- `Lagged` events: synthetic `item.completed` with `type: "error"` text `"<N> events were dropped..."` if the in-process channel overflows. Surface but don't treat as fatal.
- No "stream end" sentinel — EOF on stdout is the signal.

### Convention Constraints (from Convention Checker)

These rules from `.claude/rules/` apply to #149 and become validation criteria during story generation:

| # | Rule | Constraint |
|---|---|---|
| C1 | `harness-protocol-shape.md` | All four protocol members (`name`, `invoke`, `strip_auth_keys`, `build_prompt`) on `CodexHarness` with exact-shape signatures. `subject` and `model` may be ignored but MUST be accepted. |
| C2 | `pure-compute-vs-io-split.md` | `build_prompt` is pure (no I/O). Classification helpers (e.g. `_classify_codex_failure`) live as module-level pure functions, individually testable. |
| C3 | `non-mutating-scrub.md` | `strip_auth_keys(env)` returns a NEW dict; tests assert input is unchanged. |
| C4 | `monotonic-time-indirection.md` | `_monotonic = time.monotonic` module-level alias; never call `time.monotonic` directly. |
| C5 | `subprocess-cwd.md` | `cwd` is keyword-only; default to runner-configured `project_dir` when None. |
| C6 | `stream-json-schema.md` | Defensive parsing posture: skip malformed lines with stderr warning + `warnings.append`, `.get(...) or {}` guards, `isinstance` before recursion, defensive `int()` on token counts. (Schema is Codex's own; the parsing posture transfers.) |
| C7 | `plan-contradiction-stop.md` | Ralph workers STOP if the plan contradicts code. The ticket text contains stale flag info (`--full-auto`); the plan resolves this in DEC, the worker honors the plan over the ticket. |
| C8 | `rule-refresh-vs-delete.md` | After implementation, refresh `harness-protocol-shape.md` to mention `CodexHarness` alongside `ClaudeCodeHarness` and `MockHarness` (Patterns & Memory story). |

No `workflow-project.md` exists. CLAUDE.md at repo root mandates `git push` at session close (relevant to closeout, not to this plan's stories).

### Cross-References

- **#143** — parent epic; comparability gotchas (reasoning tokens, refusal semantics).
- **#148 (PR #157, MERGED)** — `Harness` protocol, `InvokeResult.harness_metadata`, `MockHarness`, the `_harnesses/` package. Direct dependency.
- **#150 (PR #158, MERGED)** — `EvalSpec.system_prompt`, `Harness.build_prompt(skill_name, args, *, system_prompt) -> str`, auto-derive from `SKILL.md` body. Direct dependency.
- **#151** — out-of-scope sibling; will add `EvalSpec.harness` + `--harness` flag + `check_codex_auth`.
- **#154** — out-of-scope sibling; will surface cost / reasoning tokens / sandbox mode in a `context.json` sidecar — consumes whatever ends up in `harness_metadata`.
- **`docs/stream-json-schema.md`** — the contract document `docs/codex-stream-schema.md` must mirror.
- **`plans/super/148-extract-harness-protocol.md`** — DEC-007 (`harness_metadata`), DEC-008 (`allow_hang_heuristic` per-instance), DEC-005/DEC-017 (`apiKeySource` + `subject` pattern).
- **`plans/super/150-prompt-resolver.md`** — DEC-006 (`build_prompt` keyword-only `system_prompt`), DEC-012 (MockHarness parity).
- **Authoritative Codex sources** (already read by Domain Expert):
  - `codex-rs/exec/src/cli.rs` — flags
  - `codex-rs/exec/src/exec_events.rs` — event/item schema
  - `codex-rs/exec/src/event_processor_with_jsonl_output.rs` — wire emitter
  - `codex-rs/exec/src/lib.rs` — exit-code logic, stdin handling

---

## Scoping Decisions (locked 2026-04-30)

The 14 ambiguities surfaced by the Ticket Analyst collapsed into 5 high-leverage scoping questions. Lower-impact tactical decisions (`subject` no-op semantics, error-category enum extension, exact `harness_metadata` keys, defensive parsing posture details) are deferred to Phase 3 Refinement after Architecture Review.

### DEC-001 — Sandbox mode for v1: `workspace-write`, hardcoded
**Decision:** `CodexHarness.invoke` always passes `-s workspace-write` to `codex exec`. No constructor knob in v1.
**Rationale:** Closest semantics to the ticket's stated intent (`--full-auto`, now deprecated). Skills written for the Claude harness assume they can edit files in `cwd`; matching that behavior gives drop-in skill compatibility. Configurability deferred to #151 (the `EvalSpec.harness` flag ticket) — that's the right seam for a `sandbox=...` field, not the harness constructor.
**Trades against:** A `read-only` default would be safer for skills that don't expect a write sandbox, but it would silently break any skill that does. We pick "preserves Claude-equivalent behavior" over "safest default."

### DEC-002 — `strip_auth_keys` strips two, explicitly preserves six
**Decision:** Strip `CODEX_API_KEY` and `OPENAI_API_KEY`. Document that `CODEX_HOME`, `SSL_CERT_FILE`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `CODEX_CA_CERTIFICATE` are explicitly preserved (via a named `_PRESERVED_FOR_DOCS` constant or a comment block in the helper).
**Rationale:** The ticket's two-key list is correct for *credentials*, but the Codex CLI also reads `CODEX_HOME` (cached-auth source), TLS bundles, and proxy vars to function under corp networks. A naive strip-only-these helper is fine; the value of explicitly listing the preserved-vars is for the *next* maintainer who reads the helper and wonders why proxy vars aren't here.
**Trades against:** DEC-002C (also strip `CODEX_HOME`) would force credentials through the env we control — useful for test isolation, but breaks the operator-cached-auth path that's the documented happy path for local dev. We pick "respect operator's auth setup" over "force-isolated auth."

### DEC-003 — `stream_events` shape: pass-through Codex events with top-level `harness: "codex"` tag
**Decision:** Each Codex NDJSON event is appended to `InvokeResult.stream_events` verbatim except for an added `"harness": "codex"` key. `type` stays Codex-native (`"item.completed"`, `"turn.completed"`, `"thread.started"`, etc.). No coercion to Claude's `{type: "assistant", message: ...}` shape.
**Rationale:** Two-harness future means downstream consumers will branch on `harness` first anyway. Coercion-to-Claude-shape (Q3=B) loses Codex-native fields like `cached_input_tokens`, `reasoning_output_tokens`, item `id`, command-execution `status` — exactly the data #154 needs. Adding shadow `_claude_type` keys (Q3=C) is an anti-pattern that bloats the schema for no current consumer.
**Trades against:** A consumer that wanted "find the assistant text" used to do `for e in stream_events if e["type"] == "assistant"`. Now they need `if e["type"] == "assistant" or (e.get("harness") == "codex" and e.get("type") == "item.completed" and e["item"]["type"] == "agent_message")`. We accept the verbosity at the consumer site in exchange for not lying about the wire shape.

### DEC-004 — `build_prompt` strategy: simple two-newline join
**Decision:** `CodexHarness.build_prompt(skill_name, args, *, system_prompt)` returns `f"{system_prompt}\n\n{args}"` when `system_prompt` is non-empty; returns `args` alone otherwise. `skill_name` is unused (Codex has no slash-command analog).
**Rationale:** Codex treats the prompt as a single user message; there's no native role separator. Markdown role markers (Q4=B) and XML wrappers (Q4=C) impose structure Codex doesn't interpret and risk colliding with skill-authored markdown content. A two-newline separator is the universal "paragraph break" that won't false-trigger anything. AGENTS.md generation (Q4=D) is explicitly out of scope per #150.
**Trades against:** A future Responses-API harness will want native `system` / `user` role separation. That harness will own its own `build_prompt` — that's why this method is on the protocol per-harness, not centralized.

### DEC-005 — `--output-last-message` is wired, used as fallback only
**Decision:** `CodexHarness.invoke` always passes `--output-last-message <tempfile>` where `<tempfile>` is a `tempfile.NamedTemporaryFile(delete=False, suffix=".txt")`. Path is recorded as `harness_metadata["last_message_path"]`. `InvokeResult.output` is built primarily from concatenating `item.completed` events of `item.type == "agent_message"` (matching Claude's `assistant` text-block pattern). The tempfile is read into `output` only as a fallback when no `agent_message` items appeared in the stream. The tempfile is deleted in the `finally` block.
**Rationale:** Stream-events are the source of truth (matches Claude's pattern of joining `text` blocks from `assistant` messages). The `--output-last-message` file is Codex's "if you only want one thing, here it is" channel — useful as a safety net when stream parsing somehow misses everything (e.g., a future Codex version changes item shape). Storing the path in `harness_metadata` lets #154's context sidecar surface it without re-deriving.
**Trades against:** Q5=A (don't use the flag) is simpler but loses the safety-net property. Q5=C (file is primary) inverts the Claude pattern unnecessarily. Q5=D (stable path, not tempfile) creates a non-temp fs side-effect that's hard to test in parallel.

### Deferred to Phase 3 Refinement (not yet decided)
- **`subject` kwarg semantics** — Codex has no `apiKeySource` analog; choices are silent no-op vs. emitting a `clauditor.runner: codex sandbox=workspace-write (subject)` line.
- **`error_category` extension** — does the closed Literal in `runner.py` need a new `"sandbox"` or `"codex-fatal"` value, or do we squash everything into existing `api`/`auth`/`rate_limit`/`timeout`?
- **`harness_metadata` key set** — exact list of keys (`thread_id`, `cached_input_tokens`, `reasoning_output_tokens`, `last_message_path`, `sandbox_mode`, `dropped_events_count`?) and whether absent keys are omitted vs. `None`.
- **Process-group cleanup** — adopt `start_new_session=True` + group kill, or stick with Claude's pid-only kill.
- **Defensive parsing posture details** — non-fatal item-level `error` and `Lagged`/dropped-events handling, malformed line behavior.

These are tactical and benefit from architecture-review findings before locking.

---

## Architecture Review (Phase 2)

Five parallel reviews ran (Security, Performance, API Design, Observability, Testing). Data Model marked **N/A** — no schema changes; `harness_metadata: dict[str, Any]` is intentionally schemaless.

### Ratings

| Area | Rating | Summary |
|---|---|---|
| Security | **concern** | Sandbox-write trade-off accepted (DEC-001 stands); five tactical concerns → refinement questions R1, R3, R5. Reviewer's "BLOCKER" on sandbox-write was downgraded — Claude's harness DOES write files via the model's Edit/Write tools, so `-s workspace-write` matches Claude's effective baseline, not exceeds it. |
| Performance | **concern** | No blockers. Two soft-caps recommended (`_CODEX_COMMAND_OUTPUT_MAX_CHARS`, `_CODEX_STREAM_EVENTS_MAX_SIZE`) → refinement question R4. Measurement asks deferred to implementation. |
| Data Model | **n/a** | No schema changes. `harness_metadata: dict[str, Any]` is open by design (#148 DEC-007). |
| API Design | **pass** | All signatures locked. `error_category` Literal stays closed (every Codex failure squashes into existing categories via substring match). `harness_metadata` key set agreed. `stream_events` asymmetry (Codex tagged, Claude unmarked) is the right call. |
| Observability | **concern** | No blockers. stderr-surface policy is asymmetric vs. Claude (always-on for Codex) → R2. Three new warning prefixes proposed → R7. Two advisory detectors recommended for v1 → R7. |
| Testing | **pass** | Test plan complete: new file `tests/test_codex_harness.py` with ~50 methods across 7 classes; one drift-guard added to `test_runner.py:TestHarnessProtocol`; 5 NDJSON fixture helpers in `conftest.py`. |

### Findings — Security
- **CONCERN**: tempfile permissions on POSIX shared hosts (`tempfile.NamedTemporaryFile(delete=False, suffix=".txt")` — `/tmp/<random>` is world-readable without sticky bit; theoretical TOCTOU window before Codex writes the final-message file).
- **CONCERN**: auth-env scrubbing scope may miss adjacent OpenAI env vars (`OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_API_VERSION`) — these aren't credentials but can leak deployment topology.
- **CONCERN**: stream-event injection — `command_execution.aggregated_output` and `web_search` results land verbatim in `stream_events`; downstream consumers (#154's context sidecar) must escape on re-prompt.
- **CONCERN**: stderr leakage — Codex tracing-subscriber output may include file paths, env-var values, or partial credentials. Filter or pass-through?
- **CONCERN**: process-group cleanup — Codex spawns subprocesses for `command_execution`; on `proc.kill()` orphans persist with file handles open. `start_new_session=True` + `os.killpg` is the POSIX fix; Windows needs a fallback.

### Findings — Performance
- **CONCERN**: `command_execution.aggregated_output` can carry multi-MB log payloads. Recommend `_CODEX_COMMAND_OUTPUT_MAX_CHARS = 65536` cap on append (16× Claude's `_RESULT_TEXT_MAX_CHARS = 4096`, but bounded).
- **CONCERN**: `stream_events` total size unbounded under DEC-003 pass-through. A 10-minute reasoning-heavy run with 100 tool calls × 10 events/call × 10 KB/event ≈ 10 MB. Recommend `_CODEX_STREAM_EVENTS_MAX_SIZE = 52_428_800` (50 MB) soft envelope; on overflow, append `"stream-events-truncated:"`-prefixed warning and halt accumulation. Surface `stream_events_truncated: bool` in `harness_metadata`.
- **PASS** on subprocess startup, watchdog accuracy, drainer threads, NDJSON parse loop, tempfile I/O cost.

### Findings — API Design (locked, ready to roll into Refinement DECs)
- `CodexHarness.__init__` signature is `(self, *, codex_bin: str = "codex", model: str | None = None)`. **No `allow_hang_heuristic`** — Codex has no hang detection; runner.py:386 short-circuits via `getattr(self.harness, "allow_hang_heuristic", None)`.
- `name: ClassVar[str] = "codex"`.
- `error_category` Literal stays closed; Codex failures squash into existing values via substring match: `"rate limit"|"quota"` → `rate_limit`; `"unauthorized"|"403"|"OPENAI_API_KEY"` → `auth`; everything else → `api`. No new Literal values.
- `harness_metadata` populated keys (when available, absent keys omitted not None): `thread_id`, `cached_input_tokens`, `reasoning_output_tokens`, `last_message_path`, `sandbox_mode`, `dropped_events_count`.
- `subject` kwarg → emit `clauditor.runner: codex sandbox=workspace-write (subject)` to stderr (mirror Claude's `apiKeySource` pattern at `_claude_code.py:705–717`, including subject sanitization).
- `stream_events` asymmetry: Codex events get top-level `"harness": "codex"`; Claude events stay unmarked. No back-port.
- `CodexHarness` lives in `_codex.py`, NOT in `_harnesses/__init__.__all__`. Mirrors `ClaudeCodeHarness` privacy.

### Findings — Observability
- **CONCERN**: stderr-surface policy. Claude only surfaces stderr on `warnings` when `returncode != 0` (`_claude_code.py:802`). Reviewer recommends Codex always surface stderr, regardless of `returncode`, because Codex's tracing logs are observationally useful even on success. This is asymmetric vs. Claude.
- **CONCERN**: three new warning prefixes proposed — `dropped-events:` (Lagged synthetic event count), `codex-deprecation:` (informational stderr notices), `last-message-empty:` (DEC-005 fallback triggered, output likely truncated). Each carries `error_category=None` (advisory, not failure).
- **CONCERN**: `apiKeySource` analog — Codex emits no event identifying which credential path was used. Recommend env-inspection at invoke time emitting `clauditor.runner: codex auth=<source>` where `<source>` ∈ `{cached, CODEX_API_KEY, OPENAI_API_KEY, unknown}`. Same `subject`-suffix sanitization as Claude.
- **CONCERN**: v1 advisory detectors. `_detect_codex_dropped_events` (surface Lagged count) and `_detect_codex_truncated_output` (warn when stream has zero `agent_message` items but `--output-last-message` file is non-empty — signals stream clipping). Both pure helpers, both append warnings without setting `error_category`.

### Findings — Testing (test plan ready, no blockers)
- New file `tests/test_codex_harness.py` with 7 test classes and ~50 methods.
- Single drift-guard `test_codex_harness_satisfies_harness_protocol` lives in existing `tests/test_runner.py:TestHarnessProtocol` alongside Claude's parallel test.
- 5 NDJSON fixture helpers in `conftest.py`: `make_fake_codex_stream`, `make_fake_codex_agent_message_item`, `make_fake_codex_reasoning_item`, `make_fake_codex_command_execution_item`, `make_fake_codex_turn_failed`, `make_fake_codex_with_lagged_event`, `make_fake_codex_malformed_line_in_stream`.
- Reuse existing `_FakePopen` from `tests/conftest.py:29–74`; optional `_FakeCodexPopen` subclass to track `--output-last-message` tempfile path during cleanup tests.
- No `MockHarness` changes needed (no protocol additions). Claude tests untouched.
- Optional gated live-smoke test (`@pytest.mark.skipif(not os.getenv("CLAUDITOR_RUN_CODEX_LIVE"), ...)`) for upstream-schema-drift detection.

## Refinement Log (Phase 3)

Architecture-review consensus locked DEC-006 through DEC-011 without user input. R1–R7 user answers locked DEC-012 through DEC-018.

### DEC-006 — `CodexHarness.__init__(*, codex_bin: str = "codex", model: str | None = None)`
Two kwargs only. `name: ClassVar[str] = "codex"`. **No `allow_hang_heuristic`** — Codex has no hang detection, and `runner.py:386` already short-circuits on missing attribute via `getattr(self.harness, "allow_hang_heuristic", None)`.

### DEC-007 — `error_category` Literal stays closed
Codex failures squash via substring match on the `error.message` / `turn.failed.error.message`:
- `"rate limit" | "quota"` (case-insensitive) → `"rate_limit"`
- `"unauthorized" | "401" | "403" | "OPENAI_API_KEY" | "invalid api key"` (case-insensitive) → `"auth"`
- everything else → `"api"`
Rate-limit check runs first (matches Claude's deterministic precedence at `_claude_code.py:336–342`). No new Literal values; no schema change to `runner.py`.

### DEC-008 — `harness_metadata` populated key set
Populated keys when available (absent keys *omitted*, never `None`):
- `thread_id: str` (from first `thread.started`)
- `cached_input_tokens: int` (from `turn.completed.usage`)
- `reasoning_output_tokens: int` (from `turn.completed.usage`)
- `last_message_path: str` (DEC-005 tempfile path; the *path*, kept for #154's sidecar consumer — the file itself is deleted in `finally`)
- `sandbox_mode: str` (literal `"workspace-write"` per DEC-001)
- `dropped_events_count: int` (from `Lagged` synthetic events; only set if non-zero)
- `stream_events_truncated: bool` (DEC-015 envelope cap; only set if `True`)
- `auth_source: str` (DEC-017; mirrors stderr line)

### DEC-009 — `subject` kwarg → stderr parity line
On every invoke, after the first `thread.started` event lands, emit one line to stderr:
```
clauditor.runner: codex sandbox=workspace-write[ (subject)]
```
Subject sanitization mirrors `_claude_code.py:705–717` exactly: replace `\r\n` with space, strip, cap at 200 chars, append in parens.

### DEC-010 — `stream_events` discriminator asymmetry
Codex events get a top-level `"harness": "codex"` key added on append. Claude events stay unmarked. **No back-port** to Claude. Consumers detect Codex by presence of the key; absence ⇒ Claude.

### DEC-011 — `CodexHarness` is private; `build_prompt` ignores `skill_name`
Lives in `src/clauditor/_harnesses/_codex.py`. NOT added to `_harnesses/__init__.__all__`. Mirrors Claude's privacy. `build_prompt(skill_name, args, *, system_prompt)` ignores `skill_name` (Codex has no slash-command analog) and returns `f"{system_prompt}\n\n{args}"` when `system_prompt` is truthy, else `args` alone. Empty string `system_prompt=""` is treated as falsy.

### DEC-012 — Strip three OpenAI vars (R1=B)
`strip_auth_keys` removes `CODEX_API_KEY`, `OPENAI_API_KEY`, **and `OPENAI_BASE_URL`**. The base-URL strip prevents an attacker-controlled `OPENAI_BASE_URL` from routing Codex traffic to a malicious endpoint. Preserve `OPENAI_ORG_ID`, `OPENAI_API_VERSION` (non-credential metadata; useful for billing audit) and the previously-named six (`CODEX_HOME`, `SSL_CERT_FILE`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `CODEX_CA_CERTIFICATE`).

### DEC-013 — Hybrid stderr surfacing (R2=D)
On every invoke (success or failure):
1. Capture full stderr via the drainer thread (Claude pattern at `_claude_code.py:514–539`).
2. Cap captured text at 8 KB (`_CODEX_STDERR_MAX_CHARS = 8192`); on overflow, append `... (truncated)` suffix.
3. Filter out lines matching auth-leak patterns: case-insensitive substring match on any of `api_key`, `Authorization`, `OPENAI_API_KEY=`, `CODEX_API_KEY=`, `CODEX_HOME=`. Filtered lines are *replaced* with `<line redacted: matched auth-leak pattern>` (count, not content) so an operator knows redaction happened.
4. Surface filtered+capped text as a single warning entry on `InvokeResult.warnings`.

### DEC-014 — Process-group cleanup with Windows fallback (R3=B)
On POSIX (`os.name != "nt"`): pass `start_new_session=True` to `Popen`; on cleanup or timeout, escalate to `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` then `SIGKILL` if needed. On Windows: skip `start_new_session`, fall back to single-pid `proc.terminate()` / `proc.kill()` (Claude's pattern). The cleanup path's `try / except OSError` already absorbs the "process group already dead" case.

### DEC-015 — Adopt both performance soft-caps (R4=A)
- `_CODEX_COMMAND_OUTPUT_MAX_CHARS = 65536` — cap on `command_execution.aggregated_output` at append time. Truncated text gets a `... (truncated)` suffix. Append a warning the first time the cap fires per invoke (not per event — avoid log flooding).
- `_CODEX_STREAM_EVENTS_MAX_SIZE = 52_428_800` (50 MB) — measured via `len(json.dumps(stream_events))` once per minute (cheap amortization) OR on every event via running byte-count. **Pick running byte-count** — `json.dumps` of a 50 MB list every minute is itself expensive. Use a `_stream_events_size: int` accumulator updated with `len(json.dumps(event))` on append. On overflow: stop appending, set `harness_metadata["stream_events_truncated"] = True`, append warning with `stream-events-truncated:` prefix, continue parsing for the final `turn.completed` (so token counts still land).

### DEC-016 — Per-invocation `TemporaryDirectory` (R5=C)
Use `tempfile.TemporaryDirectory()` as a context manager wrapping the entire `invoke` body. The `--output-last-message` tempfile lives inside (path: `<tempdir>/last_message.txt`). Cleanup is automatic on context exit (success, exception, or timeout). The tempdir's name is captured into `harness_metadata["last_message_path"]` BEFORE deletion so #154's consumer has a string reference for logs (the file is gone — that's fine; the path is just a label).

### DEC-017 — auth-source stderr line via env-inspection (R6=B)
At the start of `invoke`, before Popen, inspect `env` (or `os.environ` if `env=None`) and pick the first matching auth source:
1. `CODEX_API_KEY` set ⇒ `auth=CODEX_API_KEY`
2. `OPENAI_API_KEY` set ⇒ `auth=OPENAI_API_KEY`
3. `$CODEX_HOME/auth.json` exists (default `~/.codex/auth.json`; respects `CODEX_HOME` override) ⇒ `auth=cached`
4. otherwise ⇒ `auth=unknown`

Emit `clauditor.runner: codex auth=<source>[ (subject)]` to stderr. Subject sanitization same as DEC-009. Also write `<source>` into `harness_metadata["auth_source"]` for programmatic access.

### DEC-018 — Both v1 advisory detectors + three warning prefixes (R7=A)
Pure helpers in `_codex.py`:
- `_detect_codex_dropped_events(stream_events) → int` — sum of all `Lagged`-synthetic-event drop counts. Returns 0 when none.
- `_detect_codex_truncated_output(stream_events, last_message_text) → bool` — `True` when no `item.completed` event with `item.type == "agent_message"` appears AND `last_message_text` (read from the tempfile) is non-empty.

Three new warning prefix constants land in `runner.py` next to `_INTERACTIVE_HANG_WARNING_PREFIX`:
- `_DROPPED_EVENTS_WARNING_PREFIX = "dropped-events:"`
- `_CODEX_DEPRECATION_WARNING_PREFIX = "codex-deprecation:"` (reserved; surfaces if Codex's stderr emits a `warning: ... deprecated` line)
- `_LAST_MESSAGE_EMPTY_WARNING_PREFIX = "last-message-empty:"`

`SkillResult.succeeded_cleanly` is **NOT** updated for these — they are advisory, `error_category=None`. (Distinct from `interactive-hang:` and `background-task:` which DO down-classify success.)

## Detailed Breakdown (Phase 4)

Seven stories: 5 implementation + Quality Gate + Patterns & Memory. Order follows the natural code-build sequence (pure helpers → class surface → invoke happy path → invoke error paths → docs → review → conventions). Each story is sized to fit one Ralph context window.

**Convention rules validated against:** C1 (`harness-protocol-shape`), C2 (`pure-compute-vs-io-split`), C3 (`non-mutating-scrub`), C4 (`monotonic-time-indirection`), C5 (`subprocess-cwd`), C6 (`stream-json-schema` parsing posture), C8 (`rule-refresh-vs-delete`).

---

### US-001 — Module skeleton + pure helpers + constants (TDD)

**Description:** Create `src/clauditor/_harnesses/_codex.py` with pure module-level helpers, constants, and the `_monotonic` alias. No class yet. All helpers are TDD-first.

**Traces to:** DEC-007, DEC-014 (constants), DEC-015 (soft-cap constants), DEC-018 (detectors + warning prefix constants). Convention rules C2, C4.

**TDD — write failing tests first** (`tests/test_codex_harness.py`):
- `TestClassifyCodexFailure` (8 tests): rate_limit precedence, auth via 401/403/`OPENAI_API_KEY`, generic → api, empty/None message handling, non-string message, truncation at 4096 chars.
- `TestDetectCodexDroppedEvents` (4 tests): zero events → 0; one Lagged synthetic with N → N; multiple Lagged synthetics → sum; malformed event ignored.
- `TestDetectCodexTruncatedOutput` (5 tests): no agent_message + non-empty file → True; one agent_message + non-empty file → False; no agent_message + empty file → False; malformed events tolerated; empty stream_events → False.

**Implementation:**
- Module docstring with rule anchors (`pure-compute-vs-io-split.md` sixth-anchor pattern).
- `_monotonic = time.monotonic` (C4).
- Constants: `_PRESERVED_ENV_VARS_DOC` (the six per DEC-012 documented as a comment), `_STRIP_ENV_VARS = frozenset({"CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"})`, `_RESULT_TEXT_MAX_CHARS = 4096`, `_CODEX_COMMAND_OUTPUT_MAX_CHARS = 65536`, `_CODEX_STREAM_EVENTS_MAX_SIZE = 52_428_800`, `_CODEX_STDERR_MAX_CHARS = 8192`, `_AUTH_LEAK_PATTERNS = (...)` per DEC-013, `_RATE_LIMIT_PATTERNS = (...)`, `_AUTH_PATTERNS = (...)`.
- Warning prefix constants in `runner.py` (NOT `_codex.py` — same module as Claude's prefixes for symmetry): `_DROPPED_EVENTS_WARNING_PREFIX`, `_CODEX_DEPRECATION_WARNING_PREFIX`, `_LAST_MESSAGE_EMPTY_WARNING_PREFIX`. Imported back into `_codex.py`.
- Pure helpers: `_classify_codex_failure(message: str | None) → tuple[str | None, str | None]`, `_detect_codex_dropped_events(stream_events: list[dict]) → int`, `_detect_codex_truncated_output(stream_events: list[dict], last_message_text: str) → bool`, `_filter_stderr(stderr_text: str) → str` (DEC-013 line-filter + cap).

**Files:**
- CREATE `src/clauditor/_harnesses/_codex.py` (~250 LOC)
- CREATE `tests/test_codex_harness.py` (~300 LOC, scaffolding + helper tests)
- MODIFY `src/clauditor/runner.py` (add three new warning prefix constants near line 137–149)

**Done when:** All TDD tests pass. `pytest tests/test_codex_harness.py` green. Module imports without error. No `CodexHarness` class yet.

**Depends on:** none.

---

### US-002 — `CodexHarness` class surface + protocol drift-guard (TDD)

**Description:** Add the `CodexHarness` class with the three protocol methods that don't invoke a subprocess: `__init__`, `strip_auth_keys`, `build_prompt`. Add a drift-guard test to existing `TestHarnessProtocol`.

**Traces to:** DEC-006 (`__init__` shape), DEC-011 (`build_prompt` shape, privacy), DEC-012 (strip 3 keys, preserve 6 named). Convention rules C1, C3.

**TDD:**
- `TestCodexHarnessStripAuthKeys` (5 tests): strips the 3 named credentials; preserves the 6 named non-credentials and arbitrary other vars; non-mutating (input dict unchanged); `None` input reads `os.environ`; empty-input edge case.
- `TestCodexHarnessBuildPrompt` (6 tests): `system_prompt=None` returns `args` unchanged; `system_prompt=""` treated as None; non-empty system_prompt + args joined with `\n\n`; preserves embedded newlines in both inputs; `skill_name` is unused (test that any value gives same output); empty args returns `f"{system_prompt}\n\n"` (or `system_prompt` — pick during impl, document).
- In `tests/test_runner.py:TestHarnessProtocol`, add `test_codex_harness_satisfies_harness_protocol` parallel to the existing Claude test.

**Implementation:**
- `class CodexHarness:` with `name: ClassVar[str] = "codex"`.
- `__init__(self, *, codex_bin: str = "codex", model: str | None = None) → None`.
- `strip_auth_keys` strips DEC-012's 3 vars, returns new dict (C3 non-mutating).
- `build_prompt` per DEC-011.
- Class is NOT exported in `_harnesses/__init__.py` (DEC-011).

**Files:**
- MODIFY `src/clauditor/_harnesses/_codex.py` (~80 LOC added)
- MODIFY `tests/test_codex_harness.py` (~150 LOC added)
- MODIFY `tests/test_runner.py` (one drift-guard test, ~20 LOC added)

**Done when:** All new tests pass. `isinstance(CodexHarness(), Harness)` is `True`. Drift-guard test mirrors Claude's pattern.

**Depends on:** US-001.

---

### US-003 — `CodexHarness.invoke` happy path (TDD)

**Description:** Implement the full subprocess lifecycle and NDJSON parse loop for the success path. Includes argv assembly, `TemporaryDirectory`, `start_new_session`, drainer thread, watchdog timer, finally cleanup, and event-type dispatch for `thread.started`, `turn.started`, `turn.completed`, and `item.completed` (subtypes `agent_message`, `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`, `web_search`, `todo_list`). Soft-cap on `command_execution.aggregated_output` (DEC-015) is wired here.

**Traces to:** DEC-001 (sandbox argv), DEC-003 (`harness` tag on every event), DEC-005 (output-last-message tempfile), DEC-008 (`harness_metadata` keys), DEC-009 (`subject` stderr line), DEC-010 (asymmetric `harness` tag), DEC-014 (process group on POSIX, Windows fallback), DEC-015 (per-event soft-cap), DEC-016 (TemporaryDirectory), DEC-017 (auth-source stderr line). Rules C5, C6.

**TDD** (`TestInvokeCodexExec` happy-path subset, ~12 tests, all using `_FakeCodexPopen`):
- argv assembled correctly (`[codex_bin, "exec", "--json", "--output-last-message", <path>, "--skip-git-repo-check", "-s", "workspace-write", "-m", model, "-"]`).
- prompt written to stdin then stdin closed.
- `cwd` forwarded to Popen (or `project_dir` default).
- `env` forwarded verbatim (no auth-stripping at this layer; caller does that).
- POSIX: `start_new_session=True` passed to Popen; Windows: not passed (mock `os.name`).
- `thread.started.thread_id` lands in `harness_metadata["thread_id"]`.
- `turn.completed.usage.{input_tokens, output_tokens, cached_input_tokens, reasoning_output_tokens}` map correctly into `InvokeResult.{input_tokens, output_tokens}` + `harness_metadata`.
- `item.completed[agent_message]` text concatenated into `InvokeResult.output` (newline-joined).
- `item.completed[reasoning]` text appears in `stream_events` but NOT in `output`.
- Every appended event has `"harness": "codex"` top-level key (DEC-010).
- `command_execution.aggregated_output` over 64 KB truncated with `... (truncated)` and one warning emitted.
- `--output-last-message` tempfile path lands in `harness_metadata["last_message_path"]`.
- `auth=<source>` stderr line emitted once with subject suffix when subject given.
- `subject` sanitization: `\r\n` → space, strip, 200-char cap.
- TemporaryDirectory deleted in finally (assert via `os.path.exists`).
- `duration_seconds` populated.

**Implementation note:** Mirror `_claude_code.py:417–865` structure but substitute Codex's argv, event types, and DEC-014/15/16/17 specifics. Keep stream-events-running-byte-count accumulator (DEC-015 envelope) in scope but defer enforcement to US-004.

**Files:**
- MODIFY `src/clauditor/_harnesses/_codex.py` (~350 LOC added — the `invoke` method)
- MODIFY `tests/test_codex_harness.py` (~400 LOC added — `TestInvokeCodexExec` + fixture helpers `make_fake_codex_stream` and the item builders)
- MODIFY `tests/conftest.py` (add `_FakeCodexPopen` and stream-builder fixtures)

**Done when:** Happy-path tests green. Drift-guard test still green. `pytest tests/test_codex_harness.py -k 'TestInvokeCodexExec and not error and not timeout'` is fully green.

**Depends on:** US-001, US-002.

---

### US-004 — `CodexHarness.invoke` error paths + stderr filter + advisory detectors

**Description:** Round out `invoke` with timeout/kill, FileNotFoundError, malformed-line skip+warn, `turn.failed` and top-level `error` classification, stderr capture+filter+cap (DEC-013), envelope cap enforcement (DEC-015), and the two advisory detectors (DEC-018).

**Traces to:** DEC-007 (classification), DEC-013 (stderr hybrid), DEC-014 (kill / killpg), DEC-015 (envelope), DEC-018 (detectors + prefixes).

**TDD** (`TestInvokeCodexExec` error-path subset, ~15 tests):
- `turn.failed.error.message` "rate limit exceeded" → `error_category="rate_limit"`, error text captured.
- `turn.failed.error.message` "401 unauthorized" → `error_category="auth"`.
- `turn.failed.error.message` "internal server error" → `error_category="api"`.
- Top-level `error` event: `error_category="api"`, error text from `error.message`.
- Malformed JSON line: warning appended, parsing continues, run still classifies via subsequent events.
- Codex binary missing: `InvokeResult(exit_code=-1, error="Codex CLI not found: ...")`.
- Timeout: watchdog kills process, on POSIX uses `os.killpg`, `error_category="timeout"`, `error="timeout"`, exit_code=-1, stream_events preserved.
- Stderr captured, filtered (auth-leak patterns redacted), capped at 8 KB. Test redaction count vs. content-leak.
- Stream-events envelope hit: `harness_metadata["stream_events_truncated"] = True`, `stream-events-truncated:` warning, parsing continues for `turn.completed`.
- `_detect_codex_dropped_events` fires when Lagged synthetics present → `dropped-events:` warning, `harness_metadata["dropped_events_count"]`.
- `_detect_codex_truncated_output` fires when no agent_message + non-empty tempfile → `last-message-empty:` warning. Output falls back to tempfile content (DEC-005 fallback).
- `succeeded_cleanly` returns `True` for runs with only advisory warnings (no `interactive-hang:` / `background-task:` prefixes).
- Cleanup runs on every exit path (success, exception, timeout) — TemporaryDirectory deleted, no orphans.

**Implementation:**
- Wire `_classify_codex_failure` into the `turn.failed` and top-level `error` event handlers.
- Wrap stderr capture with `_filter_stderr` (DEC-013) before appending to warnings.
- After parse loop, run both detectors and append warnings as appropriate.
- On stream-events envelope overflow: stop appending events but continue reading lines from stdout to find the final `turn.completed` (so token usage still lands).
- POSIX kill path uses `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` then SIGKILL escalation; Windows uses `proc.terminate()` / `proc.kill()`. Both wrapped in `try/except OSError` to absorb already-dead state.

**Files:**
- MODIFY `src/clauditor/_harnesses/_codex.py` (~150 LOC added)
- MODIFY `tests/test_codex_harness.py` (~300 LOC added)
- MODIFY `tests/conftest.py` (add `make_fake_codex_turn_failed`, `make_fake_codex_with_lagged_event`, `make_fake_codex_malformed_line_in_stream`)

**Done when:** All TDD tests green. `pytest tests/test_codex_harness.py` fully green. Tempfile-cleanup tests prove no `/tmp/...codex...` leaks across the test run.

**Depends on:** US-003.

---

### US-005 — `docs/codex-stream-schema.md`

**Description:** Author the Codex NDJSON reference doc paralleling `docs/stream-json-schema.md`. Pure docs; no code. Operators / future maintainers read this as the contract.

**Traces to:** DEC-003, DEC-007, DEC-008, DEC-010, DEC-018. The doc cites Codex source paths verbatim from the Domain Expert's report (Discovery section).

**Acceptance:**
- ≥200 lines, ≤350 lines (matches `stream-json-schema.md` bar).
- Sections: Transport contract, Top-level event types, `Usage` field set, `ThreadItem` `item.type` matrix, Failure surface (`turn.failed` vs top-level `error`), `--output-last-message` semantics, Auth precedence, `harness` tag (DEC-010), Error category mapping (DEC-007), `harness_metadata` key contract (DEC-008), Advisory warnings (DEC-018), Operational gotchas (process group, stderr, Lagged events).
- Each event type has a TypeScript-like type sketch + a one-line citation to a Codex source file (e.g., `from openai/codex codex-rs/exec/src/exec_events.rs:11–39`).
- Cross-link from `docs/stream-json-schema.md` and `docs/transport-architecture.md` to the new doc.

**Files:**
- CREATE `docs/codex-stream-schema.md` (~250 lines)
- MODIFY `docs/stream-json-schema.md` (add 1-line "see also" link)
- MODIFY `docs/transport-architecture.md` (add Codex transport row to whatever architecture matrix exists)

**Done when:** Doc renders cleanly via `mkdocs serve`. Cross-links resolve. CodeRabbit / `docs-only` review passes (Quality Gate).

**Depends on:** US-004 (so the documented behavior matches the implemented behavior).

---

### US-006 — Quality Gate (code review × 4 + CodeRabbit)

**Description:** Run the project's standard quality bar before merge. Each pass fixes all real bugs found.

**Acceptance:**
- 4 sequential `code-review` passes; each pass surfaces zero new issues by the final pass (or only style nits).
- CodeRabbit review (if available on the PR) clean.
- `pytest` fully green: `pytest tests/test_codex_harness.py tests/test_runner.py`.
- `ruff check src/clauditor/_harnesses/_codex.py tests/test_codex_harness.py` clean.
- `mypy` (if configured) green.
- `mkdocs build --strict` clean.
- All convention rules from Phase 1 (C1–C8) satisfied. Specifically verify: `inspect.signature` drift-guard locks, non-mutating scrub property test, `_monotonic` not bypassed.

**Files:** No new files; fixes land in any of US-001..US-005.

**Done when:** All checks pass; PR is ready to mark "ready for review."

**Depends on:** US-005.

---

### US-007 — Patterns & Memory

**Description:** Refresh `.claude/rules/harness-protocol-shape.md` to mention `CodexHarness` alongside `ClaudeCodeHarness` and `MockHarness` as canonical implementations. Capture any new patterns that emerged during implementation as new rules or as additions to existing ones.

**Acceptance:**
- `.claude/rules/harness-protocol-shape.md` updated to list `CodexHarness` as a canonical implementation alongside Claude / Mock. Anchors point to specific lines in `_codex.py`.
- If a new pattern emerged worth memorializing (likely candidates: hybrid stderr filter, per-invocation `TemporaryDirectory` lifecycle, asymmetric `stream_events` discriminator, advisory-warning vs failure-warning distinction), draft a new `.claude/rules/<topic>.md`.
- If `_filter_stderr`'s line-redaction pattern ends up reusable, factor a rule for it.
- Update memory notes if any project-level facts changed (unlikely, but check).

**Files:**
- MODIFY `.claude/rules/harness-protocol-shape.md`
- Possibly CREATE `.claude/rules/<new-pattern>.md` (0–2 new rules)

**Done when:** Rules reviewed and committed. PR comment links each new/updated rule to the line in `_codex.py` that exemplifies it.

**Depends on:** US-006.

## Beads Manifest (Phase 7)

_pending_
