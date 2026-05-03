# 151: Multi-harness — `EvalSpec.harness` field + `--harness` CLI flag (four-layer precedence)

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/151
- **Branch:** `feature/151-harness-precedence`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/151-harness-precedence`
- **PR:** _(pending)_
- **Phase:** detailing
- **Epic:** _(pending devolve)_
- **Sessions:** 1 (2026-05-03)
- **Total decisions:** 13 (DEC-001 through DEC-013)
- **Stories:** 8 implementation + Quality Gate + Patterns & Memory = 10
- **Depends on:** #148 (CLOSED — Harness protocol extracted), #149 (CLOSED — CodexHarness shipped)
- **Blocks:** #152

---

## Discovery

### Ticket summary

**What:** Promote harness selection (which subprocess CLI runs the skill —
`ClaudeCodeHarness` invoking `claude -p` vs `CodexHarness` invoking
`codex exec --json`) from a runner-construction kwarg to a full
four-layer-precedence knob mirroring the established pattern from
`transport` (#86) and `grading_provider` (#146).

Adds:
1. `EvalSpec.harness` spec field accepting `{"claude-code", "codex", "auto"}`
2. `--harness` CLI flag on the four skill-execution commands
   (`validate`, `grade`, `capture`, `run`)
3. `CLAUDITOR_HARNESS` env var with whitespace normalization
4. `_resolve_harness(args, eval_spec)` shared helper in `cli/__init__.py`
5. Auto-resolution: `auto` → prefer `claude` on PATH, fall back to `codex`,
   fail with actionable error if neither
6. `check_codex_auth(cmd_name)` per-harness auth guard accepting
   `CODEX_API_KEY` or `OPENAI_API_KEY`
7. `CodexAuthMissingError` sibling exception class (per `precall-env-validation.md`)
8. One-time stderr announcement on `auto → codex` resolution

**Why:** Epic #143 multi-harness initiative. #148 extracted the `Harness`
protocol; #149 shipped `CodexHarness` as the second non-mock implementation;
#151 exposes harness selection at all four operator-intent layers so a CI
pipeline can force `--harness codex` for a single run, a skill author can
declare `"harness": "codex"` per-skill in `eval.json`, and an operator with
only Codex installed gets auto-resolution without extra ceremony.

**Done when (per ticket acceptance):**
1. Tests cover all four precedence layers (CLI > env > spec > default)
2. `clauditor validate --harness codex` runs the skill under `CodexHarness`
3. `"harness": "codex"` in `eval.json` works without any CLI flag
4. `.claude/rules/spec-cli-precedence.md` canonical-implementations section
   updated with `harness` as a sixth four-layer-precedence anchor

**Out of scope (per ticket):**
- Per-grader-call harness selection. Harness governs skill execution only;
  `grading_provider` (#146) and `transport` (#86) govern grader calls. They
  are independent axes — the `--harness` flag does NOT land on the five
  LLM-mediated grader commands (`extract`, `triggers`, `compare`,
  `propose-eval`, `suggest`) because those don't run skills.

### Codebase findings

#### `SkillRunner.__init__` already accepts `harness=`

Post #148, `runner.py:228-264` already accepts a keyword-only
`harness: Harness | None = None`:

```python
def __init__(
    self,
    project_dir: str | Path | None = None,
    timeout: int = 300,
    claude_bin: str = "claude",
    *,
    harness: Harness | None = None,
):
    from clauditor._harnesses._claude_code import ClaudeCodeHarness
    # ...
    if harness is not None:
        if claude_bin != "claude":
            _warnings.warn(...)
        self.harness = harness
    else:
        self.harness = ClaudeCodeHarness(claude_bin=claude_bin)
```

So the runner-side surface is ready. The gap #151 closes is the
**resolution + plumbing** layer: how does `SkillSpec.run` (or the CLI seam
above it) decide which `Harness` instance to pass?

#### `EvalSpec` parallel fields (the precedence template)

`schemas.py`:
- `transport: str | None = None` — line 307, validator 740-760, `to_dict` 959.
- `grading_provider: str | None = None` — line 338, validator 789-808,
  `to_dict` 968. Accepts `{"anthropic", "openai", "auto"} | None`.
- `sync_tasks` — line 319, validator 769-778, `to_dict` 963.

The validator pattern is identical: literal-set membership check with
`bool` guard and per-field `ValueError` message naming the allowed set.
`to_dict` emits the field only when non-`None` (minimal-diff round-trip).

#### `_resolve_*` helper template

`cli/__init__.py:77-176` houses two existing four-layer resolvers:

- `_resolve_grader_transport(args, eval_spec=None) -> str` (77-107)
- `_resolve_grading_provider(args, eval_spec=None) -> str` (110-176)

Both are **thin CLI wrappers** that:
1. Read the CLI value via `getattr(args, "<field>", None)`
2. Read env via `os.environ.get("CLAUDITOR_<FIELD>")`, whitespace-normalize
3. Read the spec value via `eval_spec.<field> if eval_spec else None`
4. Delegate to a **pure** `clauditor._providers.resolve_<field>(cli, env, spec, ...)` helper
5. Catch `ValueError` from the pure resolver, print to stderr,
   `raise SystemExit(2)`

Argparse type validators (`_transport_choice`, `_provider_choice`) live
nearby (44-73). They reject malformed CLI values at argparse time
(exit 2 directly via `argparse.ArgumentTypeError`).

#### `CodexHarness` already shipped (#149)

`src/clauditor/_harnesses/_codex.py:466-510`:
- `name: ClassVar[str] = "codex"` (line 493)
- `__init__(*, codex_bin: str = "codex", model: str | None = None)` (495-510)
- `strip_auth_keys` (512-524) strips `CODEX_API_KEY`, `OPENAI_API_KEY`,
  `OPENAI_BASE_URL` (case-insensitive). The double-strip exists because
  Codex CLI consumes `OPENAI_API_KEY` natively.

#### Auth machinery

`_providers/_auth.py`:
- `check_any_auth_available(cmd_name)` (214-252) — Anthropic relaxed (key OR `claude` on PATH)
- `check_openai_auth(cmd_name)` (288-330) — OpenAI strict (key only)
- `check_provider_auth(provider, cmd_name)` (333-350) — dispatcher
- `announce_implicit_no_api_key()` (69-88) — public helper, one-shot
- `announce_call_anthropic_deprecation()` (127-143) — public helper, one-shot
- Reset pattern: tests `monkeypatch.setattr(..., False)` autouse fixture

For #151, the analogous pieces:
- `check_codex_auth(cmd_name)` — accepts `CODEX_API_KEY` OR `OPENAI_API_KEY`
  (per ticket); raises `CodexAuthMissingError` (new, sibling of `Exception`)
- `_announced_auto_codex_harness` flag + `_AUTO_CODEX_ANNOUNCEMENT` constant
  + `announce_auto_codex_harness()` helper (analogous to the implicit-no-api-key family)

#### Inline `auto → cli` announcement (the comparable shape)

`_providers/_anthropic.py:112` declares `_announced_cli_transport: bool = False`
and `_CLI_AUTO_ANNOUNCEMENT` constant (185-188). The print-and-flip lives
**inline** inside `call_anthropic` (453-456) — NOT in a public helper. Per
`centralized-sdk-call.md` "Implicit-coupling announcements", new members
should use the `Final[str]` constant + public helper shape (the post-#86
pattern), so `auto → codex` should ship with a public helper for testability.

#### `SkillResult.harness_metadata`

`runner.py:101` — `harness_metadata: dict[str, Any] = field(default_factory=dict)`.
Post-#148/DEC-007 forward-compat surface; `CodexHarness` populates this
with `reasoning` items per #149. `#151` does NOT need to extend this; the
`name: ClassVar` on each harness already provides identity.

#### CLI commands today

`cli/grade.py`, `cli/validate.py`, `cli/capture.py`, `cli/run.py` are the
four skill-execution seams. Each already has a `--transport` block; each
loads the spec via `SkillSpec.from_file`, resolves transport via
`_resolve_grader_transport`, runs auth via `check_provider_auth`, then
calls `spec.run(...)`. The `harness` resolution should fire at the same
seam (after spec load, before `spec.run`), and the resolved harness should
thread into `spec.run` as a new keyword override — see Q4 below.

`cli/extract.py`, `cli/triggers.py`, `cli/compare.py`, `cli/propose_eval.py`,
`cli/suggest.py` are LLM-mediated graders that do NOT run skills. They get
no `--harness` flag (out-of-scope per ticket).

### Convention constraints (load-bearing for this work)

1. **`spec-cli-precedence.md`** (CORE) — adds `harness` as a sixth
   four-layer-precedence anchor. Update the canonical-implementations
   section in the same PR. The shape is uniform CLI > env > spec > default;
   per-knob deviation must be called out (e.g. our `auto` resolution is
   PATH-based, not config-based).

2. **`pure-compute-vs-io-split.md`** — `_resolve_harness()` (the CLI
   wrapper) and the underlying pure `resolve_harness(cli, env, spec)` are
   split: pure helper raises `ValueError`, CLI wrapper owns stderr +
   `SystemExit(2)`. Auto-resolution's `shutil.which()` call is the one
   I/O surface; it lives in the pure helper because it's idempotent and
   `which()` is the contract (operators set PATH, not the resolver).

3. **`constant-with-type-info.md`** — `EvalSpec.harness` validator rejects
   anything outside `{"claude-code", "codex", "auto"}`, with a `bool`
   guard.

4. **`precall-env-validation.md`** — `CodexAuthMissingError` is a direct
   subclass of `Exception` (NOT `AnthropicAuthMissingError` or any shared
   parent). Sibling pattern preserves structural CLI routing per
   `llm-cli-exit-code-taxonomy.md`. Auth check fires AFTER `--dry-run`
   early-return AND BEFORE any harness invocation. Defense-in-depth at the
   `CodexHarness.invoke` site is already in place per #149.

5. **`harness-protocol-shape.md`** — protocol surface unchanged. Auth is
   not on the protocol; per-harness construction args (`codex_bin`,
   `claude_bin`) live in `__init__`. The CLI seam is where harness
   selection is made; the harness is constructed and passed to
   `SkillRunner` (or `SkillSpec.run`). This is consistent with the
   existing `harness=` kwarg on `SkillRunner.__init__`.

6. **`centralized-sdk-call.md`** "Implicit-coupling announcements" family —
   `_announced_auto_codex_harness` joins the existing three flags. Use the
   public-helper shape (`announce_auto_codex_harness()`), not inline. Home
   the flag/constant/helper in `_providers/_auth.py` (auth-coupled — the
   notice says "auto-resolved to codex; ensure CODEX_API_KEY / OPENAI_API_KEY
   is set").

7. **`llm-cli-exit-code-taxonomy.md`** — invalid `--harness` / env value
   routes to exit 2 via argparse `type=` validator (CLI) or `SystemExit(2)`
   from the resolver wrapper (env). `CodexAuthMissingError` routes to
   exit 2 (pre-call input-validation category, NOT exit 3 which is
   reserved for actual SDK call failures).

8. **`back-compat-shim-discipline.md`** — adding a new spec field. No
   existing module is being split, so Patterns 1-3 don't apply directly.
   But: tests that monkeypatch the resolver should target
   `clauditor._providers.resolve_harness` (canonical), not
   `clauditor.cli._resolve_harness` (the wrapper).

9. **`pytester-inprocess-coverage-hazard.md`** — does NOT apply (no
   pytester-inproc tests added).

10. **`bundled-skill-docs-sync.md`** — does NOT apply (no SKILL.md
    workflow change).

Non-applicable rules (briefly): `json-schema-version.md` (no new sidecar),
`monotonic-time-indirection.md` (resolver is sync), `stream-json-schema.md`
(no streaming JSON), `pre-llm-contract-hard-validate.md` (config validation,
not LLM-output), `eval-spec-stable-ids.md` (singleton config, not list
entries), `path-validation.md` / `subprocess-cwd.md` /
`project-root-home-exclusion.md` (no path manipulation),
`sidecar-during-staging.md` (no sidecar), `non-mutating-scrub.md`
(per-harness `strip_auth_keys` already non-mutating per #149),
`positional-id-zip-validation.md`, `mock-side-effect-for-distinct-calls.md`,
`per-type-drift-hints.md`, `permissive-parser-strict-validator.md`,
`internal-skill-live-test-tmp-symlink.md`, `dual-version-external-schema-embed.md`,
`data-vs-asserter-split.md`, `in-memory-dict-loader-path.md`,
`readme-promotion-recipe.md`, `plan-contradiction-stop.md`,
`llm-judge-prompt-injection.md`, `skill-identity-from-frontmatter.md`,
`rule-refresh-vs-delete.md` (not a refactor — see post-merge note in
the rule-applicability appendix).

### Architecture review areas (Phase 2 candidates)

- **Auth design** — `CodexAuthMissingError`, `check_codex_auth` env-key
  acceptance (`CODEX_API_KEY` OR `OPENAI_API_KEY`), defense-in-depth
- **CLI / argparse design** — `_harness_choice` validator, default-`None`
  vs default-`"auto"` decision, flag placement (only on 4 commands)
- **Auto-resolution semantics** — `shutil.which("claude")` first, then
  `shutil.which("codex")`, error message when neither found
- **Announcement family extension** — public-helper shape in `_auth.py`,
  one-shot per process, test reset pattern
- **Multi-harness dispatcher analog** — should we extract a sibling
  `multi-harness-dispatch.md` rule? (Probably defer until #152's third
  harness lands.)
- **`SkillSpec.run` plumbing** — does `SkillSpec` get a `harness_override`
  kwarg (mirroring `timeout_override`, `env_override`)? Or does the CLI
  construct the `Harness` instance and pass it to `SkillRunner` directly,
  bypassing `SkillSpec.run`? See Q4.
- **Pytest fixture extension** — does `clauditor_spec` factory grow a
  `harness=` kwarg? See Q5.

---

## Phase 1 scoping (questions for user)

**Q1 — Spec field default sentinel: `None` or `"auto"`?**

Per #146 DEC-001a, `grading_provider` shipped as `str | None = None` with
runtime treatment of `None == "auto"`, deferred default-flip to a follow-up.
The reasoning was back-compat: existing CLI seams used falsy-`None`
short-circuits. For `harness`, no such call sites exist yet (this is a
greenfield field), so the constraint is weaker.

- **A.** `harness: str | None = None`. Treat `None` as `"auto"` at the
  resolver. `to_dict` emits the field only when non-`None`. Mirrors #146's
  conservative default; preserves the "minimal-diff round-trip" property
  for existing eval.json files (which all lack the field today).
- **B.** `harness: str = "auto"`. Direct match to ticket text. `to_dict`
  always emits `"harness": "auto"`. Slightly noisier round-trip but no
  sentinel ambiguity. Pre-#151 specs that omitted the field still load
  fine (default applies).
- **C.** Same as B but `to_dict` skips emission when value is `"auto"`
  (the default). Best-of-both: ticket-exact field signature, minimal-diff
  round-trip. Adds one `if self.harness != "auto"` branch in `to_dict`.

**Recommendation:** **C** — matches ticket verbatim and preserves
round-trip minimalism. Differs from #146's choice because we have no
falsy-`None` legacy short-circuits to migrate around.

**Q2 — Auto-resolution failure mode (neither `claude` nor `codex` on PATH).**

When `harness="auto"` resolves at runtime and neither binary is found:

- **A.** Raise `ValueError` from the pure resolver; CLI wrapper prints
  `"ERROR: --harness=auto found neither 'claude' nor 'codex' on PATH"`
  and exits 2. Hard fail at resolve time (pre-spec-run).
- **B.** Fall through to `claude-code`. Skill subprocess later fails with
  the existing `claude not found` error from `ClaudeCodeHarness.invoke`.
  Soft fall-through, late surfacing.
- **C.** Same as A but the message also lists the spec field path the user
  could set (`'harness': 'claude-code'` or `'codex'`) and the env var to
  set explicitly. Most actionable.

**Recommendation:** **C** — operator intent direction, fail loud at
resolve time, actionable message.

**Q3 — `check_codex_auth` env-key acceptance.**

The ticket says "accepts `CODEX_API_KEY` or `OPENAI_API_KEY`". Resolution
options:

- **A.** Strict OR: at least one of the two must be set (non-empty,
  whitespace-trimmed). If neither, raise `CodexAuthMissingError` with a
  message naming both. Mirrors `check_any_auth_available`'s relaxed shape
  (key OR CLI on PATH). **Codex CLI on PATH is NOT a substitute** — `auto`
  resolution already used PATH; auth is an additional check.
- **B.** Hierarchy: prefer `CODEX_API_KEY`; fall back to `OPENAI_API_KEY`
  with a one-time stderr advisory ("using OPENAI_API_KEY for Codex
  authentication"). Most informative; one extra announcement family member.
- **C.** Same as A but warn on `OPENAI_API_KEY`-only via the same
  `auto → codex` notice (don't add a second announcement). Simpler.

**Recommendation:** **A** — simplest, parallels `check_openai_auth`.

**Q4 — `SkillSpec.run` plumbing for the resolved harness.**

The harness instance must reach `SkillRunner.run()`. Three placements:

- **A.** `SkillSpec.run` grows a keyword arg `harness_override:
  Harness | None = None`. Mirrors `timeout_override` / `env_override`
  shape per `spec-cli-precedence.md`. CLI constructs the `Harness`
  instance from the resolved string and passes it. Requires the caller
  (CLI) to know how to construct `ClaudeCodeHarness` / `CodexHarness`
  from a string — minor coupling.
- **B.** `SkillSpec.run` grows a keyword arg `harness_name_override:
  str | None = None`. The runner / spec internally constructs the
  harness from the name. CLI passes a string only. Construction logic
  lives in one place (`SkillSpec.run` or a sibling helper).
- **C.** CLI bypasses `SkillSpec.run` for harness override, constructs
  its own `SkillRunner(harness=<harness>)` and calls `runner.run(...)`
  directly. Skips `SkillSpec.run`'s timeout / env / cwd resolution
  layer. **Worst** — duplicates `SkillSpec.run`'s integration.

**Recommendation:** **B** — string-typed override is simpler at the seam
boundary, harness construction stays in one place. If a future caller
needs a custom-instantiated harness (e.g. mock in tests), they pass a
fully-constructed `SkillRunner(harness=mock)` per the existing #148
contract — that path is unchanged.

**Q5 — Pytest fixture support for `harness`.**

`clauditor_spec` and `clauditor_grader` fixture factories don't currently
expose a harness override.

- **A.** In scope: add `harness=` kwarg to `clauditor_spec` (and downstream
  `clauditor_runner`). Tests that want to pin a harness pass it; tests that
  don't get auto-resolution honoring `eval_spec.harness`. ~15 lines.
- **B.** Out of scope. Fixtures continue to use `ClaudeCodeHarness` by
  default. Tests that need Codex construct `SkillRunner(harness=CodexHarness())`
  directly. Defer for a follow-up.
- **C.** Compromise: extend the fixture factory to honor
  `eval_spec.harness` only (no kwarg override). Tests with Codex specs
  Just Work; explicit override remains direct construction.

**Recommendation:** **C** — keeps fixture surface stable, picks up the
spec field "for free", explicit override is rare and the direct
construction path is fine.

**Q6 — `--harness` flag on `cli/run.py` semantics.**

`cli/run.py` is the lowest-level "run a skill once and print output"
command. Today it doesn't load an `EvalSpec` (it accepts a skill name +
args, no eval needed).

- **A.** Add `--harness` flag, default `None`. Resolve via env > default.
  No spec field path (no spec is loaded).
- **B.** Same as A but if `--eval-spec` is passed (it isn't today, but
  could be), spec field also participates. Forward-compat for a
  hypothetical flag.
- **C.** Skip `cli/run.py` for #151 — it's a thin debug helper. Land
  `--harness` only on `validate`, `grade`, `capture`. Smaller surface.

**Recommendation:** **A** — ticket lists `cli/run.py` explicitly. Three-
layer (CLI > env > default; no spec) is fine. The argparse `type=` validator
is shared.

**Q7 — Announcement scope on `auto → codex` resolution.**

Mirrors `auto → cli` (transport) which fires once per process via the
inline-flip-at-call-site pattern.

- **A.** One-shot per process. `_announced_auto_codex_harness` flag in
  `_providers/_auth.py` (auth-coupled). Public helper
  `announce_auto_codex_harness()` called from `_resolve_harness()` (or
  the underlying pure helper) when the auto-branch picks codex AND the
  flag is False. Standard pattern.
- **B.** One-shot per CLI command. Reset between commands. More noise;
  no clear value-add for batch invocations.
- **C.** Always announce when auto-resolves to codex. Noisy.

**Recommendation:** **A** — matches the existing announcement-family
contract.

**Q8 — Validator strictness vs harness installation.**

Should `EvalSpec.from_dict` cross-check that the declared harness is
actually installed (e.g. reject `"harness": "codex"` if `codex` isn't on
PATH)?

- **A.** No load-time PATH check. Validator only checks the literal-set
  membership. Missing-binary errors surface at `harness.invoke` time.
- **B.** Load-time check: when `harness != "auto"` and the corresponding
  binary isn't on PATH, emit a stderr WARNING (not error) via the
  conformance-style soft-warn hook in `SkillSpec.from_file`. Spec still
  loads; user sees a hint.
- **C.** Hard load-time failure. **Bad** — locks specs to the operator's
  current env, breaks portability.

**Recommendation:** **A** — keeps validation pure (no I/O), portability
preserved. Auto-resolution failure mode (Q2-C) already handles the
missing-binary case at resolve time when `harness="auto"`.

---

## Phase 1 Decisions (locked)

- **DEC-001 (Q1=C):** `EvalSpec.harness: str = "auto"` (literal default,
  not `None` sentinel). `to_dict` skips emission when value equals
  `"auto"` to preserve minimal-diff round-trip on pre-#151 specs. Differs
  from #146's `None` sentinel because no falsy-`None` legacy short-
  circuits exist for this greenfield field.
- **DEC-002 (Q2=C):** Auto-resolution failure (neither `claude` nor
  `codex` on PATH) raises `ValueError` from the pure resolver; CLI
  wrapper exits 2 with a message naming all three escape hatches:
  `--harness=<name>` flag, `CLAUDITOR_HARNESS=<name>` env var, and the
  `'harness'` field path in `eval.json`.
- **DEC-003 (Q3=A):** `check_codex_auth(cmd_name)` is strict-OR — at
  least one of `CODEX_API_KEY` or `OPENAI_API_KEY` must be set
  (non-empty, whitespace-trimmed). No CLI-on-PATH fallback (parallels
  `check_openai_auth`'s shape; Codex has no documented "subscription
  only" auth analog like Claude Pro/Max).
- **DEC-004 (Q4=B):** `SkillSpec.run` grows
  `harness_name_override: str | None = None` (string-typed). A new
  pure helper `_harnesses.construct_harness(name) -> Harness`
  constructs the instance from the literal name. CLI passes a string
  only. Tests that need a custom mock harness keep using the existing
  #148 contract (`SkillRunner(harness=mock)`).
- **DEC-005 (Q5=C):** Pytest fixture factories (`clauditor_spec`,
  `clauditor_runner`, downstream graders) honor `eval_spec.harness`
  automatically via the same resolver path; NO new `harness=` kwarg on
  the factory. Tests needing a non-spec harness construct
  `SkillRunner(harness=...)` directly.
- **DEC-006 (Q6=A):** `cli/run.py` gets `--harness`, three-layer
  precedence (CLI > env > default). The shared `_resolve_harness`
  helper accepts `eval_spec=None` so the spec layer is skipped cleanly.
- **DEC-007 (Q7=A):** One-shot-per-process announcement on
  `auto → codex` resolution. `_announced_auto_codex_harness` flag +
  `_AUTO_CODEX_ANNOUNCEMENT` constant + `announce_auto_codex_harness()`
  public helper, all in `_providers/_auth.py` (auth-coupled — the
  notice mentions `CODEX_API_KEY`/`OPENAI_API_KEY`). Mirrors the post-
  #86 public-helper shape.
- **DEC-008 (Q8=A):** No load-time PATH check in `EvalSpec.from_dict`
  for the harness binary. Validator only enforces literal-set
  membership (`{"claude-code","codex","auto"}`). Missing-binary
  surfaces at auto-resolve time (DEC-002) or at `harness.invoke` time
  (existing error path).

---

## Architecture Review

Two parallel review passes (Auth/Security + CLI/Data-Model/Test). Overall
verdict: **PASS** with three concerns surfaced, all resolved below.

### Auth / Security review (7 areas)

| # | Area | Rating | Notes |
|---|------|--------|-------|
| 1 | `CodexAuthMissingError` as direct `Exception` subclass | PASS | Sibling pattern preserves structural CLI routing per `precall-env-validation.md` + `llm-cli-exit-code-taxonomy.md`. |
| 2 | Strict-OR `CODEX_API_KEY` / `OPENAI_API_KEY` (DEC-003) | PASS w/ note | Cross-key confusion (operator sets `OPENAI_API_KEY` for grading + selects Codex) surfaces late at `codex exec` invoke time as classified `error_category="auth"` — not silent leakage. Acceptable late-surface UX. |
| 3 | Defense-in-depth at `CodexHarness.invoke` | PASS | Subprocess auth failure surfaces via `_classify_codex_failure` keyword classifier; no hang risk. Pre-call guard is first line of defense, in-loop classifier is second. |
| 4 | `shutil.which` PATH-manipulation threat | PASS | Standard CLI threat model; PATH is operator-controlled. Same shape as existing `_claude_cli_is_available`. |
| 5 | Stderr scrubbing covers `CODEX_API_KEY=` patterns | PASS | `_AUTH_LEAK_PATTERNS` in `_harnesses/_codex.py` already includes both `OPENAI_API_KEY=` and `CODEX_API_KEY=` plus the `_API_KEY_REGEX` bare-token catch. Post-#151 redaction surface complete. |
| 6 | `_AUTH_LEAK_PATTERNS` completeness | PASS | All five anchor patterns present (api_key, Authorization, OPENAI_API_KEY=, CODEX_API_KEY=, CODEX_HOME=). |
| 7 | Announcement notice avoids value interpolation | PASS | `_AUTO_CODEX_ANNOUNCEMENT` will be a `Final[str]` constant (no `.format(value=...)` calls), parallel to existing `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT`. Names only, no values. |

### CLI / Data-Model / Test review (10 areas)

| # | Area | Rating | Notes |
|---|------|--------|-------|
| 1 | `EvalSpec.harness: str = "auto"` + skip-if-default `to_dict` | PASS | No generic `dataclasses.fields(EvalSpec)` consumers; `lint.py` uses `asdict()` only on issue dataclasses, not `EvalSpec`. Round-trip stays minimal-diff for pre-#151 specs. |
| 2 | `SkillSpec.run(harness_name_override=)` keyword arg | PASS | Existing overrides: `timeout_override`, `env_override`, `sync_tasks_override`. Adding a fourth override-style kwarg sits at the readability high-water mark but stays under the cliff. |
| 3 | `construct_harness(name)` location | **CONCERN → DEC-009** | Plan didn't pin file. Resolved: `_harnesses/__init__.py::construct_harness(name) -> Harness` with deferred per-call imports of `_claude_code` and `_codex` modules per `back-compat-shim-discipline.md` Pattern 3 (avoids circular import; `__init__.py` is already imported by both submodules at protocol-class load). |
| 4 | `--harness` flag scope (4 commands, not 9) | PASS | Skill-execution surface only. Sub-question: does `cli/grade.py --baseline` arm propagate the resolved harness to BOTH the with-skill and without-skill `spec.run` calls? Answer below. |
| 5 | `_harness_choice` argparse type validator | PASS | Sibling of `_transport_choice`/`_provider_choice`; literal three-set; rejects unknown values at argparse time (exit 2). |
| 6 | `shutil.which` in pure resolver layer | PASS w/ precedent note | Existing `resolve_transport`/`resolve_grading_provider` are PATH-free. #151's `resolve_harness` is the first four-layer resolver to read PATH. Acceptable per plan's reasoning (PATH is contract, not state) and matches `_claude_cli_is_available` precedent. Document the precedent in the rule update. |
| 7 | Test surface ~31 tests | PASS | Schema (5) + resolver (10) + env (3) + auth (4) + announcement (3) + CLI integration (4) + round-trip (2). Plus ~2 fixture-honors-spec tests = 33 total. |
| 8 | Pytest fixture extension (DEC-005) | PASS | Spec-field honoring is automatic via `spec.run()` invoking the resolver under the hood. No kwarg added; fixture surface stable. |
| 9 | Live-runner test gating | PASS | Existing `CLAUDITOR_RUN_LIVE`-gated tests in `tests/test_bundled_review_skill.py` use the default Anthropic harness; #151 changes nothing for them. A new live-Codex test is out of scope (no live Codex CI today). |
| 10 | Auto-resolve `claude`-first preference | PASS | Pre-existing operators are on Anthropic; defaulting to claude-code preserves zero-friction upgrade. Codex-only operators set `CLAUDITOR_HARNESS=codex` once. |

### Concerns resolved

**Concern A — `CodexAuthMissingError` location (CLI/data review #3 + auth review #1).**
Resolved: the **exception class** lives in `_providers/__init__.py` next
to `AnthropicAuthMissingError` and `OpenAIAuthMissingError` (preserves
class-identity invariant per `back-compat-shim-discipline.md` Pattern 2);
the **helper function** `check_codex_auth(cmd_name)` lives in
`_providers/_auth.py` (co-located with `check_any_auth_available`,
`check_openai_auth`). Tracked as **DEC-010** below.

**Concern B — Announcement fires from wrapper, not pure resolver
(CLI/data review #3 / Auth review #7).** Resolved: the
`announce_auto_codex_harness()` call lives in the **CLI wrapper**
`_resolve_harness()` (which already owns stderr + `SystemExit(2)` per
`pure-compute-vs-io-split.md`), NOT in the pure
`_providers.resolve_harness()`. The pure helper returns a tuple
`(name: str, auto_resolved_to: str | None)` so the wrapper knows when to
fire the notice; the pure helper itself never touches stderr. Tracked as
**DEC-011** below.

**Concern C — `construct_harness()` placement (CLI/data review #3).**
Resolved: `_harnesses/__init__.py::construct_harness(name: str) -> Harness`
with deferred per-call imports. The two existing harnesses both import
from `_harnesses/__init__.py` (the `Harness` protocol and `InvokeResult`),
so eager imports of `_claude_code`/`_codex` from `__init__.py` would
circular-import. Deferred import inside the function body matches the
back-compat-shim Pattern 3 already used by `call_model` in
`_providers/__init__.py`. Tracked as **DEC-012** below.

**Minor note D — Harness-name case-sensitivity.** Validator rejects case
variants (`"ClaudeCode"`, `"CodeX"`) loudly. Already implied by literal-set
membership; called out explicitly to forestall future "did you mean
case-insensitive?" PRs. No new DEC needed (DEC-008 covers this).

**Minor note E — `--baseline` arm of `grade`.** The resolved harness
propagates to BOTH the primary skill run AND the baseline skill run
(both go through `spec.run` with the same `harness_name_override`).
Per DEC-013 below.

### Phase 2 additional decisions

- **DEC-009 (Concern C):** `construct_harness(name: str) -> Harness` lives
  in `_harnesses/__init__.py` with deferred per-call imports of
  `_claude_code` and `_codex` modules (back-compat-shim-discipline Pattern 3
  applied). Raises `ValueError` for unknown names (mirrors `call_model`
  dispatcher shape).
- **DEC-010 (Concern A):** `CodexAuthMissingError` lives in
  `_providers/__init__.py` (sibling of the existing two
  `*AuthMissingError` classes; direct subclass of `Exception`, NOT of
  any shared parent). The auth-check helper `check_codex_auth(cmd_name)`
  lives in `_providers/_auth.py` co-located with the other
  `check_*_auth` helpers. The dispatcher `check_provider_auth` does NOT
  grow a Codex branch (Codex is a HARNESS, not a PROVIDER — different
  axis); the CLI seam directly calls `check_codex_auth(cmd_name)` when
  the resolved harness is `"codex"`.
- **DEC-011 (Concern B):** Pure resolver
  `_providers.resolve_harness(cli, env, spec) -> tuple[str, str | None]`
  returns `(resolved_name, auto_resolved_to)` where the second element is
  `"codex"` when auto picked codex, `None` otherwise. The CLI wrapper
  `_resolve_harness(args, eval_spec=None)` reads the tuple and calls
  `announce_auto_codex_harness()` when the second element is `"codex"`.
  Pure helper stays I/O-free (except `shutil.which` for PATH lookup,
  per CLI/data review #6 precedent note).
- **DEC-012:** Auth-check ordering — when `harness="codex"` resolves
  (whether via auto or explicit), `check_codex_auth(cmd_name)` fires
  AFTER `--dry-run` early-return AND BEFORE `spec.run()` invocation, at
  the same CLI seam where `check_provider_auth(provider, cmd_name)`
  already fires for grader auth. Both checks fire (provider auth +
  harness auth) in the four skill-execution commands when the harness is
  codex, since both subsystems are exercised. Order: provider auth
  first, harness auth second (so a missing `ANTHROPIC_API_KEY` for
  grading is reported before a missing `CODEX_API_KEY` for execution).
- **DEC-013:** `--baseline` arm of `grade` propagates the resolved
  harness to BOTH the primary skill run AND the baseline run. The
  baseline executes the same skill without the eval spec but uses the
  same harness — running the baseline under a different harness would
  defeat the purpose (an A/B comparison must isolate the variable). No
  separate `--baseline-harness` flag.

---

---

## Refinement Log

All open concerns resolved during architecture review (see DEC-009 through
DEC-013 above). No additional questions surfaced. Total decisions: 13
(DEC-001 through DEC-013).

---

## Detailed Breakdown

Stories ordered by natural dependency: schema → pure resolver → auth helper
→ CLI plumbing → SkillSpec.run integration → fixture extension → docs +
quality gate + patterns memory.

### US-001 — `EvalSpec.harness` field + load-time validator

**Description:** Add `harness: str = "auto"` field to `EvalSpec` dataclass
with literal-set membership validation in `from_dict`. Conditional emission
in `to_dict` (skip when value equals default `"auto"`).

**Traces to:** DEC-001, DEC-008.

**Acceptance criteria:**
- `EvalSpec.harness` field declared with `str = "auto"` default
- `from_dict` validates against `{"claude-code", "codex", "auto"}`,
  rejects bool, non-string, and unknown literal values with
  `ValueError` message naming the allowed set
- `to_dict` emits `"harness"` key only when value differs from `"auto"`
- Round-trip `from_dict(to_dict(spec))` preserves both default-`"auto"`
  and explicit-`"codex"` shapes
- `uv run pytest tests/test_schemas.py -k harness` passes
- `uv run ruff check src/ tests/` clean

**Done when:** Schema validator unit tests cover all 5 cases (default,
each of 3 literals explicitly, invalid-string rejection, bool rejection,
null rejection). `to_dict` skip-if-default path is tested for both `"auto"`
(skipped) and `"codex"` (emitted).

**Files:**
- `src/clauditor/schemas.py` — add field at the EvalSpec dataclass
  (sibling of `transport`, line ~307); validator block in `from_dict`
  (sibling of transport block, ~740-760); `to_dict` skip-if-default
  emission (sibling of ~959).
- `tests/test_schemas.py` — add `TestEvalSpecHarness` test class
  (~5 tests).

**Depends on:** none

---

### US-002 — Pure `resolve_harness` helper + `construct_harness` dispatcher

**Description:** Add the pure four-layer resolver
`resolve_harness(cli, env, spec) -> tuple[str, str | None]` in
`_providers/__init__.py` (precedent: `resolve_transport`,
`resolve_grading_provider`). Add the `construct_harness(name) -> Harness`
dispatcher in `_harnesses/__init__.py` with deferred per-call imports of
the two harness submodules.

**Traces to:** DEC-002, DEC-009, DEC-011.

**Acceptance criteria:**
- `resolve_harness(cli, env, spec)` returns
  `(resolved_name, auto_resolved_to)`:
  - When `cli` is non-`None` non-`"auto"`: returns `(cli, None)`
  - When CLI is unset, env layer wins; whitespace-only env normalizes
    to `None`
  - When all four layers are `None` or `"auto"`: PATH lookup runs
    (`claude` first, then `codex`); returns `(picked, picked)` when
    auto-selection succeeds; raises `ValueError` if neither found, with
    a message naming `--harness=<name>`, `CLAUDITOR_HARNESS=<name>`,
    and the `'harness'` field in `eval.json`
  - When precedence resolves to a non-`"auto"` value, `auto_resolved_to`
    is always `None` (no announcement should fire)
  - Invalid values from any layer raise `ValueError` with the literal
    set named
- `construct_harness(name)` returns `ClaudeCodeHarness()` for
  `"claude-code"`, `CodexHarness()` for `"codex"`; raises `ValueError`
  for any other input (including `"auto"` — auto must be resolved
  before construction)
- Both helpers are pure (no stderr, no `sys.exit`)
- Both fully unit-tested

**TDD:** Write failing tests first, in this order:
- `resolve_harness` returns explicit cli value
- `resolve_harness` whitespace-empty env normalizes to None
- `resolve_harness` spec falls through when cli + env are None
- `resolve_harness` default `"auto"` triggers PATH lookup
- `resolve_harness` auto-resolves to `claude-code` when `claude` on PATH
- `resolve_harness` auto-resolves to `codex` when only `codex` on PATH;
  returns `auto_resolved_to="codex"`
- `resolve_harness` raises ValueError when neither on PATH
- `resolve_harness` raises ValueError on unknown literal (per layer)
- `construct_harness("claude-code")` returns ClaudeCodeHarness
- `construct_harness("codex")` returns CodexHarness
- `construct_harness("auto")` and `construct_harness("unknown")` raise

**Done when:** ~12 unit tests pass; coverage on the new functions is
100%. `resolve_harness` and `construct_harness` are importable from
their canonical paths.

**Files:**
- `src/clauditor/_providers/__init__.py` — add `resolve_harness`
  alongside `resolve_transport` and `resolve_grading_provider`.
  `shutil.which` is the only non-pure call; it's already a precedent
  (see `_claude_cli_is_available`).
- `src/clauditor/_harnesses/__init__.py` — add `construct_harness`
  with deferred per-call imports of `_claude_code` and `_codex` modules
  (back-compat-shim-discipline Pattern 3).
- `tests/test_providers.py` (or new `tests/test_resolve_harness.py`) —
  pure-resolver tests with `monkeypatch.setattr(shutil, "which", ...)`
  for PATH stubbing.
- `tests/test_harnesses.py` (or sibling) — `construct_harness` tests.

**Depends on:** US-001 (need the spec field shape stable so that the
spec-layer parameter type-checks)

---

### US-003 — `check_codex_auth` + `CodexAuthMissingError` + announcement family

**Description:** Add the three pre-call auth pieces: the exception class
in `_providers/__init__.py`, the helper function in `_providers/_auth.py`,
and the auto→codex announcement family member.

**Traces to:** DEC-003, DEC-007, DEC-010, DEC-011.

**Acceptance criteria:**
- `CodexAuthMissingError` declared at `_providers/__init__.py`,
  direct subclass of `Exception` (NOT of `AnthropicAuthMissingError`
  or any shared parent)
- `check_codex_auth(cmd_name: str) -> None` in `_providers/_auth.py`:
  - Returns silently when `CODEX_API_KEY` OR `OPENAI_API_KEY` is set
    (both checked via whitespace-trimmed non-empty)
  - Raises `CodexAuthMissingError` with a message naming both env vars
    and a pointer to `https://platform.openai.com/api-keys`
- `_announced_auto_codex_harness: bool = False` module-level flag
  + `_AUTO_CODEX_ANNOUNCEMENT: Final[str] = "..."` constant in
  `_providers/_auth.py` (auth-coupled home; matches existing pattern)
- `announce_auto_codex_harness()` public helper — print-and-flip;
  one-shot per process; emits a notice naming `CODEX_API_KEY` and
  `OPENAI_API_KEY` (env-var names only, never values)
- Class-identity test: import `CodexAuthMissingError` from both
  `_providers` and `_providers._auth` (re-export); assert
  `from_a is from_b` (per `back-compat-shim-discipline.md` Pattern 2)
- Tests for the auth helper cover: only `CODEX_API_KEY` set, only
  `OPENAI_API_KEY` set, both set, neither set (raises),
  whitespace-only values treated as unset
- Tests for the announcement: fires once per process, the
  `monkeypatch.setattr(..., False)` autouse-fixture pattern resets
  between tests

**TDD:** Write failing tests first for the auth helper (4 cases) and the
announcement (one-shot + reset).

**Done when:** ~9 unit tests pass; class-identity invariant verified; the
announcement family member integrates with the existing test reset
pattern in `tests/test_providers_auth.py`.

**Files:**
- `src/clauditor/_providers/__init__.py` — `CodexAuthMissingError`
  class declaration + re-export.
- `src/clauditor/_providers/_auth.py` — `check_codex_auth`,
  `_announced_auto_codex_harness`, `_AUTO_CODEX_ANNOUNCEMENT`,
  `announce_auto_codex_harness`.
- `tests/test_providers_auth.py` — extend existing test classes with
  `TestCheckCodexAuth` and `TestAnnounceAutoCodexHarness`.

**Depends on:** none (parallel-safe with US-001 and US-002)

---

### US-004 — `_harness_choice` argparse validator + `_resolve_harness` CLI wrapper

**Description:** Add the CLI-side helpers in `cli/__init__.py`. The
argparse `type=` validator rejects malformed values at parse time; the
wrapper threads CLI/env/spec through the pure resolver and routes errors
+ fires the announcement.

**Traces to:** DEC-002, DEC-006, DEC-007, DEC-011.

**Acceptance criteria:**
- `_harness_choice(value: str) -> str` argparse-type callable parallels
  `_transport_choice` and `_provider_choice`. Rejects unknown values
  with `argparse.ArgumentTypeError` (argparse maps to exit 2 for free).
- `_resolve_harness(args, eval_spec=None) -> str`:
  - Reads `getattr(args, "harness", None)` for CLI layer
  - Reads `os.environ.get("CLAUDITOR_HARNESS")` for env layer; strips
    whitespace; whitespace-only normalizes to None
  - Reads `eval_spec.harness` for spec layer (when `eval_spec` is
    non-None); treats `None` AND `"auto"` as fall-through
  - Calls `clauditor._providers.resolve_harness(cli, env, spec)`
  - On `ValueError` from the pure helper: prints `f"ERROR: {exc}"` to
    stderr, raises `SystemExit(2)`
  - On success: if pure helper returned non-None
    `auto_resolved_to == "codex"`, calls
    `announce_auto_codex_harness()` BEFORE returning the resolved name
  - Returns the resolved name (string)
- `_resolve_harness` wrapper accepts `eval_spec=None` cleanly so
  `cli/run.py` (no spec) Just Works
- Wrapper is fully unit-tested with `monkeypatch.setattr` patching
  `clauditor._providers.resolve_harness` (canonical seam) and
  `clauditor._providers._auth.announce_auto_codex_harness`

**TDD:** Tests written first for: each precedence layer wins correctly,
invalid env raises SystemExit(2) with stderr message, announcement fires
when auto-resolves to codex AND not when auto-resolves to claude-code.

**Done when:** ~8 unit tests pass; `_resolve_harness` is a one-call
swap-in for any CLI seam.

**Files:**
- `src/clauditor/cli/__init__.py` — `_harness_choice` (sibling of
  `_transport_choice` ~44-56) and `_resolve_harness` (sibling of
  `_resolve_grader_transport` ~77-107).
- `tests/test_cli_helpers.py` (or sibling) — `TestHarnessChoice`,
  `TestResolveHarness`.

**Depends on:** US-002 (pure resolver), US-003 (announcement helper)

---

### US-005 — Wire `--harness` flag into `validate`/`grade`/`capture`/`run`

**Description:** Add `--harness {claude-code,codex,auto}` argparse argument
to the four skill-execution CLI commands; thread the resolved harness
name through to `SkillSpec.run` (via the new
`harness_name_override` kwarg added in US-006). Wire `check_codex_auth`
at each seam after the existing `check_provider_auth` call.

**Traces to:** DEC-006, DEC-012, DEC-013.

**Acceptance criteria:**
- Each of `cli/validate.py`, `cli/grade.py`, `cli/capture.py`,
  `cli/run.py` adds `--harness` argument with
  `type=_harness_choice`, `default=None`,
  `choices=("claude-code", "codex", "auto")`,
  help text matching the existing `--transport` template
- Each command resolves
  `harness_name = _resolve_harness(args, spec.eval_spec)` after spec
  load (or `_resolve_harness(args, None)` for `cli/run.py`)
- Each command, AFTER `--dry-run` early-return AND AFTER
  `check_provider_auth(...)`, calls `check_codex_auth("validate")`
  (etc.) when `harness_name == "codex"`. Catches
  `CodexAuthMissingError`, prints to stderr, returns exit 2.
- The resolved `harness_name` threads into `spec.run(...)` as
  `harness_name_override=harness_name` (US-006). For `cli/run.py`
  which constructs `SkillRunner` directly, the resolved name passes
  through to `construct_harness(harness_name)` and the result is
  passed as `SkillRunner(harness=...)`.
- `cli/grade.py --baseline` arm propagates the SAME resolved harness
  to BOTH the primary and baseline `spec.run` calls (DEC-013).
- Smoke tests: `clauditor validate --harness codex <skill>` succeeds
  on a Codex-installed env (mocked subprocess); fails with
  `CodexAuthMissingError` exit 2 when both env vars unset.

**Done when:** integration tests verify all four commands accept the
flag, route to the correct harness, and propagate auth failures correctly.
~6 integration tests.

**Files:**
- `src/clauditor/cli/validate.py` — add argparse block + resolver call
  + auth check + thread to spec.run.
- `src/clauditor/cli/grade.py` — same; both `spec.run` arms get the
  override; baseline arm shares the resolved harness.
- `src/clauditor/cli/capture.py` — same.
- `src/clauditor/cli/run.py` — add argparse block; this command
  constructs `SkillRunner` directly, so the integration is
  `runner = SkillRunner(harness=construct_harness(harness_name))`.
- `tests/test_cli_*.py` — add per-command smoke tests.

**Depends on:** US-001, US-002, US-003, US-004, US-006

---

### US-006 — `SkillSpec.run` `harness_name_override` kwarg

**Description:** Add the keyword-only override on `SkillSpec.run` and the
internal resolution that calls `construct_harness(name)` to materialize
the harness instance, then passes it to `SkillRunner.run` via the
existing `harness=` kwarg path.

**Traces to:** DEC-004.

**Acceptance criteria:**
- `SkillSpec.run` signature gains
  `harness_name_override: str | None = None` after `*,` (keyword-only,
  parallels `timeout_override`, `env_override`, `sync_tasks_override`)
- When `harness_name_override` is non-None: `SkillSpec.run` calls
  `construct_harness(harness_name_override)` and constructs an ad-hoc
  `SkillRunner(...)` with that harness, OR — if `self.runner` already
  exists with the right harness — reuses it. (Recommended: when
  override is non-None, construct a fresh `SkillRunner` with the same
  config but the new harness; the construction cost is negligible.)
- When `harness_name_override` is None: existing behavior unchanged
  (`self.runner` used as-is, default `ClaudeCodeHarness`)
- Tests cover override-applied path AND override-omitted path
- `tests/test_spec.py` test class `TestSkillSpecRunHarnessOverride`

**Done when:** ~3 unit tests pass; the override is the canonical seam
for the CLI to inject harness selection without duplicating the
fallback-to-spec-field logic.

**Files:**
- `src/clauditor/spec.py` — `SkillSpec.run` signature + body.
- `tests/test_spec.py` — `TestSkillSpecRunHarnessOverride`.

**Depends on:** US-002 (`construct_harness`)

---

### US-007 — Pytest fixture honors `eval_spec.harness` automatically

**Description:** Per DEC-005 (Q5=C), no new fixture kwarg. The fixture
factories pass `harness_name_override=eval_spec.harness` (when set and
non-`"auto"`) into `spec.run`. Auto-resolution still fires when needed.

**Traces to:** DEC-005.

**Acceptance criteria:**
- `clauditor_spec` factory threads
  `harness_name_override=spec.eval_spec.harness` into
  `spec.run` when the spec field is non-`"auto"` (so `"auto"` and
  unset still defer to the resolver's auto path)
- Tests verify a Codex-spec eval.json picks up the harness without a
  fixture-side kwarg
- Live-runner tests untouched (default `claude-code` path)

**Done when:** ~2 fixture tests pass.

**Files:**
- `src/clauditor/pytest_plugin.py` — extend fixture factory bodies.
- `tests/test_pytest_plugin.py` — add `TestFixtureHonorsHarness`.

**Depends on:** US-006

---

### US-008 — Update `.claude/rules/spec-cli-precedence.md`

**Description:** Per ticket acceptance criterion #4, add `harness` as the
sixth four-layer-precedence canonical implementation in the rule's
"Canonical implementations" section. Document the precedent of
`shutil.which` in the pure resolver layer (first such case).

**Traces to:** ticket acceptance criterion #4.

**Acceptance criteria:**
- New `### Four-layer precedence — harness (#151)` subsection in
  `.claude/rules/spec-cli-precedence.md` matching the structure of the
  existing "transport" and "grading_provider" anchors
- Lists CLI flag, env var, spec field, default
- Notes the auto-resolution PATH-lookup behavior and that this is the
  first four-layer resolver to read PATH from the pure layer
- Cross-references `harness-protocol-shape.md`,
  `centralized-sdk-call.md` (announcement family), and
  `precall-env-validation.md`
- Trace line at the bottom of the subsection: "Traces to DEC-001
  through DEC-013 of `plans/super/151-harness-precedence.md`."

**Done when:** the rule renders cleanly; an author opening
`spec-cli-precedence.md` finds `harness` listed alongside `transport`
and `grading_provider`.

**Files:**
- `.claude/rules/spec-cli-precedence.md` — new subsection.

**Depends on:** US-001 through US-007 (rule should reflect what shipped)

---

### US-009 — Quality Gate

**Description:** Run code reviewer 4 times across the full changeset,
fixing every real bug found each pass. Run CodeRabbit review if
available. Verify project quality gates pass.

**Acceptance criteria:**
- 4 passes of `code-reviewer` agent across the diff; all real findings
  resolved (false positives documented inline if any)
- CodeRabbit review run (if PR is open); all findings addressed
- `uv run ruff check src/ tests/` clean
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80% coverage gate enforced
- Coverage on new code (`resolve_harness`, `construct_harness`,
  `_resolve_harness`, `check_codex_auth`,
  `announce_auto_codex_harness`, `EvalSpec.harness` validator) ≥ 90%

**Done when:** all gates green; no real findings from any review pass.

**Depends on:** US-001 through US-008

---

### US-010 — Patterns & Memory

**Description:** Update `.claude/rules/` and `MEMORY.md` with patterns
discovered during #151 implementation. Specifically: the
"PATH-lookup-in-pure-resolver" precedent (first four-layer resolver to
read PATH), the "harness vs provider — different axis" clarification
(harness is NOT a provider; auth dispatcher does NOT grow a Codex branch).

**Acceptance criteria:**
- If new patterns surfaced, write them up as `.claude/rules/*.md`
  entries OR extend existing rules
- If no new patterns surfaced (unlikely given the cross-axis
  clarification needed), document the absence in the rule's "When this
  rule does NOT apply" section
- Update `MEMORY.md` index entries if new rule files are added

**Done when:** any patterns from this ticket are durable and
discoverable.

**Depends on:** US-009

---

## Beads Manifest

_(populated after devolve)_
