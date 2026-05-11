# 175: Codex ChatGPT-login auth — recognize `auth_mode=chatgpt` in `check_codex_auth`

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/175
- **Branch:** `feature/175-codex-chatgpt-login-auth`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/175-codex-chatgpt-login-auth`
- **PR:** https://github.com/wjduenow/clauditor/pull/179
- **Phase:** devolved
- **Epic:** clauditor-lacb
- **Sessions:** 1 (2026-05-11)
- **Total decisions:** 10 (DEC-001 through DEC-010)
- **Depends on:** #95 (CLOSED — Anthropic relaxed-guard precedent), #149 (CLOSED — Codex harness), #151 (CLOSED — harness precedence + `check_codex_auth` dispatch)
- **Sibling concept:** #83 (CLOSED — Anthropic subscription-auth gap, the conceptual analog this ticket fixes for Codex)

---

## Discovery

### Ticket summary

**What:** `check_codex_auth` in `src/clauditor/_providers/_auth.py:483-532` is a strict-OR over only two env vars (`CODEX_API_KEY`, `OPENAI_API_KEY`). The Codex CLI itself supports a third auth path — **ChatGPT login** — where credentials live in `~/.codex/auth.json` as `{"auth_mode": "chatgpt", "OPENAI_API_KEY": null, "tokens": {...}}` and no env var is set. A user authenticated this way can run `codex exec` directly, but every clauditor command that resolves `harness=codex` raises `CodexAuthMissingError` and exits 2 at the pre-flight before any subprocess work.

**Confirmed live:** `/home/wesd/.codex/auth.json` on this machine has `auth_mode="chatgpt"`, `OPENAI_API_KEY: null`, populated `tokens`. The bug reproduces exactly as the ticket describes.

**Why it matters:** This is the *first* user-visible friction in real-world cross-harness validation (`#143`). Codex users who logged in via `codex login` against ChatGPT Plus/Pro/Enterprise (the GUI-friendly default flow) get an opaque exit-2 from clauditor while `codex exec` works fine in the same shell. Symmetric UX hit to what `#83` documented for Anthropic before `#95` fixed it.

### Codebase findings

**Current pre-flight (`src/clauditor/_providers/_auth.py`):**
- `check_codex_auth(cmd_name)` at lines 483-532 — strict-OR over `_codex_api_key_is_set()` and `_openai_api_key_is_set()` only.
- Anthropic analog `check_any_auth_available(cmd_name)` at lines 214-252 — accepts `_api_key_is_set()` OR `_claude_cli_is_available()`. Per DEC-008 of #86, the CLI branch is pure PATH-presence; auth validation is deferred to the subprocess.
- `_claude_cli_is_available()` at lines 200-211 — single-line `shutil.which("claude") is not None`. Pure helper; deliberately does NOT verify the CLI is functional or authenticated.
- `_CODEX_AUTH_MISSING_TEMPLATE` at lines 459-466 — names `CODEX_API_KEY`, `OPENAI_API_KEY`, `platform.openai.com`. Three durable substrings pinned by tests.

**Exception class:** `CodexAuthMissingError` is defined at `src/clauditor/_providers/__init__.py:112-143` as a **direct subclass of `Exception`** (NOT a subclass of `AnthropicAuthMissingError` / `OpenAIAuthMissingError`). Sibling-not-subclass shape preserves structural exit-code routing per `.claude/rules/llm-cli-exit-code-taxonomy.md` and `.claude/rules/multi-provider-dispatch.md`. Caught in four CLI commands, all routing to exit 2:
- `cli/validate.py:191`, `cli/capture.py:159`, `cli/grade.py:421`, `cli/run.py:98`.

**Critical existing precedent in CodexHarness (`src/clauditor/_harnesses/_codex.py:1334-1372`):**
`CodexHarness._detect_auth_source()` already implements the file-detection logic for **observability/logging only** (not auth validation). Detects `cached` source via `Path("$CODEX_HOME/auth.json").exists()` (default `~/.codex/auth.json`). Returns `{"CODEX_API_KEY", "OPENAI_API_KEY", "cached", "unknown"}`. Per DEC-017 of `#149`, the `"cached"` source-name was forward-declared but **never fires today** because `check_codex_auth` blocks before the harness invokes — `#175` is what makes the `"cached"` value finally reachable.

**`CodexHarness.invoke` env handling:** `strip_auth_keys` (lines 551-573) strips `CODEX_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` only. `HOME` and `CODEX_HOME` are preserved — the cached-auth file IS accessible at subprocess time. So the only blocker is the pre-flight guard's acceptance set.

**Announcement family in `_auth.py`:** Three members today, all auth-coupled, all in the rule's canonical implementation list:
- `_announced_implicit_no_api_key` + `announce_implicit_no_api_key()` (#95 US-002).
- `_announced_call_anthropic_deprecation` + `announce_call_anthropic_deprecation()` (#144 US-007).
- `_announced_auto_codex_harness` + `announce_auto_codex_harness()` (#151 US-002 / DEC-007).

A new ChatGPT-login announcement would be the fourth family member, same shape: module-level `bool` flag + `Final[str]` constant + public helper. Likely needed to surface "Accepted Codex ChatGPT-login auth from `~/.codex/auth.json`" once per process.

**Tests:** `tests/test_providers_auth.py::TestCheckCodexAuth` (lines 897-996) covers the existing strict-OR. `TestAnnounceAutoCodexHarness` covers the existing announcement shape. `tests/test_codex_harness.py` already exercises `_detect_auth_source` including CODEX_HOME-based and default `~/.codex` paths.

**No existing `auth_mode` / `chatgpt` references in clauditor code.** The detection-by-file-existence in `CodexHarness._detect_auth_source` is the only existing file-touch, and it doesn't open/parse the JSON.

### `~/.codex/auth.json` schema (verified against `openai/codex` source + live file)

Canonical schema from `codex-rs/login/src/auth/storage.rs::AuthDotJson`:

```
{
  "auth_mode":      "chatgpt" | "apikey" | "chatgptAuthTokens" | "agentIdentity" | null,
  "OPENAI_API_KEY": str | null,
  "tokens": {                            // populated for chatgpt mode
    "id_token":      str (JWT),
    "access_token":  str (JWT),
    "refresh_token": str (opaque),
    "account_id":    str | null
  } | null,
  "last_refresh":   ISO-8601 datetime str | null,
  "agent_identity": str (JWT) | null    // separate identity flow
}
```

- **File location:** `$CODEX_HOME/auth.json`, default `~/.codex/auth.json`. The `CODEX_HOME` env var override is authoritative — any clauditor code reading the file must consult `os.environ.get("CODEX_HOME", ...)`.
- **Permissions:** Written `0o600`. Same-user readable; safe to `Path.exists()` and parse.
- **Storage backend can be keyring instead of file** — controlled by `AuthCredentialsStoreMode` config. In keyring mode the file may be absent even when logged in. Pure file-parse strategy false-negatives this case.
- **`auth_mode` enum values:** Load-bearing for us are `"chatgpt"` and `"apikey"`. `"chatgptAuthTokens"` is OpenAI-internal/unstable. `"agentIdentity"` is a separate flow.
- **Ephemeral token state:** `tokens.access_token` is a JWT expiring ~1h after `iat`; `refresh_token` rotates server-side. Codex itself handles refresh transparently and produces crisp error messages on revoked/expired refresh tokens (the four `RefreshTokenFailedReason` variants — "Please log out and sign in again"). So **file presence + valid `auth_mode` is a strong signal even without token freshness checking** — codex catches the stale case downstream with a clear message.
- **Security:** Contents include refresh / access / id tokens and (in apikey mode) the raw API key. Reading the file to inspect `auth_mode` is safe; **never log or surface `tokens.*` or `OPENAI_API_KEY` content** in stderr / sidecars / `harness_metadata`.

### Conventions / rules consulted

**Hard-applies — drive design:**

1. **`precall-env-validation.md`** — auth-missing exception class is a **direct subclass of `Exception`** (sibling, not subclass of other auth-missing classes). Routes to **exit 2** via the CLI's `except` ladder. Pure helper: no stderr, no `sys.exit`. CLI wrapper owns I/O. Open design question: do we keep using `CodexAuthMissingError` (extend acceptance set) or introduce a new sibling class? Strong precedent: `check_any_auth_available` extended its acceptance set without introducing a new class. Likely outcome: stay with `CodexAuthMissingError`, just expand the acceptance set + update the error template.

2. **`multi-provider-dispatch.md`** — Codex is a **harness axis**, NOT a provider. `check_provider_auth` does NOT route Codex (DEC-010 of `#151`). The CLI seam directly calls `check_codex_auth` when `harness == "codex"`. **#175 does NOT touch `check_provider_auth`'s dispatcher.**

3. **`centralized-sdk-call.md` — implicit-coupling announcement family.** If we want to surface "ChatGPT-login auth accepted" to surprised users (parallel to `announce_auto_codex_harness`), a fourth announcement-family member is the canonical shape: module-level `_announced_*: bool` flag + `Final[str]` constant + public helper. Reset mechanism = `monkeypatch.setattr(..., False)`. Anchor: `_providers/_auth.py`.

4. **`spec-cli-precedence.md`** — `harness` resolution (CLI > env > spec > auto-PATH) already in place from `#151`. `#175` does NOT touch resolution; it only widens the auth-acceptance set inside `check_codex_auth`.

**Apply — shape implementation:**

5. **`pure-compute-vs-io-split.md`** — new helpers must be pure: read file / probe PATH, return bool/string, never print stderr. Caller owns I/O. The announcement helper is the sole exception (and matches the family's documented "implicit-coupling announcement" exception).

6. **`stream-json-schema.md` — defensive-read posture.** If parsing `auth.json`, every field tolerated-if-missing, every malformed branch degrades to "no signal" rather than raise. Use `.get("auth_mode") != "chatgpt"` style; never crash on malformed JSON; treat the whole file as untrusted third-party shape.

7. **`permissive-parser-strict-validator.md`** — if file-parse goes ahead, split into permissive parser (`_parse_codex_auth_json(path) -> dict | None`) + strict validator (`_is_chatgpt_login_acceptable(parsed) -> bool`). Both pure. The parser never raises; the validator returns bool, not raises.

8. **`non-mutating-scrub.md`** — if we ever serialize parsed `auth.json` (e.g. for diagnostics / harness_metadata), scrub `tokens.*` and `OPENAI_API_KEY` content to redacted placeholders. Returns new dict, never mutates. **Better: don't serialize at all.** The `auth_mode` string is the only field we need.

9. **`path-validation.md`** — reading `~/.codex/auth.json`. The rule's "When this rule does NOT apply" section covers HOME-owned files, but we still follow `resolve(strict=False)` + `is_file()` discipline before opening to avoid surprises with symlinks or stale paths. Honoring `CODEX_HOME` widens the input surface — apply the rule's recipe to the resolved path.

10. **`test-infra-shutil-which-coupling.md`** — if we add a `_codex_cli_is_available()` branch via `shutil.which("codex")`, audit `tests/conftest.py` for autouse `shutil.which` pins. The existing `_force_api_transport_in_tests` autouse patches `shutil.which → None` (#86). The `#151` harness work added a parallel `CLAUDITOR_HARNESS=claude-code` env-pin at a higher precedence layer to short-circuit harness PATH lookups. We will likely need an analogous pin to short-circuit the new auth-PATH lookup (or a deliberate decision that the autouse `which → None` correctly forces the env-var branch in tests).

11. **`llm-cli-exit-code-taxonomy.md`** — auth-missing routes to exit 2. No change from current behavior.

12. **`back-compat-shim-discipline.md` Patterns 1 + 3** — if a new flag/helper lands, mutable flag NOT re-exported from any shim; test patches target canonical location; deferred imports if needed.

**Mention briefly:**

13. **`constant-with-type-info.md`** — only if we maintain a typed `auth.json` schema in clauditor (probably overkill — defensive `.get()` is enough).

14. **`pre-llm-contract-hard-validate.md`** — N/A; no LLM output involved.

15. **`monotonic-time-indirection.md`** — N/A; no duration tracking.

**No `workflow-project.md` found anywhere in repo or `~/.claude/`.** No project-specific super-plan additions.

### Proposed scope

A small, well-precedented change. The shape of the fix is well-bounded by the precedents from `#95` (Anthropic relaxed guard) and `#149` (codex harness `_detect_auth_source` already does file-existence detection for the observability axis). Three implementation paths are open; the planner must choose one before stories can be written.

**Implementation paths (mutually exclusive — pick one):**

- **Path A — Parse `~/.codex/auth.json`:** Read file, accept if `auth_mode in {"chatgpt", "apikey"}` AND the relevant token/key field is non-empty. Honor `CODEX_HOME`. Crisp errors; tied to codex's serde schema. Misses keyring-backend users.
- **Path B — Trust `codex` on PATH:** `shutil.which("codex") is not None` → accept. Pure mirror of `check_any_auth_available`'s Anthropic shape. Zero schema-drift risk. False-positive: `codex` installed but never logged in passes pre-flight, fails downstream with `codex`'s own (crisp) auth error.
- **Path C — Hybrid:** PATH check AS WELL AS env-var, plus file-existence as a tie-breaker for the error message detail. Both signals fail-open into the same accept-or-reject decision; the announcement names which signal won.

The Anthropic precedent (`#95`) chose pure-CLI-on-PATH (Path B). That's the strongest precedent for symmetry. Path A has a cost (binding to codex's serde shape) that doesn't pull its weight: codex itself produces crisp "log out and sign in again" messages downstream when a stale `auth.json` is present, so the pre-flight doesn't earn much by trying to validate the file ourselves.

### Open questions (for refinement)

1. **Which implementation path: A, B, or C?** (Recommended: B — mirrors `#95`; minimal new surface; honest about deferring to codex's own auth UX.)
2. **One-shot announcement when the new branch fires?** Likely yes — surfaces the implicit decision to surprised users, parallel to the three existing auth-coupled announcements.
3. **Stay with `CodexAuthMissingError`, or new sibling class?** Strong precedent says stay (just expand the acceptance set). Confirm.
4. **Update the runner's `apiKeySource` stderr** so the forward-declared `"cached"` source value (DEC-017 of `#149`) finally fires when ChatGPT-login is the accepted path? Plausibly out-of-scope for `#175`, but adjacent enough to bundle.
5. **Test-infra coupling:** if Path B/C, what's the autouse pin shape? Add `CODEX_API_KEY` env-pin in `conftest.py`, or rely on the existing `which → None` patch to force the env-var branch in tests?
6. **Honor `CODEX_HOME` if Path A/C touches the file?** (Recommended: yes, mirror codex's own behavior.)

---

## Architecture Review

| Area | Rating | Summary |
|---|---|---|
| Security | **pass** | PATH-attack surface is pre-existing and inherited from `_claude_cli_is_available` (`#95`). `_detect_auth_source` file probe is `os.path.isfile`-only, no shell, no path/token leak in stderr. Announcement text stays static `Final[str]` (no user-controlled interpolation). |
| Observability | **pass with note** | Reviewer flagged the codex-side `InvokeResult.api_key_source` hardcoded `None` at `_codex.py:1179` as a structural asymmetry vs claude-code (`_claude_code.py:792`). On closer inspection this is intentional separation of channels: codex uses `harness_metadata["auth_source"]` (set in `_detect_auth_source`) and a dedicated stderr line `clauditor.runner: codex auth=<source>` at `_codex.py:719`. Both already wire `cached` correctly — proven by `tests/test_codex_harness.py:1208-1222`. The forward-declared `cached` value finally reaches production users once `#175` widens `check_codex_auth`. Typed-field symmetry on `InvokeResult.api_key_source` is out of scope (DEC-007). |
| Testing strategy | **pass** | Conftest's autouse `_force_api_transport_in_tests` patches `shutil.which → None` globally; this is the right default (forces env-var branch in tests). The new PATH-branch tests follow the established local-override pattern from `tests/test_resolve_harness.py:24-44` — no conftest changes needed. New test surfaces: ~5 cases in `TestCheckCodexAuth`, 7-8 cases in new `TestAnnounceCodexCliOnPath`, one CLI end-to-end test for the `cached` source-label firing. |

No project-specific review areas (no `workflow-project.md` found). Data-model, API-design, and migration reviews are N/A — this is a pure helper-internal acceptance-set widening with no on-disk schema changes.

---

## Refinement Log

### DEC-001 — Path B: `shutil.which("codex")` extends the acceptance set

Extend `check_codex_auth` to a three-branch strict-OR: `CODEX_API_KEY` set OR `OPENAI_API_KEY` set OR `shutil.which("codex") is not None`. Mirror of `check_any_auth_available`'s Anthropic shape from `#95` DEC-001 / DEC-009. No `auth.json` parse — trust the CLI binary on PATH; defer auth verification to codex itself, which produces crisp `"Please log out and sign in again"` errors downstream for stale tokens.

### DEC-002 — Reuse `CodexAuthMissingError`; expand template

Keep the existing exception class. Update `_CODEX_AUTH_MISSING_TEMPLATE` to mention the new acceptance path ("install the `codex` CLI" as a third option). Preserve the three existing durable substrings (`CODEX_API_KEY`, `OPENAI_API_KEY`, `platform.openai.com`) so pre-`#175` tests pinning them still pass. Mirrors `#95`'s decision to extend `_AUTH_MISSING_TEMPLATE` without introducing a new class.

### DEC-003 — Add fourth announcement-family member

Add a new auth-coupled member to the implicit-coupling announcement family documented in `.claude/rules/centralized-sdk-call.md`:
- Module-level flag: `_announced_codex_cli_on_path: bool = False` in `_providers/_auth.py`.
- Constant: `_CODEX_CLI_ON_PATH_ANNOUNCEMENT: Final[str]` — static text, no interpolation.
- Public helper: `announce_codex_cli_on_path()` — print-and-flip; once-per-process.
- Re-export the public helper from `_providers/__init__.py` (mirror existing pattern for `announce_auto_codex_harness`).

Call site: inside `check_codex_auth`, fire ONLY when the PATH branch is the load-bearing acceptance signal (see DEC-009). Tests reset the flag via the standard `monkeypatch.setattr(..., False)` autouse pattern.

### DEC-004 — Three durable substrings for the new announcement

Pin in tests:
1. `"codex"` — names the CLI binary involved.
2. `"PATH"` — names the acceptance mechanism (so users grok why pre-flight passed when no env var is set).
3. `"~/.codex/auth.json"` — names where codex itself looks for credentials; helps a user who has codex on PATH but never logged in figure out what to do next.

Stylistic copy edits won't churn tests; verbatim assertions are forbidden. Same discipline as `_AUTO_CODEX_ANNOUNCEMENT`'s durable substring pinning.

### DEC-005 — Verify-and-test cached source-label wiring (forward-declared in DEC-017 of `#149`)

The wiring is already in place: `CodexHarness._detect_auth_source` at `_codex.py:1334-1372` correctly detects the cached file, populates `harness_metadata["auth_source"] = "cached"`, and `_codex.py:719` emits `clauditor.runner: codex auth=cached` to stderr. Proven unit-test-side by `tests/test_codex_harness.py::test_auth_source_cached_when_auth_json_exists` (line 1208-1222).

**What `#175` adds:** one new CLI-end-to-end integration test that exercises the full chain (CLI seam → `check_codex_auth` passes via PATH → harness invoke → stderr emits `codex auth=cached`) so the previously-unreachable production code path is regression-pinned. This makes the DEC-017 surface finally fire in production.

### DEC-006 — No conftest changes; tests of the new branch use local `shutil.which` override

The existing autouse `_force_api_transport_in_tests` patches `shutil.which → None` globally (and pins `CLAUDITOR_HARNESS=claude-code` to short-circuit harness resolution). This is the correct test default: it forces the env-var branch in `check_codex_auth` for tests that don't care about the new PATH branch.

Tests that exercise the new branch override locally:
```python
import clauditor._providers as _providers_mod
monkeypatch.setattr(
    _providers_mod.shutil, "which",
    lambda name: "/usr/bin/codex" if name == "codex" else None,
)
```
Proven pattern in `tests/test_resolve_harness.py:24-44`. No new autouse env-pin needed because the new branch is the *third* OR branch, not a precedence layer above PATH (per `.claude/rules/test-infra-shutil-which-coupling.md`'s "parallel pin at higher precedence" prescription, which applies when the new resolver consults the same default as the pre-existing one — here the new branch IS the PATH consumer, so the pin direction is reversed).

### DEC-007 — Out of scope: typed-field symmetry on `InvokeResult.api_key_source`

`CodexHarness.invoke` returns `InvokeResult(api_key_source=None, harness_metadata={"auth_source": "..."})` — the typed field is hardcoded `None`; the value lives in `harness_metadata`. Claude-code harness uses `InvokeResult.api_key_source` directly (`_claude_code.py:792`).

This asymmetry is **intentional separation of channels**, not a bug:
- `harness_metadata` is an extension point for harness-specific observability (`_codex.py` reasoning items, agent-identity flags, future codex-only fields).
- `api_key_source` is a typed top-level field surfaced into `SkillResult` and downstream readers (sidecar columns, audit JSON).

Migrating codex onto the typed field would touch sidecar schema, audit columns, and downstream test fixtures — out of `#175`'s scope. File a follow-up ticket if the symmetry matters; do not bundle here.

### DEC-008 — Do NOT parse `~/.codex/auth.json`

Path B's whole point is to avoid binding clauditor to codex's serde schema. The file is touched only by `_detect_auth_source` (file-existence check via `os.path.isfile`, no JSON parse, no field inspection). The pre-flight `check_codex_auth` does not open the file.

This means clauditor cannot distinguish "codex installed but never logged in" from "codex logged in" at pre-flight time — but codex itself produces a crisp `"Please log out and sign in again"` error downstream when a stale or absent auth blocks `codex exec`. The eventual user error message is fine; we just pay one extra round-trip vs Path A. Trade matches the `#95` Anthropic precedent exactly.

### DEC-009 — Announcement fires only when PATH branch is load-bearing

Inside `check_codex_auth`:
1. Check `_codex_api_key_is_set()` — if True, return None (silent).
2. Check `_openai_api_key_is_set()` — if True, return None (silent).
3. Check `_codex_cli_is_available()` — if True, fire `announce_codex_cli_on_path()`, then return None.
4. Else raise `CodexAuthMissingError`.

The announcement fires only when the PATH branch is the *load-bearing* acceptance signal. If an env var would have accepted anyway, no announcement (matches `.claude/rules/spec-cli-precedence.md`'s "implicit coupling at the operator-intent layers" subsection — announcements surface implicit decisions to surprised users, not explicit ones).

### DEC-010 — Pre-flight order: env vars FIRST, PATH SECOND

Same as DEC-009's resolution order. Beyond the announcement-suppression rationale, this also minimizes the surface where the new code can affect existing test posture: every test that already passes pre-flight via an env var sees the new branch as a no-op, because env-var checks short-circuit before PATH lookup. Tests that exercise the PATH branch must unset both env vars in their setup.

---

## Detailed Breakdown

### US-001 — Pure helper `_codex_cli_is_available` + 4th announcement-family member

**Description:** Add the foundational pieces for the Path B branch: a pure helper that checks `shutil.which("codex")`, plus a new auth-coupled announcement-family member (flag + constant + public helper). No call sites yet — those land in US-002. Lands in `_providers/_auth.py` and `_providers/__init__.py` only.

**Traces to:** DEC-003, DEC-004, DEC-009.

**Files:**
- `src/clauditor/_providers/_auth.py` — add `_codex_cli_is_available() -> bool` (parallel to `_claude_cli_is_available` at lines 200-211). Add module-level `_announced_codex_cli_on_path: bool = False` (parallel to `_announced_auto_codex_harness` at line 399). Add `_CODEX_CLI_ON_PATH_ANNOUNCEMENT: Final[str]` (parallel to `_AUTO_CODEX_ANNOUNCEMENT` at line 409). Add public `announce_codex_cli_on_path() -> None` print-and-flip helper (parallel to `announce_auto_codex_harness` at line 418).
- `src/clauditor/_providers/__init__.py` — re-export `announce_codex_cli_on_path` and `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` alongside the existing announcement-helper re-exports.

**TDD (write tests first):**
- `tests/test_providers_auth.py::TestCodexCliIsAvailable` (NEW class, 2 tests):
  - `test_returns_true_when_shutil_which_returns_path` — patches `_providers_mod.shutil.which` to return `"/usr/bin/codex"` for `"codex"`, asserts True.
  - `test_returns_false_when_shutil_which_returns_none` — patches to return None, asserts False (this is also the autouse default).
- `tests/test_providers_auth.py::TestAnnounceCodexCliOnPath` (NEW class, 7 tests; mirrors `TestAnnounceAutoCodexHarness` shape):
  - Autouse fixture resets `_announced_codex_cli_on_path` to False.
  - `test_first_call_emits_announcement` — asserts `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` in stderr.
  - `test_second_call_silent` — drain first, second call's stderr empty.
  - `test_constant_names_codex` — `"codex"` substring.
  - `test_constant_names_path` — `"PATH"` substring.
  - `test_constant_names_auth_json` — `"~/.codex/auth.json"` substring.
  - `test_constant_does_not_interpolate_values` — no `"{value"`, no `"sk-"`.
  - `test_autouse_resets_between_tests` (split into two paired tests).

**Acceptance:**
- `uv run ruff check src/ tests/` clean.
- `uv run pytest tests/test_providers_auth.py::TestCodexCliIsAvailable tests/test_providers_auth.py::TestAnnounceCodexCliOnPath -v` all pass.
- `_announced_codex_cli_on_path` is NOT re-exported through any back-compat shim per `.claude/rules/back-compat-shim-discipline.md` Pattern 1 (mutable globals).
- Public helper `announce_codex_cli_on_path` IS re-exported from `_providers/__init__.py` (functions are safe to re-export per Pattern 1).

**Done when:** Both new test classes pass; the new helpers exist at the canonical seam in `_providers/_auth.py`; the announcement helper is re-exported. No callers wired yet — that's US-002.

**Depends on:** none.

---

### US-002 — Extend `check_codex_auth` acceptance set + update error template

**Description:** Wire the helper + announcement from US-001 into `check_codex_auth`. Three-branch strict-OR per DEC-001 (env-codex OR env-openai OR codex-on-PATH). Update `_CODEX_AUTH_MISSING_TEMPLATE` to mention the third acceptance path while preserving the three existing durable substrings. The four CLI seams (`validate`, `grade`, `capture`, `run`) and the two pytest fixtures (`clauditor_runner`, `clauditor_spec`) inherit the change for free — no edits at their call sites.

**Traces to:** DEC-001, DEC-002, DEC-004, DEC-009, DEC-010.

**Files:**
- `src/clauditor/_providers/_auth.py` — modify `check_codex_auth` body (lines 483-532) per DEC-009/DEC-010 order. Update `_CODEX_AUTH_MISSING_TEMPLATE` (lines 459-466) to add a third bullet mentioning the codex CLI installation path. Preserve `CODEX_API_KEY`, `OPENAI_API_KEY`, `platform.openai.com` substrings.

**TDD (write tests first):**
- `tests/test_providers_auth.py::TestCheckCodexAuth` — add new tests, preserve all existing:
  - `test_codex_on_path_no_env_vars_passes` — both env vars deleted, `shutil.which("codex")` returns path → passes, announcement fires.
  - `test_codex_on_path_with_codex_env_silent` — `CODEX_API_KEY` set AND `which` returns path → passes, NO announcement (env branch short-circuits per DEC-010).
  - `test_codex_on_path_with_openai_env_silent` — same as above with `OPENAI_API_KEY`.
  - `test_codex_whitespace_env_on_path_passes_with_announcement` — both env vars whitespace-only, `which` returns path → passes, announcement fires (whitespace treated as unset per existing `_codex_api_key_is_set` / `_openai_api_key_is_set` shape).
  - `test_neither_env_nor_path_raises` — both env vars unset, `which` returns None → raises `CodexAuthMissingError`. Pin all three existing durable substrings.
  - `test_template_mentions_codex_cli_path` — single substring assertion on the new copy in `_CODEX_AUTH_MISSING_TEMPLATE` (NEW durable substring TBD; e.g. `"codex"` CLI mention).
- `tests/test_providers_auth.py::TestCheckCodexAuth::test_neither_key_set_raises_with_hint` (existing) — verify it still passes byte-identically for the three pre-existing substrings.

**Acceptance:**
- `uv run ruff check src/ tests/` clean.
- `uv run pytest tests/test_providers_auth.py::TestCheckCodexAuth -v` all pass (existing + new).
- `uv run pytest --cov=clauditor.\_providers.\_auth --cov-report=term-missing tests/test_providers_auth.py -v` shows the new branches covered.
- The four CLI seams (`cli/validate.py:191`, `cli/capture.py:159`, `cli/grade.py:421`, `cli/run.py:98`) are NOT edited — they inherit the change.
- Announcement fires ONLY when PATH branch is load-bearing (DEC-009). Verified by `test_codex_on_path_with_codex_env_silent` / `test_codex_on_path_with_openai_env_silent`.

**Done when:** All new and existing `TestCheckCodexAuth` tests pass; manual smoke test from this machine (`CODEX_API_KEY` unset, `OPENAI_API_KEY` unset, `~/.codex/auth.json` present with `auth_mode=chatgpt`, `codex` on PATH) — running `clauditor run <skill>` with `harness=codex` passes pre-flight (this reproduces the live failure case).

**Depends on:** US-001.

---

### US-003 — CLI end-to-end test for `cached` source-label production wiring (DEC-017 of `#149`)

**Description:** Add one CLI-end-to-end integration test that proves the forward-declared `cached` value finally fires in production after `#175`'s acceptance-set widening. Sets up a mock `~/.codex/auth.json`, no env vars, `shutil.which("codex")` returning a path, and a mocked codex subprocess that returns a clean stream. Asserts pre-flight passes, stderr emits `clauditor.runner: codex auth=cached`, exit code 0. This pins the previously-unreachable production code path against future regressions.

**Traces to:** DEC-005.

**Files:**
- `tests/test_codex_harness.py` OR `tests/test_cli_codex_e2e.py` (NEW file if it doesn't exist; see below) — add the integration test. The reviewer noted that `tests/test_codex_harness.py:1208-1222` already has `test_auth_source_cached_when_auth_json_exists` which exercises the harness-only chain. This new test exercises the CLI-seam-to-stderr chain.

**TDD (write test first):**
- `test_cli_run_with_codex_chatgpt_login_emits_cached_source_label` — full integration:
  1. `tmp_path` setup: skill file with valid SKILL.md + eval.json declaring `harness: codex`.
  2. `CODEX_HOME=tmp_path/.codex/` with `auth.json` (single-key JSON `{"auth_mode": "chatgpt"}` — content not parsed, only existence matters).
  3. `monkeypatch.delenv("CODEX_API_KEY")`, `monkeypatch.delenv("OPENAI_API_KEY")`.
  4. `monkeypatch.setattr(_providers_mod.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)` — override the autouse `which → None`.
  5. Mock `subprocess.Popen` for the codex harness with a fake stream returning a successful agent message.
  6. Invoke `cli/run.py::cmd_run` (or simplest CLI seam).
  7. Assert: exit code 0, `clauditor.runner: codex auth=cached` in `capsys.readouterr().err`, no `CodexAuthMissingError` raised.

**Acceptance:**
- `uv run ruff check tests/` clean.
- `uv run pytest tests/test_codex_harness.py::test_cli_run_with_codex_chatgpt_login_emits_cached_source_label -v` (or the new test path) passes.
- The test exercises the live failure case from the ticket repro steps — running it BEFORE US-001/US-002 land would fail with exit 2 + `CodexAuthMissingError`; running it AFTER passes with `auth=cached` on stderr.

**Done when:** New integration test passes; the chain `CLI seam → check_codex_auth → harness.invoke → _detect_auth_source → stderr emit` is regression-pinned.

**Depends on:** US-002.

---

### US-004 — Quality Gate (code review × 4 + CodeRabbit + project validation)

**Description:** Run the code-reviewer subagent four times across the full `#175` changeset, fixing every real bug each pass. Run CodeRabbit review if available. After all reviews, the project validation (`uv run ruff check src/ tests/` + `uv run pytest --cov=clauditor --cov-report=term-missing` with 80% coverage gate) must pass cleanly.

**Files:** Any file in the `#175` changeset; pass-specific.

**Acceptance:**
- Four code-reviewer passes complete; every real bug found in each pass is fixed before the next pass starts.
- CodeRabbit review (if available) addressed.
- `uv run ruff check src/ tests/` exits 0.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥ 80% coverage on touched modules.
- `uv run pytest tests/test_providers_auth.py tests/test_codex_harness.py tests/test_cli.py -v` all pass.
- No new test pollution (autouse fixtures intact, no leaked module-level state).

**Done when:** Validation suite passes cleanly after all review passes.

**Depends on:** US-001, US-002, US-003.

---

### US-005 — Patterns & Memory: update rules and docs for the new family member

**Description:** Update the canonical implementation list in `.claude/rules/centralized-sdk-call.md` "Implicit-coupling announcements — an emerging family" subsection to add the 4th `_auth.py`-resident member (`_announced_codex_cli_on_path` + `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` + `announce_codex_cli_on_path`). Document the DEC-017-fires-in-production transition. Optionally annotate `.claude/rules/precall-env-validation.md` if the relaxed-guard-on-codex shape adds a teaching not already covered.

**Traces to:** DEC-003, DEC-005.

**Files:**
- `.claude/rules/centralized-sdk-call.md` — add the new family member to the bulleted enumeration; cite `#175`'s US-001 as the canonical anchor; preserve byte-stable references to the three existing members.
- `.claude/rules/precall-env-validation.md` — small update if needed (likely just a citation that codex now has a CLI-on-PATH branch parallel to Anthropic's).
- Plan document (`plans/super/175-codex-chatgpt-login-auth.md`) — fill in the Beads Manifest section with epic + task IDs once devolved.
- README / docs — no changes (the CLI surface is unchanged; the announcement is the user-visible signal).

**Acceptance:**
- `.claude/rules/centralized-sdk-call.md` enumeration now lists 5 members of the announcement family (was 4, since the previous count includes 3 `_auth.py` + 1 `_anthropic.py` member; the new addition makes the `_auth.py` count 4 and total 5). Cross-references to test classes intact.
- `git grep '_announced_codex_cli_on_path'` finds the rule's canonical-implementation citation alongside the source-of-truth file.
- No memory pollution: no new memory files unless the user explicitly asks (per the project's CLAUDE.md / auto-memory guidance — most of what `#175` teaches is already in rules).

**Done when:** Rules updated; `git diff` shows only the additive rule citation; no orphaned references.

**Depends on:** US-004.

---

## Beads Manifest

- **Epic:** `clauditor-lacb` — 175: Codex ChatGPT-login auth
- **US-001:** `clauditor-v3b5` — Pure helper `_codex_cli_is_available` + 4th announcement-family member. **READY** (no blockers).
- **US-002:** `clauditor-0oe4` — Extend `check_codex_auth` acceptance set + update error template. Blocked by US-001.
- **US-003:** `clauditor-xf8p` — CLI end-to-end test for cached source-label production wiring. Blocked by US-002.
- **US-004 (Quality Gate):** `clauditor-fuyz` — Code-reviewer × 4 + CodeRabbit + project validation. Blocked by US-001, US-002, US-003.
- **US-005 (Patterns & Memory):** `clauditor-11zj` — Update rules for the new announcement family member. Blocked by US-004.

**Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/175-codex-chatgpt-login-auth`
**Branch:** `feature/175-codex-chatgpt-login-auth`
**Plan PR:** https://github.com/wjduenow/clauditor/pull/179

### Execution

1. Run Ralph: `/ralph-run`.
2. Monitor: `bd list --status=in_progress`.
3. After completion: `/closeout`.

---

## Session Notes

### Session 1 — 2026-05-11

- Created worktree at `/home/wesd/dev/worktrees/clauditor/feature/175-codex-chatgpt-login-auth` (plain `git worktree`; bark hit its 9-worktree cap from stale registrations).
- Ran four parallel research subagents (ticket analyst, codebase scout, convention checker, domain expert).
- Verified live failure on this machine: `/home/wesd/.codex/auth.json` has `auth_mode=chatgpt`, `OPENAI_API_KEY: null`, populated `tokens`.
- Discovery → user picked all four recommended scope answers: Path B + announcement + verify-cached-wiring + reuse `CodexAuthMissingError`.
- Ran focused architecture review (security + observability + testing). Security PASS. Observability flagged a perceived blocker on `InvokeResult.api_key_source` hardcoded `None` for codex — resolved via DEC-007 as intentional channel-separation (codex uses `harness_metadata["auth_source"]` not the typed field). Testing PASS.
- Refinement → 10 decisions captured (DEC-001 through DEC-010). All operator-intent choices confirmed; mechanical decisions baked.
- Detailing → 3 implementation stories (US-001/US-002/US-003) + Quality Gate (US-004) + Patterns & Memory (US-005). All right-sized for one context window each.
- Phase advance: discovery → architecture → refinement → detailing. Next: publish PR.

---

## Session Notes

### Session 1 — 2026-05-11

- Created worktree at `/home/wesd/dev/worktrees/clauditor/feature/175-codex-chatgpt-login-auth` (plain `git worktree`; bark hit its 9-worktree cap from stale registrations).
- Ran four parallel research subagents: ticket analyst, codebase scout, convention checker, domain expert (codex `auth.json` schema).
- Verified live failure mode on this machine: `/home/wesd/.codex/auth.json` has `auth_mode=chatgpt`, `OPENAI_API_KEY: null`, populated `tokens`. Codex itself works; clauditor pre-flight rejects.
- Key precedent: `CodexHarness._detect_auth_source` already detects file existence for observability/logging (DEC-017 of `#149`). The forward-declared `"cached"` source-name has never fired because the pre-flight blocks first — `#175` is what makes it reachable.
- Strong precedent for Path B (trust `codex` on PATH): `check_any_auth_available` (#95) did exactly this for the Anthropic side and the symmetry is structurally clean.
- Phase advance: discovery complete → awaiting scoping answers before architecture.
