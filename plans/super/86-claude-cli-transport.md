# Super Plan: #86 — Route direct-SDK calls through `claude -p` subprocess to enable subscription auth

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/86
- **Branch:** `feature/86-claude-cli-transport`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/86-claude-cli-transport`
- **Phase:** `devolved`
- **Predecessor PR (Option A, shipped):** https://github.com/wjduenow/clauditor/pull/87 (merged into `dev`)
- **Predecessor plan:** `plans/super/83-subscription-auth-gap.md`
- **Sessions:** 2
- **Last session:** 2026-04-22
- **Total decisions:** 21 (DEC-001 through DEC-021)
- **PR URL:** (pending push)

---

## Discovery

### Ticket Summary

**What:** Replace the guard-then-fail behavior shipped in #83 (pre-flight `AnthropicAuthMissingError` when `ANTHROPIC_API_KEY` is unset) with guard-then-fall-back-to-subprocess. Add a second transport inside the centralized `src/clauditor/_anthropic.py::call_anthropic` seam that invokes `claude -p --output-format {json|stream-json}` as a subprocess and parses the CLI's response into an `AnthropicResult`-compatible shape. The `claude` CLI natively supports both API-key and subscription auth, so routing through it closes the exact gap #83 documented.

**Why:** #83 made the error actionable; it did not make the feature work. The six LLM-mediated commands (`grade`, `propose-eval`, `suggest`, `triggers`, `extract`, `compare --blind`) still cannot run under subscription-only auth — which is clauditor's primary target segment. The hybrid landed with a forward-pointer ("Subscription support via `claude -p` is tracked in #86."); this epic delivers that support.

**Who benefits:** Same user segment as #83 — engineers on a Pro/Max subscription iterating on skills, who chose their plan specifically to avoid per-token billing. After this lands, they can run the full L2+L3 grading/propose/suggest/trigger story with no API key.

**Done when:**

1. All six LLM-mediated commands succeed under subscription-only auth (no `ANTHROPIC_API_KEY`, `~/.claude/` creds present).
2. All six continue to succeed under API-key auth (regression).
3. `_anthropic.py` retains single-source-of-truth property — new callers get both transports for free (`.claude/rules/centralized-sdk-call.md`).
4. Token accounting (`input_tokens` / `output_tokens` on `AnthropicResult`) works for both transports; sidecars record the same shape.
5. Error categorization (`rate_limit`, `auth`, `api`, connection) maps cleanly across both transports.
6. #83's pre-flight guard relaxes — missing `ANTHROPIC_API_KEY` no longer fails these commands if `claude -p` auth is available.
7. Docs updated (`README.md` + `docs/cli-reference.md` — the `## Authentication and API Keys` section's matrix changes rows from ✗ to ✓).
8. Coverage stays ≥80%; ruff passes.

### Scope adjustment from the ticket

The ticket says **"all five LLM-mediated commands"**. #83's QG Pass 2 (DEC-017) found that `compare --blind` routes through `blind_compare_from_spec` → `call_anthropic`, so #83 added a sixth guard. **This plan operates on six commands, not five**: `grade`, `propose-eval`, `suggest`, `triggers`, `extract`, and `compare --blind`. The pytest plugin fixtures (`clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare`) are three additional surfaces that also invoke `check_anthropic_auth` today.

### Codebase Findings

#### Central seam — `src/clauditor/_anthropic.py` (post-#83)

- **`call_anthropic(prompt, *, model, max_tokens=4096) -> AnthropicResult`** (lines 255–260). All six LLM callers import and call this.
- **`AnthropicResult`** dataclass (lines 155–180): `response_text: str`, `text_blocks: list[str]`, `input_tokens: int`, `output_tokens: int`, `raw_message: Any` (the SDK `Message` object — None-safe downstream).
- **Retry ladder** (lines 16–28): `RateLimitError` → 3 retries (4 attempts), `APIStatusError` ≥500 → 1 retry, 4xx non-401/403 → no retry, `AuthenticationError`/`PermissionDeniedError` → no retry with `ANTHROPIC_API_KEY`-hint message, `APIConnectionError` → 1 retry.
- **Backoff**: `_compute_backoff(i)` = `2 ** i` seconds × uniform `[0.75, 1.25]` jitter. Uses `_sleep` / `_rand_uniform` module-level aliases per `.claude/rules/monotonic-time-indirection.md`.
- **`AnthropicHelperError(RuntimeError)`** (exit 3 per taxonomy): wraps any non-retriable/exhausted SDK failure with a sanitized user-facing message, preserves original via `from exc`.
- **`AnthropicAuthMissingError(Exception)`** (exit 2, new in #83): pre-flight class, thrown by `check_anthropic_auth`.
- **`check_anthropic_auth(cmd_name: str) -> None`** (lines 122–152): pure helper, reads only `os.environ`, interpolates `_AUTH_MISSING_TEMPLATE` (with durable substrings `"ANTHROPIC_API_KEY"`, `"Claude Pro"`, `"console.anthropic.com"` per DEC-012), raises when `ANTHROPIC_API_KEY` is absent/empty/whitespace.
- **`TypeError` wrap** (lines 285–298, 359–379 — DEC-008/DEC-015 of #83): both the `AsyncAnthropic()` constructor and `messages.create` catch `TypeError` and raise `AnthropicHelperError` with a fixed sanitized message.

#### Existing subprocess transport — `src/clauditor/runner.py` (already battle-tested)

This is the **load-bearing finding**: the subprocess machinery #86 needs already exists, fully tested, and is the canonical parser for `claude -p` output.

- **`SkillRunner._invoke`** (lines 382–392, 436–444): spawns `subprocess.Popen([claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose"], env=env, cwd=cwd)`. Prompt passed as argv, stdout is NDJSON.
- **`env_without_api_key(base_env=None)`** (lines 33–46): non-mutating helper that strips `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from an env dict. Matches `.claude/rules/non-mutating-scrub.md`.
- **Watchdog** (lines 495–518): `threading.Timer(timeout, _on_timeout)` → `proc.kill()`.
- **`_classify_result_message(msg: dict) -> tuple[str | None, str | None]`** (lines 214–277): pure classifier for stream-json `type="result"` messages. Ordered keyword matching: `rate_limit` (429 / "rate limit" / "rate-limit") → `auth` (401 / 403 / "unauthorized" / "authentication" / "ANTHROPIC_API_KEY" substring) → `api` (fallback). Rate-limit runs before auth so ambiguous strings classify as `rate_limit`.
- **`apiKeySource` extraction** (lines 557–565, 84, from #64 DEC-017): first `type="system", subtype="init"` message's `apiKeySource` field is surfaced as `SkillResult.api_key_source`.
- **Token accounting**: stream-json `result` message's `usage.input_tokens` / `output_tokens` parsed defensively at lines ~600, populated onto `SkillResult`.
- **`SkillResult`** (lines 50–108): carries `output`, `exit_code`, `duration_seconds`, `error`, `error_category`, `input_tokens`, `output_tokens`, `api_key_source`, plus `raw_messages` / `stream_events` arrays.
- **`stream-json` parser is rule-protected** (`.claude/rules/stream-json-schema.md`): every field tolerated-if-missing; strict `is True` on `is_error`; malformed lines skipped with stderr warning; result text truncated at 4 KB.

#### Six call sites of `call_anthropic` (all unchanged on their surface)

| File | Function | Line | max_tokens | Pure-parser seam |
|---|---|---|---|---|
| `grader.py` | `extract_and_grade` | 718–728 | 4096 | `build_extraction_assertion_set` |
| `grader.py` | `extract_and_report` | 751–763 | 4096 | `build_extraction_report_from_text` |
| `quality_grader.py` | `grade_quality` | 889–904 | 4096 | `build_grading_report` |
| `quality_grader.py` | `blind_compare` | 521–524 | 2048 ×2 via `asyncio.gather` | `combine_blind_results` |
| `suggest.py` | `propose_edits` | 986–987 | variable (pre-call token budget) | `parse_propose_edits_response` |
| `propose_eval.py` | `propose_eval` | 732–733 | variable | `parse_propose_eval_response` + `validate_proposed_spec` |
| `triggers.py` | `test_triggers` | 193 | 1024 | `parse_trigger_response` |

All seven invocations use the same `call_anthropic(prompt, model=..., max_tokens=...)` signature. The pure-builder split (`.claude/rules/pure-compute-vs-io-split.md` fifth anchor) means transport selection is free of parser concerns — only `call_anthropic` needs to branch.

#### Six CLI guard sites (post-#83)

| CLI file | Line | Guard call |
|---|---|---|
| `cli/grade.py` | 243 | `check_anthropic_auth("grade")` |
| `cli/extract.py` | 81 | `check_anthropic_auth("extract")` |
| `cli/propose_eval.py` | 305 | `check_anthropic_auth("propose-eval")` |
| `cli/suggest.py` | 172 | `check_anthropic_auth("suggest")` |
| `cli/triggers.py` | 89 | `check_anthropic_auth("triggers")` |
| `cli/compare.py` | 214 | `check_anthropic_auth("compare --blind")` |

#### Pytest plugin fixtures (three surfaces)

- `clauditor_grader` (pytest_plugin.py:200–229) — calls `check_anthropic_auth("grader")` at factory invocation, runs `grade_quality` via `asyncio.run()`.
- `clauditor_triggers` (307–327) — calls `check_anthropic_auth("triggers")`, runs `test_triggers`.
- `clauditor_blind_compare` (261–303) — calls `check_anthropic_auth("blind_compare")`, runs `blind_compare_from_spec`.

All three raise `AnthropicAuthMissingError` at fixture-invocation time (DEC-005/DEC-013 of #83). Whether they relax symmetrically with the CLI is a scoping question below.

#### Docs touch points

- `docs/cli-reference.md:239–287` — `## Authentication and API Keys` section with a matrix showing which commands need the key. **Six of the rows will flip from ✗ to ✓ (or ✓ conditional on `claude -p` auth cached)**.
- `docs/cli-reference.md:285–287` — explicit "tracked in #86" forward-pointer that this PR removes or replaces.
- `README.md:~156` — D2-lean `--no-api-key` teaser; may gain one sentence about "subscription now works for grading too".
- `docs/stream-json-schema.md:1–184` — already the authoritative reference for the NDJSON format the new transport consumes; may need a new section describing "non-skill" invocations (no skill name, raw prompt).

#### Critical reusability fact

`SkillRunner._invoke` is parameterized on a `skill_name` because it constructs a `/<name>` slash command in the prompt. But the Popen command is just `[claude_bin, "-p", prompt, ...]` — **`skill_name` is only baked into the `prompt` argument, not the command line**. A thin adapter that calls `SkillRunner` (or an extracted lower-level helper) with a raw prompt and an empty skill name is the path of least resistance. Alternative: a new ~40-line bare invoker in `_anthropic.py`. Both are in scope for Phase 2 / Refinement.

### Rule Compliance Gate (from Convention Checker)

Seven rules apply directly; two partial; rest unrelated. The load-bearing five:

- **`.claude/rules/centralized-sdk-call.md`** — Transport decision MUST live inside `call_anthropic`; never at a call site. The six callers stay transport-blind. The exception surface (`AnthropicHelperError`) stays unified so CLI commands' catch-and-route-to-exit-3 logic does not change.
- **`.claude/rules/pure-compute-vs-io-split.md`** (fifth anchor) — Pure builders (`build_grading_prompt`, `build_extraction_prompt`, `build_blind_prompt`, …) and pure parsers (`build_grading_report`, `combine_blind_results`, `parse_extraction_response`, …) see NO changes. Transport-aware code lives only in `call_anthropic` (and any helper it spawns). Tests for the pure layer stay SDK-free.
- **`.claude/rules/stream-json-schema.md`** — The CLI-transport parser consumes stream-json output per this rule's defensive contract: `.get(...) or {}` guards, `isinstance` before recursion, strict `is True` on `is_error`, truncate `result` text to 4 KB, warn-and-skip on missing `result` message. Reusing `SkillRunner._invoke` gets this for free.
- **`.claude/rules/llm-cli-exit-code-taxonomy.md`** — CLI-transport failures map to the existing four-exit-code table: missing-binary/unreachable-auth → exit 2 (pre-call); subprocess-timeout/non-zero-exit/malformed-JSON → wrap in `AnthropicHelperError` → exit 3. No new exit codes; no string-match routing.
- **`.claude/rules/monotonic-time-indirection.md`** — If the new transport measures its own duration (round-trip from spawn to `result` message), it must use the existing `_monotonic` alias in `_anthropic.py`.

Three secondary rules:

- **`.claude/rules/subprocess-cwd.md`** — If the new transport accepts `cwd` it's keyword-only. Reuse of `SkillRunner` inherits this.
- **`.claude/rules/non-mutating-scrub.md`** — `env_without_api_key` already non-mutating; any new redaction (if `apiKeySource` / auth labels flow into transcripts) continues the pattern.
- **`.claude/rules/spec-cli-precedence.md`** — If transport selection becomes spec-configurable (CLI > spec > default), follow the three-layer shape. Today the plan defaults to CLI-flag-only precedence; spec field may arrive later.

No `workflow-project.md` found; standard super-plan flow. `CLAUDE.md` mandates: `bd` for tracking, 80% coverage gate, ruff lint, `asyncio_mode = "strict"`.

### Open Questions (to resolve in Refinement)

These are structured as lettered options. Pick one per question, or propose a variant.

---

**Q1. Transport selection policy — the default (no flag passed).**

- **(A)** Prefer API key when present; fall back to CLI subprocess when `ANTHROPIC_API_KEY` is unset. This is the minimum-churn default — users who have a key keep the existing fast path; subscription-only users now work. No behavior change for anyone currently happy.
- **(B)** Prefer CLI subprocess whenever it's available (subscription auth cached in `~/.claude/`), regardless of whether a key is exported. This matches the spirit of `--no-api-key` (which implicitly says "use whatever the CLI has") and avoids silently spending API tokens when a Pro/Max subscription is available.
- **(C)** Make it an explicit CLI flag from day one: `--transport {api,cli,auto}`, default `auto` = behavior of (A). Users who want (B)-style default opt in via `--transport cli`.

**Q2. `--no-api-key` semantics after #86.** This flag exists today on `grade`, `validate`, `capture`, `run` (per #64 DEC-004) and strips `ANTHROPIC_API_KEY` from the **subprocess** env only; it has NO effect on the parent-side SDK call today.

- **(A)** `--no-api-key` keeps its #64 scope exactly. Transport selection for the parent-side LLM call follows Q1's default; the flag does not influence it. If Q1=(A), a user who passes `--no-api-key` with a key still set will use the SDK transport for grading (key still visible to the Python process).
- **(B)** `--no-api-key` now forces the CLI transport for the parent-side LLM call too. The flag becomes "no API key, period — use subscription for both child and parent". Matches a natural reading of the flag name.
- **(C)** A new flag (`--transport cli` from Q1=(C)) is the only way to force CLI for the parent; `--no-api-key` stays scoped to the subprocess.

**Q3. Subprocess invocation shape.**

- **(A)** Reuse `SkillRunner._invoke` by calling it from `_anthropic.py` with a raw prompt (no skill name). Zero new subprocess plumbing — env stripping, timeout watchdog, stream-json parsing, `apiKeySource`, error classification all come for free. Minor concern: `SkillRunner` is shaped around slash-command skills; a raw-prompt path is a mode it doesn't currently advertise, though the underlying Popen doesn't care.
- **(B)** Extract the low-level Popen/stream-json-parse loop from `SkillRunner._invoke` into a shared helper (`_invoke_claude_cli(prompt, *, timeout, env, ...)`) that both `SkillRunner.run` and the new transport use. Keeps `SkillRunner` focused on skills; centralizes the subprocess primitive.
- **(C)** Write a fresh, minimal invoker in `_anthropic.py` (~40 lines) that duplicates just the pieces needed for a raw-prompt call. Faster to ship, no cross-module refactor.

**Q4. Output format.** Orthogonal to Q3 but related.

- **(A)** `--output-format stream-json --verbose` (same as today). Full NDJSON parser; `result` message carries final text + usage + `is_error`; `init` message carries `apiKeySource`. Reuses every defensive pattern already in place.
- **(B)** `--output-format json` (non-streaming). One envelope, simpler to parse — but no existing parser, unclear whether it carries usage/`apiKeySource` in the same shape, and no existing test fixtures.

**Q5. Error categorization + retry parity.** The SDK transport today distinguishes `RateLimitError` / `APIStatusError`-5xx / `AuthenticationError` / `PermissionDeniedError` / `APIConnectionError`. The CLI transport's `_classify_result_message` categorizes as `rate_limit` / `auth` / `api`.

- **(A)** Apply the same retry ladder on CLI-transport failures: `rate_limit` → 3 retries with backoff; `api` category with obvious-5xx text → 1 retry; `auth` → no retry (raise). Parity with the SDK retry taxonomy. Calls feel equivalent regardless of transport.
- **(B)** No retry on the CLI transport — let the `claude` CLI handle its own retries (it already has some internal behavior for rate limits). Simpler, but CLI's retry model is less documented.
- **(C)** Retry only on transport-level failures (subprocess timeout, binary missing, malformed JSON) — NOT on `is_error: true` result messages (let those fail-fast to the caller, who can rerun).

**Q6. Exception class for CLI-transport failures.**

- **(A)** Preserve the `AnthropicHelperError` façade: CLI-transport failures wrap into the same `AnthropicHelperError(message)` the SDK path raises. Callers stay transport-blind. User-facing message still says "API error" (maybe generalize wording to "Claude error").
- **(B)** New `ClaudeCLIError(AnthropicHelperError)` subclass for CLI-transport failures. Callers catching `AnthropicHelperError` keep working; future code can differentiate.
- **(C)** New unrelated class `ClaudeCLIError(RuntimeError)`; callers stay transport-blind via an explicit `(AnthropicHelperError, ClaudeCLIError)` catch at every site (which is a scatter; discouraged).

**Q7. `AnthropicResult` shape for the CLI transport.**

- **(A)** Populate `response_text`, `text_blocks`, `input_tokens`, `output_tokens` identically; set `raw_message = None` (no SDK Message). Document that `raw_message` may be `None` under the CLI transport. Existing callers that use `raw_message` for refusal inspection (check all six — most do NOT) get `None` and must tolerate it.
- **(B)** Add a new `source: Literal["api", "cli"]` field to `AnthropicResult` so sidecars and grading reports can record which transport ran. Useful for audit/trend history and `--variance` reports where one transport's consistency may differ from the other's.
- **(C)** Both (A) and (B): populate tokens identically AND add `source`.

**Q8. Pre-flight guard relaxation — the new pure helper.**

- **(A)** Rename `check_anthropic_auth(cmd_name)` → `check_any_auth_available(cmd_name)`. Passes if **either** `ANTHROPIC_API_KEY` is set OR `claude` binary is on PATH (presence check, not auth verification). Fails (exit 2) only when both avenues are closed. `AnthropicAuthMissingError` stays for the terminal-fail case; its message updates to name both options.
- **(B)** Delete the guard entirely; let `call_anthropic` handle the missing-auth case at call time by routing to the CLI transport and, if that also fails, raising `AnthropicHelperError` (exit 3). `AnthropicAuthMissingError` is deprecated. Trade-off: we lose the pre-spend exit-2 clarity; a subscription-only user whose CLI auth is also expired sees an exit-3 error instead of an exit-2 one.
- **(C)** Keep both: pre-flight `check_any_auth_available` (exit 2 on both-missing) AND a terminal-fail inside `call_anthropic` (exit 3 on all-failed). Belt-and-suspenders.

**Q9. Pytest plugin fixtures (`clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare`).**

- **(A)** Mirror the CLI: fixtures stop requiring `ANTHROPIC_API_KEY`. If the key is missing and the `claude` binary is available, fixtures run under the CLI transport. Test suites under subscription-only CI now work without extra config.
- **(B)** Keep the fixtures stricter than the CLI: require `ANTHROPIC_API_KEY` or an explicit `CLAUDITOR_FIXTURE_ALLOW_CLI=1` env. Rationale: in tests, hidden fallbacks mask CI-config bugs; forcing an explicit opt-in surfaces them.
- **(C)** Fixtures get a constructor kwarg `transport="auto"|"api"|"cli"` that mirrors Q1=(C); default "auto" matches CLI behavior; test authors can pin for determinism.

**Q10. Concurrency cap for the CLI transport.** `blind_compare` uses `asyncio.gather(call_anthropic, call_anthropic)` — two concurrent calls. Subprocess fan-out is heavier than in-process SDK calls.

- **(A)** Preserve the parallel pattern — spawn two subprocesses concurrently. Subscription auth is cached on disk; no conflict between concurrent CLIs.
- **(B)** Serialize CLI-transport calls inside `blind_compare` (document the 2× latency cost on subscription auth).
- **(C)** Cap CLI-transport concurrency at N via an `asyncio.Semaphore`. N=2 matches blind-compare today; N=4 leaves headroom for variance runs. Configurable later.

**Q11. Docs + rule updates.**

- **(A)** Update the matrix in `docs/cli-reference.md` (flip six rows to ✓ under subscription auth). Update the README D2 teaser. Remove the "#86" forward-pointer line.
- **(B)** (A) plus a new `docs/transport-architecture.md` deep-dive (2–3 pages) documenting the two-transport design, selection rules, failure modes, and the retry-parity table. Applies the `.claude/rules/readme-promotion-recipe.md` shape (D3 teaser in cli-reference, full reference in the new doc).
- **(C)** (A) plus an update to `.claude/rules/centralized-sdk-call.md` codifying "the seam now owns transport selection, and future callers inherit both transports for free" as a new anchor section. Optionally update `.claude/rules/stream-json-schema.md` to add a new anchor for "non-skill CLI invocations".

**Q12. Do we add a spec-field for transport?** `EvalSpec.transport: Literal["api", "cli", "auto"] = "auto"` — skill authors can pin per-skill (e.g. research skills always prefer subscription due to token cost).

- **(A)** No — out of scope for this epic. Wait until a real user asks. Current precedence stays CLI-flag-only.
- **(B)** Yes — follow `.claude/rules/spec-cli-precedence.md`'s three-layer pattern (CLI > spec > default). Small add; feels natural alongside `timeout` and `allow_hang_heuristic`.
- **(C)** Yes but with a stricter contract: the spec field can only DOWNGRADE (prefer CLI for a specific skill) or UPGRADE (prefer API for a specific skill), not act as a hard pin — operator CLI override still wins per the precedence rule.

---

## Architecture Review

### Ratings

| Area | Rating | Summary |
|---|---|---|
| Security | **concern** | Two required mitigations: (1) CLI-transport subprocess env must strip `ANTHROPIC_API_KEY` to preserve DEC-001's subscription-first routing; (2) `ClaudeCLIError` message template must commit to sanitization (no stderr/JSON-body leakage) mirroring #83 DEC-015. One verification task: audit `raw_message` callers before merge. |
| Performance | **concern** | DEC-001's default-switch makes all current users pay subprocess spawn overhead (estimated 0.5–1 s per call, unquantified). Rate-limited sessions amplify: 3 retries = 3 spawns. Requires (1) pre-ship benchmark + documentation, (2) migration guidance. No blockers — variance/blind-compare patterns remain correct; FD pressure under budget. |
| Data Model | **concern** | One surprise: `BlindReport.to_json()` has NO `schema_version` today — adding `transport_source` forces inaugural `schema_version: 1` + legacy-tolerant loader that defaults missing field to `"api"`. GradingReport + ExtractionReport bump 1→2 with same default-on-load rule. `EvalSpec.transport` additive, no EvalSpec version bump. |
| API Design / CLI UX | **concern** | Design is sound; copy is not yet committed. Three items to finalize in Refinement: (a) 2×2 auth-state matrix table in the new `docs/transport-architecture.md`; (b) error-message templates for revised `check_any_auth_available` + `ClaudeCLIError` (drafts provided, user to approve); (c) `--transport` flag placement on six commands with explicit note that `compare --blind` is conditional. |
| Observability | **pass** (with gaps to decide) | Token accounting parity verified. Three optional signals to decide in Refinement: (1) stderr line on `auto`→CLI resolution; (2) stderr retry line (CLI + SDK for symmetry); (3) add `duration_seconds` + `api_key_source` to `AnthropicResult` for full parity with `SkillResult`. Audit/trend/variance segmentation is explicitly out-of-scope (follow-up). |
| Testing | **pass** (with action items) | ~80 new test cases across 4–5 files; all existing mocking patterns reusable. Hard requirements: zero real subprocess spawns; branch coverage per retry category; regression smoke test after `_invoke_claude_cli` extraction; pre-merge audit that no `call_anthropic` caller depends on `raw_message is not None`. |

**Overall rating:** three `concern`, three `pass` — no blockers. All concerns resolve via Refinement-log decisions (no redesign needed).

### Findings — Security

- **S1 (required mitigation):** `_invoke_claude_cli` when invoked from `_anthropic.py` MUST pass `env=env_without_api_key(os.environ)` to the subprocess. Without this, a parent process with `ANTHROPIC_API_KEY` exported forks a child `claude -p` that inherits the key and silently switches to API-tier auth — defeating DEC-001's subscription-first routing. `SkillRunner` handles this today only when the user passes `--no-api-key`; for the CLI transport, stripping must be unconditional because the whole point is subscription routing. Recommended shape: `_invoke_claude_cli` accepts an `env=` kwarg and the `_anthropic.py` caller always builds it via `env_without_api_key()`.
- **S2 (required mitigation):** `ClaudeCLIError` messages MUST be sanitized — no raw `result.text` substring, no stderr passthrough, no exception string interpolation. Match #83 DEC-015's pattern: a fixed template per category (rate_limit / auth / api / transport), `__cause__` preserved for debugging. The existing 4 KB truncation on `SkillResult.error` is NOT sufficient — the CLI transport's error message is user-facing and needs category-keyed templates, not a truncated echo.
- **S3 (verification before merge):** audit `quality_grader.py` (and any other `call_anthropic` caller) for `raw_message.XYZ` access patterns. DEC-007 sets `raw_message = None` under CLI. If any caller does `if result.raw_message.stop_reason == "refusal":` without a None-check, CLI-transport callers would `AttributeError`. This is a pre-implementation verification task, not a design decision, but must land before US-006 (see Detailing).
- **S4 (pass, no action):** prompt-as-argv exposure is a pre-existing characteristic of `SkillRunner`; not a new risk. `shutil.which("claude")` TOCTOU and PATH-injection are pre-existing host-trust assumptions, not introduced by #86.

### Findings — Performance

- **P1 (required):** benchmark `claude -p` cold-start + auth verification. Estimated 0.5–1 s per call (Node CLI startup + auth disk read). This is THE load-bearing number — if it's actually 2+ s, the user-visible latency impact of DEC-001's default warrants revisiting. Add a benchmark US to Detailing.
- **P2 (required):** DEC-001's breaking change needs migration guidance. Concrete ask: one sentence in README's `## Authentication and API Keys` teaser, one subsection in the new `docs/transport-architecture.md` ("Existing users: what changed?"), and a clearly-documented `--transport api` escape hatch.
- **P3 (inherent, accepted):** retry amplification. A rate-limited call now costs 3 extra subprocess spawns instead of 3 cheap HTTP retries. This is a direct consequence of DEC-005's parity choice; the alternative (DEC-005 option B: no retry) is worse UX. Accepted.
- **P4 (acceptable):** blind-compare 2-parallel remains correct (subscription auth is disk-cached, no mutex); FD pressure stays well under `ulimit`. Triggers fan-out via `asyncio.gather(*tasks)` is already the canonical shape. No change.

### Findings — Data Model

- **D1 (schema surprise):** `quality_grader.py::BlindReport.to_json()` has NO `schema_version` field today — adding `transport_source` forces its inaugural versioning. Audit-side loader must detect two shapes: pre-#86 legacy (no `schema_version`, no `transport_source` → treat as v1-equivalent with `transport_source="api"`) and post-#86 v1 (both fields present). `_check_schema_version` needs a per-file adaptation.
- **D2 (schema bumps):** `GradingReport.to_json()` and `ExtractionReport.to_json()` bump `schema_version: 1 → 2`. The loader (`audit.py::_check_schema_version`) must accept both versions; missing `transport_source` on a v1 file defaults to `"api"` at load time.
- **D3 (EvalSpec, no bump needed):** `EvalSpec.transport: Literal["api","cli","auto"] = "auto"` is additive. `EvalSpec` is not versioned (it's user-authored config), so no bump. Load-time validation follows `.claude/rules/constant-with-type-info.md`: `field_types = {"transport": str}` + explicit literal-set check + bool-rejection guard.
- **D4 (`AnthropicResult` is in-memory):** confirmed — `AnthropicResult` is never serialized directly. DEC-007's `source` field lives in-memory only; it's copied into sidecar dataclasses at their serialization points.
- **D5 (out-of-scope but flagged):** `clauditor audit`/`trend` do NOT currently segment by transport. Sidecars will carry the field; a future `--by-transport` flag is a follow-up.

### Findings — API Design / CLI UX

- **U1 (copy: auth-state matrix).** A concrete 2×2 matrix (API-key y/n × CLI-auth-cached y/n) with the effective default and the behavior under each of `--transport {api,cli,auto}` + `--no-api-key` must live in `docs/transport-architecture.md`. Full draft in the review output; to be committed in Detailing.
- **U2 (copy: error-message drafts).**
  - Revised `check_any_auth_available`: names both escape hatches (API key OR Claude CLI); preserves the three #83 durable substrings (`"ANTHROPIC_API_KEY"`, `"Claude Pro"` / `"Pro/Max"`, `"console.anthropic.com"`); adds a fourth (`"claude CLI"`). Draft:
    ```
    ERROR: No usable authentication found.
    clauditor {cmd_name} needs either:
      1. ANTHROPIC_API_KEY exported (API key from https://console.anthropic.com/), OR
      2. claude CLI installed and authenticated (Claude Pro/Max subscription)
    Commands that don't need authentication: validate, capture, run, lint, init, badge, audit, trend.
    ```
  - `ClaudeCLIError` templates (one per category): rate_limit, auth, api, transport (binary-missing / timeout / malformed-JSON). All sanitized per S2.
- **U3 (flag placement).** `--transport {api,cli,auto}` lands on six commands (`grade`, `extract`, `propose-eval`, `suggest`, `triggers`, `compare`). Shared argparse `type=` validator (`_transport_choice`) in `cli/__init__.py`. On `compare`, the flag only applies in `--blind` mode — help text makes this explicit. Default `None` at the argparse layer so the spec-field precedence from DEC-012 kicks in cleanly (matches `.claude/rules/spec-cli-precedence.md`).
- **U4 (migration guidance).** Required: README auth-teaser sentence + a `## Migration from pre-#86` section in the new `docs/transport-architecture.md`. Covers the "I had both, now it goes through CLI" case and the `--transport api` escape hatch.
- **U5 (doctor).** Optional: `clauditor doctor` gains two checks — "API-key-available" (env check) + "CLI-transport-available" (shutil.which). Optional probe via `claude -p --help` to surface stale-auth early. Low priority; add as a later US.
- **U6 (env-var naming).** `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is the name — read naturally, binary toggle.
- **U7 (`--dry-run` interaction).** Preserved from #83 DEC-002: `--dry-run` exits before any `call_anthropic` invocation, so transport selection + guard + retries don't run. Documented explicitly.

### Findings — Observability

- **O1 (optional stderr line):** when `auto` resolves to CLI transport (DEC-001 default firing), emit one info line per process-lifetime: `clauditor: using Claude CLI transport (subscription auth)`. When resolution picks API, no line (explicit defaults are not surprising). Makes the DEC-001 behavior change visible without being noisy.
- **O2 (optional stderr retry line):** `clauditor: retrying {cmd_name} after {category} (attempt N/4); waiting Ms`. Both transports emit the same line (symmetry) for consistency. Recommended; improves debugability of intermittent rate-limits.
- **O3 (parity fields):** `AnthropicResult` gains `duration_seconds: float = 0.0` and `api_key_source: str | None = None` so both transports report on equal footing. `duration_seconds` measured via `_monotonic` alias (already in `_anthropic.py`); `api_key_source` populated from the stream-json `init` event under CLI, or left `None` under SDK (the SDK doesn't expose which env var auth'd the call).
- **O4 (out of scope):** audit/trend/variance transport segmentation — no sidecar changes in #86 other than adding the field; aggregators consume the field in a follow-up.

### Findings — Testing

- **T1:** Test surface sizing confirmed — ~80 test cases across `test_anthropic.py` (+25 `TestCallViaClaudeCli` + 8 `TestComputeRetryDecision` + 3–4 `TestCheckAnyAuthAvailable` expansion), `test_runner.py` (+8 `TestInvokeClaudeCli` + regression smoke test), `test_schemas.py` (+6 `TestEvalSpecTransport`), `test_spec.py` (+5 `TestTransportPrecedence`), `test_pytest_plugin.py` (+3 strict-mode tests).
- **T2 (required before merge):** verify `grep -rn "raw_message" src/clauditor/` has no `.attr` access pattern that would AttributeError on `None`. Tracks S3.
- **T3 (required):** zero real subprocess spawns in tests. All CLI-transport tests mock `subprocess.Popen` (reuse `_FakePopen` from `tests/conftest.py`) or patch `shutil.which`.
- **T4 (required):** branch coverage per retry category (rate_limit/auth/api/connection) × per-transport (SDK/CLI) × success/recovery/exhausted. ~24 retry-branch cells; each with ≥1 test.
- **T5 (pass):** no `pytester.runpytest_inprocess` + `--cov` + `mock.patch` hazard — all new tests use direct-mocking patterns that avoid `.claude/rules/pytester-inprocess-coverage-hazard.md`.

### Concerns requiring Refinement-log resolution

All surfaced concerns resolved as DEC-013 through DEC-021 in the Refinement Log.

---

## Refinement Log

### Discovery scoping decisions (session 1)

- **DEC-001 — Default transport is subscription-first (Q1=B).** When both API key and cached subscription auth are available, `call_anthropic` routes through the CLI subprocess. The rationale is token-cost symmetry with `--no-api-key`'s spirit: a Pro/Max user who set `ANTHROPIC_API_KEY` for unrelated tooling (which is most of them) should not be silently billed per-token for clauditor grading when a flat-rate subscription is already paid for.
  *Behavior change:* yes — current users with `ANTHROPIC_API_KEY` exported will start going through the CLI transport by default. This is a breaking change for API-only workflows; callers who need the SDK path must now pass `--transport api` (or equivalent escape hatch TBD in Refinement) or unset their cached subscription.
  *Detection:* "subscription available" = `claude` binary on PATH. Auth verification deferred to the first call (matches Q7-like laziness). Architecture Review must call out whether a probe is warranted.

- **DEC-002 — `--no-api-key` keeps its #64 scope (Q2=A).** The flag continues to mean "strip `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` from the skill subprocess env only" per `plans/super/64-runner-auth-timeout.md` DEC-004. It does NOT force the CLI transport for the parent-side LLM call.
  *Consequence:* under DEC-001's subscription-first default, the flag is effectively idempotent for most grading scenarios (both paths now honor subscription). It remains load-bearing for users who have **only an API key** (no subscription) and still want the child `claude -p` to use a specific auth configuration — a narrow but real case.
  *Open tension:* the interaction between DEC-001 and DEC-002 creates a subtle matrix. Architecture Review (API Design / CLI UX) must document the four auth-state cells cleanly in `docs/cli-reference.md` and/or the new transport doc (see DEC-011).

- **DEC-003 — Extract a shared subprocess invoker (Q3=B).** The Popen + stream-json parse loop at the core of `SkillRunner._invoke` (runner.py:382–605) is extracted into a shared helper both `SkillRunner.run` and the new `call_anthropic` CLI path call. Likely shape: a module-private function (`_invoke_claude_cli(prompt, *, cwd, env, timeout, parse_init=True) -> InvokeResult`) lives in `runner.py` (or a new `_claude_cli.py` sibling module) and returns a lean result struct that both surfaces can project onto their own dataclasses.
  *Rationale:* `.claude/rules/centralized-sdk-call.md`'s invariant (one retry taxonomy, one error classifier, one parsing seam) generalizes to the transport layer. Duplicating the Popen loop into `_anthropic.py` (Q3=C) would produce two drift-prone NDJSON parsers. Reuse-in-place (Q3=A) couples `_anthropic.py` to `SkillRunner`'s skill-name-shaped surface, which is semantically wrong for raw-prompt calls.
  *Scope risk:* touching `SkillRunner._invoke` is a cross-epic refactor. Architecture Review (Testing) must validate that the existing `tests/test_runner.py` coverage exercises the extracted helper at parity.

- **DEC-004 — CLI transport uses `--output-format stream-json --verbose` (Q4=A).** Same format `SkillRunner` uses today. Every defensive pattern in `.claude/rules/stream-json-schema.md` applies verbatim; existing fixtures and the `_classify_result_message` helper are reused.
  *Rationale:* `--output-format json` (non-streaming) has no existing parser, no test fixtures, and uncertain schema. Taking the streaming path reduces new code surface to near-zero on the parsing side. The `result` message at stream end carries `usage` (tokens) and `is_error` + classification fields already.

- **DEC-005 — Full retry parity (Q5=A).** CLI-transport failures map their `_classify_result_message` category to the same retry ladder the SDK transport uses: `rate_limit` → up to 3 retries with `2 ** i × [0.75, 1.25]` jitter; `api` with 5xx-hint substrings → 1 retry; `auth` → no retry (raise); transport-level failures (timeout, binary missing, malformed JSON) → 1 retry then raise.
  *Rationale:* calls feel identical regardless of transport. A skill author observing `--variance` runs or debugging intermittent failures shouldn't need to know which transport ran to reason about retry behavior.
  *Implementation seam:* the retry ladder currently lives inline in `call_anthropic`; it becomes a small pure helper (`_compute_retry_decision(category, retry_index) -> Literal["retry","raise"]`) that both transport branches call. This is a natural extension of `.claude/rules/pure-compute-vs-io-split.md`.

- **DEC-006 — New exception class `ClaudeCLIError(AnthropicHelperError)` (Q6=B).** Subclass so every existing `except AnthropicHelperError:` caller stays transport-blind; future callers that want to branch on transport can `except ClaudeCLIError:`.
  *Message shape:* user-facing message retains the `AnthropicHelperError` "sanitized, no SDK exception text" contract. Category (rate_limit / auth / api / transport) surfaces as an attribute on the exception for telemetry / log routing; not substring-matched.
  *Exit code:* same exit 3 mapping as `AnthropicHelperError` per `.claude/rules/llm-cli-exit-code-taxonomy.md`. No new exit code.

- **DEC-007 — Add `source: Literal["api","cli"]` on `AnthropicResult` (Q7=B).** Populated by each transport branch before return. Sidecars that persist `AnthropicResult`-derived data (GradingReport, ExtractionReport, BlindReport, etc.) gain a `transport_source` field so `clauditor audit` / `clauditor trend` / `--variance` reports can segment by transport.
  *`raw_message` handling (secondary, flagged):* the ticket Q7 did not resolve what `raw_message` becomes under CLI transport. Default: `raw_message = None` under CLI; downstream callers (three today in `quality_grader.py` inspecting refusal flags on the SDK `Message`) must tolerate `None`. Architecture Review should verify no caller treats `raw_message` as non-None unconditionally.
  *Schema version:* sidecars that gain `transport_source` bump their `schema_version` per `.claude/rules/json-schema-version.md`. Loaders that read pre-#86 sidecars (no `transport_source` field) must tolerate its absence and default to `"api"` (the only transport at the time).

- **DEC-008 — Relax the pre-flight guard: `check_any_auth_available(cmd_name)` (Q8=A).** Rename `check_anthropic_auth` → `check_any_auth_available` (semantically: "any usable auth exists"). Passes when either `ANTHROPIC_API_KEY` is set OR the `claude` binary is on PATH. Fails (exit 2) with a revised `AnthropicAuthMissingError` message only when both avenues are closed.
  *Message shape:* updates to name both escape hatches ("export `ANTHROPIC_API_KEY`, OR install/authenticate the `claude` CLI"). The three DEC-012 durable substrings from #83 (`"ANTHROPIC_API_KEY"`, `"Claude Pro"`, `"console.anthropic.com"`) stay; we add a fourth for `"claude CLI"` or similar. Final copy drafted in Architecture Review.
  *Binary detection:* `shutil.which("claude")` is the probe. No auth-verification call at guard time (deferring that cost per DEC-001's note).
  *Test-asserted substrings:* ≥3 durable anchors; exact set TBD in Refinement.
  *Forward-pointer cleanup:* the `"tracked in #86"` sentence from #83 DEC-016 is removed from the error message in this PR; the `docs/cli-reference.md:285–287` paragraph is deleted or rewritten.

- **DEC-009 — Pytest fixtures stay strict (Q9=B).** `clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare` continue to require `ANTHROPIC_API_KEY` (or a new opt-in env `CLAUDITOR_FIXTURE_ALLOW_CLI=1`) at fixture-invocation time. Tests running under subscription-only CI do not automatically fall back to the CLI transport.
  *Rationale:* in test suites, hidden transport fallbacks mask CI-configuration bugs; an explicit opt-in surfaces them. An engineer who wants fixture-level subscription support sets the env var in their test config. Matches #83's DEC-005/DEC-013 rationale.
  *Implementation:* fixtures keep calling a strict helper (either the pre-rename `check_anthropic_auth`, preserved under a new name like `check_api_key_only(cmd_name)`, or a fixture-local guard that checks `ANTHROPIC_API_KEY` OR `CLAUDITOR_FIXTURE_ALLOW_CLI`). The CLI commands call the new `check_any_auth_available` (DEC-008). Two helpers, one decision per caller.
  *`AnthropicAuthMissingError`:* remains the exception type raised by the strict fixture path; terminology is unified.

- **DEC-010 — Parallel subprocess fan-out under `blind_compare` (Q10=A).** The two `call_anthropic` calls inside `blind_compare` continue to run via `asyncio.gather` under CLI transport. Two concurrent `claude -p` subprocesses are acceptable: subscription auth is read from disk; no mutex.
  *Rationale:* serialization would double blind-compare latency on subscription auth; a semaphore adds a knob we don't need today. Reviewable by Architecture Review (Performance) for machine-load concerns.

- **DEC-011 — Docs scope: matrix flip + new `docs/transport-architecture.md` (Q11=B).** `docs/cli-reference.md`'s `## Authentication and API Keys` section updates its matrix (six rows flip ✗ → ✓ under subscription), removes the "#86 forward-pointer" paragraph, and gains a D3-rich teaser pointing at the new `docs/transport-architecture.md`. The new doc follows the `.claude/rules/readme-promotion-recipe.md` promoted-doc opener template and covers: two-transport design, selection rules, auth-state matrix (from DEC-001 + DEC-002 tension), failure modes, retry-parity table, token-accounting parity, `source` field semantics (DEC-007).
  *README:* D2-lean teaser (1-2 sentences + link) updated to say "subscription auth now works for grading too", respecting the existing auth-teaser budget.

- **DEC-012 — Add `EvalSpec.transport: Literal["api","cli","auto"] = "auto"` with three-layer precedence (Q12=B).** Follows `.claude/rules/spec-cli-precedence.md`: CLI `--transport {api,cli,auto}` flag wins > `EvalSpec.transport` wins > default `auto` (DEC-001's subscription-first).
  *Load-time validation:* `EvalSpec.from_dict` validates the field is one of the three literal strings; any other value raises at load (exit 2).
  *Resolution point:* inside `SkillSpec.run`, same shape as `timeout_override` / `allow_hang_heuristic`. Threaded to `call_anthropic` via a new keyword-only `transport_override=` parameter.
  *CLI argparse:* new `--transport` flag added to the six LLM-mediated commands (grade, extract, propose-eval, suggest, triggers, compare). A shared argparse `type=` validator in `cli/__init__.py::_transport_choice`.
  *Compatibility:* a sidecar written by pre-#86 clauditor (no `transport` field on the spec) loads cleanly with default `"auto"`. No schema version bump on `EvalSpec` — new optional field with a default is additive.

### Architecture-review decisions (session 1)

- **DEC-013 — CLI transport strips `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` unconditionally (R1=A).** When `call_anthropic` resolves to the CLI transport and invokes `_invoke_claude_cli`, the `env=` argument is built via `env_without_api_key(os.environ)`. The parent's key is never inherited by the child `claude -p` subprocess.
  *Rationale:* DEC-001's "subscription-first" guarantee only holds if the child CLI can't silently reintroduce API-tier auth. Passing the env through (option B) would let the CLI's own precedence rules pick API auth when a key is set, defeating the whole point of the CLI transport.
  *Implementation:* `_invoke_claude_cli(prompt, *, cwd, env, timeout)` accepts `env` as a kwarg; the `_anthropic.py` caller always builds it via `env_without_api_key()`. `SkillRunner.run` continues to pass `env=None` (inherit) unless `--no-api-key` is in play per DEC-002.

- **DEC-014 — `ClaudeCLIError` uses fixed per-category templates with machine-readable suffix (R2a=B).** Four templates, keyed on `_classify_result_message`'s categories (`rate_limit`, `auth`, `api`, `transport`). Each template ends with ` (transport=cli, category=<cat>)` — parseable by log scrapers and future `clauditor audit --by-category` segmentation without substring-matching exception text.
  *Template shape:*
  ```
  rate_limit → "Anthropic rate limit exceeded (after retries). Try again later. (transport=cli, category=rate_limit)"
  auth       → "Claude CLI authentication failed. Run `claude` interactively to refresh credentials, or export ANTHROPIC_API_KEY and pass --transport api. (transport=cli, category=auth)"
  api        → "Claude CLI returned an error (category=api). See `clauditor doctor` for diagnostics. (transport=cli, category=api)"
  transport  → "Claude CLI subprocess failed (binary missing, timeout, or malformed output). (transport=cli, category=transport)"
  ```
  *`__cause__` preserved* for debugging via Python's exception-chaining. User-facing message does NOT echo the stream-json `result` text (sanitization per #83 DEC-015).
  *Attribute surface:* `ClaudeCLIError` carries `category: str` as a public attribute; no other fields. Exit 3 per `.claude/rules/llm-cli-exit-code-taxonomy.md`.

- **DEC-015 — `check_any_auth_available` error-message copy committed (R2b=approve-with-`{cmd_name}`).**
  ```
  ERROR: No usable authentication found.
  clauditor {cmd_name} needs either:
    1. ANTHROPIC_API_KEY exported (API key from https://console.anthropic.com/), OR
    2. claude CLI installed and authenticated (Claude Pro/Max subscription)
  Commands that don't need authentication: validate, capture, run, lint, init,
  badge, audit, trend.
  ```
  *Test-asserted durable substrings (four anchors):* `"ANTHROPIC_API_KEY"`, `"Claude Pro"`, `"console.anthropic.com"`, `"claude CLI"`. Preserves #83 DEC-012's three + adds the CLI-path anchor.
  *`{cmd_name}` interpolation* via the same helper signature as #83 DEC-006 (`check_any_auth_available(cmd_name: str) -> None`).

- **DEC-016 — Ballpark benchmark inline in `docs/transport-architecture.md` (R3=C).** The new doc includes a `## Spawn-overhead benchmark` section with: single reference machine (`uname -a` reported), ≥10 runs, mean + p95 + stddev, measured over a trivial prompt (`"say hi"`). Separate rows for cold-start (first call) and warm-start (subsequent). Caveat paragraph: "Your mileage will vary; subprocess spawn cost is environment-dependent. Use `--transport api` if latency matters more than subscription-cost parity."
  *Implementation:* small script at `scripts/bench_cli_transport.py` (not shipped as a `clauditor` subcommand); numbers pasted into the doc. Not a CI-gated benchmark; numbers are snapshotted at PR time and refreshed if anyone later notices drift.

- **DEC-017 — Four-layer precedence via new `CLAUDITOR_TRANSPORT` env var (R4=B).** Extends DEC-012's three-layer precedence to four layers: **CLI flag > `CLAUDITOR_TRANSPORT` env var > `EvalSpec.transport` > default (`"auto"`)**. The env var accepts the same three literal values (`api` / `cli` / `auto`); invalid values fail loudly at resolution time.
  *Rationale:* CI pipelines that want to pin transport behavior project-wide don't want to modify every `clauditor <cmd>` invocation. An env var is the idiomatic surface for that.
  *Implementation:* resolution lives in `SkillSpec.run` per `.claude/rules/spec-cli-precedence.md`; a small pure helper `resolve_transport(cli_override, env_override, spec_value) -> Literal["api","cli","auto"]` takes three inputs and returns the winning value. Env consultation happens at `SkillSpec.run` level, not inside `call_anthropic` — keeps `call_anthropic` transport-blind on this axis.
  *Docs:* `docs/transport-architecture.md`'s precedence diagram reflects all four layers; `docs/cli-reference.md`'s auth section mentions the env var with a one-line example.
  *Migration surface:* README teaser gets one sentence. `docs/transport-architecture.md` gets a `## Migration from pre-#86` subsection covering the "I had both, now it's CLI" case and the `--transport api` / `CLAUDITOR_TRANSPORT=api` escape hatches.

- **DEC-018 — `BlindReport` gains inaugural `schema_version: 1` (R5=A).** Its first versioned shape is labelled v1 (not v2 to match siblings — a cosmetic concern that doesn't matter once loaders tolerate legacy). Legacy `BlindReport` sidecars (missing `schema_version`, missing `transport_source`) load as v1-equivalent with `transport_source="api"`.
  *Sibling reports:* `GradingReport` bumps `schema_version: 1 → 2`. `ExtractionReport` bumps `1 → 2`. `audit.py::_check_schema_version` call sites must accept both versions for those two; accept missing (legacy) + v1 for BlindReport.
  *Default-on-load for missing field:* every loader that reads any of the three report types injects `transport_source="api"` before constructing the dataclass when the field is absent. Preserves `.claude/rules/json-schema-version.md`'s "skip + warn on mismatch" contract by treating "v1 with transport_source injected" as the equivalent of "v2 with transport_source already present" for these specific loaders.

- **DEC-019 — Stderr: only `auto`→CLI announcement, retries silent (R6=B).** When `call_anthropic`'s resolution picks CLI transport via the `"auto"` default path (not via explicit `--transport cli` or spec-field `cli`), emit one stderr info line per Python-process lifetime:
  ```
  clauditor: using Claude CLI transport (subscription auth); pass --transport api to opt out
  ```
  *One-shot:* a module-level flag in `_anthropic.py` (`_announced_cli_transport = False`) prevents re-emission across multiple `call_anthropic` calls in the same process (e.g. `--variance 5` = 5 calls, 1 announcement).
  *Retries stay silent:* both SDK and CLI transports inherit the current SDK behavior — retry backoffs are internal, not announced. Preserves symmetry; no new noise on flaky networks.

- **DEC-020 — `AnthropicResult` gains only `duration_seconds` (R7=B).** New field: `duration_seconds: float = 0.0`. Populated by both transport branches via the `_monotonic` alias (module-level in `_anthropic.py` per `.claude/rules/monotonic-time-indirection.md`). Measures from `call_anthropic` entry through final result, EXCLUDING retry sleeps (so a single-attempt 5s call and a successful-after-retry 12s call both report the successful attempt's own duration, not the end-to-end wall clock — follow what `grade_quality` already does).
  *No `api_key_source` field on `AnthropicResult`:* SDK transport has no meaningful analog (it doesn't emit an `apiKeySource` signal). Adding a perpetually-`None` field under SDK is noise. CLI-transport callers who need `apiKeySource` can read it from the underlying `SkillResult` if the `_invoke_claude_cli` helper surfaces it (which it does — via a thin result struct).
  *Downstream propagation:* sidecar classes (`GradingReport`, `ExtractionReport`, `BlindReport`) already have their own `duration_seconds` fields (most do, via `grade_quality`'s own timing). This DEC does NOT require sidecar changes for duration — just `AnthropicResult`-level parity for callers that want per-Anthropic-call timing.

- **DEC-021 — `clauditor doctor` gains two presence checks; no probe (R8=B).** New checks:
  - `api-key-available`: `ok` if `ANTHROPIC_API_KEY` set non-empty; `info` if unset (not a failure).
  - `claude-transport-available`: `ok` if `shutil.which("claude")` returns a path; `info` if not.
  Additional summary line once both checks run: `Effective default transport: <api|cli|none>` based on the four-layer precedence with no CLI flag / spec field in play.
  *No `claude -p --help` probe:* the probe idea was rejected because stale-auth scenarios (the exact case `doctor` is meant to diagnose) can make the probe hang or fail unpredictably, producing worse UX than "binary present, auth state unknown". A future `clauditor doctor --deep` subcommand can revisit.

### Cross-decision interactions (for Architecture Review)

- **DEC-001 + DEC-008 + DEC-012**: the selection chain at call time is `CLI override (DEC-012) → spec field (DEC-012) → default (DEC-001, "auto")`. When resolved to `"auto"`, the runtime picks based on `claude` binary presence (subscription-first). If neither transport is available and `"auto"` falls through, `check_any_auth_available` (DEC-008) raises exit 2.
- **DEC-001 behavior change**: every existing CI pipeline with `ANTHROPIC_API_KEY` exported AND the `claude` CLI installed will switch to CLI transport silently. Architecture Review must surface the migration path (maybe `--transport api` documentation for users who want to pin the old behavior).
- **DEC-003 + DEC-004**: the extracted `_invoke_claude_cli` needs a `parse_init: bool = True` toggle or similar so the CLI-transport caller can decline the `apiKeySource` surfacing (or, more likely, inherit it).
- **DEC-006 + DEC-007**: `ClaudeCLIError` carries a `category` attribute; `AnthropicResult.source` carries `"cli"`. Together they enable downstream callers to distinguish transport-aware behavior cleanly.

---

## Detailed Breakdown

Right-sized stories for Ralph execution. Natural ordering: foundational refactor → verification → transport machinery → config layer → guard relaxation → persisted-schema changes → UX polish → docs → quality gate → patterns.

Every story's acceptance criteria ends with `uv run ruff check src/ tests/` + `uv run pytest --cov=clauditor --cov-report=term-missing` per CLAUDE.md.

---

### US-001 — Extract shared `_invoke_claude_cli` helper from `SkillRunner._invoke`

**Description.** Pure refactor. Pull the Popen + stream-json parse loop out of `SkillRunner._invoke` into a module-private helper that both `SkillRunner.run` and (in US-003) the new CLI-transport path will call. No behavior change.

**Traces to:** DEC-003.

**TDD.** Before extracting, capture the shape invariants in regression tests:
- Smoke test: `SkillRunner.run(skill_name, args)` produces a `SkillResult` with every current field populated (output, exit_code, duration_seconds, error, error_category, input_tokens, output_tokens, api_key_source, raw_messages, stream_events).
- Fixture replay: canonical success stream-json → `SkillResult.output` matches a pinned value.
- Error-category replay: 429-style stream-json `result` → `error_category == "rate_limit"`.

**Files.**
- `src/clauditor/runner.py` — extract `_invoke_claude_cli(prompt: str, *, skill_name: str | None, args: str, cwd: Path | None, env: dict[str, str] | None, timeout: int | None, claude_bin: str, allow_hang_heuristic: bool) -> InvokeResult`. `SkillRunner._invoke` becomes a thin wrapper that builds the prompt (handles `skill_name` slash-command synthesis + `args` concat) and projects `InvokeResult` onto `SkillResult`.
- `tests/test_runner.py` — add regression-smoke + fixture-replay tests; existing tests stay green.

**Acceptance.**
- All existing `tests/test_runner.py` tests pass unmodified.
- New `TestInvokeClaudeCli` class (≥8 tests) exercises the helper directly: success, rate-limit classification, auth classification, 5xx, malformed-NDJSON skip-and-warn, missing-result-message warning, timeout kill, FileNotFoundError on binary-missing.
- `_invoke_claude_cli` exported from `runner.py` as a module-private name (leading underscore) but importable by `_anthropic.py` (US-003).
- Coverage for `runner.py` stays ≥ current baseline.

**Done when.** Ruff clean; pytest green with coverage ≥80%; grep shows `_invoke_claude_cli` called from `runner.py` only (US-003 adds the second caller).

**Depends on.** None.

---

### US-002 — `raw_message` caller audit + None-tolerance

**Description.** Defensive verification task. Audit every `call_anthropic` caller for `raw_message.attr` access patterns that would `AttributeError` on `None`. Fix any found by adding `is not None` guards or by routing through a helper that returns a typed default. This lands before US-003 so the CLI-transport switch never exposes a latent bug.

**Traces to:** DEC-007 (secondary — `raw_message = None` under CLI), Security S3 verification.

**Files.**
- `src/clauditor/quality_grader.py` — check `grade_quality`, `blind_compare`; fix any `result.raw_message.stop_reason` / `.content` / etc. to handle `None`.
- `src/clauditor/grader.py` — same for `extract_and_grade`, `extract_and_report`.
- `src/clauditor/suggest.py`, `propose_eval.py`, `triggers.py` — same.

**Acceptance.**
- `grep -rn "raw_message\." src/clauditor/ | grep -v "raw_message is\|raw_message =\|raw_message:\|raw_message,"` returns only safe patterns (attribute access guarded by `if raw_message is not None`).
- If any refusal-detection code exists on `raw_message`, it degrades gracefully to "no refusal signal available" under `None` (logs a stderr info line if useful).
- Unit test per affected caller: pass `AnthropicResult(raw_message=None, ...)` through the caller's code path; no AttributeError.

**Done when.** `raw_message` audit grep is clean; regression tests green.

**Depends on.** None (runs parallel to US-001).

---

### US-003 — Add CLI transport inside `call_anthropic` + `AnthropicResult` fields + `ClaudeCLIError`

**Description.** Core delivery. Extend `call_anthropic` with a second transport branch that invokes `_invoke_claude_cli` (from US-001). Add `AnthropicResult.source` (DEC-007) and `duration_seconds` (DEC-020). Add `ClaudeCLIError(AnthropicHelperError)` with per-category templates (DEC-014). Env-strip per DEC-013. Retry parity per DEC-005. Announcement per DEC-019. Transport selection resolved in `SkillSpec.run` (US-004), so `call_anthropic` takes an explicit `transport: Literal["api","cli","auto"] = "auto"` kwarg and does its own "auto"-resolution via `shutil.which("claude")`.

**Traces to:** DEC-001, DEC-003, DEC-004, DEC-005, DEC-006, DEC-007, DEC-013, DEC-014, DEC-019, DEC-020.

**TDD.** Write retry-branch tests before implementing retry logic:
- `TestCallViaClaudeCli` class (~25 tests): per-category retry, recovery, exhaustion, transport-level failures (binary-missing, timeout, malformed-JSON).
- `TestComputeRetryDecision` (~8 pure tests): extract `_compute_retry_decision(category, retry_index) -> Literal["retry","raise"]` as a pure helper both branches call.
- `TestAnthropicResultFields`: `source == "api"` under SDK; `source == "cli"` under CLI; `duration_seconds > 0` for both.
- `TestAutoTransportResolution`: `transport="auto"` + `shutil.which` returns path → picks CLI; returns None → picks API.
- `TestStderrAnnouncement`: first `auto`→CLI resolution emits stderr line; second call in same process does not.

**Files.**
- `src/clauditor/_anthropic.py` — add `ClaudeCLIError`; extend `AnthropicResult`; add `call_anthropic(..., transport="auto")` kwarg; extract `_compute_retry_decision`; add CLI branch that calls `_invoke_claude_cli` with `env=env_without_api_key()`; add `_announced_cli_transport` module flag.
- `src/clauditor/runner.py` — import nothing new; `_invoke_claude_cli` already exported from US-001.
- `tests/test_anthropic.py` — new test classes; existing classes stay green.

**Acceptance.**
- `call_anthropic(prompt, model=..., max_tokens=..., transport="auto")` resolves correctly per DEC-001 and emits the announcement per DEC-019.
- `call_anthropic(..., transport="api")` forces SDK; `transport="cli"` forces subprocess.
- `AnthropicResult.source` populated correctly per branch; `duration_seconds` populated in both.
- `ClaudeCLIError` raised with the correct category template + `(transport=cli, category=<cat>)` suffix; `__cause__` preserved; user-facing message does NOT echo stream-json `result` text.
- Retry ladder parity: `rate_limit` up to 3 retries on CLI; `auth` no retry; `api`/5xx one retry; transport-level errors one retry.
- Coverage for `_anthropic.py` ≥ 90%.

**Done when.** Ruff clean; all new test classes green; six existing `call_anthropic` callers still work unchanged (no caller signature edits yet — `transport` kwarg defaults to `"auto"`).

**Depends on.** US-001, US-002.

---

### US-004 — `EvalSpec.transport` field + four-layer precedence + `--transport` CLI flag + `CLAUDITOR_TRANSPORT` env var

**Description.** Wire the user-facing transport controls. Add `EvalSpec.transport` with load-time validation (DEC-012). Add pure resolver `resolve_transport(cli_override, env_override, spec_value)` per DEC-017. Add `--transport {api,cli,auto}` argparse flag to the six LLM-mediated commands. Thread `transport_override` through `SkillSpec.run` → `call_anthropic(..., transport=...)` per `.claude/rules/spec-cli-precedence.md`.

**Traces to:** DEC-012, DEC-017.

**TDD.**
- `TestEvalSpecTransport` in `tests/test_schemas.py`: valid `"api"/"cli"/"auto"` load; invalid string rejects; non-string rejects; bool rejects; missing defaults to `"auto"`.
- `TestResolveTransport` in `tests/test_transport.py` (or extend `test_anthropic.py`): CLI > env > spec > default precedence; invalid env-var values rejected loudly.
- `TestSkillSpecTransportThread` in `tests/test_spec.py`: `SkillSpec.run(..., transport_override="api")` propagates; `eval_spec.transport="cli"` wins when no override; default "auto" when neither set.
- `TestCLITransportFlag`: argparse rejects non-literal values with exit 2 (per `.claude/rules/llm-cli-exit-code-taxonomy.md`).

**Files.**
- `src/clauditor/schemas.py` — `EvalSpec.transport: Literal["api","cli","auto"] = "auto"`; validator block in `from_dict` following the `allow_hang_heuristic`/`timeout` patterns, with `field_types = {"transport": str}` per `.claude/rules/constant-with-type-info.md` + literal-set check + bool guard.
- `src/clauditor/spec.py` — `SkillSpec.run(..., transport_override: str | None = None)`; calls `resolve_transport(transport_override, os.environ.get("CLAUDITOR_TRANSPORT"), eval_spec.transport if eval_spec else None)`; threads to `call_anthropic(..., transport=resolved)`.
- `src/clauditor/_anthropic.py` — add `resolve_transport()` pure helper (new file seam; keep importable from tests and `spec.py`).
- `src/clauditor/cli/__init__.py` — add shared `_transport_choice` argparse `type=` validator.
- `src/clauditor/cli/{grade,extract,propose_eval,suggest,triggers,compare}.py` — each gains `--transport {api,cli,auto}` (default `None`); `compare` help-text notes "only used with `--blind`".
- `src/clauditor/pytest_plugin.py` — `clauditor_spec` factory fixture threads `transport_override=` to `SkillSpec.run`.
- `tests/` — new `test_transport.py` or extension of `test_anthropic.py` + updates to `test_schemas.py`, `test_spec.py`, `test_cli*.py`.

**Acceptance.**
- Four-layer precedence verified end-to-end: CLI > env > spec > default.
- Invalid values at any layer produce exit 2 with clear error.
- Pre-#86 `eval.json` files (no `transport` field) load cleanly with `transport="auto"`.
- Coverage unchanged or improved for `schemas.py`, `spec.py`, `_anthropic.py`.

**Done when.** Ruff clean; all new tests green; six CLI commands accept `--transport` in their help output.

**Depends on.** US-003.

---

### US-005 — Relax pre-flight guard: `check_any_auth_available`

**Description.** Rename `check_anthropic_auth(cmd_name)` → `check_any_auth_available(cmd_name)` per DEC-008. Passes when `ANTHROPIC_API_KEY` is set OR `shutil.which("claude")` returns a path. Raises `AnthropicAuthMissingError` with the DEC-015 message only when both are absent. Preserve the strict variant (`check_api_key_only`) for the three pytest fixtures per DEC-009.

**Traces to:** DEC-008, DEC-009, DEC-015.

**TDD.**
- `TestCheckAnyAuthAvailable` (~12 tests): key present + CLI absent → passes; key absent + CLI present → passes; both present → passes; both absent → raises with all four durable substrings.
- `TestCheckApiKeyOnly` (~4 tests): strict variant for fixtures; raises whenever `ANTHROPIC_API_KEY` is absent regardless of CLI presence.
- `TestPytestFixturesStrictMode` (~3 tests): `clauditor_grader` / `clauditor_triggers` / `clauditor_blind_compare` raise without `ANTHROPIC_API_KEY` unless `CLAUDITOR_FIXTURE_ALLOW_CLI=1` is set.

**Files.**
- `src/clauditor/_anthropic.py` — rename the function; update `_AUTH_MISSING_TEMPLATE`; add `check_api_key_only` strict variant; `shutil.which("claude")` probe.
- `src/clauditor/cli/{grade,extract,propose_eval,suggest,triggers,compare}.py` — replace `check_anthropic_auth(...)` calls with `check_any_auth_available(...)`.
- `src/clauditor/pytest_plugin.py` — three fixture factories use `check_api_key_only` unless `CLAUDITOR_FIXTURE_ALLOW_CLI=1` set in env.
- `tests/test_anthropic.py` — `TestCheckAnthropicAuth` renamed and expanded.
- `tests/test_pytest_plugin.py` — new strict-mode tests.

**Acceptance.**
- All four durable substrings (`"ANTHROPIC_API_KEY"`, `"Claude Pro"`, `"console.anthropic.com"`, `"claude CLI"`) present in the new error message.
- `{cmd_name}` interpolates correctly for all six commands.
- Subscription-only user running `clauditor grade` with `claude` on PATH no longer sees the auth-missing error.
- Pytest fixtures still raise on missing key unless opt-in env set.

**Done when.** Ruff clean; all new/updated tests green; #83's forward-pointer sentence in docs is removed in US-007.

**Depends on.** US-004 (uses the resolve_transport plumbing for the pass-through path).

---

### US-006 — Sidecar schema bumps: `GradingReport`, `ExtractionReport`, `BlindReport`

**Description.** Persist `transport_source` in grading sidecars. Bump `GradingReport.schema_version 1 → 2` and `ExtractionReport.schema_version 1 → 2`. Add inaugural `BlindReport.schema_version = 1` per DEC-018. Update `audit.py::_check_schema_version` call sites to accept both versions and default missing `transport_source` to `"api"` at load.

**Traces to:** DEC-007, DEC-018.

**TDD.**
- Per-report `test_to_json_includes_transport_source` / `test_schema_version_bumped` tests.
- `test_loads_legacy_no_transport_source_defaults_to_api` per report.
- `test_blind_report_inaugural_schema_version` covering the new field.
- `test_audit_accepts_both_v1_and_v2` for GradingReport / ExtractionReport.

**Files.**
- `src/clauditor/quality_grader.py` — `GradingReport.to_json` (bump), `BlindReport.to_json` (add `schema_version`).
- `src/clauditor/grader.py` — `ExtractionReport.to_json` (bump).
- `src/clauditor/audit.py` — `_check_schema_version` accepts `{1, 2}` for the bumped reports; inject `transport_source="api"` default on load when missing.
- `tests/test_quality_grader.py`, `tests/test_grader.py`, `tests/test_audit.py` — new tests.

**Acceptance.**
- Legacy sidecars (no `schema_version` on BlindReport, no `transport_source` on any) load cleanly.
- New sidecars carry `schema_version` as first key + `transport_source` per `.claude/rules/json-schema-version.md`.
- `clauditor audit` continues to produce identical reports for pre-#86 iterations.

**Done when.** Ruff clean; all tests green; schema_version writer/loader symmetry verified.

**Depends on.** US-003 (`AnthropicResult.source` must exist for sidecars to read).

---

### US-007 — Stderr announce on `auto`→CLI + `doctor` presence checks

**Description.** Implement DEC-019's one-shot stderr announcement inside `call_anthropic`'s `auto`-resolution branch. Add DEC-021's two presence checks to `clauditor doctor` plus the summary "Effective default transport" line.

**Traces to:** DEC-019, DEC-021.

**TDD.**
- `TestCallAnthropicAutoAnnouncement` (already in US-003's test plan — move here if not landed earlier).
- `TestDoctorTransportChecks`: both checks present; summary line reflects four-layer precedence correctly; no probe is invoked.

**Files.**
- `src/clauditor/_anthropic.py` — the module-flag + stderr line (if not landed in US-003).
- `src/clauditor/cli/doctor.py` — two new checks + summary line.
- `tests/test_anthropic.py`, `tests/test_doctor.py` — test cases.

**Acceptance.**
- Running `clauditor doctor` shows both new checks with ok/info status.
- Running any LLM command under default config emits the announcement line on stderr exactly once per process.
- No probe invocation (`claude -p --help` or similar) is ever spawned by `doctor`.

**Done when.** Ruff clean; tests green; manual `clauditor doctor` output reviewed.

**Depends on.** US-005 (doctor reads the same resolution path as the new guard).

---

### US-008 — Docs: `docs/transport-architecture.md` + matrix update + README teaser + benchmark

**Description.** Ship all user-facing docs for the transport work. New deep-reference doc with auth-state matrix, migration section, benchmark numbers, and all drafts from Architecture Review. Update `docs/cli-reference.md`'s `## Authentication and API Keys` matrix (flip six rows). Remove #83's forward-pointer paragraph. Update README's D2-lean auth teaser.

**Traces to:** DEC-011, DEC-015, DEC-016, DEC-017 (doc portion).

**Files.**
- `docs/transport-architecture.md` — new file; follows `.claude/rules/readme-promotion-recipe.md` promoted-doc opener template; sections:
  - `## Why two transports`
  - `## Auth-state matrix` (2×2 table with behavior per `--transport` / `--no-api-key` / `CLAUDITOR_TRANSPORT`)
  - `## Precedence (four layers)` — CLI > env > spec > default
  - `## Error categories` (with `ClaudeCLIError` templates)
  - `## Spawn-overhead benchmark` (DEC-016)
  - `## Migration from pre-#86`
  - `## Known limitations` (no cache-tokens under CLI, `raw_message=None` under CLI, `api_key_source` only under CLI)
- `docs/cli-reference.md` — flip six rows in the matrix; remove #83's "tracked in #86" paragraph; add D3-rich teaser linking to `transport-architecture.md`; add one-line `--transport` entry to each of the six commands' flag lists.
- `README.md` — update the `## Authentication and API Keys` D2-lean teaser per DEC-017 (one sentence + link).
- `scripts/bench_cli_transport.py` — small benchmarking script (not shipped as a subcommand); numbers pasted into transport-architecture.md.

**Acceptance.**
- Matrix in `docs/cli-reference.md` flipped correctly.
- All four DEC-015 durable substrings visible in the error-message example in the docs.
- `docs/transport-architecture.md` opener template matches `.claude/rules/readme-promotion-recipe.md` (title + purpose paragraph + breadcrumb blockquote).
- README H2 anchor preserved (no rename).

**Done when.** Markdown renders cleanly; all internal links resolve; benchmark numbers present with machine/sample-size caveat.

**Depends on.** US-003, US-004, US-005, US-006, US-007 (docs describe behavior already in place).

---

### US-009 — Quality Gate (4× code review + CodeRabbit + full validation)

**Description.** Run code reviewer four times across the full changeset, fixing all real issues found in each pass. Run CodeRabbit review. After all fixes, validate with `uv run ruff check src/ tests/` + `uv run pytest --cov=clauditor --cov-report=term-missing`. Coverage gate: ≥80%.

**Traces to:** all prior USs.

**Files.** As needed for fixes; no new features.

**Acceptance.**
- Four code-reviewer passes complete with zero remaining real bugs (false positives documented).
- CodeRabbit comments addressed or false-positives flagged.
- Coverage ≥ 80% project-wide.
- Ruff clean.
- `grep -rn "check_anthropic_auth" src/` returns zero hits (fully renamed per DEC-008).
- `grep -rn "call_anthropic" src/clauditor/ | grep -v "transport=" | wc -l` shows all callers routed through `SkillSpec.run` OR intentionally use `transport="auto"`.

**Done when.** All gates green.

**Depends on.** US-001 through US-008.

---

### US-010 — Patterns & Memory (always last, priority 99)

**Description.** Codify the new patterns learned during #86. Update rules, docs, and memory as appropriate.

**Traces to:** meta.

**Files.**
- `.claude/rules/centralized-sdk-call.md` — new anchor section: "Multi-transport routing (CLI + SDK)" documenting that the centralized seam now owns transport selection, and that future callers inherit both transports for free.
- `.claude/rules/spec-cli-precedence.md` — extend to note the four-layer variant (CLI > env var > spec > default) with the `CLAUDITOR_TRANSPORT` pattern as the canonical implementation.
- `.claude/rules/json-schema-version.md` — add a note on "inaugural schema_version on a previously-unversioned sidecar" using BlindReport (US-006) as the anchor.
- Possibly a new rule: `.claude/rules/transport-selection-seam.md` codifying the full pattern (resolve at `SkillSpec.run`, thread to `call_anthropic`, env-strip at the subprocess seam, stderr-announce once per process on auto-resolution).
- Memory: flag any user-preference / project-preference that emerged during the session.

**Acceptance.**
- Each rule update has a `## Canonical implementation` block pointing at #86 file:line anchors.
- No rule contradictions with existing content.

**Done when.** Rule files cleanly committed; plan's `## Meta` section updated with final stats (sessions, total decisions, PR URL).

**Depends on.** US-009.

---

### Dependency graph

```
US-001 ──┐
         ├─→ US-003 ─→ US-004 ─→ US-005 ─→ US-007 ┐
US-002 ──┘           │                            │
                     └─→ US-006 ─────────────────→ US-008 ─→ US-009 ─→ US-010
```

US-001 and US-002 run in parallel (different files). US-003 blocks on both. US-004 depends on US-003's `transport` kwarg. US-005 depends on US-004's precedence machinery. US-006 depends on US-003 (`AnthropicResult.source`). US-007 depends on US-005 (doctor reads the same resolver). US-008 depends on everything user-facing landing first. US-009 gates on US-001–US-008. US-010 always last.

---

## Beads Manifest

- **Epic:** `clauditor-9a4` — `#86: claude CLI subprocess transport (epic)`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/86-claude-cli-transport`
- **Branch:** `feature/86-claude-cli-transport`
- **Plan PR:** https://github.com/wjduenow/clauditor/pull/89

### Task graph

| Bead ID | Title | Depends on |
|---|---|---|
| `clauditor-9a4.1` | US-001 — Extract shared `_invoke_claude_cli` | — |
| `clauditor-9a4.2` | US-002 — `raw_message` caller audit + None-tolerance | — |
| `clauditor-9a4.3` | US-003 — CLI transport in `call_anthropic` + fields + `ClaudeCLIError` | 9a4.1, 9a4.2 |
| `clauditor-9a4.4` | US-004 — `EvalSpec.transport` + four-layer precedence | 9a4.3 |
| `clauditor-9a4.5` | US-005 — Relax guard → `check_any_auth_available` | 9a4.4 |
| `clauditor-9a4.6` | US-006 — Sidecar schema bumps | 9a4.3 |
| `clauditor-9a4.7` | US-007 — Stderr announce + `doctor` checks | 9a4.5 |
| `clauditor-9a4.8` | US-008 — Docs + matrix + benchmark | 9a4.3, 9a4.4, 9a4.5, 9a4.6, 9a4.7 |
| `clauditor-9a4.9` | US-009 — Quality Gate (4× reviewer + CodeRabbit + validation) | 9a4.1–9a4.8 |
| `clauditor-9a4.10` | US-010 — Patterns & Memory | 9a4.9 |

Initial `bd ready`: `clauditor-9a4.1` + `clauditor-9a4.2` (both have no upstream deps and can run in parallel).
