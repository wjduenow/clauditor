# Super Plan: #83 — Subscription-only users blocked from L3 grading / proposer / suggester (direct-SDK auth gap)

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/83
- **Branch:** `feature/83-subscription-auth-gap`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/83-subscription-auth-gap`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/87
- **Follow-up issue:** https://github.com/wjduenow/clauditor/issues/86
- **Sessions:** 1
- **Last session:** 2026-04-21

---

## Discovery

### Ticket Summary

**What:** Five LLM-mediated CLI commands (`grade`, `propose-eval`, `suggest`, `triggers`, `extract`) crash with an opaque `TypeError: Could not resolve authentication method` traceback from deep inside the Anthropic Python SDK when a user has no `ANTHROPIC_API_KEY` exported but is otherwise on a Pro/Max subscription (credentials cached at `~/.claude/`).

**Why:** Two independent auth paths exist:

1. **Child subprocess** — `runner.py::SkillRunner._invoke` spawns `claude -p`. The `claude` CLI natively supports both API-key and subscription auth. The `--no-api-key` flag added in #70 strips `ANTHROPIC_API_KEY` from the subprocess env so the CLI falls back to subscription auth.
2. **Parent direct-SDK call** — `_anthropic.py::call_anthropic` constructs `anthropic.AsyncAnthropic()`. The Anthropic Python SDK is API-only; it does not read subscription credentials from `~/.claude/`. Requires `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`).

The `--no-api-key` flag covered only case 1. Case 2 is unreachable — the SDK has no subscription mode to switch to. Subscription-only users are therefore gated out of every LLM-mediated clauditor workflow, which is the majority of the product's value prop beyond L1 smoke tests.

**Who benefits:** Engineers iterating on skills under an active Pro/Max subscription — the single user segment clauditor most targets, who chose their plan specifically to avoid per-token billing.

**Done when:**
1. LLM-mediated commands under subscription-only auth fail with a clear, actionable exit-2 message (not opaque traceback).
2. Error tells user which auth mode is missing, why subscription doesn't cover it, and concrete next step.
3. `validate`, `capture`, `run`, `lint`, `init`, `badge`, `audit`, `trend` remain usable under subscription-only auth (regression guard).
4. `README.md` and `docs/cli-reference.md` document which commands require API key.

**Recommended approach (per ticket):** Option A (pre-flight auth check + actionable error) plus Option C (docs, piggybacks at no extra cost). Defer Option B (route direct-SDK calls through `claude -p` subprocess) to a follow-up epic.

### Codebase Findings

**The centralized SDK seam:**
- `src/clauditor/_anthropic.py:217` — `client = AsyncAnthropic()` with no explicit `api_key=` kwarg. SDK falls back to env lookup (`ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`).
- Failure path: `client.messages.create()` call at line ~225 raises deep SDK `TypeError` before any HTTP round-trip. `call_anthropic` does not currently catch this — it propagates to the CLI as an uncaught traceback.
- The existing `AnthropicHelperError` wraps `AuthenticationError` (HTTP 401/403) but NOT the pre-HTTP `TypeError`, which is the failure mode described in the ticket.

**The `--no-api-key` CLI flag (today):**
- Exposed on: `cli/grade.py:114`, `cli/validate.py`, `cli/capture.py:96`, `cli/run.py:87`.
- Backed by `src/clauditor/runner.py::env_without_api_key()` (line 33-46) which strips `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the subprocess env.
- Scope: child-`claude -p`-only. Has NO effect on the parent direct-SDK path.

**LLM-mediated commands (will get the guard):**
- `cli/grade.py::cmd_grade` — calls `grader.py` (L2 extraction) + `quality_grader.py::grade_quality` (L3) + optionally `blind_compare`.
- `cli/propose_eval.py::cmd_propose_eval` — calls `propose_eval.py`.
- `cli/suggest.py::cmd_suggest` — calls `suggest.py`.
- `cli/triggers.py::cmd_triggers` — calls `triggers.py`.
- `cli/extract.py::cmd_extract` — calls `grader.py` (L2 extraction only).

**Safe commands (regression-guard target):**
- `cli/audit.py`, `cli/compare.py`, `cli/doctor.py`, `cli/init.py`, `cli/setup.py`, `cli/trend.py` — verified zero imports of `_anthropic` / `call_anthropic`.
- `cli/validate.py`, `cli/capture.py`, `cli/run.py` — run the skill subprocess only; no parent-side SDK call.
- `cli/lint.py`, `cli/badge.py` — static, sidecar-reading.

**Existing pre-flight patterns to mirror:**
- `src/clauditor/cli/__init__.py:15-40` — `_unit_float`, `_positive_int`. These are `argparse` `type=` validators that raise `argparse.ArgumentTypeError` → argparse auto-routes to exit 2. Shape hint for the new helper but likely not the right seam (auth is an env check, not an arg check).
- CLI commands that do early-return exit-2 for pre-call input errors (see `plans/super/52-propose-eval.md` DEC-006): linear chain of early-return branches, ordered external→internal.

**`--dry-run` flag presence:**
- `grade` — `--dry-run` (line 49): prints prompt, exit 0 without API call.
- `propose-eval` — `--dry-run` (line 75): prints prompt, exit 0 without API call.
- `extract` — `--dry-run` (line 30): prints prompt, exit 0 without API call.
- `triggers` — `--dry-run` (line 24): prints sample prompts, exit 0 without API call.
- `suggest` — **no `--dry-run`** today.
- All four existing dry-run gates check `args.dry_run` BEFORE any `call_anthropic` invocation. Auth pre-flight must run AFTER the dry-run check so `--dry-run` does not require a key.

**Docs touch points (AC#4):**
- `docs/cli-reference.md:112, 115, 119, 122, 125` — already explains `--no-api-key` for the subprocess env path.
- `README.md:156` — one-line mention of `--no-api-key` as a Pro/Max option.
- Neither doc today tells the user "subscription auth does NOT cover L3 grading / propose-eval / suggest / triggers / extract". This is the documentation gap.

### Rule Compliance Gate (from Convention Checker)

Rules that apply to this work:

- **`.claude/rules/llm-cli-exit-code-taxonomy.md`** — Pre-call env-validation failure routes to **exit 2**, not exit 3. Guard MUST fire BEFORE any `call_anthropic` invocation. Exit 3 is reserved for actual `AnthropicHelperError` from a real API round-trip.
- **`.claude/rules/centralized-sdk-call.md`** — The new pre-flight guard sits UPSTREAM of `call_anthropic` (it prevents the helper from ever being called with a missing key). Does not replace or compete with the centralized helper — it is purely a pre-spend gate. No new SDK-call surface is introduced.
- **`.claude/rules/pure-compute-vs-io-split.md`** — The env-check decision should be a pure function (inspects `os.environ`, returns bool or raises a structured exception) with a thin CLI wrapper that emits stderr and maps to exit 2. Pattern anchor: `src/clauditor/setup.py::plan_setup` returning a `SetupAction` enum, with the CLI dispatching on the enum.
- **`.claude/rules/readme-promotion-recipe.md`** — README update (AC#4) must stay D2-lean: one sentence + small code block + link to `docs/cli-reference.md`. Full reference (matrix of which commands need the key, error-message example, troubleshooting) lands in `docs/cli-reference.md`. Do NOT rename any existing H2 — anchor preservation.
- **`.claude/rules/pre-llm-contract-hard-validate.md`** — Spirit applies: fail loudly at the earliest safe moment, with a specific actionable error, not a silent fallback.
- **`.claude/rules/bundled-skill-docs-sync.md`** — If the bundled `/clauditor` SKILL.md's workflow section is touched, the README teaser and `docs/skill-usage.md` must be updated in the same PR. This ticket likely does NOT touch SKILL.md but noting the rule in case the workflow mentions `grade` preconditions.

No `workflow-project.md` found. Standard super-plan flow applies.

### Open Questions (to resolve in Refinement)

**Q1. Which env vars count as "auth present"?**
- (A) Only `ANTHROPIC_API_KEY`.
- (B) `ANTHROPIC_API_KEY` OR `ANTHROPIC_AUTH_TOKEN`. *(matches SDK's own fallback chain)*
- (C) Something else.

**Q2. How does `--dry-run` interact with the guard?**
- (A) `--dry-run` exempts the guard (no API call happens anyway).
- (B) Guard fires even with `--dry-run` (consistent UX: "the command requires a key").

**Q3. How does `--no-api-key` interact with the guard on `grade`?**
- (A) `--no-api-key` has no effect on the parent-SDK guard — user still needs a key for L3 grading. If they pass `--no-api-key`, they're saying "subprocess uses subscription; parent uses API key". Guard still fires if `ANTHROPIC_API_KEY` is unset.
- (B) `--no-api-key` is interpreted as "do not hit the Anthropic API at all in this process". Run L1 only; skip the L3/extraction parts. (Significant behavior change.)
- (C) `--no-api-key` is an error when combined with commands that require parent-SDK auth — guard fires with a specific "this command needs a key in the parent process; --no-api-key only affects the subprocess" message.

**Q4. Where does the guard live?**
- (A) Pure helper `check_anthropic_auth() -> None | AuthMissing` in `src/clauditor/_anthropic.py` (sibling to `call_anthropic`), invoked from each CLI command function AFTER the `--dry-run` check, BEFORE `await call_anthropic(...)` or its orchestrator.
- (B) Helper in `src/clauditor/cli/__init__.py::_require_api_key_for_direct_sdk(cmd_name)` that prints and returns exit 2.
- (C) Decorator on every LLM-mediated CLI command.

**Q5. Should the guard apply to the pytest plugin fixtures (`clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare`) too?**
- (A) Yes — fixture factory raises `pytest.skip` with the same message when no key is set.
- (B) No — pytest users are expected to know about their env; let the SDK error propagate inside tests.
- (C) Yes, but via `pytest.skip(reason=...)` so CI under subscription-only env cleanly skips instead of erroring.

**Q6. Error message copy — draft and commit to a verbatim form in the plan?**
The ticket proposes roughly:
```
ERROR: clauditor grade needs ANTHROPIC_API_KEY for the L3 grading call.
Subscription auth covers the skill subprocess only — the Python Anthropic
SDK is API-only. Options:
  - Export ANTHROPIC_API_KEY (pay-per-token for grading; --no-api-key can
    still force subscription for the skill subprocess).
  - Skip grading flags to run L1-only via 'clauditor validate' — no API key required.
```

- (A) Commit to that copy verbatim (tests assert substring).
- (B) Commit to a shorter one-paragraph version, log test-asserted substrings as decisions.
- (C) Parameterize per-command (`grade` mentions grading; `propose-eval` mentions eval-spec proposal; etc.).

**Q7. Documentation shape (AC#4)?**
- (A) Single new `## Authentication and API Keys` section in `docs/cli-reference.md` with a feature-impact matrix (mirror of the ticket's matrix), plus a D2-lean teaser in README.
- (B) Per-command note in each command's section of `docs/cli-reference.md`, plus a standalone troubleshooting section.
- (C) Both: matrix section + per-command sentence.

**Q8. Wrap the SDK `TypeError` too?**
Even with the pre-flight guard, `call_anthropic` itself currently lets the SDK's `TypeError` propagate (if someone ever calls it directly from a new code path without going through the guard). Should this PR also tighten `_anthropic.call_anthropic` to catch `TypeError` and wrap it as an `AnthropicHelperError("auth config missing")`?
- (A) Yes — defense in depth. Every future caller inherits the safety.
- (B) No — the guard is sufficient; scope this PR tight.

**Q9. Does `extract` belong in the guarded set?**
The ticket's feature-impact matrix lists `extract` as failing (last row), but the explicit "Affected call sites" list at the end mentions grader.py (L2). `extract` maps to L2 extraction — confirm it hits `call_anthropic`.
- (A) Yes, include `extract` in the five guarded commands (confirmed by codebase scout: `cli/extract.py::cmd_extract` → `grader.py` → `call_anthropic`).
- (B) Skip `extract`.

---

## Architecture Review

### Ratings

| Area | Rating | Summary |
|---|---|---|
| Security | **pass** | One mitigation: the `TypeError` wrap in `call_anthropic` must not echo SDK exception text into the user-facing message (defense-in-depth against hypothetical partial-auth leakage in future SDK versions). |
| API Design / CLI UX | **concern** | Three items to nail down in Refinement: helper signature + exception class; final error-message copy (+ test-asserted substrings); pytest fixture exception type. |
| Data Model | **n/a** | No schema changes. |
| Performance | **n/a** | Trivial `os.environ.get` on five command paths. |
| Observability | **pass** | Stderr format matches existing `ERROR:` prefix convention. No new log files, flags, env vars, or telemetry surface. Exit 2 already documented in `README.md:104` and `docs/cli-reference.md:94, 113`. |
| Testing | **pass** | `monkeypatch.setenv` / `delenv` is the canonical isolation mechanism. No pytester+coverage+mock.patch hazard. The helper is trivially reachable for 100% branch coverage. |

### Findings

**Security (PASS with one required mitigation):**
- No existing code logs or stringifies the API key value. Only `apiKeySource` labels (e.g. "ANTHROPIC_API_KEY", "claude.ai", "none") appear in output — see `runner.py:~200`.
- The pre-flight check reads presence only (`os.environ.get(...)` truthy); no timing side-channel.
- `--no-api-key` does NOT bypass the guard (by design); it strips env for subprocess only.
- **Required mitigation:** the new `except TypeError` branch inside `call_anthropic` must raise with a fixed, sanitized message (`"Anthropic SDK client initialization failed — verify ANTHROPIC_API_KEY is set."`) and `from exc` — NOT include `str(exc)` or `exc.args` in the user-facing message. Defense in depth even though the SDK's current `TypeError` message does not contain tokens.

**API Design / CLI UX (CONCERN — items for Refinement):**
- **Helper signature (DEC pending):** recommend new exception class `AnthropicAuthMissingError(Exception)` distinct from `AnthropicHelperError`. Rationale: `AnthropicHelperError` is already routed to exit 3 in the taxonomy (API-failure category); reusing it for a pre-call env check conflates exit-2 and exit-3 categories. A new class keeps the exit-code routing structural rather than a string-match hack.
- **Integration point per command (file:line — to validate in implementation):**
  - `cli/grade.py` — after `--dry-run` early-return (~line 234), before `allocate_iteration()` (~240).
  - `cli/propose_eval.py` — after `--dry-run` early-return (~line 298), before `propose_eval(...)` call (~300).
  - `cli/suggest.py` — no `--dry-run`; lands at the earliest safe point after arg parsing and BEFORE `propose_edits(...)` (~line 184). Consider adding a preceding zero-signal early-exit still works — guard goes after that.
  - `cli/triggers.py` — after `--dry-run` early-return (~line 81), before `test_triggers(...)` (~85).
  - `cli/extract.py` — after `--dry-run` early-return (~line 74), before `extract_and_grade(...)` (~108).
- **Error message (DEC pending — draft below):** user picked Q6=B (shorter one-paragraph + test-asserted substrings). Draft copy:
  ```
  ERROR: ANTHROPIC_API_KEY is not set.
  Subscription auth (~/.claude/) covers the skill subprocess only — the
  Python Anthropic SDK used for grading/proposing/suggesting is API-only.
  Export ANTHROPIC_API_KEY to proceed (you can still pass --no-api-key to
  force subscription auth for the skill subprocess itself).
  Commands that do not require an API key: validate, capture, run, lint,
  init, badge, audit, trend.
  ```
  Test-asserted substrings: `"ANTHROPIC_API_KEY is not set"`,
  `"Python Anthropic SDK"`, `"API-only"`,
  `"Export ANTHROPIC_API_KEY"`. (Substrings, not byte-identical message — leaves
  room for typo fixes without churning tests.)
- **Pytest fixture exception (DEC pending):** user picked Q5=A (raise, NOT skip — C was the skip option). Recommend `RuntimeError` (or the same `AnthropicAuthMissingError`) at fixture-invocation time so pytest reports it cleanly as a setup failure, not a skipped test. Rationale: a CI run under subscription-only auth that silently SKIPs would hide a test-config regression; a hard error surfaces it. (If user wants skip later, flip to `pytest.skip` — that's a one-line change.)

**Observability (PASS):**
- Existing `ERROR:` prefix convention (22 uses) — new message uses it. `suggest.py` has 3 legacy `Error:` outliers; not our concern here.
- No new log files, flags, or env vars. No telemetry impact (`metrics.py` local-only; no pre-flight rows hit `history.jsonl`).
- Exit 2 is already documented in `README.md:104` ("exit-code contract: 2 = input error") and `docs/cli-reference.md:94, 113`. New AC#4 docs will reinforce this.

**Testing (PASS):**
- New test file `tests/test_cli_auth_guard.py` for CLI integration tests (or extend an existing file — TBD in Refinement). Env manipulation always via `monkeypatch` fixture.
- Helper unit tests in `tests/test_anthropic.py::TestCheckAnthropicAuth` — five cases: key present; absent; empty string; whitespace-only; `ANTHROPIC_AUTH_TOKEN` present but `ANTHROPIC_API_KEY` absent (still raises per Q1=A).
- Regression test (parametrized over 8 commands) verifies `ANTHROPIC_API_KEY` substring does not appear in stderr when the key is unset — cheap insurance for AC#3.
- TypeError-wrap test in `tests/test_anthropic.py::TestCallAnthropicTypeError` — patch `AsyncAnthropic.messages.create` to raise `TypeError`, assert `AnthropicHelperError` with a sanitized substring; confirm `__cause__` preserves the original.
- No `pytester.runpytest_inprocess` + `mock.patch` hazard — tests use `main([...])` or `__wrapped__` direct fixture calls.

### Open concerns to resolve in Refinement

1. **Helper signature + exception class** (API/UX #1). Recommend new `AnthropicAuthMissingError`.
2. **Final error-message copy + test-asserted substrings** (API/UX #3). Draft above; user to approve or edit.
3. **Pytest fixture exception type** (API/UX #5). Recommend `RuntimeError` (Q5=A reading).
4. **Message framing: name the command or not?** Draft uses generic copy; per-command context (e.g. "clauditor grade needs...") is Q6=C territory. User picked Q6=B so generic is correct, but confirm the command name does NOT appear.
5. **Doc anchor name** for `docs/cli-reference.md` section. Recommend `## Authentication and API Keys` → GitHub anchor `#authentication-and-api-keys`.

---

## Refinement Log

### Discovery scoping decisions

- **DEC-001** — **Auth env-var set: `ANTHROPIC_API_KEY` only.** Not `ANTHROPIC_AUTH_TOKEN`.
  *Rationale:* Keep the check tightly scoped to the documented clauditor auth surface. If a future user has only `ANTHROPIC_AUTH_TOKEN` set, the SDK would still authenticate — but the guard would fire. This is a conservative choice the user explicitly picked (Q1=A); can widen later if it bites.
- **DEC-002** — **`--dry-run` exempts the guard.** Guard runs AFTER `--dry-run` early-return in all four commands that have one (grade, propose-eval, extract, triggers). `suggest` has no `--dry-run` today — guard lands at the earliest safe point.
  *Rationale:* `--dry-run` prints a prompt and exits 0 without any API call; no reason to require a key. Matches user Q2=A.
- **DEC-003** — **`--no-api-key` does NOT bypass the guard on `grade`.** If `ANTHROPIC_API_KEY` is unset, guard fires regardless. `--no-api-key` continues to mean "strip the key from the subprocess env only" (its existing #70 semantics — DEC-004 of `plans/super/64-runner-auth-timeout.md`).
  *Rationale:* The parent-side Anthropic SDK call is unavoidable for L3 grading; `--no-api-key` in isolation does not make L3 runnable. Preserves #70's contract. Matches user Q3=A.
- **DEC-004** — **Guard is a pure helper in `_anthropic.py`.** Not a decorator, not an argparse validator. Each CLI command calls it directly after the dry-run gate, before any `call_anthropic` / orchestrator invocation.
  *Rationale:* Matches `.claude/rules/pure-compute-vs-io-split.md`. The helper raises a domain exception; CLI wrappers map to exit 2 + stderr. Matches user Q4=A.
- **DEC-005** — **Pytest fixtures raise (not skip) on missing key.** `clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare` each raise the same `AnthropicAuthMissingError` the CLI uses.
  *Rationale:* A CI run under subscription-only auth that silently SKIPs hides a test-config regression. A hard error surfaces it. Matches user Q5=A (A was "raises a clear error"; C was the skip option). If we need opt-in skip later, that's a one-line change.
- **DEC-006** — **Error-message style: single template, per-command name substituted, test-asserted substrings.** Helper signature is `check_anthropic_auth(cmd_name: str)`; the message template names the command (e.g. `clauditor grade`) for immediately actionable UX. Not five tailored copies — one template, one substitution point.
  *Rationale:* User asked for the command name so they know exactly which invocation triggered the guard. Still satisfies Q6=B's "one shorter paragraph" intent — there is still one paragraph, just with the command name interpolated. Helper stays pure: takes `cmd_name` as an argument, no environment lookup of `sys.argv`, no `os.path.basename` magic. Caller passes the subcommand string (e.g. `"grade"`, `"propose-eval"`) and the helper formats `f"clauditor {cmd_name}"` into the template.
- **DEC-007** — **Docs shape: matrix + per-command one-liner + D2-lean README teaser.**
  *Rationale:* Matches user Q7=C. Full feature-impact matrix lives in a new `## Authentication and API Keys` section of `docs/cli-reference.md`; each LLM-mediated command's existing section gets a one-liner pointer; README gets a 1-2 sentence teaser linking to the new section. README teaser respects `.claude/rules/readme-promotion-recipe.md` D2-lean shape.
- **DEC-008** — **`call_anthropic` wraps `TypeError` as defense-in-depth.** Even though the pre-flight guard eliminates the primary path, the wrap catches any future caller that bypasses the guard.
  *Rationale:* Matches user Q8=A. Small, additive, no perf cost.
- **DEC-009** — **`extract` is in the guarded set.** Confirmed by scout: `cli/extract.py::cmd_extract` → `grader.py` → `call_anthropic`. Five guarded commands total: grade, propose-eval, suggest, triggers, extract.
  *Rationale:* Matches user Q9=A.

### Architecture-review decisions

- **DEC-010** — **New exception class `AnthropicAuthMissingError(Exception)` in `src/clauditor/_anthropic.py`.** Distinct from `AnthropicHelperError` (which is routed to exit 3 in the llm-cli-exit-code-taxonomy for real API failures).
  *Rationale:* Pre-call env check is an input-validation error (exit 2), not an API failure (exit 3). A new exception class makes the exit-code routing structural (CLI catches `AnthropicAuthMissingError` → exit 2; catches `AnthropicHelperError` → exit 3), not a string-match or attribute-check hack.
- **DEC-011** — **Error message copy (names the command + forward-points to #86).**

  Template (with `{cmd_name}` substituted at runtime — e.g. `grade`, `propose-eval`, `suggest`, `triggers`, `extract`):

  ```
  ERROR: ANTHROPIC_API_KEY is not set.
  clauditor {cmd_name} calls the Anthropic API directly and needs an API
  key — a Claude Pro/Max subscription alone does not grant API access.
  Get a key at https://console.anthropic.com/, then export
  ANTHROPIC_API_KEY=... and re-run. Subscription support via claude -p
  is tracked in #86.
  Commands that don't need a key: validate, capture, run, lint, init,
  badge, audit, trend.
  ```

  *Rationale:* Hybrid Option A + forward-pointer to Option B. User asked whether subscription auth could ever work for `grade`; the honest answer is "not in this PR, but the subprocess-transport path (Option B of #83) is doable and now tracked in #86." The added sentence closes the "pay or go home forever" tone without expanding this PR's scope. Keeps the three load-bearing substrings stable; the `#86` reference is NOT a test-asserted substring (so a renumber/close does not churn tests).

- **DEC-012** — **Test-asserted substrings (3 durable anchors).**
  1. `"ANTHROPIC_API_KEY"` — env-var name must appear.
  2. `"Claude Pro"` — must name the subscription product.
  3. `"console.anthropic.com"` — must point at the concrete next step.

  *Rationale:* Three anchors survive stylistic copy edits (typo fixes, reword a clause) while pinning the load-bearing message content. Dropping to 3 from the first draft's 4 reduces test churn.
- **DEC-013** — **Pytest fixture exception = `AnthropicAuthMissingError`.** Same class as the CLI; one source of truth for the message.
  *Rationale:* User picked Q3=A in this round. Keeps the fixture's error message byte-identical to the CLI message, so users learn one error shape.
- **DEC-014** — **Doc anchor = `## Authentication and API Keys`** in `docs/cli-reference.md` (GitHub anchor `#authentication-and-api-keys`). README teaser links to this anchor.
  *Rationale:* User Q4=A. Title is descriptive and GitHub-slug-stable.
- **DEC-015** — **`TypeError` wrap in `call_anthropic` uses a fixed sanitized message.** No SDK exception text in the user-facing message. Original exception preserved via `__cause__` for debugging.
  *Rationale:* User Q5=A. Security mitigation: even though the current SDK's `TypeError` message does not contain tokens, defense-in-depth against future SDK versions that might include partial auth state in diagnostics. Concrete message: `"Anthropic SDK client initialization failed — verify ANTHROPIC_API_KEY is set."`
- **DEC-016** — **Option B deferred to follow-up #86.** The hybrid route: ship Option A now (this PR) and file a tracked follow-up for the `claude -p` subprocess transport that would restore subscription support for the LLM-mediated commands.
  *Rationale:* User-picked option 3 of the three-way fork (keep scope / expand scope / hybrid). The error message from DEC-011 references #86 so subscription-only users know the gap is being worked on; clauditor docs (per DEC-007) will link the same issue. When #86 lands, this PR's guard relaxes from "fail" to "fall back to subprocess"; the `AnthropicAuthMissingError` class stays for the no-auth-anywhere case. Issue created: https://github.com/wjduenow/clauditor/issues/86.
- **DEC-017** — **Guarded set expanded from five to six commands after QG pass 2 discovered `compare --blind` was missing.** The original plan (DEC-009) enumerated five commands by tracing `call_anthropic` usages across `grader.py`, `quality_grader.py::grade_quality`, `quality_grader.py::blind_compare`, `suggest.py`, `propose_eval.py`, `triggers.py`. Reviewer in QG pass 2 noticed that `cli/compare.py::_run_blind_compare` also routes through `blind_compare_from_spec` → `call_anthropic`, so a subscription-only user running `clauditor compare before.txt after.txt --spec s.md --blind` would have hit the US-002 defense-in-depth `AnthropicHelperError` (exit 3, less actionable) instead of the DEC-012 exit-2 guard. Added `check_anthropic_auth("compare --blind")` in `_run_blind_compare` after `validate_blind_compare_spec` (same exit-2 surface). Matrix in `docs/cli-reference.md`, README teaser, and tests updated accordingly.
  *Rationale:* The guard invariant is "every LLM-mediated CLI path routes through `call_anthropic` in the Python process → every such path needs the pre-flight guard at exit 2". Five vs six is an incidental count; the invariant is what matters. Memorializing the expansion here so future readers understand why the code guards six commands but earlier DECs reference five. Companion rule: the QG process (four reviewer passes) exists to catch exactly this class of enumeration-drift bug — two independent passes both verified the guard placement is correct; the scope gap only surfaced when Pass 2 asked "are we sure this list is exhaustive?" which the initial plan didn't. No new user-facing decision here — just a scope correction.

---

## Detailed Breakdown

Natural ordering (backend-Python):
primitives (exception + helper) → seam (TypeError wrap) → CLI integration → fixture integration → regression tests → docs → Quality Gate → Patterns & Memory.

Validation command (appears in every story's Done-when):
`uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing`
(80% coverage gate enforced, per CLAUDE.md).

---

### US-001 — Add `AnthropicAuthMissingError` + `check_anthropic_auth` pure helper

**Description:** Introduce a new exception class `AnthropicAuthMissingError(Exception)` and a pure helper `check_anthropic_auth(cmd_name: str) -> None` in `src/clauditor/_anthropic.py`. Helper reads `os.environ` for `ANTHROPIC_API_KEY` only; raises `AnthropicAuthMissingError(message)` when absent, empty, or whitespace-only. Message is a single template string interpolating `cmd_name` per DEC-011.

**Traces to:** DEC-001, DEC-004, DEC-010, DEC-011, DEC-012.

**Rules compliance:**
- `.claude/rules/pure-compute-vs-io-split.md` — helper is pure (no I/O, no stderr; caller maps to exit 2 + stderr).
- `.claude/rules/centralized-sdk-call.md` — lives in the same module as `call_anthropic`, shares the auth-concern seam.
- `.claude/rules/llm-cli-exit-code-taxonomy.md` — new exception class enables structural exit-2 routing (distinct from `AnthropicHelperError` exit-3).

**Acceptance criteria:**
- `AnthropicAuthMissingError` subclasses `Exception` (not `AnthropicHelperError` — keeps exit-code routing structural).
- `check_anthropic_auth(cmd_name: str) -> None` — raises on missing/empty/whitespace-only `ANTHROPIC_API_KEY`; returns `None` on any non-empty value.
- Message uses `{cmd_name}` substitution into the DEC-011 template. Exact string match (all three DEC-012 substrings) on at least one positive and one negative test.
- `ANTHROPIC_AUTH_TOKEN` set but `ANTHROPIC_API_KEY` missing → still raises (DEC-001: only `ANTHROPIC_API_KEY` counts).
- Validation command passes; coverage for new code is 100%.

**Done when:** Five unit tests in `tests/test_anthropic.py::TestCheckAnthropicAuth` pass (present / absent / empty / whitespace / `AUTH_TOKEN`-only-still-raises); helper raises with command name interpolated; `uv run pytest` green.

**Files:**
- `src/clauditor/_anthropic.py` — add class + helper (~15 lines).
- `tests/test_anthropic.py` — new test class (~40 lines).

**TDD:** Yes.
1. Write failing test: `test_key_present_returns_none` (env set → no exception).
2. Write failing test: `test_key_absent_raises` (env unset → raises; assert all three DEC-012 substrings present).
3. Write failing test: `test_key_empty_string_raises`.
4. Write failing test: `test_key_whitespace_only_raises`.
5. Write failing test: `test_auth_token_only_still_raises`.
6. Implement class + helper until green.
7. Run validation command; confirm green + coverage held.

**Depends on:** none.

---

### US-002 — Wrap SDK `TypeError` in `call_anthropic` as defense-in-depth

**Description:** Add `except TypeError as exc: raise AnthropicHelperError(fixed_message) from exc` inside `call_anthropic` in `src/clauditor/_anthropic.py`, covering the `AsyncAnthropic()` construction site and the `messages.create()` call. Sanitized message per DEC-015 — no SDK exception text surfaced.

**Traces to:** DEC-008, DEC-015.

**Rules compliance:**
- `.claude/rules/centralized-sdk-call.md` — keeps the one retry/error taxonomy consistent. `AnthropicHelperError` (not `AnthropicAuthMissingError`) because when the pre-flight guard is bypassed, the `TypeError` path behaves like any other SDK-side failure — exit 3 territory.

**Acceptance criteria:**
- Sanitized message: `"Anthropic SDK client initialization failed — verify ANTHROPIC_API_KEY is set."` (exact; no interpolation).
- `__cause__` preserves the original `TypeError` for debugging.
- No `str(exc)`, `exc.args`, `repr(exc)` interpolation in the user-facing message (security mitigation).
- Validation command passes; no new ruff warnings.

**Done when:** Unit test in `tests/test_anthropic.py::TestCallAnthropicTypeError` passes: mock `AsyncAnthropic.messages.create` to raise `TypeError`, assert `AnthropicHelperError` raised with the fixed substring, assert `__cause__` is the original `TypeError`, assert SDK's exception message does NOT appear in the user-facing message.

**Files:**
- `src/clauditor/_anthropic.py` — add `except TypeError` branch to the existing retry/categorization ladder (~8 lines).
- `tests/test_anthropic.py` — one new test (~30 lines).

**TDD:** Yes.
1. Write failing test asserting `TypeError` → `AnthropicHelperError` with sanitized message + preserved `__cause__`.
2. Implement the `except TypeError` branch.
3. Validation command.

**Depends on:** US-001 (conceptually parallel, but implementing after US-001 avoids conflicting edits to the same file).

---

### US-003 — Wire auth guard into five LLM-mediated CLI commands

**Description:** Add `check_anthropic_auth(cmd_name)` call + `AnthropicAuthMissingError → exit 2 + stderr` handler to each of the five LLM-mediated CLI command functions. Guard lands AFTER `--dry-run` early-return (DEC-002) and BEFORE any `call_anthropic` / orchestrator invocation. `--no-api-key` does NOT bypass the guard (DEC-003).

**Traces to:** DEC-002, DEC-003, DEC-004, DEC-009, DEC-011.

**Rules compliance:**
- `.claude/rules/llm-cli-exit-code-taxonomy.md` — exit 2 for missing env (pre-call input error), distinct from exit 3 (API failure).
- `.claude/rules/pre-llm-contract-hard-validate.md` — fail early, fail loud, actionable message.

**Acceptance criteria:**
- Five CLI commands guarded: `grade`, `propose-eval`, `suggest`, `triggers`, `extract`.
- Each command's `_cmd_*_impl` (or equivalent) invokes the guard at the designated seam (file:line ranges in Architecture Review section — to reconfirm at implementation).
- `--dry-run` variants (grade, propose-eval, triggers, extract) exit 0 without the guard firing (env-unset test cases).
- `suggest` (no `--dry-run`) fires guard at the earliest safe point after arg parsing / zero-signal early-exit.
- On guard failure: `print(str(exc), file=sys.stderr); return 2`.
- Validation command passes.

**Done when:** Integration tests in `tests/test_cli_auth_guard.py` (new file) cover all five commands:
- Missing-key test per command → exit 2 + all three DEC-012 substrings in stderr + command name ("clauditor grade", etc.) in message.
- Dry-run test per dry-run-having command → exit 0 with key unset, no guard message in stderr.
- Suggest-with-key-present test → guard passes, propose_edits reached (mocked).

**Files:**
- `src/clauditor/cli/grade.py` — insert guard after `--dry-run` check.
- `src/clauditor/cli/propose_eval.py` — same.
- `src/clauditor/cli/suggest.py` — insert guard after zero-signal early-exit, before propose_edits.
- `src/clauditor/cli/triggers.py` — insert guard after `--dry-run` check.
- `src/clauditor/cli/extract.py` — insert guard after `--dry-run` check.
- `tests/test_cli_auth_guard.py` — new integration test file.

**TDD:** Partially — write the missing-key + dry-run tests first for all five commands, then implement the guard. The dry-run-exempts-guard case is the one most likely to break if the guard lands in the wrong spot, so writing those first catches ordering bugs.

**Depends on:** US-001.

---

### US-004 — Wire auth guard into pytest fixtures

**Description:** `clauditor_grader`, `clauditor_triggers`, `clauditor_blind_compare` fixtures in `src/clauditor/pytest_plugin.py` each call `check_anthropic_auth(fixture_name)` at the factory-invocation seam (where the test actually uses the fixture to run a grading call). On missing key, propagate `AnthropicAuthMissingError` — pytest renders it as a fixture-setup failure. Per DEC-005 (raise, not skip) and DEC-013 (reuse `AnthropicAuthMissingError`).

**Traces to:** DEC-005, DEC-013.

**Rules compliance:**
- No applicable `.claude/rules/` constraint beyond general test-fixture discipline.
- `.claude/rules/pytester-inprocess-coverage-hazard.md` — tests for the fixtures use `__wrapped__` direct calls, NOT `pytester.runpytest_inprocess + mock.patch`.

**Acceptance criteria:**
- `clauditor_grader` factory raises `AnthropicAuthMissingError` on invocation with `ANTHROPIC_API_KEY` unset.
- Same for `clauditor_triggers`, `clauditor_blind_compare`.
- Error message interpolates a sensible `cmd_name` (e.g. `"grader"`, `"triggers"`, `"blind_compare"`) for debuggability.
- Existing fixture tests (that set the key or mock the SDK call) remain green.

**Done when:** `tests/test_pytest_plugin.py::TestClauditorFixturesAuthGuard` (new class) — one test per fixture, `monkeypatch.delenv("ANTHROPIC_API_KEY")`, call factory via `__wrapped__`, assert `AnthropicAuthMissingError` raised.

**Files:**
- `src/clauditor/pytest_plugin.py` — add guard call to three fixture factories (~3 lines each).
- `tests/test_pytest_plugin.py` — new test class (~60 lines).

**TDD:** Yes. Write failing tests for each fixture, then add the guard calls.

**Depends on:** US-001.

---

### US-005 — Regression tests: eight commands remain usable without `ANTHROPIC_API_KEY`

**Description:** Parametrized test that confirms `validate`, `capture`, `run`, `lint`, `init`, `badge`, `audit`, `trend` do NOT fail with an `ANTHROPIC_API_KEY` message when the env var is unset. Guards against accidentally adding the guard to a wrong command in US-003.

**Traces to:** AC#3 of ticket body; regression guard for DEC-003 and DEC-009.

**Rules compliance:** Standard test discipline; `monkeypatch.delenv` for env isolation.

**Acceptance criteria:**
- One parametrized test `test_<cmd>_not_guarded` for each of eight commands.
- Each test invokes the command via `main([cmd_name, ...])` with minimal fixture input and `ANTHROPIC_API_KEY` unset.
- Assertion: `"ANTHROPIC_API_KEY"` does NOT appear in stderr. (Exit code may vary — some commands may exit non-zero for other input reasons; that's fine, just not for the auth gap.)
- Test file co-located with other regression tests (likely `tests/test_cli_auth_guard.py` from US-003, extended).

**Done when:** Parametrized test passes for all eight commands; zero false positives.

**Files:**
- `tests/test_cli_auth_guard.py` — extend with a `TestRegressionNoApiKey` class (~60 lines).

**TDD:** No — mechanical assertion, write it after US-003 to leverage its existing CLI-invocation helpers.

**Depends on:** US-003 (for CLI-invocation helper patterns).

---

### US-006 — Docs: `docs/cli-reference.md` authentication section

**Description:** Add a new `## Authentication and API Keys` section to `docs/cli-reference.md`. Content: feature-impact matrix (identical to the ticket's), explanation of the two auth modes, the `#86` follow-up pointer for subscription support, and per-command one-liners in each of the five LLM-mediated command sections (`## grade`, `## propose-eval`, `## suggest`, `## triggers`, `## extract`) linking to `#authentication-and-api-keys`.

**Traces to:** DEC-007, DEC-014, DEC-016. AC#4 of ticket.

**Rules compliance:**
- `.claude/rules/readme-promotion-recipe.md` — anchor-preservation on any existing H2 we touch; new H2 text byte-stable.

**Acceptance criteria:**
- New section `## Authentication and API Keys` with:
  - Feature-impact matrix (13-row table from the ticket).
  - 2-3 sentence explanation (API-key vs subscription modes, why SDK is API-only).
  - Exit-2 behavior + pointer to the new error-message shape.
  - "Subscription auth via `claude -p` is tracked in #86" pointer.
- Each of five LLM command sections (`## grade`, `## propose-eval`, `## suggest`, `## triggers`, `## extract`) gains one sentence: "Requires `ANTHROPIC_API_KEY`. See [Authentication and API Keys](#authentication-and-api-keys)."
- GitHub slug resolves (`#authentication-and-api-keys`) — no manual anchor override needed.
- No existing H2 renamed; no anchor broken.

**Done when:** `docs/cli-reference.md` renders on GitHub with the new section; anchor link works; per-command notes visible.

**Files:**
- `docs/cli-reference.md` — new section (~40 lines) + 5 one-liner inserts (~5 lines).

**TDD:** No — doc content.

**Depends on:** US-001, US-003, DEC-016 (#86 reference).

---

### US-007 — Docs: README D2-lean teaser

**Description:** Add a 1-2 sentence teaser to `README.md` mentioning the auth requirement for LLM-mediated commands, linking to `docs/cli-reference.md#authentication-and-api-keys`. Per `.claude/rules/readme-promotion-recipe.md` D2-lean shape (one sentence + link).

**Traces to:** DEC-007, DEC-014. AC#4 of ticket.

**Rules compliance:**
- `.claude/rules/readme-promotion-recipe.md` — D2-lean teaser (≤6 lines). No new code block if the existing auth section already has one; preserve README length budget.

**Acceptance criteria:**
- Teaser of ~2-3 lines in the appropriate spot (likely near or after the existing `--no-api-key` mention at README.md:156).
- Contains link to `docs/cli-reference.md#authentication-and-api-keys`.
- Respects ~165-line budget (check current line count; note delta).

**Done when:** README teaser renders; link resolves to the new section.

**Files:**
- `README.md` — ~3-line addition.

**TDD:** No — doc content.

**Depends on:** US-006 (needs the anchor to exist).

---

### US-008 — Quality Gate

**Description:** Run code reviewer agent four times across the full changeset, fixing every real bug found on each pass. Also run CodeRabbit (or equivalent) if available. Validation command must pass after all fixes; 80% coverage gate held.

**Traces to:** implicit project discipline.

**Acceptance criteria:**
- Four distinct `code-reviewer` agent invocations, each fixing every real bug surfaced (dismiss false positives with written rationale).
- CodeRabbit review addressed if the draft PR has one.
- Final validation: `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing` — all green, coverage ≥80%.
- No new warnings vs baseline.

**Done when:** Four review passes clean; full validation command green; coverage gate held.

**Files:** varies (wherever real bugs surface).

**TDD:** No — review/fix cycle.

**Depends on:** US-001, US-002, US-003, US-004, US-005, US-006, US-007 (all implementation stories).

---

### US-009 — Patterns & Memory

**Description:** Update `.claude/rules/` with any patterns learned during this implementation (e.g. a rule for "new exception class for a new exit-code category" if it emerges as generalizable). Update memory (`MEMORY.md` entries) only if the ticket surfaces a load-bearing user preference or collaboration note.

**Traces to:** implicit project discipline.

**Acceptance criteria:**
- If a new rule is warranted, it follows the canonical-implementation shape of existing rules (trigger / pattern / why / canonical / when-applies / when-not-applies).
- If no new rule is warranted, the story closes with a brief justification ("no new generalizable pattern emerged").
- Candidate rules to evaluate during this story:
  - *"Pre-call environment validation: pure helper in the same module as the SDK seam, distinct exception class, exit-2 routing."* — likely worth codifying as a sibling to `llm-cli-exit-code-taxonomy.md`.
  - *"Forward-pointer in error messages: name the tracking issue, do NOT test-assert the issue number."* — maybe, if the pattern recurs.

**Done when:** New rule(s) landed or justification recorded; MEMORY updates (if any) made.

**Files:**
- `.claude/rules/<new-rule>.md` (if applicable).
- `/home/wesd/.claude/projects/-home-wesd-Projects-clauditor/memory/*.md` (if applicable).

**TDD:** No — documentation/pattern capture.

**Depends on:** US-008.

---

### Summary table

| # | Story | Depends on | Files touched | TDD |
|---|---|---|---|---|
| US-001 | Exception class + pure helper | — | `_anthropic.py`, `test_anthropic.py` | Yes |
| US-002 | TypeError wrap | US-001 | `_anthropic.py`, `test_anthropic.py` | Yes |
| US-003 | CLI wiring (5 commands) | US-001 | 5× `cli/*.py`, `test_cli_auth_guard.py` | Partial |
| US-004 | Pytest fixture wiring | US-001 | `pytest_plugin.py`, `test_pytest_plugin.py` | Yes |
| US-005 | Regression tests (8 cmds) | US-003 | `test_cli_auth_guard.py` | No |
| US-006 | docs/cli-reference.md | US-001, US-003 | `docs/cli-reference.md` | No |
| US-007 | README teaser | US-006 | `README.md` | No |
| US-008 | Quality Gate | US-001..US-007 | varies | No |
| US-009 | Patterns & Memory | US-008 | `.claude/rules/`, memory | No |

---

## Beads Manifest

- **Epic:** `clauditor-2df` — `#83: Subscription-auth gap — pre-flight guard for LLM-mediated commands`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/83-subscription-auth-gap`

| Bead | Story | Deps |
|---|---|---|
| `clauditor-2df.1` | US-001 — Exception class + pure helper | — |
| `clauditor-2df.2` | US-002 — TypeError wrap | `.1` |
| `clauditor-2df.3` | US-003 — CLI wiring (5 commands) | `.1` |
| `clauditor-2df.4` | US-004 — Pytest fixtures | `.1` |
| `clauditor-2df.5` | US-005 — Regression tests (8 cmds) | `.3` |
| `clauditor-2df.6` | US-006 — `docs/cli-reference.md` | `.1`, `.3` |
| `clauditor-2df.7` | US-007 — README teaser | `.6` |
| `clauditor-2df.8` | US-008 — Quality Gate | `.1`..`.7` |
| `clauditor-2df.9` | US-009 — Patterns & Memory (P4) | `.8` |

Ready work at time of devolve: `bd ready` should surface `clauditor-2df.1` first (no deps); `.2`/`.3`/`.4` unblock once `.1` closes.

---

## Session Notes

### Session 1 — 2026-04-21 → 2026-04-22 (Discovery → Publish)
- Fetched ticket #83. Short name: `subscription-auth-gap`.
- Created worktree `/home/wesd/dev/worktrees/clauditor/feature/83-subscription-auth-gap` off `origin/dev`.
- Parallel research: Ticket Analyst, Codebase Scout, Convention Checker.
- Phase 1 scoping: nine questions, nine answers (Q1..Q9 all A except Q6=B, Q7=C — DEC-001..DEC-009).
- Phase 2 architecture review: Security PASS (with TypeError-sanitize mitigation), API/UX CONCERN (3 items), Observability PASS, Testing PASS.
- Phase 3 refinement: five follow-up questions; user refined the error message twice — first to strip the "subprocess" jargon, then to name the offending command. Asked whether subscription auth could work for `grade`; chose hybrid option 3 (Option A now + file #86 for Option B). DEC-010..DEC-016 locked.
- Filed GitHub issue #86 for the `claude -p` subprocess-transport follow-up.
- Phase 4 detailing: 9 stories (US-001..US-009) with dependencies, TDD annotations, and rules-compliance notes.
- Phase 5 publish: committed plan, pushed branch, opened draft PR #87 targeting `dev`.
- Phase 7 devolve: created epic `clauditor-2df` + 9 child beads with dependency edges; phase set to `devolved`.
- **Next:** start Ralph against `bd ready`; `clauditor-2df.1` (US-001) is the entry point.
