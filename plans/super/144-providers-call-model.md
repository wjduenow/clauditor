# 144 — Multi-provider: extract `_providers/` package with `call_model` dispatcher

## Meta
- **Ticket:** [#144](https://github.com/wjduenow/clauditor/issues/144)
- **Parent epic:** [#143](https://github.com/wjduenow/clauditor/issues/143) (Multi-provider / multi-harness, Epic A)
- **Branch:** `feature/144-providers-call-model`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/144-providers-call-model`
- **Phase:** published
- **Sessions:** 1 (2026-04-28)
- **Type:** pure refactor (no behavior change)

---

## Discovery

### What the ticket asks for

Pure refactor that extracts `src/clauditor/_anthropic.py` (975 lines, single anchor for every Anthropic SDK call per `.claude/rules/centralized-sdk-call.md`) into a `_providers/` package with a thin dispatcher. Today everything routes through `call_anthropic`; after this, everything routes through `call_model(provider="anthropic", ...)` — but the runtime path through the Anthropic SDK is unchanged.

This is **Epic A, ticket 1 of 4** in the multi-provider plan (#143). The goal is to make room for `_openai.py` (#145) without scattering provider-specific logic through six grader call sites.

### Scope (verbatim from ticket)

- Create `src/clauditor/_providers/` package
- Add `call_model(prompt, *, provider, model, transport, max_tokens, subject) → ModelResult` as the new public surface
- Rename `AnthropicResult` → `ModelResult`; add `provider: Literal["anthropic", "openai"]` field; existing `source: Literal["api", "cli"]` stays
- Move the existing `call_anthropic` body into `_providers/_anthropic.py` as the anthropic backend
- Rewire all six grader call sites (`grader.py`, `quality_grader.py` ×2, `triggers.py`, `propose_eval.py`, `suggest.py`) to use `call_model(provider="anthropic", ...)`
- Keep `call_anthropic` as a deprecated alias for one release for back-compat in tests
- Update `.claude/rules/centralized-sdk-call.md` to reference the new dispatcher

### Out of scope (per ticket)

- OpenAI backend (#145)
- `EvalSpec.grading_provider` field (#146)
- `grading.json` / `extraction.json` `provider` field bump (#147)

### Codebase findings

**Module under refactor:** `src/clauditor/_anthropic.py` (975 lines). Contents:

| Symbol | Kind | Public? | Notes |
|---|---|---|---|
| `AnthropicResult` | dataclass | yes | Renamed → `ModelResult`; gains `provider` field |
| `AnthropicHelperError` | exception | yes | Still re-exported; many CLI commands `except` it |
| `AnthropicAuthMissingError` | exception | yes | Pre-call auth guard, exit-2 routing |
| `ClaudeCLIError` | exception | yes | Subclass of `AnthropicHelperError` (DEC-006 of #86) |
| `call_anthropic` | async fn | yes | Body moves into provider, name kept as deprecated alias |
| `check_any_auth_available` | fn | yes | Used by every LLM-mediated CLI command + 3 pytest fixtures |
| `check_api_key_only` | fn | yes | Used by some CLI commands |
| `announce_implicit_no_api_key` | fn | yes | Called by `cli/grade.py` for #95 |
| `resolve_transport` | fn | yes | Public per `.claude/rules/spec-cli-precedence.md` |
| `_call_via_sdk`, `_call_via_claude_cli` | async fn | private | Provider-specific SDK + CLI transport branches |
| `_compute_backoff`, `_compute_retry_decision`, `_extract_result`, `_body_excerpt`, `_classify_invoke_result` | fn | private | Provider-specific helpers |
| `_sleep`, `_rand_uniform`, `_rng` | aliases | private but tests patch them | Per `.claude/rules/monotonic-time-indirection.md` |
| `_announced_cli_transport`, `_CLI_AUTO_ANNOUNCEMENT` | flag + const | private but tests patch | Implicit-coupling announcement family |
| `_announced_implicit_no_api_key`, `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT` | flag + const | private but tests patch | Same family |
| `_VALID_TRANSPORT_VALUES` | const | private | |
| `_AUTH_MISSING_TEMPLATE`, `_AUTH_MISSING_TEMPLATE_KEY_ONLY` | const | private | |
| `_RATE_LIMIT_MAX_RETRIES`, `_SERVER_MAX_RETRIES`, `_CONN_MAX_RETRIES` | const | private | |
| `_BODY_EXCERPT_CHARS`, `_CLI_TRANSPORT_TIMEOUT` | const | private | |
| `_CLI_ERROR_TEMPLATES` | const | private | |
| `_api_key_is_set`, `_claude_cli_is_available` | fn | private | Auth-detection helpers |

**Six grader call sites (the rewire targets):**

1. `src/clauditor/grader.py:798` — `extract_and_grade` / `extract_and_report` (one shared `await call_anthropic(...)` site).
2. `src/clauditor/quality_grader.py:590` — `grade_quality`.
3. `src/clauditor/quality_grader.py:598` — `blind_compare` (the `asyncio.gather(call_anthropic, call_anthropic)` pair, 1 import, 2 await calls).
4. `src/clauditor/triggers.py:189-194` — trigger precision judge.
5. `src/clauditor/propose_eval.py:842-857` — propose-eval orchestrator (per-attempt).
6. `src/clauditor/suggest.py:995-998` — suggest edit proposer.

**CLI commands that catch `AnthropicHelperError` / `AnthropicAuthMissingError`:** all six LLM-mediated CLI commands (`grade`, `extract`, `propose-eval`, `suggest`, `triggers`, `compare --blind`) — they import these classes by name from `clauditor._anthropic`. Confirmed via grep.

**Pytest fixtures that import:** `pytest_plugin.py` has 3 places using `check_any_auth_available` from `clauditor._anthropic`.

**Tests that patch private symbols:**

- `tests/test_anthropic.py` — patches `_sleep`, `_rand_uniform`, `_announced_cli_transport`, `_announced_implicit_no_api_key`, `_extract_result`, `_compute_backoff`, etc.
- `tests/test_anthropic.py::TestStderrAnnouncement`, `TestAnnounceImplicitNoApiKey` — autouse fixtures that `monkeypatch.setattr(clauditor._anthropic, "_announced_*", False)` to reset between tests.
- Various other test modules patch `clauditor._anthropic.call_anthropic` as the SDK seam.

The ticket explicitly says _"All existing tests pass without modification"_ — so the deprecated alias module must keep these patch targets live.

### Convention/rules survey

Read every `.claude/rules/*.md`. The ones that govern this refactor:

| Rule | What it says | Application here |
|---|---|---|
| `centralized-sdk-call.md` | One seam (`call_anthropic`) owns retry, error categorization, token accounting, transport routing. | The seam moves to `call_model`; rule must be refreshed in-place per `rule-refresh-vs-delete.md` (the pattern is still load-bearing; the file location shifts). |
| `rule-refresh-vs-delete.md` | When a refactor changes the *context* a rule describes but not the *pattern*, refresh the framing in-place. | This rule applies directly to `centralized-sdk-call.md`. |
| `monotonic-time-indirection.md` | Module-level `_sleep` / `_rand_uniform` aliases for asyncio safety. | The aliases live with the SDK transport (in `_providers/_anthropic.py`); the back-compat shim must re-export them so tests can keep patching `clauditor._anthropic._sleep`. |
| `spec-cli-precedence.md` | `resolve_transport`, transport announcement family, implicit coupling helpers. | All public functions stay public; locations move from `_anthropic.py` → `_providers/__init__.py` (re-export). The shim preserves `clauditor._anthropic.<name>` imports. |
| `precall-env-validation.md` | Pre-call env-var guards co-located with the SDK seam: `check_any_auth_available`, `check_api_key_only`, `AnthropicAuthMissingError`. | Locations move; public API names unchanged; shim re-exports. |
| `pure-compute-vs-io-split.md` | Pure helpers separated from I/O. | The dispatcher itself is a thin pure compute layer (`provider`-string → backend-fn lookup); the SDK calls live in the backend file. Natural split. |
| `llm-cli-exit-code-taxonomy.md` | `AnthropicAuthMissingError` → exit 2; `AnthropicHelperError` → exit 3. | Exception class identity preserved; `ClaudeCLIError` stays subclass of `AnthropicHelperError`. |

### Key constraints from the survey

1. **Test compatibility** — every existing `monkeypatch.setattr("clauditor._anthropic.X", ...)` must still resolve at runtime. The shim must keep all module-level attributes (`_sleep`, `_rand_uniform`, `_announced_cli_transport`, etc.) accessible at `clauditor._anthropic.X`.
2. **Exception identity** — `AnthropicHelperError`, `AnthropicAuthMissingError`, `ClaudeCLIError` are the SAME class objects regardless of where they're imported from. CLI `except` clauses depend on this.
3. **Acceptance criterion: "no call site outside `_providers/` imports from `_anthropic.py` directly"** — this is the new invariant. Existing CLI / pytest_plugin / grader imports of `clauditor._anthropic.<name>` must migrate to `clauditor._providers.<name>`. The deprecated `clauditor._anthropic` module exists for **outside-clauditor** consumers (none today, but the ticket calls it out for safety).

   **However**, this conflicts with criterion 1: tests patch `clauditor._anthropic.X` → if no clauditor code imports from there, those patches no-op. We need to either (a) migrate test imports too, or (b) keep the shim's re-exports binding-compatible (mutable module attributes that the production code also reads from).

   **Resolution:** the production code's import sites switch to `clauditor._providers`. The shim keeps `clauditor._anthropic.<name>` available for back-compat, but tests that patch internals (`_sleep`, `_rand_uniform`, etc.) must patch the new canonical location (`clauditor._providers._anthropic._sleep`) — OR — we keep the test patches working by making the shim file the single source of truth for those module-private aliases. **This is a key design question for refinement.** (See DEC candidates below.)

### Open questions for refinement

- **Q1 (signature):** ticket says `call_model(prompt, *, provider, model, transport, max_tokens, subject)` — but `call_anthropic` today does NOT have `subject`. What is `subject` for? Plausible answers: telemetry/logging tag, announcement context, or future per-call observability. Need to clarify with user.
- **Q2 (`provider` validation):** ticket types `provider: Literal["anthropic", "openai"]` — but only `"anthropic"` is implemented. What does `call_model(provider="openai", ...)` do today? `NotImplementedError`? Validation error?
- **Q3 (test patch targets):** see analysis above. Where do tests patch the internals — at the new canonical location, at the back-compat shim, or both? See DEC candidates.
- **Q4 (deprecation warning):** ticket says "deprecated alias for one release". Emit `DeprecationWarning` from `call_anthropic`? On import of `clauditor._anthropic`? Silent alias?
- **Q5 (auth helper location):** `check_any_auth_available` and friends are Anthropic-specific today (they check `ANTHROPIC_API_KEY`). Do they live in `_providers/_anthropic.py` (provider-bound) or `_providers/__init__.py` (package-level)? Today they're not provider-aware; for #146 they'll likely become provider-aware.
- **Q6 (sidecar shape):** `ModelResult` adds `provider` field. Sidecars (`grading.json`, `extraction.json`) read `result.source` to populate `transport_source`. Do they ALSO populate a `provider_source` field now, or is that #147's job?

---

## Refinement Log

### Decisions

**DEC-001 — `subject` parameter dropped from this ticket.** `call_model`'s signature in this PR is `call_model(prompt, *, provider, model, transport, max_tokens) → ModelResult` — no `subject`. The ticket's signature line was the only place `subject` appeared across all of #143's 13-issue Epic A/B/C surface; with no documented type, value vocabulary, or downstream consumer, adding it now risks landing the wrong shape. Defer until #154 (`context.json` per-iteration sidecar) or another concrete consumer materializes.
- **Why:** subagent review of #143-#155 found exactly one mention of `subject` (in #144's signature line itself). No body, comment, or downstream ticket defines it. Adding an undefined surface forces a churn pass when consumers actually land.
- **How to apply:** the dispatcher signature lands without `subject`; if a future ticket needs per-call telemetry tags, it adds the parameter with a documented vocabulary. Note in #144's PR body that `subject` was deliberately omitted pending a concrete consumer.
- **Ticket deviation:** acceptance line says "All existing tests pass without modification" — still satisfied (signature change is additive at all call sites). The ticket scope line listed `subject` in the signature; that part of the scope is deferred.

**DEC-002 — `provider="openai"` raises `NotImplementedError`.** When the dispatcher receives `provider="openai"` (literal type permits it; #145 hasn't landed), it raises `NotImplementedError("openai provider lands in #145")` at dispatch time. No stub `_openai.py` file. No `ValueError` (which would route to exit 2 — wrong category for "the feature isn't built yet").
- **Why:** `NotImplementedError` is the standard Python convention for "type-permitted, runtime-not-yet-supported". Distinct from input validation. CLI commands won't catch it (they catch `AnthropicHelperError` / `AnthropicAuthMissingError`); it'll surface as an uncaught exception with a clear message — appropriate for "you typed a literal that's not implemented".
- **How to apply:** `_providers/__init__.py::call_model` does `if provider == "openai": raise NotImplementedError(...)` before any backend dispatch. A test asserts the message and the exception class.

**DEC-003 — Test file rename: `tests/test_anthropic.py` → `tests/test_providers_anthropic.py`.** 1792 lines, 22 test classes, 6 distinct private-symbol patch targets (`_sleep`, `_rand_uniform`, `_monotonic`, `_announced_cli_transport`, `_announced_implicit_no_api_key`, `shutil`). All `clauditor._anthropic.X` patch paths update to `clauditor._providers._anthropic.X` (or `clauditor._providers._auth.X` for the auth-helper subset). No back-compat shim re-exports private aliases.
- **Why:** option C (keep private aliases at the package seam so the shim re-exports work) couples three modules to one shared mutable namespace and creates a hidden import-order dependency. Option D moves the test file once, updates patch paths once, and the deprecated shim only re-exports public names — clean blast radius, single source of truth for each private alias.
- **Ticket deviation:** acceptance line says "All existing tests pass without modification" — this DEC explicitly deviates. The user (ticket author) accepted the deviation 2026-04-28; the PR body will note this so reviewers see the trade-off. Tests still pass; they just live under a new filename with updated import strings.
- **How to apply:** `git mv tests/test_anthropic.py tests/test_providers_anthropic.py`; sed-style rewrite of `clauditor._anthropic` → `clauditor._providers._anthropic` (and `_providers._auth` for the auth-helper subset, which is a subset of test classes — split during the move). Verify all 22 test classes pass post-rename.

**DEC-004 — `DeprecationWarning` once per process via module-level flag.** `clauditor._anthropic.call_anthropic` (the back-compat alias) emits a single `DeprecationWarning` on first call per process. Implementation matches the implicit-coupling-announcement family: a module-level `_announced_call_anthropic_deprecation: bool = False` flag + an inline print-and-flip block, OR a public helper `announce_call_anthropic_deprecation()` co-located with the existing `announce_implicit_no_api_key()` in `_providers/_auth.py` per `centralized-sdk-call.md`'s "Implicit-coupling announcements — an emerging family" section.
- **Why:** silent deprecation (option A) means users never learn the seam moved. Per-call (option B) is noisy in test suites. Module-import-time (option D) fires for any code that imports `clauditor._anthropic` even if it never calls — including some test setup paths that import to monkeypatch. Once-per-process via call-time flag is the established pattern (#86, #95).
- **How to apply:** prefer the public-helper shape (per #95's update to the rule, the new `Final[str]` constant + public emitter helper is the target shape going forward). Add `_announced_call_anthropic_deprecation` flag + `_CALL_ANTHROPIC_DEPRECATION_NOTICE: Final[str]` constant + `announce_call_anthropic_deprecation()` helper; `clauditor._anthropic.call_anthropic`'s body calls the helper before delegating to `call_model`. Add a regression test class `TestCallAnthropicDeprecationAnnouncement` paralleling the existing announcement-family test classes.

**DEC-005 — Auth helpers in `_providers/_auth.py`.** `check_any_auth_available`, `check_api_key_only`, `announce_implicit_no_api_key`, `_announced_implicit_no_api_key`, `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT`, `_AUTH_MISSING_TEMPLATE`, `_AUTH_MISSING_TEMPLATE_KEY_ONLY`, `_api_key_is_set`, and `_claude_cli_is_available` move into a new `src/clauditor/_providers/_auth.py` module. `AnthropicAuthMissingError` stays at the package level (`_providers/__init__.py`) since it's referenced by `_anthropic.py`'s SDK calls AND by `_auth.py`'s checks.
- **Why:** auth checks today read `ANTHROPIC_API_KEY`, but the abstraction shape is provider-agnostic ("does this process have credentials for the chosen provider?"). #146 will introduce per-provider auth (OpenAI's `OPENAI_API_KEY`); putting auth in `_auth.py` now means #146 extends an existing seam instead of carving one out. Matches `precall-env-validation.md`'s "co-located with the SDK seam" guidance — `_providers/` IS the SDK seam, and `_auth.py` is its auth sub-seam.
- **How to apply:** create `_providers/_auth.py`; move the auth helpers + their constants + their announcement family. `_providers/__init__.py` re-exports them (stable public surface). The deprecated shim `clauditor/_anthropic.py` re-exports from `clauditor._providers` so existing CLI imports keep working until callers migrate.

**DEC-006 — `ModelResult.provider` field + `provider_source` read-through on report dataclasses, no schema bump.** `ModelResult` adds `provider: Literal["anthropic", "openai"] = "anthropic"`. `GradingReport` and `ExtractionReport` gain a new `provider_source: str = "anthropic"` field that reads through from `ModelResult.provider` at the call site (paralleling the existing `transport_source` field's wiring). Sidecar JSON shape is **unchanged** — the new field is in-memory only this ticket; #147 owns the `schema_version: 3` bump that lights it up on disk.
- **Why:** option A (defer entirely) means #147 has to touch every report-dataclass call site; option C (full schema bump now) inflates this ticket's scope and risks merging `provider` into sidecars before the OpenAI backend exists to populate non-default values; option B pre-positions the field on the in-memory dataclass without changing what lands on disk, so #147 becomes a small "wire `provider_source` into `to_json` + bump version" patch.
- **How to apply:** `BlindReport`, `GradingReport`, `ExtractionReport` (and any other report dataclass that today carries `transport_source`) get a `provider_source: str = "anthropic"` field. Call sites that today do `transport_source=result.source` add `provider_source=result.provider`. `to_json` methods do NOT include the field this ticket — assert via test that the JSON keys are unchanged.
- **Schema-version anchor:** when #147 lands, it bumps `schema_version` from 2 to 3 and adds `provider_source` to the JSON payload + extends the audit loader's `_check_schema_version` to accept `{1, 2, 3}` with `provider_source` defaulting to `"anthropic"` for v1/v2 reads.

### Session notes

**2026-04-28 — Discovery session 1:**
- Fetched ticket; confirmed scope is pure refactor, no behavior change.
- Surveyed `_anthropic.py` (975 lines, 6 public symbols + 12 private patch targets).
- Located all 6 grader call sites + all CLI/pytest import sites.
- Read all `.claude/rules/*.md`; flagged `centralized-sdk-call.md` for refresh per `rule-refresh-vs-delete.md`.
- Identified test-compatibility concern: production-code imports must move to `_providers/`, but test internals-patching needs a stable target. User chose DEC-003 (rename test file).
- Spawned subagent to research `subject` parameter — found undefined across all 13 Epic A/B/C tickets; user chose DEC-001 (drop from this ticket).
- Captured DEC-001 through DEC-006. Ready for architecture review.

---

## Architecture Review

For a pure refactor of an SDK seam with no behavior change, three of the six baseline review areas reduce to "pass — covered by acceptance criterion `all existing tests pass`": **Performance** (no runtime path changes; same retry policy, same SDK calls, same backoff/jitter), **Data Model** (no DB / on-disk schema changes — DEC-006 explicitly defers the sidecar bump to #147), **Observability** (announcement family preserved, deprecation notice ADDED but follows established pattern). Detailed review below for the four areas with actual signal.

| Area | Rating | Notes |
|---|---|---|
| Security | concern | Auth helper relocation; shim re-export footprint |
| Performance | pass | No runtime path changes (refactor only) |
| Data Model | pass | No on-disk shape changes this ticket (DEC-006) |
| API Design | concern | Public surface re-export discipline; exception-class identity |
| Observability | pass | Existing announcement family preserved; new deprecation notice follows pattern |
| Testing Strategy | concern | DEC-003 test rename + patch path rewrite; private-alias coverage |
| **Refactor-specific:** Import-graph integrity | concern | Six grader sites + ~10 CLI/pytest sites + shim mechanics |

### Security — concern

**Findings:**

1. **`AnthropicAuthMissingError` exit-2 routing must survive the move.** Per `.claude/rules/llm-cli-exit-code-taxonomy.md`, this exception class is the structural marker that routes pre-call auth errors to exit 2 (vs `AnthropicHelperError` → exit 3). Six CLI commands `except AnthropicAuthMissingError as exc: return 2`. After the move, the class object identity must be preserved — i.e. `from clauditor._anthropic import AnthropicAuthMissingError` and `from clauditor._providers import AnthropicAuthMissingError` must yield the **same class object**, not two distinct subclasses.
   - **Mitigation:** define the class exactly once (in `_providers/__init__.py` per DEC-005); the deprecated shim does `from clauditor._providers import AnthropicAuthMissingError` (re-export, not redefinition). Add a regression test:
     ```python
     def test_auth_missing_error_class_identity():
         from clauditor._anthropic import AnthropicAuthMissingError as A
         from clauditor._providers import AnthropicAuthMissingError as B
         assert A is B
     ```
2. **No new attack surface.** The dispatcher introduces no input parsing, no new env-var reads, no new network paths. `provider` is a `Literal` validated by string equality; `transport` validation is the same as today's `_resolve_transport`.
3. **`ANTHROPIC_API_KEY` handling unchanged.** `check_any_auth_available` and `check_api_key_only` move to `_auth.py` (DEC-005) but their env-read shape is byte-identical. Same for `announce_implicit_no_api_key`'s key-strip path (#95).

### API Design — concern

**Findings:**

1. **Two public surfaces, one canonical, one deprecated.** Discipline: every public symbol that lives in `_providers/__init__.py` (`call_model`, `ModelResult`, all 3 exception classes, `check_any_auth_available`, `check_api_key_only`, `announce_implicit_no_api_key`, `resolve_transport`) must be re-exported by `clauditor._anthropic` for one release per the ticket. The deprecated shim is a thin file: `from clauditor._providers import *` + `__all__` definition + DEC-004 deprecation announcement on `call_anthropic` calls.
2. **Exception-class identity preserved (see Security #1).** Same applies to `AnthropicHelperError` and `ClaudeCLIError`.
3. **Acceptance criterion: "no call site outside `_providers/` imports from `_anthropic.py` directly".** Six grader sites + 3 CLI commands + 3 pytest fixtures must update their imports. Easy to find with grep; cheap to migrate.
4. **`ModelResult` provider field default.** Setting `provider: Literal[...] = "anthropic"` (DEC-006) means existing test fixtures that construct `AnthropicResult(...)` without naming `provider` keep working. Renamed-but-default-compatible.
5. **`call_model` is a thin dispatcher, not a class.** Per `.claude/rules/centralized-sdk-call.md`'s shape, the seam is a function. Keep it that way — no `ProviderRegistry` class, no plugin hook, no factory pattern. Just a string-keyed `if/elif` dispatch (or a `dict[str, callable]` if it grows beyond 2 providers).

### Testing Strategy — concern

**Findings:**

1. **DEC-003 rename is mechanical but verbose.** 1792 lines, 22 test classes, ~50 patch sites with `clauditor._anthropic.X` strings. Splitting between `tests/test_providers_anthropic.py` (SDK seam tests: `TestCallAnthropic`, `TestComputeBackoff`, `TestRetryDecision`, etc.) and `tests/test_providers_auth.py` (auth tests: `TestCheckAnyAuth`, `TestAnnounceImplicitNoApiKey`, `TestStderrAnnouncement`) keeps each file under ~1200 lines.
2. **Six distinct private-symbol patch targets** (`_sleep`, `_rand_uniform`, `_monotonic`, `_announced_cli_transport`, `_announced_implicit_no_api_key`, `shutil`) — patch paths update per the file each symbol lives in. The autouse-fixture `monkeypatch.setattr(..., False)` reset pattern in `TestStderrAnnouncement` and `TestAnnounceImplicitNoApiKey` (per `centralized-sdk-call.md`) must be preserved; reset target paths update.
3. **New regression test for class identity (Security #1) + new test class for DEC-004 deprecation announcement.** Estimated +50 lines of test code; shape mirrors `TestStderrAnnouncement`.
4. **Coverage gate (80%, per `CLAUDE.md`).** Refactor preserves logic; coverage % should not regress. Verify post-merge.
5. **Test for `NotImplementedError` on `provider="openai"` (DEC-002).** Single test, ~5 lines.

### Refactor-specific: Import-graph integrity — concern

**Findings:**

1. **Production-code import migration list (10 files):**
   - `src/clauditor/grader.py` (1 import)
   - `src/clauditor/quality_grader.py` (1 import for `grade_quality`, 1 for `blind_compare`)
   - `src/clauditor/triggers.py` (1 import)
   - `src/clauditor/propose_eval.py` (1 import)
   - `src/clauditor/suggest.py` (1 import)
   - `src/clauditor/cli/grade.py` (catches `AnthropicAuthMissingError`)
   - `src/clauditor/cli/extract.py` (same)
   - `src/clauditor/cli/propose_eval.py` (same)
   - `src/clauditor/cli/suggest.py` (same)
   - `src/clauditor/cli/triggers.py` (same)
   - `src/clauditor/cli/compare.py` (`compare --blind` path)
   - `src/clauditor/pytest_plugin.py` (3 fixture imports)
2. **The deprecated shim's exact contents.** `src/clauditor/_anthropic.py` becomes ~30 lines:
   - Module docstring noting deprecation + pointer to `_providers/`
   - `from clauditor._providers import *` (or explicit re-export list)
   - `__all__` definition (matches `_providers.__all__`)
   - The DEC-004 deprecation announcement family lives in `_providers/_auth.py` (so it can be tested in isolation); the shim's `call_anthropic` calls `announce_call_anthropic_deprecation()` then delegates to `call_model(provider="anthropic", ...)`.
3. **`grader.py` and `quality_grader.py` reference `AnthropicResult` in docstrings.** Search-and-replace `AnthropicResult` → `ModelResult` in those docstrings (no runtime impact, but consistency matters per `.claude/rules/centralized-sdk-call.md`'s refresh).
4. **Rule refresh per `rule-refresh-vs-delete.md`.** `.claude/rules/centralized-sdk-call.md` refresh edits-in-place:
   - Opening: "Route every Anthropic SDK call through the centralized helper" → "Route every model call through the centralized dispatcher" (or similar — preserve the heading text per the rule's H2-byte-identical guidance, just update the body).
   - "The pattern" code example: `from clauditor._anthropic import call_anthropic` → `from clauditor._providers import call_model`; usage example shows `call_model(prompt, provider="anthropic", model=...)`.
   - "Canonical implementation" path string updates: `src/clauditor/_anthropic.py::call_anthropic` → `src/clauditor/_providers/__init__.py::call_model` + `src/clauditor/_providers/_anthropic.py` for the body.
   - Historical validation notes (#86 transport routing, #95 implicit-coupling) preserved byte-verbatim.
   - "Implicit-coupling announcements — an emerging family" section: add the DEC-004 deprecation announcement as the third member alongside the two existing flags. Path strings update.

### Concerns summary

All four "concern" ratings are **mitigatable within the standard refactor playbook** — none are blockers. The user's six decisions cleanly resolve the design surface; what remains is mechanical execution + careful import-graph migration + test rename + rule refresh. No blockers for the implementation phase.

---

---

## Detailed Breakdown

Validation command across all stories (per `CLAUDE.md`):

```bash
uv run ruff check src/ tests/
uv run pytest --cov=clauditor --cov-report=term-missing  # 80% coverage gate
```

Stories are ordered to keep `_anthropic.py` working as a re-export shim throughout the migration. Production-code imports stay valid at every story boundary; nothing breaks until the final shim shrink (US-005). Each story is ralph-sized: completable in one context window, with clear file scope and an objective "done when".

---

### US-001 — Move auth helpers to `_providers/_auth.py`

**Description:** Create the `_providers/` package skeleton and move all auth-related helpers into a new `_providers/_auth.py` module. `AnthropicAuthMissingError` lands at the package level (`_providers/__init__.py`) so it's importable from a stable seam regardless of where the SDK seam sits. `_anthropic.py` adds `from clauditor._providers._auth import *` re-exports so existing callers keep working unmodified.

**Traces to:** DEC-005

**Acceptance criteria:**
- `src/clauditor/_providers/__init__.py` and `src/clauditor/_providers/_auth.py` exist.
- `_auth.py` defines: `check_any_auth_available`, `check_api_key_only`, `announce_implicit_no_api_key`, `_announced_implicit_no_api_key`, `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT`, `_AUTH_MISSING_TEMPLATE`, `_AUTH_MISSING_TEMPLATE_KEY_ONLY`, `_api_key_is_set`, `_claude_cli_is_available`.
- `_providers/__init__.py` defines `AnthropicAuthMissingError` and re-exports all `_auth.py` symbols.
- `clauditor/_anthropic.py` re-exports the same symbols so `from clauditor._anthropic import check_any_auth_available` still works.
- Class-identity invariant: `clauditor._anthropic.AnthropicAuthMissingError is clauditor._providers.AnthropicAuthMissingError`.
- `uv run pytest` passes; `uv run ruff check` passes.

**Done when:** `_providers/_auth.py` exists with all 9 auth symbols, package-level exception class is importable from both `clauditor._providers` and `clauditor._anthropic` (same class object), all existing tests pass without modification.

**Files:**
- NEW: `src/clauditor/_providers/__init__.py` (~30 lines: package docstring, `AnthropicAuthMissingError` definition, re-exports from `_auth`, `__all__`).
- NEW: `src/clauditor/_providers/_auth.py` (~150 lines moved from `_anthropic.py`).
- MODIFIED: `src/clauditor/_anthropic.py` — removes the moved bodies; adds `from clauditor._providers._auth import *` and `from clauditor._providers import AnthropicAuthMissingError` re-export block.
- NEW: `tests/test_anthropic.py::TestExceptionClassIdentity` — single regression test for class-identity invariant.

**Depends on:** none

**TDD:**
- `test_auth_missing_error_class_identity` — `clauditor._anthropic.AnthropicAuthMissingError is clauditor._providers.AnthropicAuthMissingError`.
- Existing `TestStderrAnnouncement` and `TestAnnounceImplicitNoApiKey` autouse-fixture reset patterns continue to work via the re-export (assert by running them — no test modification needed).

---

### US-002 — Move SDK seam to `_providers/_anthropic.py`, rename `AnthropicResult` → `ModelResult`

**Description:** Move every Anthropic-specific SDK helper (transport branches, retry logic, token extraction, announcement family, constants, exception classes) from `_anthropic.py` into a new `_providers/_anthropic.py` module. Rename `AnthropicResult` → `ModelResult` and add the `provider: Literal["anthropic", "openai"] = "anthropic"` field. **No new dispatcher in this story** — `call_model` lands in US-003. **No rule refresh in this story** — that lands in US-004. `_anthropic.py` re-exports every moved public symbol for back-compat; existing callers keep working unmodified.

**Traces to:** DEC-006 (partial — `ModelResult.provider` field), DEC-005 (companion seam — auth helpers already moved in US-001).

**Acceptance criteria:**
- `src/clauditor/_providers/_anthropic.py` exists and defines `_call_via_sdk`, `_call_via_claude_cli`, `_compute_backoff`, `_compute_retry_decision`, `_extract_result`, `_body_excerpt`, `_classify_invoke_result`, `_sleep`, `_rand_uniform`, `_rng`, `_announced_cli_transport`, `_CLI_AUTO_ANNOUNCEMENT`, retry constants (`_RATE_LIMIT_MAX_RETRIES`, `_SERVER_MAX_RETRIES`, `_CONN_MAX_RETRIES`), `_BODY_EXCERPT_CHARS`, `_CLI_TRANSPORT_TIMEOUT`, `_CLI_ERROR_TEMPLATES`, `resolve_transport`, `_resolve_transport`, `_VALID_TRANSPORT_VALUES`, `AnthropicHelperError`, `ClaudeCLIError`, `ModelResult`, and `call_anthropic` (in its existing form — kept in this provider module since it IS the anthropic backend).
- `ModelResult` carries `provider: Literal["anthropic", "openai"] = "anthropic"` as a new field; existing `source: Literal["api", "cli"]` field unchanged.
- Back-compat alias `AnthropicResult = ModelResult` exists in `_providers/_anthropic.py` so existing tests / docstrings naming `AnthropicResult` keep working.
- `_providers/__init__.py` re-exports every public symbol from `_anthropic.py` (`AnthropicHelperError`, `ClaudeCLIError`, `ModelResult`, `AnthropicResult`, `resolve_transport`, `call_anthropic`).
- `clauditor/_anthropic.py` re-exports every moved public symbol from `clauditor._providers` — existing CLI / pytest-plugin / grader imports keep working.
- No `call_model` dispatcher yet (lands US-003).
- No `.claude/rules/` edits yet (US-004).
- `uv run pytest` passes; `uv run ruff check` passes.

**Done when:** `_providers/_anthropic.py` exists with the full SDK seam moved out of `_anthropic.py`, `ModelResult` carries the `provider` field, all existing tests still pass through the back-compat re-exports.

**Files:**
- NEW: `src/clauditor/_providers/_anthropic.py` (~700 lines moved from `_anthropic.py`).
- MODIFIED: `src/clauditor/_providers/__init__.py` — re-exports every public symbol from `_anthropic.py`; updates `__all__`.
- MODIFIED: `src/clauditor/_anthropic.py` — removes moved bodies; re-export skeleton (`from clauditor._providers import *` + explicit re-exports for any names not in `__all__`).
- MODIFIED: `tests/test_anthropic.py` — add `TestModelResult` class (`provider` field default test, `AnthropicResult is ModelResult` identity test). Existing test classes untouched.

**Depends on:** US-001

**TDD:**
- `test_model_result_provider_default` — `ModelResult(...)` default for `provider` is `"anthropic"`.
- `test_anthropic_result_alias_identity` — `from clauditor._providers._anthropic import AnthropicResult, ModelResult; assert AnthropicResult is ModelResult`.
- `test_call_anthropic_still_works_via_shim` — existing `from clauditor._anthropic import call_anthropic` resolves to the same callable as `from clauditor._providers import call_anthropic`.
- All existing `tests/test_anthropic.py` test classes pass without modification (verifying re-export coverage).

---

### US-003 — Add `call_model` dispatcher in `_providers/__init__.py`

**Description:** Add a thin dispatcher `call_model(prompt, *, provider, model, transport, max_tokens) → ModelResult` in `_providers/__init__.py` that routes `provider="anthropic"` to the existing `call_anthropic` backend in `_providers/_anthropic.py` and raises `NotImplementedError("openai provider lands in #145")` for `provider="openai"`. Signature does NOT carry `subject` (DEC-001).

**Traces to:** DEC-001 (no `subject`), DEC-002 (`provider="openai"` raises `NotImplementedError`).

**Acceptance criteria:**
- `_providers/__init__.py::call_model(prompt: str, *, provider: Literal["anthropic", "openai"], model: str, transport: str = "auto", max_tokens: int = 4096) → ModelResult` exists.
- `provider="anthropic"` delegates to `clauditor._providers._anthropic.call_anthropic(prompt, model=model, transport=transport, max_tokens=max_tokens)` and returns the resulting `ModelResult`.
- `provider="openai"` raises `NotImplementedError` with message containing `"#145"` (so users have a pointer to the issue).
- `call_model` is exported from `_providers/__init__.py::__all__`.
- `_anthropic.py` shim re-exports `call_model` so `from clauditor._anthropic import call_model` works (transitional; CLI / graders will migrate in later stories).
- `uv run pytest` passes; `uv run ruff check` passes.

**Done when:** `call_model` exists, routes anthropic correctly, raises `NotImplementedError` for openai, signature matches DEC-001 (no `subject`).

**Files:**
- MODIFIED: `src/clauditor/_providers/__init__.py` — add `call_model` function (~25 lines including docstring) and add to `__all__`.
- MODIFIED: `src/clauditor/_anthropic.py` — re-export `call_model` from the shim.
- MODIFIED: `tests/test_anthropic.py` — add `TestCallModel` class with the four TDD cases below.

**Depends on:** US-002

**TDD:**
- `test_call_model_routes_anthropic_to_call_anthropic` — patch `clauditor._providers._anthropic.call_anthropic` as `AsyncMock`; `await call_model(prompt, provider="anthropic", model="claude-3-5-haiku-latest", transport="api", max_tokens=4096)`; assert the mock was awaited with the same kwargs.
- `test_call_model_anthropic_returns_model_result` — patch `call_anthropic` to return a `ModelResult(provider="anthropic", source="api", ...)`; `result = await call_model(...)`; assert `isinstance(result, ModelResult)` and `result.provider == "anthropic"`.
- `test_call_model_openai_raises_not_implemented` — `with pytest.raises(NotImplementedError, match="#145"): await call_model(..., provider="openai", ...)`.
- `test_call_model_signature_does_not_include_subject` — `import inspect; sig = inspect.signature(call_model); assert "subject" not in sig.parameters` (DEC-001 guard).

---

### US-004 — Refresh `.claude/rules/centralized-sdk-call.md` for the new dispatcher seam

**Description:** Refresh `.claude/rules/centralized-sdk-call.md` in-place per `.claude/rules/rule-refresh-vs-delete.md`. The pattern (one centralized seam owns retry, error categorization, token accounting, transport routing) is still load-bearing — only the file location and seam name change. Update opening framing, code-example path strings, "Why this shape" reasoning, "What NOT to do" anti-patterns, and "Canonical implementation" file paths. Preserve historical validation notes (#86 transport routing, #95 implicit-coupling) byte-verbatim.

**Traces to:** the rule-refresh discipline of `.claude/rules/rule-refresh-vs-delete.md`; the new seam introduced in US-003.

**Acceptance criteria:**
- Opening framing of `centralized-sdk-call.md`: rewritten to describe the `call_model` dispatcher seam in `clauditor._providers`. Heading text byte-identical (per `rule-refresh-vs-delete.md`'s "Identical H2 text, not a rename" guidance) — e.g. `# Rule: Route every Anthropic SDK call through the centralized helper` stays unchanged (the rule's name reflects the pattern, not the implementation file).
- "The pattern" code example: `from clauditor._anthropic import AnthropicHelperError, call_anthropic` → `from clauditor._providers import AnthropicHelperError, call_model`. Usage example shows `call_model(prompt, provider="anthropic", model=..., transport=..., max_tokens=...)`.
- "Inside `_anthropic.py`" code example: framed as "Inside `_providers/_anthropic.py`" with the SDK-backend body and the alias-indirection note (`_sleep`, `_rand_uniform` per `monotonic-time-indirection.md`).
- "Why this shape" reasoning: re-audited; references to "the centralized helper" updated to "the centralized dispatcher" where appropriate. Underlying truths preserved.
- "Canonical implementation" path strings updated:
  - `src/clauditor/_anthropic.py::call_anthropic` → `src/clauditor/_providers/__init__.py::call_model` (dispatcher) + `src/clauditor/_providers/_anthropic.py::call_anthropic` (anthropic backend) + `src/clauditor/_providers/_anthropic.py::ModelResult` (dataclass).
  - The four call-site bullets (`grader.py`, `quality_grader.py`, `suggest.py`, `triggers.py`) — paths unchanged but the import line each shows updates from `from clauditor._anthropic` → `from clauditor._providers`.
- "Multi-transport routing (CLI + SDK, #86)" subsection: historical validation note + DEC pointers preserved byte-verbatim. Path strings updated.
- "Implicit-coupling announcements — an emerging family" subsection: lists the existing two members (`_announced_cli_transport`, `_announced_implicit_no_api_key`). Path strings updated to `_providers/_anthropic.py` and `_providers/_auth.py` respectively. **DEC-004's third member (`_announced_call_anthropic_deprecation`) is added in US-007's edit, not this story.**
- "Companion rules" pointers: any path-bearing references audited and updated.
- "When this rule applies" / "When this rule does NOT apply" sections: re-audited for stale path references.
- `uv run pytest` passes; `uv run ruff check` passes (no Python files modified, so this is a sanity check).
- `grep` regression: no `clauditor\._anthropic\.` substrings in `centralized-sdk-call.md` except inside back-compat / deprecation context.

**Done when:** the rule reads coherently against the post-refactor file layout, every path string and code example is current, every historical validation note is preserved byte-verbatim.

**Files:**
- MODIFIED: `.claude/rules/centralized-sdk-call.md` — in-place refresh.

**Depends on:** US-003

**TDD:** N/A — rule-prose edits, no Python logic. Verification is a careful read-through plus the `grep` regression assertion above.

---

### US-005 — Rewire 6 grader call sites to `call_model`, add `provider_source` to report dataclasses

**Description:** Update each of the six grader call sites to import `call_model` from `clauditor._providers` and call it with `provider="anthropic"`. At each report-dataclass construction site, read `result.provider` and pass it through to a new `provider_source: str = "anthropic"` field on `BlindReport`, `GradingReport`, and `ExtractionReport`. Sidecar JSON shape is **unchanged** this ticket — `to_json` does NOT include the new field; #147 owns the schema bump.

**Traces to:** DEC-006 (in-memory `provider_source` field, no schema bump), and the ticket's "rewire all six grader call sites" requirement.

**Acceptance criteria:**
- Six call sites updated:
  - `src/clauditor/grader.py:798` — `extract_and_grade` / `extract_and_report` shared site.
  - `src/clauditor/quality_grader.py:590` — `grade_quality`.
  - `src/clauditor/quality_grader.py:598` — `blind_compare`'s `gather` pair (1 import; both `await` calls switch to `call_model`).
  - `src/clauditor/triggers.py:189-194`.
  - `src/clauditor/propose_eval.py:842-857`.
  - `src/clauditor/suggest.py:995-998`.
- Each site: `from clauditor._providers import call_model, AnthropicHelperError` (or whichever exception class it catches); `await call_model(prompt, provider="anthropic", model=..., transport=..., max_tokens=...)`.
- `BlindReport`, `GradingReport`, `ExtractionReport` dataclasses each gain `provider_source: str = "anthropic"` field. (Whatever other report dataclasses today carry `transport_source` get the same treatment.)
- At each report-construction site, `provider_source=result.provider` is set alongside the existing `transport_source=result.source`.
- `to_json` methods of these dataclasses do NOT include `provider_source` in the output — assert via test that the JSON keys are byte-identical to today.
- `AnthropicResult` references in docstrings of `grader.py`, `quality_grader.py` updated to `ModelResult`.
- No call site outside `src/clauditor/_providers/` imports from `clauditor._anthropic` directly (per ticket acceptance) — verified by grep + new test.
- `uv run pytest` passes; `uv run ruff check` passes.

**Done when:** all six grader call sites use `call_model`, all three report dataclasses carry `provider_source` (in-memory only), JSON sidecar shape unchanged, no production-code module under `src/clauditor/` (excluding `_providers/`) imports from `_anthropic.py`.

**Files:**
- MODIFIED: `src/clauditor/grader.py` — call site + `ExtractionReport` dataclass `provider_source` field.
- MODIFIED: `src/clauditor/quality_grader.py` — `grade_quality` + `blind_compare` call sites + `BlindReport` / `GradingReport` dataclass `provider_source` field.
- MODIFIED: `src/clauditor/triggers.py` — call site.
- MODIFIED: `src/clauditor/propose_eval.py` — call site.
- MODIFIED: `src/clauditor/suggest.py` — call site.
- MODIFIED: `tests/test_quality_grader.py`, `tests/test_grader.py`, `tests/test_triggers.py`, `tests/test_propose_eval.py`, `tests/test_suggest.py` — patch paths update from `clauditor._anthropic.call_anthropic` to `clauditor._providers.call_model`; new tests for `provider_source` defaulting + JSON-shape preservation.

**Depends on:** US-003 (must precede; US-004 rule refresh can run in parallel but linear chain is preserved for ralph)

**TDD:**
- `test_grading_report_provider_source_defaults_to_anthropic` — constructed `GradingReport` has `report.provider_source == "anthropic"`.
- `test_grading_report_to_json_does_not_include_provider_source` — assert `"provider_source" not in json.loads(report.to_json())`.
- `test_extraction_report_provider_source_propagates` — when `call_model` returns a `ModelResult` with `provider="anthropic"`, the resulting report has `provider_source == "anthropic"`.
- `test_blind_report_provider_source_set` — same for `BlindReport`.
- `test_no_anthropic_imports_outside_providers` — production-code grep regression: `grep -rn "from clauditor._anthropic" src/clauditor/` returns hits only inside `src/clauditor/_providers/` and `src/clauditor/_anthropic.py` (the shim itself).

---

### US-006 — Rewire CLI commands and `pytest_plugin.py` to import from `_providers`

**Description:** Update the six LLM-mediated CLI commands and the three `pytest_plugin.py` fixture-import sites to import `AnthropicAuthMissingError`, `AnthropicHelperError`, `check_any_auth_available`, `check_api_key_only`, and `announce_implicit_no_api_key` from `clauditor._providers` instead of `clauditor._anthropic`. After this story, the only files importing from `clauditor._anthropic` are the shim itself and `tests/test_anthropic.py` (which is renamed in US-006).

**Traces to:** ticket acceptance "no call site outside `_providers/` imports from `_anthropic.py` directly".

**Acceptance criteria:**
- `src/clauditor/cli/grade.py`, `cli/extract.py`, `cli/propose_eval.py`, `cli/suggest.py`, `cli/triggers.py`, `cli/compare.py` all import from `clauditor._providers`.
- `src/clauditor/pytest_plugin.py` updates 3 fixture-imports to `clauditor._providers`.
- Exception-class identity preserved: CLI `except AnthropicAuthMissingError` clauses still catch the same exception object the helper raises.
- Existing CLI exit-code routing tests pass without modification.
- `uv run pytest` passes; `uv run ruff check` passes.

**Done when:** zero production-code files under `src/clauditor/` (excluding `_providers/` and `_anthropic.py` itself) import from `clauditor._anthropic`. The grep-regression test from US-005 strengthens to allow only the shim itself.

**Files:**
- MODIFIED: `src/clauditor/cli/grade.py`, `src/clauditor/cli/extract.py`, `src/clauditor/cli/propose_eval.py`, `src/clauditor/cli/suggest.py`, `src/clauditor/cli/triggers.py`, `src/clauditor/cli/compare.py`.
- MODIFIED: `src/clauditor/pytest_plugin.py`.

**Depends on:** US-005

**TDD:**
- The grep-regression test from US-005 strengthens: `grep -rn "from clauditor._anthropic" src/clauditor/` returns hits ONLY in `src/clauditor/_anthropic.py` (the shim).
- Existing CLI exit-code routing tests (`tests/test_cli_grade.py::test_cmd_grade_exits_2_on_missing_auth`, etc.) all pass — verifying class-identity preservation across the import-source change.

---

### US-007 — Shrink `_anthropic.py` to a deprecated shim, add DEC-004 deprecation announcement

**Description:** Reduce `src/clauditor/_anthropic.py` to a ~30-line back-compat shim: module docstring noting deprecation, explicit re-export of every public symbol from `clauditor._providers`, `__all__` list, and a `call_anthropic` thin wrapper that emits a `DeprecationWarning` once per process before delegating to `call_model(provider="anthropic", ...)`. Add `announce_call_anthropic_deprecation()` helper, `_announced_call_anthropic_deprecation: bool` flag, and `_CALL_ANTHROPIC_DEPRECATION_NOTICE: Final[str]` constant in `_providers/_auth.py` per the announcement-family pattern. Add the third member to "Implicit-coupling announcements — an emerging family" in `centralized-sdk-call.md`.

**Traces to:** DEC-004, ticket requirement "Keep `call_anthropic` as a deprecated alias for one release for back-compat in tests".

**Acceptance criteria:**
- `src/clauditor/_anthropic.py` is ~30 lines: docstring, re-export block, `__all__`, `call_anthropic` deprecated wrapper.
- `call_anthropic(prompt, *, model, transport, max_tokens) → ModelResult` exists; signature matches today's; first call per process triggers `announce_call_anthropic_deprecation()` then awaits `call_model(prompt, provider="anthropic", model=model, transport=transport, max_tokens=max_tokens)`.
- `_providers/_auth.py` defines `_announced_call_anthropic_deprecation: bool = False`, `_CALL_ANTHROPIC_DEPRECATION_NOTICE: Final[str]`, and `announce_call_anthropic_deprecation() -> None` (the public emitter helper, paralleling `announce_implicit_no_api_key`).
- `.claude/rules/centralized-sdk-call.md` "Implicit-coupling announcements — an emerging family" section lists the third member with the same shape as the existing two.
- New test class `TestCallAnthropicDeprecationAnnouncement` in `tests/test_anthropic.py` (or in `tests/test_providers_auth.py` after the US-006 split) — paralleling existing `TestStderrAnnouncement` / `TestAnnounceImplicitNoApiKey` with the autouse `monkeypatch.setattr(..., False)` reset fixture.
- `uv run pytest` passes; `uv run ruff check` passes.
- All existing `from clauditor._anthropic import call_anthropic` test imports still resolve and work (with one-time deprecation notice on stderr per process).

**Done when:** `clauditor._anthropic` is a ~30-line deprecated shim, `call_anthropic` works but emits a `DeprecationWarning` once per process, the announcement family has three members documented in `centralized-sdk-call.md`.

**Files:**
- MODIFIED (heavy shrink): `src/clauditor/_anthropic.py` — from ~975 lines (post-US-002 re-export skeleton) down to ~30 lines.
- MODIFIED: `src/clauditor/_providers/_auth.py` — adds the three deprecation-announcement symbols + helper.
- MODIFIED: `src/clauditor/_providers/__init__.py` — re-exports `announce_call_anthropic_deprecation` and the constant.
- MODIFIED: `.claude/rules/centralized-sdk-call.md` — adds DEC-004 as the third member of the announcement family.
- MODIFIED: `tests/test_anthropic.py` — adds `TestCallAnthropicDeprecationAnnouncement`.

**Depends on:** US-006 (must precede so no production code triggers the deprecation warning during normal operation; only test imports do).

**TDD:**
- `test_call_anthropic_emits_deprecation_warning_first_call` — capture stderr, assert the notice appears.
- `test_call_anthropic_announcement_only_fires_once` — second call within same process, no second notice.
- `test_call_anthropic_delegates_to_call_model` — patch `clauditor._providers.call_model` as `AsyncMock`, call `call_anthropic`, assert `call_model.await_args.kwargs["provider"] == "anthropic"`.
- Reset fixture: autouse `monkeypatch.setattr("clauditor._providers._auth._announced_call_anthropic_deprecation", False)` between tests in the new class.

---

### US-008 — Rename `tests/test_anthropic.py` → `tests/test_providers_anthropic.py` + update patch paths

**Description:** Pure-rename story: `git mv tests/test_anthropic.py tests/test_providers_anthropic.py`, then rewrite every `clauditor._anthropic.X` patch-path string in the renamed file to its new canonical location (`clauditor._providers._anthropic.X` or `clauditor._providers._auth.X` per the symbol). All 22 existing test classes stay in this single file at this story boundary — splitting auth-test classes into a separate file lands in US-009.

**Traces to:** DEC-003 (first half of the test-file move). Explicit deviation from ticket acceptance "All existing tests pass without modification" — flagged in PR body.

**Acceptance criteria:**
- `tests/test_anthropic.py` removed (via `git mv` to preserve blame).
- `tests/test_providers_anthropic.py` contains all 22 test classes (auth-related ones split out in US-009).
- Patch paths updated per symbol location:
  - `clauditor._anthropic._sleep` → `clauditor._providers._anthropic._sleep`
  - `clauditor._anthropic._rand_uniform` → `clauditor._providers._anthropic._rand_uniform`
  - `clauditor._anthropic._monotonic` → `clauditor._providers._anthropic._monotonic`
  - `clauditor._anthropic._announced_cli_transport` → `clauditor._providers._anthropic._announced_cli_transport`
  - `clauditor._anthropic._announced_implicit_no_api_key` → `clauditor._providers._auth._announced_implicit_no_api_key`
  - `clauditor._anthropic.shutil` → `clauditor._providers._anthropic.shutil`
- All 22 test classes pass under the new file location.
- `uv run pytest` passes; `uv run ruff check` passes.
- Coverage % does not regress.
- `grep -rn "clauditor._anthropic\." tests/` returns hits only for legitimate back-compat-tests (`test_call_anthropic_still_works_via_shim`, etc.) — never for private-symbol patches.

**Done when:** the test file lives at the new path, every patch string updated, all 22 test classes still green.

**Files:**
- DELETED via `git mv`: `tests/test_anthropic.py`.
- NEW (via `git mv`): `tests/test_providers_anthropic.py` (still ~1792 lines at this boundary).

**Depends on:** US-007

**TDD:** N/A — mechanical rename. Verification is `pytest tests/test_providers_anthropic.py` green.

---

### US-009 — Split auth-related test classes into `tests/test_providers_auth.py`

**Description:** Move the auth-helper test classes out of `tests/test_providers_anthropic.py` into a new `tests/test_providers_auth.py`. After this story, the SDK-seam tests live in one file and the auth tests live in another, mirroring the production-code split between `_providers/_anthropic.py` and `_providers/_auth.py`.

**Traces to:** DEC-003 (second half of the test-file move).

**Acceptance criteria:**
- `tests/test_providers_auth.py` exists and contains: `TestCheckAnyAuthAvailable`, `TestCheckApiKeyOnly`, `TestApiKeyIsSet`, `TestClaudeCliIsAvailable`, `TestAnnounceImplicitNoApiKey`, `TestCallAnthropicDeprecationAnnouncement` (added in US-007), `TestExceptionClassIdentity` (added in US-001), plus any other auth-helper-specific test class.
- `tests/test_providers_anthropic.py` no longer contains any of the moved classes; retains SDK-seam classes (`TestCallAnthropic`, `TestCallModel`, `TestComputeBackoff`, `TestComputeRetryDecision`, `TestExtractResult`, `TestBodyExcerpt`, `TestClassifyInvokeResult`, `TestStderrAnnouncement` for CLI-transport announcement, `TestResolveTransport`, `TestModelResult`, etc.).
- Imports in each file are scoped to the symbols actually used (no orphan `from clauditor._providers._auth import X` in the SDK-seam file or vice versa).
- `tests/test_providers_anthropic.py` lands at ~1200 lines; `tests/test_providers_auth.py` lands at ~600 lines (rough estimate; balance shifts with class boundaries).
- All test classes pass in their new locations.
- `uv run pytest` passes; `uv run ruff check` passes.
- Coverage % does not regress.

**Done when:** SDK-seam tests and auth tests live in separate files, each scoped to the production module it covers, all tests green.

**Files:**
- MODIFIED: `tests/test_providers_anthropic.py` — removes auth-helper test classes (~600 lines).
- NEW: `tests/test_providers_auth.py` (~600 lines).

**Depends on:** US-008

**TDD:** N/A — mechanical move. Verification is the test suite green and class boundaries clean.

---

### US-010 — Quality Gate

**Description:** Run code reviewer four times across the full changeset, fixing every real bug found each pass. Run CodeRabbit if available. Validation must pass after all fixes.

**Acceptance criteria:**
- Code reviewer agent (subagent_type=code-reviewer) run 4× over the diff `dev..HEAD` for `feature/144-providers-call-model`. Each pass produces a list of findings; real bugs and concerns are addressed before the next pass. False positives are documented in the PR body.
- CodeRabbit review run if available; comments addressed (or marked false-positive in the PR body).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% coverage gate.
- No regressions in coverage percentage versus `dev` baseline.
- Class-identity invariant verified end-to-end: a fresh `pytest` run on a clean checkout shows the exception classes are stable across both import sources.

**Done when:** four reviewer passes complete with no remaining real bugs, CodeRabbit (if used) green or comments dispositioned, validation passes.

**Files:** any files needing fixes from the reviewer findings.

**Depends on:** US-001 through US-009 (all implementation complete)

---

### US-011 — Patterns & Memory

**Description:** Update `.claude/rules/`, `docs/`, and the auto-memory system with any new patterns learned during the refactor. Most rule updates already landed in US-004 (`centralized-sdk-call.md` refresh) and US-007 (announcement family extension); this story is the final sweep.

**Acceptance criteria:**
- Re-read `.claude/rules/rule-refresh-vs-delete.md` as the canonical anchor for the rule edit done in US-004 — confirm the refresh respected the byte-verbatim historical-validation-note discipline.
- If anything new was learned that doesn't fit an existing rule (e.g. "back-compat shim re-exports must be `from X import *`, never `_re-define = X.symbol`"), draft a new rule file or extend an existing one.
- Update the auto-memory `MEMORY.md` index if any user/feedback/project facts came up (none anticipated for a pure refactor).
- Verify `.claude/rules/centralized-sdk-call.md` references `call_model` not `call_anthropic` in every "When this rule applies" / "Canonical implementation" reference.

**Done when:** rules and memory reflect the post-refactor reality; no stale `call_anthropic`-as-canonical mentions remain in `.claude/rules/`.

**Files:**
- POSSIBLY MODIFIED: `.claude/rules/centralized-sdk-call.md` (final cleanup if anything missed in US-004/US-007).
- POSSIBLY NEW: a new `.claude/rules/<name>.md` if a fresh pattern emerged.

**Depends on:** US-010

---

### Story dependency graph

```
US-001 (auth move)
  └── US-002 (SDK seam move + ModelResult rename)
        └── US-003 (call_model dispatcher)
              └── US-004 (rule refresh)
                    └── US-005 (rewire 6 graders + provider_source)
                          └── US-006 (rewire CLI + pytest_plugin imports)
                                └── US-007 (shim shrink + deprecation announcement)
                                      └── US-008 (test file rename + patch path rewrite)
                                            └── US-009 (split auth tests into separate file)
                                                  └── US-010 (Quality Gate)
                                                        └── US-011 (Patterns & Memory)
```

Linear chain by design — every story preserves the property that `_anthropic.py` works as a re-export shim, so the migration order is the reverse of "what depends on what" rather than "what can run in parallel". Ralph executes them in order.

(US-004 rule-refresh and US-005 grader rewire touch disjoint file sets and could in principle run in parallel; the linear chain is preserved for ralph simplicity. If a future ralph supports parallel beads, this is the natural fork point.)

---

---

## Beads Manifest

*Pending.*
