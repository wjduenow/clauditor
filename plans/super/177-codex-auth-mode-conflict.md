# 177 — Codex auth-mode conflict (env var set, but `~/.codex/auth.json` in ChatGPT-login mode)

## Meta

- **Ticket:** [#177](https://github.com/wjduenow/clauditor/issues/177)
- **Phase:** published
- **PR:** https://github.com/wjduenow/clauditor/pull/181
- **Branch:** `feature/177-codex-auth-mode-conflict`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/177-codex-auth-mode-conflict`
- **Sibling tickets:** #175 (clauditor accepting ChatGPT-login as valid auth — merged plan), #143 (multi-provider/multi-harness epic, parent)
- **Sessions:** 1 (2026-05-11)

## Ticket Summary

When `codex login` (ChatGPT mode) has been run, `~/.codex/auth.json` carries
`auth_mode=chatgpt` and `OPENAI_API_KEY=null`. clauditor's `check_codex_auth`
accepts an operator's `OPENAI_API_KEY` (or `CODEX_API_KEY`) env var as valid
auth — pre-flight passes. But the codex subprocess **ignores the env var**
when auth.json is present in chatgpt mode and routes via ChatGPT, which
then rejects every model the harness asks for:

> `"The 'gpt-5-codex' model is not supported when using Codex with a ChatGPT account."`

The user sees this only after the subprocess fails, by which time:
- A grading run may have already spent Anthropic/OpenAI API tokens on Layer 2/3 calls.
- The error message names `gpt-5-codex` (the model), not the auth-mode mismatch (the root cause).
- The actionable next step (`codex login --with-api-key`) is absent from the error.

The auth surfaces are inconsistent: clauditor recognizes env vars
(`OPENAI_API_KEY`, `CODEX_API_KEY`), but the codex CLI only recognizes
auth state in `~/.codex/auth.json` — either ChatGPT login OR an API-key
login materialized via `codex login --with-api-key`.

## Discovery

### Codebase findings (from Codebase Scout)

- **`check_codex_auth`** lives at `src/clauditor/_providers/_auth.py:582-663`.
  Three-branch strict-OR per #175 DEC-001/DEC-010:
  1. `_codex_api_key_is_set()` — `CODEX_API_KEY` (whitespace-trimmed).
  2. `_openai_api_key_is_set()` — `OPENAI_API_KEY` (whitespace-trimmed).
  3. `_codex_cli_is_available()` — `shutil.which("codex") is not None`.
  Env-var branches short-circuit BEFORE PATH probe; announcement fires
  ONLY when PATH is the load-bearing signal. **Function does NOT parse
  `~/.codex/auth.json`** (#175 DEC-008 — explicit refusal).
- **`CodexAuthMissingError`** is a direct `Exception` subclass; sibling
  of `AnthropicAuthMissingError` / `OpenAIAuthMissingError`. CLI routes
  to exit 2 per `.claude/rules/llm-cli-exit-code-taxonomy.md`.
- **Announcement family entry** `_announced_codex_cli_on_path` +
  `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` (`Final[str]`, three durable
  substrings: `"codex"`, `"PATH"`, `"~/.codex/auth.json"`) fires from
  inside the PATH-accept branch only.
- **Subprocess-side error mapping** lives in
  `src/clauditor/_harnesses/_codex.py::_classify_codex_failure` (lines
  310-359). `_AUTH_PATTERNS` matches `"401"`, `"403"`, `"OPENAI_API_KEY"`,
  `"invalid api key"`. The chatgpt-mode rejection string ("is not
  supported when using Codex with a ChatGPT account") does NOT match,
  so the failure lands in `error_category="api"`. This is the
  silent-failure surface.
- **`CodexHarness._detect_auth_source`** (`_codex.py:1334-1372`) is the
  ONE place that already touches `~/.codex/auth.json` — but with
  `os.path.isfile()` existence-check only (no JSON parse), for
  observability/`harness_metadata["auth_source"]="cached"`.
- **CLI seams** that call `check_codex_auth`: `cli/validate.py:190`,
  `cli/grade.py:420`, `cli/capture.py:158`, `cli/run.py:96`. Plus two
  pytest fixture factories (`clauditor_runner` and `clauditor_spec`).
- **Tests** in `tests/test_providers_auth.py`:
  `TestCheckCodexAuth`, `TestAnnounceCodexCliOnPath`,
  `TestCheckCodexAuthPathBranch`. Autouse pin pattern resets the
  announcement flag via `monkeypatch.setattr(..., False)`.

### Convention-checker findings (binding constraints)

| Rule | Implication for #177 |
| --- | --- |
| `pure-compute-vs-io-split.md` | Auth helpers stay pure. Any new `auth.json` read is filesystem I/O — extract to a separate pure helper (`_parse_codex_auth_json -> dict \| None`); validation (`_is_chatgpt_login_only -> bool`) stays pure; `check_codex_auth` owns the file-read I/O at the orchestrator seam. |
| `precall-env-validation.md` | **Reuse `CodexAuthMissingError`**, extend the message template. Do NOT introduce a new sibling exception class — would force edits to all 4 CLI seams + 2 fixture factories. |
| `centralized-sdk-call.md` (announcement family) | If #177 adds a new acceptance/refusal branch with a one-shot stderr notice, it becomes the **8th** announcement-family member. Lives in `_providers/_auth.py` (auth-coupled). Same shape: module-level flag + `Final[str]` constant + public helper; reset via `monkeypatch.setattr(..., False)`. |
| `back-compat-shim-discipline.md` Pattern 1 | New mutable flag NOT re-exported from any shim. Public helper + `Final[str]` constant ARE safe to re-export. |
| `llm-cli-exit-code-taxonomy.md` | `CodexAuthMissingError` -> exit 2 (pre-call validation). No exit-code change. |
| `multi-provider-dispatch.md` | Codex is a **harness**, not a provider. `check_provider_auth` does NOT route Codex. File-detection lives in `check_codex_auth`, called directly when `harness=="codex"`. |
| `harness-protocol-shape.md` | Sibling-exception invariant already satisfied — no class changes needed. |
| `constant-with-type-info.md` | Only binds if #177 introduces a typed `auth.json` schema constant. If we keep parsing defensive (`.get("auth_mode")`), this rule does not apply. |
| `non-mutating-scrub.md` | Only binds if parsed auth.json content is serialized for diagnostics. Recommend NOT serializing — surface only the verdict (bool). |
| `plan-contradiction-stop.md` | If a worker discovers #175's `_codex_cli_is_available` / `announce_codex_cli_on_path` are missing or broken pre-implementation, STOP. |

### Load-bearing decisions from #175

| #175 DEC | Implication for #177 |
| --- | --- |
| **DEC-001** Path B: `shutil.which("codex")` extends acceptance | #177's file-detection layer would extend the order to: env-vars -> PATH -> file. Or replace one of those branches with a smarter check. |
| **DEC-002** Reuse `CodexAuthMissingError`; extend template | Locked. #177 extends the template again. |
| **DEC-008** **DO NOT parse `~/.codex/auth.json`** — trust the CLI; avoid schema binding | **This is the load-bearing refusal #177 may need to reverse or narrowly relax.** The DEC-008 rationale was "avoid binding clauditor to codex's serde". Any relaxation needs explicit justification. |
| **DEC-009** Announcement fires only when the branch is load-bearing | Carries forward. If #177 adds a new announcement, it fires only when that branch is the deciding factor. |
| **DEC-010** Pre-flight order: env vars FIRST, PATH SECOND | #177 extends to: env vars -> PATH -> file (or a different ordering depending on chosen fix). |

## Proposed Scope

The fix space breaks into orthogonal axes (each is a real choice):

1. **Where to detect the conflict** — pre-flight (before LLM-grader spends tokens) vs subprocess error mapping (after subprocess fails) vs both.
2. **What to detect** — explicit conflict (env var set AND auth.json says chatgpt) vs broad ("auth.json says chatgpt, period").
3. **DEC-008 relationship** — narrowly relax (parse one field, `.get("auth_mode")`, defensive) vs honor (no file parse, only subprocess mapping).
4. **Docs** — README + docs/ callouts, or code-only, or both.

Open scoping questions below drive these choices.

## Scoping decisions (Phase 1 answers)

- **Q1 — Detection layer: Both** (pre-flight + subprocess fallback).
  Pre-flight catches the common case before any LLM-grader API spend;
  subprocess mapping is defense-in-depth for cases where pre-flight is
  bypassed (auth.json deleted mid-run, sandboxed CI, future codex
  auth-mode values not yet enumerated).
- **Q2 — Refusal scope: Broad.** Refuse whenever `auth_mode == "chatgpt"`,
  regardless of env vars. **This reverses #175's PATH-accept branch for
  the ChatGPT-login-only case** (the case where `_codex_cli_is_available()`
  was the load-bearing signal and the user had no env var set). Rationale:
  ChatGPT-mode rejects every model the harness currently asks for
  (`gpt-5-codex`, `gpt-5`), so accepting it via the PATH branch was
  shipping a latent failure. Better to refuse pre-flight with a clear
  remediation than route every user through the subprocess rejection.
- **Q3 — Subprocess mapping: In scope.** `_classify_codex_failure`
  extended to recognize the chatgpt-rejection string and route it to a
  clearer auth-flavored category. Defense-in-depth alongside the pre-flight.

## Architecture Review (Phase 2)

### Rating summary

| Area | Rating | Key finding |
| --- | --- | --- |
| **Security / robustness** (auth.json read) | pass | Failure-open on parse failure; 1 MB cap; defensive `isinstance(str)` guard on `auth_mode`; never serialize parsed content. `CODEX_HOME` env override already honored by `_detect_auth_source` — mirror that resolution in the new helper. |
| **Pure / I/O split** (file read placement) | pass | Pure helper `_parse_codex_auth_json(path) -> dict \| None` does the I/O; pure classifier `_auth_mode_is_acceptable(parsed) -> bool` does the verdict; `check_codex_auth` orchestrates. Mirrors `.claude/rules/pure-compute-vs-io-split.md` 6th anchor (`_classify_result_message` / `_detect_interactive_hang`). |
| **Subprocess error mapping** (`_classify_codex_failure`) | pass | Add primary pattern `"not supported when using Codex with a ChatGPT account"` to `_AUTH_PATTERNS`; reuse `"auth"` category — no `Literal` extension, no sidecar schema bump. ~10 LOC of production, ~20 LOC of tests. |
| **Test churn** (PATH-branch flip) | concern | 3 tests in `TestCheckCodexAuthPathBranch` flip from accept→raise: `test_codex_on_path_no_env_vars_passes`, `test_codex_whitespace_env_on_path_passes_with_announcement`, `test_announcement_one_shot_across_calls`. Plus new tests for: chatgpt-refusal, apikey-accept, missing-file-fall-through, malformed-file-fall-through, `CODEX_HOME`-override. |
| **Error template / announcement copy** | concern | `_CODEX_AUTH_MISSING_TEMPLATE` bullet 3 ("codex CLI installed on PATH and authenticated via ChatGPT login") becomes false. `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` phrase "typically the ChatGPT-login flow" becomes misleading. Both need rewrites; durable substrings can be preserved. |
| **#175 DEC contradictions** | concern | 6 DECs from #175 superseded: **DEC-001** (PATH-accept unconditional), **DEC-002** (codex CLI as third path), **DEC-003** (announcement family member), **DEC-004** (announcement substring justification), **DEC-008** (do NOT parse auth.json — **directly reversed**), **DEC-009** (announcement fires when PATH branch is load-bearing). Refinement log must cross-link each. |
| **`.claude/rules/centralized-sdk-call.md` refresh** | concern | Per `.claude/rules/rule-refresh-vs-delete.md`: refresh in place (pattern survives, context narrows). Lines 541-569 — update the announcement family member's fire condition and substring justification. Patterns & Memory story owns this edit. |
| **Public-API churn** (announcement re-exports) | pass | `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` (Final[str]) and `announce_codex_cli_on_path()` (function) are re-exported from `_providers/__init__.py`. Re-wording the constant text is benign — three durable substrings (`"codex"`, `"PATH"`, `"~/.codex/auth.json"`) are still preserved. No deprecation shim needed. |
| **Auth-mode value enumeration** | concern | `auth_mode` values per codex source: `"chatgpt"`, `"apikey"`, `"chatgptAuthTokens"`, `"agentIdentity"`, `null`. Issue covers `"chatgpt"` only — should `"chatgptAuthTokens"` ALSO refuse? Refinement-phase decision (see Q4). |
| **Pytest fixture impact** | concern | Two fixtures (`clauditor_runner`, `clauditor_spec`) call `check_codex_auth`. Tests that set `OPENAI_API_KEY` + run a fixture-codex flow will now also need to mock out auth.json (or rely on `tmp_path` `HOME` override) to avoid pre-flight refusal. The autouse `monkeypatch.setenv("CLAUDITOR_HARNESS", "claude-code")` from `tests/conftest.py:754` already steers most tests away from codex; this should keep churn contained. |
| **Performance** | pass | One extra `Path.stat()` + JSON parse per pre-flight invocation. Negligible (auth.json < 4 KB). |
| **Data model** (sidecar schema) | pass | No schema bump. Reusing `"auth"` category in `_classify_codex_failure` avoids any `MAX_SCHEMA_VERSION` change. Pre-flight refusals don't write sidecars (exit 2 before workspace allocation). |
| **Observability** | pass | Existing `CodexHarness._detect_auth_source` keeps the `"cached"` source label for harness observability. Pre-flight failure routes through the same `CodexAuthMissingError` -> exit 2 -> stderr surface as today. |
| **Documentation** | pass | Repo greps find no current Codex auth setup section in README or `docs/`. The error template IS the user-facing surface; rewriting it covers the doc gap. CHANGELOG entry is the only additional doc artifact. |
| **`plan-contradiction-stop.md` pre-check** | pass | #175 implementation verified live in `_auth.py` (`_announced_codex_cli_on_path` at line 489, `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` at 502, `check_codex_auth` at 582). No worker-stop preconditions broken. |

### Blockers

None. All findings are tracked-concern level: each maps to a story slot in Phase 4, not a "halt before refinement" condition.

### Concerns to resolve in Phase 3 (Refinement)

1. **Auth-mode value enumeration scope** — only `"chatgpt"`, or also `"chatgptAuthTokens"`, or any non-`"apikey"` value?
2. **Failure-open precise semantics** — when `auth.json` exists but parses to `None` (malformed), fall through to env-var/PATH checks, OR refuse outright? (Subagent 1 recommends failure-open.)
3. **Error message wording for the chatgpt-mode refusal case** — does it share `_CODEX_AUTH_MISSING_TEMPLATE` (reworded), or does the chatgpt-mode refusal warrant a SECOND template emphasizing "you have auth, it's just the wrong mode — run `codex login --with-api-key`"?
4. **Announcement member future** — `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` reword text + whether a new sibling announcement is needed when the PATH branch accepts (e.g. "codex on PATH, auth.json says apikey or absent — pre-flight accepting").

## Refinement Log

### Decisions

- **DEC-001 — Detection layer: both pre-flight + subprocess fallback.**
  Pre-flight (`check_codex_auth`) parses `auth.json` and refuses on `auth_mode == "chatgpt"` BEFORE any LLM-grader API spend; subprocess fallback (`_classify_codex_failure`) recognizes the chatgpt-rejection string for cases where pre-flight is bypassed (auth.json deleted mid-run, sandboxed CI, future codex versions). Phase 1 Q1 = Both.
- **DEC-002 — Refusal scope: broad.** Refuse whenever `auth.json` declares
  `auth_mode == "chatgpt"`, regardless of env vars. **Reverses #175 DEC-001's
  PATH-accept of ChatGPT-login** (the case where `_codex_cli_is_available()`
  was load-bearing and no env var was set). Rationale: ChatGPT-mode rejects
  every model the harness currently asks for (`gpt-5-codex`, `gpt-5`), so
  accepting it via PATH shipped a latent failure. Phase 1 Q2 = Broad.
- **DEC-003 — Subprocess mapping in scope here.** `_classify_codex_failure`
  gains a primary `_AUTH_PATTERNS` entry; bundled with the pre-flight work
  rather than split to a follow-up. Defense-in-depth. Phase 1 Q3 = In scope.
- **DEC-004 — Auth-mode value enumeration: refuse only `"chatgpt"` exactly.**
  Conservative. `"chatgptAuthTokens"`, `"agentIdentity"`, and any future value
  pass pre-flight; if they later prove to share the model-rejection behavior,
  add them in a follow-up. Phase 3 Q1 = Only `"chatgpt"`.
- **DEC-005 — Parse failure: failure-open.** When `_parse_codex_auth_json`
  returns `None` (missing file, `JSONDecodeError`, oversize, non-dict root,
  IO error), pre-flight falls through to existing env-var / PATH checks.
  No opinion -> defer to other auth signals. Matches the defensive read-shape
  in `.claude/rules/stream-json-schema.md`. Phase 3 Q2 = Failure-open.
- **DEC-006 — Two error templates.** `_CODEX_AUTH_MISSING_TEMPLATE` stays
  for the "no auth path at all" case (bullet 3 reworded to drop the
  ChatGPT-login claim). New `_CODEX_AUTH_CHATGPT_MODE_TEMPLATE` for the
  "auth.json says chatgpt" refusal case, emphasizing `codex login
  --with-api-key`. Distinct messages for distinct failure modes. Phase 3 Q3 = Two.
- **DEC-007 — Announcement: keep, reword, no new member.**
  `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` survives the family with reworded body
  (drop "typically the ChatGPT-login flow"); fires under the narrowed
  PATH-accept condition (auth.json absent OR `auth_mode != "chatgpt"`).
  Three durable substrings preserved (`"codex"`, `"PATH"`,
  `"~/.codex/auth.json"`). Refusals raise `CodexAuthMissingError` — the
  exception is the user signal; no announcement on refusal. Phase 3 Q4 = Keep+reword.
- **DEC-008 — Pure / I/O split.** New helpers:
  - `_parse_codex_auth_json(path: Path) -> dict | None` — I/O, defensive.
  - `_auth_mode_is_acceptable(parsed: dict | None) -> bool` — pure verdict
    (`True` when parsed is `None`, or parsed is dict and `auth_mode` is
    missing / not a string / a string other than `"chatgpt"`; `False` only
    when parsed is dict AND `isinstance(auth_mode, str)` AND
    `auth_mode == "chatgpt"`).
  - `_codex_auth_json_path() -> Path` — resolves `$CODEX_HOME/auth.json`
    if `CODEX_HOME` is set (whitespace-trimmed non-empty), else
    `~/.codex/auth.json` via `Path.home()`.
  `check_codex_auth` is the orchestrator that owns the I/O call.
  Mirrors `.claude/rules/pure-compute-vs-io-split.md` Sixth anchor.
- **DEC-009 — Honor `CODEX_HOME` env var.** Auth.json path resolution
  consults `$CODEX_HOME/auth.json` first, falls back to `~/.codex/auth.json`.
  Mirrors the codex CLI's own behavior and the existing
  `CodexHarness._detect_auth_source` resolution.
- **DEC-010 — Reuse `CodexAuthMissingError`.** Per `.claude/rules/precall-env-validation.md`,
  one auth-missing class per harness. The two templates feed the same
  exception class; no new class. CLI seams unchanged.
- **DEC-011 — Reuse `"auth"` error category** in `_classify_codex_failure`.
  No `Literal["rate_limit", "auth", "api"]` extension; no sidecar schema
  bump. Per `.claude/rules/json-schema-version.md`, new Literal values
  inside an existing field don't bump the schema version — only new fields
  do. Audit aggregation passes through the category string unchanged.
- **DEC-012 — 1 MB file-size cap** on `auth.json` read. Real codex
  auth.json files are < 4 KB; cap defends against symlink-bomb / accidental
  oversize. Exceeding the cap returns `None` (failure-open per DEC-005).
- **DEC-013 — Defensive `isinstance(auth_mode, str)` guard.** Per
  `.claude/rules/constant-with-type-info.md`, a JSON value carrying
  `"auth_mode": true` would otherwise enter the string comparison with
  unsafe semantics. Explicit `isinstance(value, str)` (not bool — though
  bool-vs-str doesn't share Python's bool-vs-int foot-gun, the discipline
  carries forward) before `== "chatgpt"`.
- **DEC-014 — Never serialize parsed auth.json downstream.** No
  sidecar field, no log line, no error-message interpolation contains
  parsed content. Tokens, account ids, refresh tokens stay in-process.
  Error messages name the file PATH only, never its body.

### Cross-links to #175 DEC contradictions

The Broad refusal in #177 directly supersedes six decisions from
`plans/super/175-codex-chatgpt-login-auth.md`:

| #175 DEC | What it said | #177 supersedes with |
| --- | --- | --- |
| **DEC-001** | Extend `check_codex_auth` to accept `shutil.which("codex")` unconditionally | DEC-002 above — narrow PATH-accept to non-chatgpt cases |
| **DEC-002** | Reuse `CodexAuthMissingError`; expand template to name codex CLI as third acceptance path | DEC-006 + DEC-010 — bullet 3 reworded; new chatgpt-mode template; same exception class |
| **DEC-003** | Add 7th announcement-family member | DEC-007 — member survives, body reworded for narrowed fire condition |
| **DEC-004** | Pin durable substrings `"codex"`, `"PATH"`, `"~/.codex/auth.json"` | DEC-007 — substrings preserved verbatim |
| **DEC-008** | Do NOT parse `~/.codex/auth.json` — avoid binding clauditor to codex's serde | DEC-008 + DEC-012 + DEC-013 — narrowly parse one field (`auth_mode`) with defensive `.get()` + bool-style guard + 1 MB cap; the schema-binding cost is one string field, the avoided cost is wasted API spend |
| **DEC-009** | Announcement fires only when PATH branch is load-bearing | DEC-002 + DEC-007 — fire surface shrinks (chatgpt-mode now refused), but the "load-bearing" predicate is unchanged in shape |

The #175 plan doc stays historical (not rewritten). This refinement log
is where the contradiction trail lives.

### Notes

- **`.claude/rules/centralized-sdk-call.md` refresh:** Lines 541-569
  (the `_announced_codex_cli_on_path` family-member section) need an
  in-place refresh per `.claude/rules/rule-refresh-vs-delete.md` —
  the pattern survives, the fire condition narrows. Owned by Patterns &
  Memory story.
- **Pytest fixture impact:** `clauditor_runner` and `clauditor_spec` both
  call `check_codex_auth`. The autouse `monkeypatch.setenv("CLAUDITOR_HARNESS",
  "claude-code")` at `tests/conftest.py:754` already steers most tests
  away from the codex path, so churn is contained. Tests that exercise
  codex specifically may need a `monkeypatch.setattr(Path, "home", ...)`
  pointing at a `tmp_path` with no `.codex/auth.json` (or a fixture-staged
  apikey-mode one).

## Detailed Breakdown (Stories)

### US-001 — Pure helpers for auth.json parse + verdict + path resolution

**Description.** Add three pure helpers in `src/clauditor/_providers/_auth.py`:
- `_codex_auth_json_path() -> Path` — resolves `$CODEX_HOME/auth.json` when
  `CODEX_HOME` is set to a non-empty (whitespace-trimmed) string, else
  `Path.home() / ".codex" / "auth.json"`.
- `_parse_codex_auth_json(path: Path) -> dict | None` — defensive read:
  returns `None` on file-not-found, `OSError`, oversize (>1 MB),
  `json.JSONDecodeError`, non-`dict` root, or unicode-decode failure.
  Never raises. UTF-8 strict decode.
- `_auth_mode_is_acceptable(parsed: dict | None) -> bool` — pure verdict:
  returns `True` when `parsed is None`, or when `parsed.get("auth_mode")`
  is missing / not a `str` / a `str` other than `"chatgpt"`. Returns
  `False` only when `isinstance(auth_mode, str) and auth_mode == "chatgpt"`.

**Traces to.** DEC-004, DEC-005, DEC-008, DEC-009, DEC-012, DEC-013, DEC-014.

**Acceptance Criteria.**
- All three helpers exist and are NOT re-exported from
  `_providers/__init__.py` (per `.claude/rules/back-compat-shim-discipline.md`
  Pattern 1; they're private I/O helpers).
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with
  ≥80% coverage on the new code (project gate).
- `uv run ruff check src/ tests/` clean.

**Done when.** New `tests/test_providers_auth.py::TestCodexAuthJsonHelpers`
covers: happy-path apikey parse, happy-path chatgpt parse,
file-not-found returns None, malformed JSON returns None, oversize file
returns None, non-dict root returns None, `auth_mode` missing returns
True (acceptable), `auth_mode = true` (JSON bool) returns True
(defensive isinstance guard), `auth_mode = "chatgpt"` returns False,
`auth_mode = "apikey"` returns True, `CODEX_HOME` env var override
resolved, `CODEX_HOME` whitespace-only ignored.

**Files.**
- `src/clauditor/_providers/_auth.py` — new helpers (private, leading `_`).
- `tests/test_providers_auth.py` — new test class.

**Depends on.** None.

**TDD.** Yes. Write the 12 test cases above first; implement helpers to pass.

---

### US-002 — Rework error templates: bullet-3 fix + new chatgpt-mode template

**Description.** In `src/clauditor/_providers/_auth.py`:
- Rework `_CODEX_AUTH_MISSING_TEMPLATE` bullet 3 to drop the
  ChatGPT-login claim. New wording (preserve durable substrings
  `CODEX_API_KEY`, `OPENAI_API_KEY`, `platform.openai.com`, `codex CLI`):
  `"3. codex CLI installed on PATH and authenticated in API-key mode\n
       (run: codex login --with-api-key)\n"`.
- Add `_CODEX_AUTH_CHATGPT_MODE_TEMPLATE` (`Final[str]`) for the
  refusal-on-chatgpt-mode case. Durable substrings:
  `"ChatGPT"`, `"~/.codex/auth.json"`, `"codex login --with-api-key"`,
  `"{cmd_name}"`. Body: explains the auth-mode mismatch, names the file,
  gives the one-line remediation.

**Traces to.** DEC-002, DEC-006, DEC-010.

**Acceptance Criteria.**
- Both templates compile as `Final[str]`.
- Existing `TestCheckCodexAuth` tests for `_CODEX_AUTH_MISSING_TEMPLATE`
  substrings still pass (durable substrings preserved).
- New `TestCodexAuthChatGPTModeTemplate` asserts the new template's
  durable substrings AND `{cmd_name}` interpolation works.
- `uv run pytest --cov=clauditor` passes; `uv run ruff check` clean.

**Done when.** Both constants exist with correct content and a test
class for each asserts the durable substrings.

**Files.**
- `src/clauditor/_providers/_auth.py` — both `Final[str]` constants.
- `tests/test_providers_auth.py` — new `TestCodexAuthChatGPTModeTemplate`
  class; existing `TestCodexAuthMissingTemplate*` tests stay green.

**Depends on.** None.

**TDD.** Partial. Write substring assertions first; populate the
constants to satisfy.

---

### US-003 — Wire pre-flight chatgpt-mode refusal into `check_codex_auth`

**Description.** Extend `check_codex_auth(cmd_name)` to consult
`_parse_codex_auth_json(_codex_auth_json_path())` and refuse via
`CodexAuthMissingError(_CODEX_AUTH_CHATGPT_MODE_TEMPLATE.format(cmd_name=cmd_name))`
when `not _auth_mode_is_acceptable(parsed)`. Insertion point per DEC-002:
the chatgpt-mode check fires BEFORE all other branches (env vars + PATH).
A user with `OPENAI_API_KEY` set AND auth.json in chatgpt mode is still
refused, per the broad-refusal decision.

**Traces to.** DEC-001, DEC-002, DEC-005, DEC-006, DEC-010.

**Acceptance Criteria.**
- `check_codex_auth` raises `CodexAuthMissingError` with the chatgpt-mode
  template when auth.json says `auth_mode == "chatgpt"` — regardless of
  env vars or PATH state.
- Pre-flight passes (returns None) when:
  - auth.json is missing AND env var is set OR codex on PATH;
  - auth.json says `auth_mode == "apikey"` AND env var is set;
  - auth.json says `auth_mode == "apikey"` AND codex on PATH (no env var);
  - auth.json is malformed AND env var is set (failure-open per DEC-005);
  - auth.json is malformed AND codex on PATH (failure-open).
- The four CLI seams (`cli/validate.py`, `cli/grade.py`, `cli/capture.py`,
  `cli/run.py`) and the two fixture factories continue to catch
  `CodexAuthMissingError` and exit 2 — no seam edits needed.

**Done when.** A new test class `TestCheckCodexAuthChatGPTModeRefusal`
covers the eight branches above. Existing `TestCheckCodexAuth` and
`TestCheckCodexAuthPathBranch` tests update per US-004.

**Files.**
- `src/clauditor/_providers/_auth.py` — extend `check_codex_auth`.
- `tests/test_providers_auth.py` — new `TestCheckCodexAuthChatGPTModeRefusal`.

**Depends on.** US-001, US-002.

**TDD.** Yes. Write the eight branch tests first; implement to satisfy.

---

### US-004 — Flip PATH-branch tests + reword `_CODEX_CLI_ON_PATH_ANNOUNCEMENT`

**Description.** Two coupled changes:
1. Reword `_CODEX_CLI_ON_PATH_ANNOUNCEMENT` (`Final[str]`) body to drop
   the "typically the ChatGPT-login flow" phrase. Preserve the three
   durable substrings (`"codex"`, `"PATH"`, `"~/.codex/auth.json"`) and
   the announcement family's print-and-flip discipline. Suggested
   wording: name `~/.codex/auth.json` as the credential file the codex
   CLI uses for API-key login (without claiming a specific auth_mode
   typical case).
2. Update three existing tests in `TestCheckCodexAuthPathBranch` that
   today expect the PATH branch to accept the ChatGPT-login case to
   now expect `CodexAuthMissingError`:
   - `test_codex_on_path_no_env_vars_passes` (was: PATH accept; now:
     refuse because the fixture stages a chatgpt auth.json — OR if
     the fixture leaves auth.json absent, the test stays as PATH-accept
     and a NEW test covers the chatgpt-staged variant).
   - `test_codex_whitespace_env_on_path_passes_with_announcement`.
   - `test_announcement_one_shot_across_calls`.

   Plus rework `TestAnnounceCodexCliOnPath` if its substring assertions
   need to flex around the reworded body.

**Traces to.** DEC-002, DEC-007.

**Acceptance Criteria.**
- Announcement body reworded; three durable substrings still pinned by
  test.
- Three flipped tests pass with their new expectations.
- `tests/test_providers_auth.py` overall passes.
- `.claude/rules/centralized-sdk-call.md` is **not yet** edited (that's
  in US-007 / Patterns & Memory). The discrepancy between rule prose
  and code body is tolerated for one PR; US-007 closes it.

**Done when.** All `TestCheckCodexAuthPathBranch` + `TestAnnounceCodexCliOnPath`
tests pass with their new expectations and the three durable substrings
still match the announcement body.

**Files.**
- `src/clauditor/_providers/_auth.py` — `_CODEX_CLI_ON_PATH_ANNOUNCEMENT`
  body reworded.
- `tests/test_providers_auth.py` — three flipped tests + announcement
  substring tests updated as needed.

**Depends on.** US-003.

**TDD.** Partial. Write the new expected behavior into the flipped tests
first; reword the announcement body to satisfy.

---

### US-005 — Subprocess error mapping: chatgpt rejection -> `"auth"` category

**Description.** In `src/clauditor/_harnesses/_codex.py`, extend
`_AUTH_PATTERNS` with the primary substring `"not supported when using
Codex with a ChatGPT account"`. The chatgpt-mode rejection in subprocess
output now classifies as `"auth"` rather than `"api"`, surfacing a
clearer signal post-hoc for any case that bypasses pre-flight. Reuse
`"auth"` category — no `Literal` extension, no sidecar schema bump
(DEC-011). Classifier stays pure per `.claude/rules/pure-compute-vs-io-split.md`
Seventh anchor — the suffix message is the orchestrator's concern (and
already lives in `InvokeResult.error`).

**Traces to.** DEC-003, DEC-011.

**Acceptance Criteria.**
- `_classify_codex_failure` returns `category == "auth"` for the
  primary pattern.
- A defensive false-positive test exercises a non-matching `"ChatGPT"`
  reference and confirms it still routes to `"api"`.
- No edit to `Literal["rate_limit", "auth", "api"]` and no
  `MAX_SCHEMA_VERSION` map change.

**Done when.** Three new tests added to `TestClassifyCodexFailure`
(primary pattern match; case-insensitive match — confirm via the
existing `.lower()` walk; false-positive guard for unrelated
`"ChatGPT"` text). All pass.

**Files.**
- `src/clauditor/_harnesses/_codex.py` — `_AUTH_PATTERNS` extension.
- `tests/test_harnesses_codex.py` (or the test module that hosts
  `TestClassifyCodexFailure`).

**Depends on.** None.

**TDD.** Yes. The three test cases are inline-assertion shape per
the rule's Seventh anchor.

---

### US-006 — Quality Gate (code review x4 + CodeRabbit + validation)

**Description.** Run `code-reviewer` agent 4 times across the full
changeset, fixing all real bugs each pass. Run CodeRabbit review if
available. Project validation must pass after all fixes:

```bash
uv sync --dev
uv run ruff check src/ tests/
uv run pytest --cov=clauditor --cov-report=term-missing  # 80% gate
```

**Traces to.** All preceding stories' Acceptance Criteria.

**Acceptance Criteria.**
- 4 code-review passes recorded; all real findings addressed (false
  positives documented).
- CodeRabbit review run on the PR; findings addressed or dismissed.
- `ruff check` and full pytest both green.
- Coverage ≥80% on changed files.

**Done when.** All quality gates pass; PR is clean.

**Files.** None (review-only).

**Depends on.** US-001, US-002, US-003, US-004, US-005.

---

### US-007 — Patterns & Memory: refresh rule + CHANGELOG entry

**Description.** Two cleanup edits:
1. **`.claude/rules/centralized-sdk-call.md` refresh** per
   `.claude/rules/rule-refresh-vs-delete.md`. Lines ~541-569 (the
   `_announced_codex_cli_on_path` family-member section). Update the
   fire condition prose to reflect the narrowed acceptance boundary
   (auth.json absent OR `auth_mode != "chatgpt"`). Update the
   durable-substring justification text. Preserve `.claude/rules/`
   history (this is a refresh, not a delete).
2. **CHANGELOG entry** under `CHANGELOG.md`. Summary: clauditor's
   `check_codex_auth` now refuses pre-flight when `~/.codex/auth.json`
   declares `auth_mode == "chatgpt"`. Includes the `codex login
   --with-api-key` remediation. Cross-links #177 and the supersession
   of #175 DECs (001, 002, 003, 004, 008, 009).

**Traces to.** DEC-002, DEC-007, plus the cross-link table.

**Acceptance Criteria.**
- Rule file's lines for the 7th announcement family member describe
  the post-#177 fire condition.
- CHANGELOG entry exists and renders cleanly.
- No new memory files (per CLAUDE.md, this project uses beads `bd
  remember`, not MEMORY.md files).

**Done when.** Rule prose matches code body; CHANGELOG entry merged.

**Files.**
- `.claude/rules/centralized-sdk-call.md` — refresh lines ~541-569.
- `CHANGELOG.md` — new entry (top of unreleased section).

**Depends on.** US-006.

---

### Story map

```
US-001 ────┐
US-002 ────┴──> US-003 ──> US-004 ──┐
US-005 ──────────────────────────────┴──> US-006 ──> US-007
```

US-001, US-002, US-005 can run in parallel. US-003 waits on US-001 + US-002.
US-004 waits on US-003. US-006 is the synchronization barrier; US-007 ships
last.

### Rules compliance check

| Rule | Story honoring it |
| --- | --- |
| `pure-compute-vs-io-split.md` (6th, 7th anchors) | US-001 (parse helper I/O vs verdict), US-005 (classifier stays pure) |
| `precall-env-validation.md` | US-002, US-003 (reuse `CodexAuthMissingError`, distinct exception class preserved) |
| `llm-cli-exit-code-taxonomy.md` | US-003 (exit 2 routing unchanged) |
| `centralized-sdk-call.md` (announcement family) | US-004 (reword), US-007 (rule refresh) |
| `back-compat-shim-discipline.md` Pattern 1 | US-001 (private helpers not re-exported), US-004 (mutable flag still not re-exported) |
| `non-mutating-scrub.md` (no serialization of parsed auth.json) | DEC-014 enforced across US-001, US-002, US-003 |
| `constant-with-type-info.md` (isinstance + bool-style guards) | US-001 (`_auth_mode_is_acceptable` `isinstance(str)`) |
| `harness-protocol-shape.md` | n/a — no protocol surface changes |
| `multi-provider-dispatch.md` | n/a — codex is a harness, not a provider; `check_provider_auth` untouched |
| `plan-contradiction-stop.md` | Verified #175 implementation live; no precondition broken |
| `rule-refresh-vs-delete.md` | US-007 refreshes (does not delete) |
| `stream-json-schema.md` (defensive read shape) | US-001 (`_parse_codex_auth_json` mirrors the skip+default discipline) |
| `json-schema-version.md` | US-005 reuses existing Literal value — no schema bump needed |
| `internal-skill-live-test-tmp-symlink.md` | n/a — no live-runner tests touched |

## Beads Manifest

_TBD — Phase 7 (devolve)._
