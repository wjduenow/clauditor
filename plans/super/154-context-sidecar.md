# 154: Comparability — per-iteration `context.json` sidecar

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/154
- **Branch:** `feature/154-context-sidecar`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/154-context-sidecar`
- **PR:** https://github.com/wjduenow/clauditor/pull/171
- **Phase:** devolved
- **Epic:** clauditor-hqv
- **Sessions:** 1 (2026-05-08)
- **Total decisions:** 11 (DEC-001 through DEC-011)
- **Depends on:** #149 (CLOSED — `CodexHarness`), #152 (CLOSED — `harness` field on L1/L2/L3 sidecars)
- **Sibling / parent:** #143 (multi-provider / multi-harness umbrella, OPEN)
- **Independent of:** #153
- **Blocks (follow-ups created from scoping):** #169 (cost_usd pricing module), #170 (reasoning_tokens capture)

---

## Discovery

### Ticket summary

**What:** Add a new per-iteration sidecar `iteration-N/<skill>/context.json` that captures comparability metadata — the data needed to interpret a score but not part of the score itself: which harness ran the skill, which model the harness used, which provider's SDK served the grader call, which model the grader used, where the system prompt came from, what sandbox mode (Codex), how many reasoning tokens, and a best-effort cost estimate. Surface the captured context in `clauditor audit --verbose` and badge generation.

**Why:** Multi-provider + multi-harness work (#143 umbrella, with #149 / #146 / #147 / #151 / #152 already shipped) means two iterations of the "same" eval can now differ along multiple dimensions: harness binary, runner model, grader provider, grader model, system-prompt source, sandbox posture. Today `audit` reads L1/L2/L3 sidecars and groups by `(provider, harness, layer, id)` (post-#147 + #152) but cannot expose WHY two iterations differ — readers see two grouped lines but not "iteration 17 ran under Codex sandbox=danger-full-access while iteration 18 ran under Codex sandbox=workspace-write." `context.json` is the single per-iteration place those gotchas live so they surface instead of being normalized away.

**Out of scope (per ticket):**
- Authoritative cost-per-provider price tables — best-effort lookup with a fallback to `null` is fine.
- Cross-iteration aggregation of context (separate concern; trend/audit grouping by context dimensions is a future ticket).
- Bumping any existing sidecar's `schema_version` — `context.json` is a NEW sidecar family, not an extension of an existing one.

### Codebase findings (from Codebase Scout)

#### Sidecar-write call sites (where `context.json` slots in)

- **Canonical write loop:** `src/clauditor/cli/grade.py::_write_workspace_sidecars` (lines 750–845). Writes `grading.json`, `assertions.json`, `extraction.json`, baseline + benchmark sidecars in turn, all into `workspace.tmp_path` BEFORE `workspace.finalize()`. Insertion point for `context.json`: just after `grading.json` is written, before the finalize, inside the same `try/except → workspace.abort()` envelope.
- **Second write site:** `src/clauditor/cli/validate.py::cmd_validate` (lines 200–258). Calls `allocate_iteration` + `workspace.tmp_path`; writes `assertions.json` only (no L2/L3). For validate-only iterations the new sidecar's `provider`, `model_grader`, `cost_usd`, `reasoning_tokens` are all `null`.
- **No other CLI commands allocate iterations.** `extract`, `capture`, `run` do NOT call `allocate_iteration` and therefore do NOT produce iteration dirs — they are out of scope for `context.json` writes.

#### Field-source inventory (every schema field, mapped to what's available today)

| context.json field | Source / where it lives today | Net effort |
|---|---|---|
| `harness: str` | `harness.name` (`ClassVar[str]`) on the protocol; resolved value already passed into `_write_workspace_sidecars` as `harness_name: str \| None` (cli/grade.py:764). For validate, resolved via `_resolve_harness(args, spec.eval_spec)` at the CLI seam too. | **Trivial** — read string already in scope. |
| `provider: str \| None` | Resolved at `cli/grade.py:386` via `_resolve_grading_provider(args, spec.eval_spec)`. Threaded into `_write_workspace_sidecars` as `provider=` (line 839). For `validate`: no LLM grader → `null`. | **Trivial** — null when no grader call ran. |
| `model_runner: str` | Codex puts it in `InvokeResult.harness_metadata["model"]` (`_codex.py:747`). Claude Code does NOT today — `argv += ["--model", effective_model]` (`_claude_code.py:538`) but stream-json `result` carries no model field. The harness instance has `self.model` (set at `__init__`), which is the value actually used. | **Small new plumbing** — DEC needed: option (a) post-call read `harness.model` from the runner; option (b) thread through `harness_metadata["model"]` for parity with Codex. (b) is more uniform. |
| `model_grader: str \| None` | `GradingReport.model` (`quality_grader.py:90`) — already persisted. For ExtractionReport too (`grader.py`). For `validate`-only: `null`. | **Trivial** — read from primary report. |
| `system_prompt_source: "skill_md" \| "agents_md" \| "explicit"` | Resolution lives in `src/clauditor/spec.py:204–228` (post-#150). `effective_system_prompt = self.eval_spec.system_prompt if (eval_spec.system_prompt is not None) else None`; if still None → auto-derived from SKILL.md body (`parse_frontmatter`). `EvalSpec.system_prompt` exists at `schemas.py:253`. **No `agents_md` path exists today** — Codex consumes a flat prompt (`_codex.py:560–562`) but does not auto-derive from `AGENTS.md`. | **DEC needed** — should `agents_md` ship in this PR or be reserved for a future ticket? Today only `"skill_md"` and `"explicit"` are reachable. Threading the source through `SkillSpec.run` → `SkillResult.harness_metadata["system_prompt_source"]` is small. |
| `sandbox_mode: str \| None` | `_codex.py:240–242` — currently `_SANDBOX_MODE = "workspace-write"` hardcoded. Already populated into `harness_metadata["sandbox_mode"]` at `_codex.py:743`. Claude Code: `null` (no sandbox concept). | **Trivial** — read from `harness_metadata["sandbox_mode"]`, default `null`. |
| `reasoning_tokens: int \| None` | NOT tracked today. Neither `InvokeResult` nor `ModelResult` carries a separate reasoning-token field. OpenAI o-series / GPT-5 reasoning models bill them separately; Anthropic thinking models too. | **DEC needed** — option (a) ship as `null`-only placeholder until a future ticket adds reasoning-token plumbing; option (b) add minimal capture inside `_providers/_openai.py` + `_providers/_anthropic.py` for the reasoning-model cases that exist today. |
| `cost_usd: float \| None` | NO pricing helper exists. `grep -rn "cost_usd\|price_per_token\|pricing" src/` returns zero hits. | **DEC needed** — option (a) ship `null`-only and defer pricing module to a follow-up; option (b) ship a small `_providers/_pricing.py` with hardcoded prices for known models, `null` for unknowns. |

#### Audit-reader integration

- `src/clauditor/audit.py::load_iterations` (lines 356–430) reads `assertions.json`, `extraction.json`, `grading.json` via `_read_json` and dispatches to `_records_from_*` helpers.
- `MAX_SCHEMA_VERSION` (lines 52–56) needs `"context": 1` added (per `json-schema-version.md`).
- `--verbose` rendering: `render_stdout_table`, `render_markdown`, `render_json`. Insertion point for the per-iteration context block is render-time, not aggregation-time — context.json is **not** aggregated (it varies per iteration; aggregation is the job of #143's future tickets).

#### Badge generation integration

- `src/clauditor/badge.py` + `src/clauditor/cli/badge.py`. Per `dual-version-external-schema-embed.md`, the shields.io-side `<skill>.json` cannot carry unknown keys; context goes in the sibling `<skill>.clauditor.json` extension under a new `context: {...}` block. Whether the extension's `schema_version` bumps from 1 to 2 is a DEC.

### Conventions / rules consulted (from Convention Checker)

**Apply — drive implementation:**

1. **`json-schema-version.md`** — Writer emits `schema_version: 1` as first key; loader registers `"context": 1` in `MAX_SCHEMA_VERSION`; `_check_schema_version` enforces. New sidecar family (NOT a bump of an existing one), so the rule's "Schema version bumps for #147" section does not need extension — but a small "New sidecar family — context.json (#154)" addition under canonical implementations is appropriate per `rule-refresh-vs-delete.md`.
2. **`sidecar-during-staging.md`** — Write inside `workspace.tmp_path` BEFORE `workspace.finalize()`; sit inside the `try/except → workspace.abort()` envelope. No `--no-context` flag (always-on), so the "Skip-write flags must clean pre-staged subtrees" subsection does not apply.
3. **`pure-compute-vs-io-split.md`** — `IterationContext` is a pure dataclass with `to_json() -> str`; the CLI caller does the `write_text`. Mirrors anchor #1 (`Benchmark.to_json` + `compute_benchmark`). Adds an eighth anchor.
4. **`constant-with-type-info.md`** — `bool is not int` guard for `reasoning_tokens` and `cost_usd` if any post-load validator is built. The dataclass holds the type via the `int | None` / `float | None` annotation; no per-key `KeySpec` table needed at this scale.
5. **`harness-protocol-shape.md`** — Read `harness.name` (the `ClassVar[str]`); no new protocol member needed. **However** — capturing `model_runner` for ClaudeCodeHarness uniformly with Codex's `harness_metadata["model"]` is consistent with the "harness_metadata as forward-compat surface" subsection (DEC-007 of #148). New protocol member for `system_prompt_source` capture may also be in scope.
6. **`stream-json-schema.md`** — Token counts come from `SkillResult` (already defensively parsed). No new stream-json reading at the sidecar layer.
7. **`pre-llm-contract-hard-validate.md`** — Hard-validate `harness ∈ {"claude-code", "codex"}`, `provider ∈ {"anthropic", "openai", None}`, `system_prompt_source ∈ {"skill_md", "agents_md", "explicit"}` at write time so a future regression cannot silently stamp garbage into history.
8. **`multi-provider-dispatch.md`** — Read `provider` from the same CLI-seam resolution that gates `check_provider_auth`. The sidecar is a downstream observer, not a new dispatch surface.
9. **`centralized-sdk-call.md`** — `ModelResult.provider` already carries the provider string. No new plumbing through `call_model`. The sidecar reads what's already returned.
10. **`data-vs-asserter-split.md`** — `IterationContext` stays methodless except `to_json` / `from_dict`. If tests grow `assert_*` helpers, they live in `asserters.py` per the rule.
11. **`rule-refresh-vs-delete.md`** — `json-schema-version.md` may benefit from a small addition documenting `context.json` as a new sidecar family. Refresh-in-place, do not create a parallel rule.

**N/A — explicitly ruled out:**

`back-compat-shim-discipline.md`, `spec-cli-precedence.md`, `non-mutating-scrub.md`, `dual-version-external-schema-embed.md` (context.json is clauditor-only — but the rule DOES apply to the badge integration step, see below), `llm-cli-exit-code-taxonomy.md`, `monotonic-time-indirection.md`, `bundled-skill-docs-sync.md`, `readme-promotion-recipe.md`, `positional-id-zip-validation.md`, `pytester-inprocess-coverage-hazard.md`, `mock-side-effect-for-distinct-calls.md`, `in-memory-dict-loader-path.md`, `precall-env-validation.md`, `per-type-drift-hints.md`, `permissive-parser-strict-validator.md`, `subprocess-cwd.md`, `path-validation.md`, `eval-spec-stable-ids.md`, `internal-skill-live-test-tmp-symlink.md`, `skill-identity-from-frontmatter.md`, `test-infra-shutil-which-coupling.md`, `llm-judge-prompt-injection.md`, `project-root-home-exclusion.md`. Note: `dual-version-external-schema-embed.md` DOES apply to the badge-integration story (where the context surfaces in the `.clauditor.json` extension, NOT the shields.io payload).

### Scoping questions for the user

These are the choices that drive the per-story breakdown — answers shape decisions DEC-001…DEC-006 below.

#### Q1: `cost_usd` — ship pricing module now, or null-only placeholder?

The ticket explicitly says "best-effort lookup with a fallback to null is fine." Two interpretations:

- **A.** Ship a minimal `src/clauditor/_providers/_pricing.py` with hardcoded `(provider, model) → (input_$/Mtok, output_$/Mtok)` for known model families today (claude-sonnet-4-6, claude-opus-4-7, gpt-5.4, gpt-5.4-mini, etc.). Unknown model → `null`. Estimated cost computed at sidecar-write time from the grader's `input_tokens`/`output_tokens` and the harness's runner tokens too. **Pro:** delivers the ticket's headline value. **Con:** prices drift; we sign up for periodic updates.
- **B.** Ship `cost_usd: float | None = None` as a placeholder field. Add a TODO + follow-up issue. **Pro:** zero maintenance burden today; pricing tables live elsewhere later (or are imported from a third-party library). **Con:** the ticket lists `cost_usd` as a first-class capture; shipping it null-only means the audit/badge surface only ever shows `null` until a follow-up lands.
- **C.** Ship pricing for a single provider (e.g. Anthropic only — we have the most authoritative numbers there) and `null` for OpenAI. **Pro:** delivers value where we're most confident. **Con:** asymmetric experience.

#### Q2: `reasoning_tokens` — placeholder or capture-now?

- **A.** Placeholder `null` for this PR. Add reasoning-token plumbing in a future ticket once we touch `_providers/_openai.py` for o-series / GPT-5 thinking-model cases. **Pro:** smaller blast radius. **Con:** users running GPT-5 grading get `reasoning_tokens=null` even when the API returns them.
- **B.** Capture inside both providers (`_providers/_anthropic.py` for thinking models, `_providers/_openai.py` for o-series / GPT-5) in this PR. Add `reasoning_tokens: int | None` to `ModelResult`. **Pro:** accurate from day one. **Con:** widens scope; needs SDK-version checks.

#### Q3: `system_prompt_source` — ship two literals now, or all three?

Today only `"skill_md"` (auto-derived) and `"explicit"` (`EvalSpec.system_prompt`) are reachable. `"agents_md"` requires new resolver work in `spec.py` to detect a sibling `AGENTS.md` and route to it (Codex convention).

- **A.** Ship the literal-set as `{"skill_md", "explicit"}` and treat `"agents_md"` as a future-reserved value. The schema validator rejects it for now; future ticket adds the resolver branch + lifts the rejection.
- **B.** Ship the full literal-set `{"skill_md", "agents_md", "explicit"}` AND wire up the AGENTS.md resolver in `spec.py` in this same PR. Adds a story.
- **C.** Skip `system_prompt_source` from this PR entirely — defer to whichever ticket actually adds AGENTS.md support. Ticket shape would change.

#### Q4: `model_runner` capture for ClaudeCodeHarness — uniform with Codex?

Codex stamps `harness_metadata["model"]` already (`_codex.py:747`). ClaudeCodeHarness does not.

- **A.** Add `harness_metadata["model"]` to `ClaudeCodeHarness.invoke` for parity. Tiny patch in `_claude_code.py`. The sidecar then reads `skill_result.harness_metadata.get("model")` uniformly. **Pro:** uniform; future-proof.
- **B.** Read `self.harness.model` directly from the runner instance after invoke. **Con:** couples sidecar writer to harness internals; ignores the protocol's `harness_metadata` forward-compat surface.

#### Q5: Audit `--verbose` rendering shape

The ticket says "Audit verbose mode shows a context block per iteration." Two render shapes:

- **A.** Per-iteration block in `render_markdown` and `render_stdout_table` — one collapsible/dedicated section per iteration listing the eight captured fields. `render_json` adds a top-level `iterations[*].context` key.
- **B.** Tabular columns in `render_stdout_table` (one extra row group) + an annotation footer in markdown listing only the dimensions where iterations differ from each other ("only show what's interesting"). More work, less noise.
- **C.** JSON-only for now (just expose `context` in `render_json`); markdown/stdout get a single-line summary. Defer rich rendering to a follow-up.

#### Q6: Badge extension integration

Per `dual-version-external-schema-embed.md`, context surfaces in the sibling `<skill>.clauditor.json` (NOT the shields.io payload). Two design choices:

- **A.** Add `extension.context: {...}` (the same eight fields) AND bump `ClauditorExtension.schema_version` 1 → 2. Loader defaults missing `context` to `null` for v1.
- **B.** Add `extension.context: {...}` as a strictly-additive optional field; do NOT bump schema_version (loaders today already tolerate unknown keys for forward-compat). Simpler, but breaks the discipline of bumping when shape changes.
- **C.** Skip badge integration for this PR; ship audit `--verbose` only. Badge integration becomes a follow-up.

---

## Architecture Review

### Session 1 — 2026-05-08

Six parallel subagents (security, performance, data model, testing, API/integration, observability). Ratings + load-bearing findings:

| Area | Rating | Headline finding |
|---|---|---|
| Security | **blocker + concern** | (Blocker) AGENTS.md resolver MUST apply `.claude/rules/path-validation.md` recipe before any file read — otherwise a hostile/typoed `EvalSpec` can escape the spec dir. (Concern) `sandbox_mode` strings need a closed-set validator (`{"read-only", "workspace-write", "danger-full-access"}`) — `harness_metadata: dict[str, Any]` carries them today with no constraint. |
| Performance | pass | All adds are sub-millisecond. AGENTS.md stat per `SkillSpec.run` is microseconds, dwarfed by subprocess launch. Audit-side context read folds into the existing per-iteration loop (1–5 ms × N iterations is invisible against grading wall time). |
| Data Model | pass + concerns | (Pass) Schema-version first-key invariant preserved; null-only `reasoning_tokens` / `cost_usd` are forward-compat without future schema bump. (Concerns) `MAX_SCHEMA_VERSION` map needs `"context.json": 1` registered; `provider ↔ model_grader` nullability invariant needs to be explicit (both null iff no grading happened); `model_runner` always-non-null claim needs the harness contract (`harness_metadata["model"]`) wired in the same PR. |
| Testing | pass + 1 concern | All test surfaces have strong existing patterns (`TestGradingReportSerialization`, `TestCodexHarnessInvoke`, `_make_iteration_fixture`). One concern: AGENTS.md resolver tests are blocked on DEC-009 (same-dir vs walk-up). No `pytester.runpytest_inprocess` hazard. |
| API / Integration | pass + 1 blocker echo | Module placement → new `src/clauditor/context.py` (parallel to `benchmark.py` / `baseline.py`). AGENTS.md resolver inline in `SkillSpec.run` (single-caller exception per `pure-compute-vs-io-split.md`). Badge schema bump 1→2 requires `from_dict` default-on-read for missing `context`. |
| Observability | pass + 1 concern | (Pass) AGENTS.md resolver decision belongs in `context.json`, NOT stderr — not an implicit-coupling announcement family member. Sidecar absence follows existing `_read_json` silent-skip convention; malformed/wrong-schema fires the existing stderr warning via `_check_schema_version`. (Concern) Audit `--verbose` shows per-iteration blocks per DEC-005, but a top-line "iterations span 2 harnesses, 1 provider" rollup is underspecified — recommend deferring to #143 aggregation tickets. |

**Blockers (must resolve before refinement closes):**

1. **AGENTS.md resolver path-validation** — DEC-009 must pin (a) the search shape AND (b) explicitly reference `.claude/rules/path-validation.md`'s recipe (`is_absolute()` reject, `resolve(strict=True)`, `is_relative_to(spec_dir)`, `is_file()`).

**Concerns to resolve in refinement (DEC-007..DEC-011):**

- **DEC-007 — `model_runner` always-non-null vs nullable.** Architecture review recommendation: declare `model_runner: str` (always-non-null) and enforce the harness contract that `harness_metadata["model"]` is populated by both ClaudeCodeHarness (new in this PR per DEC-004) and CodexHarness (already populates per `_codex.py:747`). If the contract holds, sidecar writer reads `skill_result.harness_metadata["model"]` with a `KeyError` if missing (loud failure surfaces a contract violation immediately).
- **DEC-008 — `system_prompt_source` in transit.** Architecture review recommendation: `harness_metadata["system_prompt_source"]` (the forward-compat surface). Pure read at the sidecar writer; no new typed `SkillResult` field needed in this PR.
- **DEC-009 — AGENTS.md search path.** Architecture review recommendation: `<skill-dir>/AGENTS.md` first, fall back to `<project-root>/AGENTS.md`. Both reads gated by the path-validation recipe (resolved path must be `is_relative_to` the spec dir OR the project root). Codex's project-root convention preserved; per-skill override available.
- **DEC-010 — `json-schema-version.md` rule refresh.** Architecture review: ADD a short paragraph under "Canonical implementation" documenting `context.json` as a new sidecar family with `MAX_SCHEMA_VERSION["context.json"] = 1`. Refresh-in-place per `rule-refresh-vs-delete.md`; no new rule file.
- **DEC-011 — Audit loader integration.** Architecture review recommendation: `context.json` is read parallel to records, attached to render-time output only; does NOT participate in `IterationRecord` / `aggregate` machinery. Per-iteration display data, not score data.

**Out-of-scope clarifications surfaced by review:**

- Audit `--verbose` rollup summary ("N iterations span K harnesses") deferred to #143 aggregation tickets — this PR ships the per-iteration block only.
- `harness_metadata` namespacing (e.g. `"system": {"prompt_source": ...}`) deferred until a future harness adds enough keys to crowd the dict.

---

## Refinement Log

### Session 1 — 2026-05-08

User answered scoping Q1–Q6 in order: B (with follow-up ticket), A (with follow-up ticket), B, A, A, A.

#### DEC-001: `cost_usd` ships as `null`-only placeholder; pricing logic in #169

**Decision:** `IterationContext.cost_usd: float | None` is declared in this PR with default `None`. No pricing module is built; the field is always serialized as `null`. Follow-up ticket #169 ("Pricing: cost_usd estimation module for context.json") owns the `_providers/_pricing.py` module + price tables + wiring into `_write_workspace_sidecars`.

**Rationale:** The ticket explicitly permits "best-effort with fallback to null." Shipping the schema slot today fixes the on-disk shape forever; the pricing logic can land asynchronously without a schema bump (the field is already there). Avoids signing this PR up for periodic price-table maintenance and keeps the diff focused on the sidecar plumbing.

**Validation criteria embedded:** the `to_json()` round-trip test must show `cost_usd: null` in the serialized output; the `from_dict` loader must accept either `null` or a `float`; the bool-guard from `constant-with-type-info.md` applies if a future writer accidentally passes `True`/`False`.

#### DEC-002: `reasoning_tokens` ships as `null`-only placeholder; capture logic in #170

**Decision:** `IterationContext.reasoning_tokens: int | None` is declared in this PR with default `None`. No `ModelResult.reasoning_tokens` field is added; no per-provider backend changes for reasoning capture. Follow-up ticket #170 ("Reasoning tokens: capture separately-billed reasoning tokens in ModelResult") owns the `ModelResult` field, the per-provider capture, and the wiring at sidecar-write time.

**Rationale:** Same logic as DEC-001 — fixing the on-disk shape now lets the capture work land asynchronously without a schema bump. Avoids growing the PR with `_providers/_anthropic.py` + `_providers/_openai.py` SDK-version checks for thinking / o-series / GPT-5 reasoning models.

**Validation criteria embedded:** `to_json()` must serialize `reasoning_tokens: null`; `from_dict` must accept `null` or an `int`; the bool-guard from `constant-with-type-info.md` (a `True` passed through must be rejected as `int` because `bool ⊂ int` in Python).

#### DEC-003: `system_prompt_source` ships full literal-set `{"skill_md", "agents_md", "explicit"}` AND wires up the AGENTS.md resolver in `spec.py` in this PR

**Decision:** The `system_prompt_source` field accepts all three literals from day one. `src/clauditor/spec.py::SkillSpec.run`'s prompt-resolution block (lines 204–228) gains an AGENTS.md detection step BETWEEN the explicit-spec branch and the SKILL.md auto-derive: if `EvalSpec.system_prompt` is None AND a sibling `AGENTS.md` exists in the skill's directory (or, for the modern layout, in the parent dir of `SKILL.md`), read its body as the system prompt and stamp `system_prompt_source = "agents_md"`. Otherwise fall through to SKILL.md auto-derive (`"skill_md"`). When `EvalSpec.system_prompt` is set, stamp `"explicit"`. The resolved label is threaded into `SkillResult` (or its `harness_metadata`) so the sidecar writer can read it without re-running the resolver.

**Rationale:** The user picked option B explicitly to avoid the "future-reserved enum value with rejection" anti-pattern that costs a follow-up to lift. Adds one resolver branch plus tests; the AGENTS.md convention is the Codex / OpenAI ecosystem norm so users running `--harness codex` will benefit immediately. The cost is one extra story (US-002) in this PR.

**Open sub-questions parked for the architecture review:**
- AGENTS.md location: same dir as SKILL.md only, or also walk up one level? (Codex's convention is project-root.) Architecture review will recommend.
- Where does the resolved `system_prompt_source` live in transit — a new field on `SkillResult` or a key in `harness_metadata`?

**Validation criteria embedded:** Hard-validate against the literal-set per `pre-llm-contract-hard-validate.md`; reject any other value at `to_json` / `from_dict` boundaries with a descriptive `ValueError`.

#### DEC-004: `model_runner` capture for ClaudeCodeHarness via `harness_metadata["model"]` (uniform with Codex)

**Decision:** Add `harness_metadata["model"] = effective_model` to `ClaudeCodeHarness.invoke` (mirroring `_codex.py:747`). `IterationContext.model_runner` is read from `skill_result.harness_metadata.get("model")` regardless of harness — one read path, no harness-name branching at the sidecar layer.

**Rationale:** Per `harness-protocol-shape.md`'s "harness_metadata as forward-compat surface" subsection (DEC-007 of #148), `harness_metadata` is exactly the right place for per-harness observability that doesn't fit the protocol's stable members. Reading `harness.model` directly couples the sidecar to harness internals and cannot survive a future harness whose model is per-call. Uniformity also pays off when a third harness lands (raw-API, Vertex, etc.) — same key works.

**Validation criteria embedded:** Both harnesses populate the key for runs that have a model; the sidecar reader uses `.get("model")` so a missing key produces `model_runner = ""` (or we declare `model_runner: str | None` — TBD in detailing). The protocol's `harness_metadata` contract (a `dict[str, Any]` per `_harnesses/__init__.py`) is unchanged.

#### DEC-005: `audit --verbose` per-iteration block in all three render formats

**Decision:** When `--verbose` is passed to `clauditor audit`, every iteration's `context.json` (when present) is loaded and rendered. Three formats:
- `render_stdout_table`: a per-iteration "Context" sub-section under each iteration's row group, listing the eight captured fields as `key: value` lines.
- `render_markdown`: a "### Context" subsection per iteration with a small `key | value` table.
- `render_json`: a top-level `iterations[*].context: {...}` key on each iteration record. Always present (verbose or not — JSON consumers shouldn't need a flag to opt in to a stable field).

**Rationale:** The user picked A for breadth — show all eight fields per iteration. Option B ("only show what differs") is appealing UX but requires a cross-iteration diff in the renderer, doubles the tests, and the user explicitly chose against it. Option C (JSON-only) defers user-facing value too long. The per-iteration block is bounded (eight small fields) so even a 50-iteration audit stays readable.

**Validation criteria embedded:** `render_json` always includes `context` (even pre-#154 iterations get `context: null`); `render_markdown` and `render_stdout_table` only render the block under `--verbose`. Audit loader uses `_records_from_context` (or equivalent helper) and gates on schema_version per `json-schema-version.md`.

#### DEC-006: Badge integration adds `extension.context` AND bumps `ClauditorExtension.schema_version` 1 → 2

**Decision:** `src/clauditor/badge.py::ClauditorExtension` gains a `context: IterationContext | None` field. The extension's `schema_version` bumps from 1 to 2; the loader defaults missing `context` to `None` for v1 reads. The shields.io payload (`<skill>.json`) is unchanged — context goes only in the sibling `<skill>.clauditor.json` per `dual-version-external-schema-embed.md` (shields.io rejects unknown top-level keys). Badge writer reads the latest iteration's `context.json` (when present) at badge-generation time.

**Rationale:** The user picked A for schema-discipline reasons — "additive optional field without bump" (option B) is the exact slow-leak that `json-schema-version.md` was written to prevent. Option C (skip badge entirely) defers value and creates a second PR for the same conceptual change. The schema bump is cheap (one number + one default-on-read) and pays compounding interest in audit-trail clarity.

**Validation criteria embedded:** Pre-#154 `<skill>.clauditor.json` files (v1) load cleanly with `context = None`; new files emit `schema_version: 2` and the full context object. Round-trip test on both v1 and v2. Per `dual-version-external-schema-embed.md`, NO change to `<skill>.json` (shields.io payload).

### Decisions resolved post-Architecture-Review (user: "All Confirm")

#### DEC-007: `model_runner: str | None` (nullable); harness contract guarantees the **key** `harness_metadata["model"]` is present (value MAY be `None`)

**Decision:** `IterationContext.model_runner` is typed `str | None`. The contract is: every harness MUST populate the **key** `harness_metadata["model"]` on every successful invoke (presence required so the sidecar writer can use an unguarded subscript and fail loudly on a contract violation), but the **value** MAY be `None` when the harness legitimately cannot expose the model that was actually used. CodexHarness always populates a concrete string (`_codex.py:747`); ClaudeCodeHarness gains the key in this PR per DEC-004 but may stamp the value as `None` when neither the constructor nor the per-call override pinned a model — the `claude` CLI's stream-json `result` message carries no model field, so the actually-used model cannot be recovered after the fact. Recording a fabricated default (e.g. `"claude-sonnet-4-6"`) would be a lie that defeats the comparability purpose of `model_runner`. `None` is the honest "no model recorded" signal.

**Post-QG narrative update:** The original DEC-007 wording ("always-non-null `str`") proved too strict in QG: forcing `ClaudeCodeHarness` to fabricate a default for the unpinned-model case was rejected as worse than nullable. The contract was tightened to "key always present" and the type was relaxed to `str | None`. The `IterationContext.from_dict` validator and the audit/badge readers all accept `None` end-to-end; the rendered `audit --verbose` column emits `"-"` for the null case.

**Rationale:** The architectural review's "always-non-null is only safe if the harness contract is mechanically enforced" insight still holds — the contract enforcement just landed on key-presence (unguarded subscript) rather than value-non-nullability. A future harness that forgets to populate the key still fails its first integration test loudly. Allowing `None` for the value preserves the loud-failure-on-key-omission shape while admitting the honest "harness cannot recover this" case.

**Validation criteria embedded:** ClaudeCodeHarness invoke unit test asserts `result.harness_metadata["model"]` is present (key always set, value may be `None`); CodexHarness retains its existing concrete-string assertion; CLI integration test asserts `context.json["model_runner"]` round-trips both `str` and `None` values.

#### DEC-008: `system_prompt_source` flows via `harness_metadata["system_prompt_source"]`

**Decision:** The label string (`"explicit" | "agents_md" | "skill_md"`) lives in `SkillResult.harness_metadata["system_prompt_source"]`. Stamped by `SkillSpec.run` in the same block where the resolved system prompt is computed. The sidecar writer reads it via `skill_result.harness_metadata["system_prompt_source"]` (unguarded subscript per DEC-007's contract-enforcement shape).

**Rationale:** `harness_metadata` is the explicit forward-compat surface (`harness-protocol-shape.md`'s "harness_metadata as forward-compat surface" subsection, DEC-007 of #148). Adding a new typed field on `SkillResult` would be a breaking change to the dataclass that ripples into every test fixture; reusing the existing forward-compat dict is the right cost. Future PRs can promote the key to a typed field if it stabilizes across multiple consumers.

**Validation criteria embedded:** Three `tests/test_spec.py::TestSkillSpecRun` cases pin the three sources (explicit / agents_md / skill_md). Sidecar round-trip test asserts the field flows through correctly. Hard-validate the literal-set at `IterationContext.from_dict` per `pre-llm-contract-hard-validate.md`.

#### DEC-009: AGENTS.md search — `<skill-dir>/AGENTS.md` first, then `<project-root>/AGENTS.md`; both gated by `path-validation.md` recipe

**Decision:** `SkillSpec.run`'s prompt-resolution block (after the `EvalSpec.system_prompt` explicit branch, before the SKILL.md auto-derive) walks two locations:

1. `<skill-dir>/AGENTS.md` (the modern layout's per-skill override).
2. If absent, `<project-root>/AGENTS.md` (the Codex / OpenAI ecosystem norm — project-root convention).

Both reads gated by the security recipe from `.claude/rules/path-validation.md`:
- Resolve via `Path.resolve(strict=True)` (the `is_file()` check is implicit since we only attempt the read when the file exists).
- Verify the resolved path is `is_relative_to` the skill dir (for case 1) or the project root (for case 2).
- Reject any path that escapes its anchor with a descriptive `ValueError` naming the offending input.

The "project root" anchor is whatever clauditor's existing project-root resolver returns (re-uses the home-exclusion-aware walker — see `.claude/rules/project-root-home-exclusion.md`). When neither location yields a valid file, fall through to the SKILL.md auto-derive (`system_prompt_source = "skill_md"`). When a file IS found and validates, stamp `system_prompt_source = "agents_md"` and use its body as the system prompt.

**Rationale:** Matches Codex's ecosystem expectation while preserving per-skill override capability. Two-tier search costs ~10 LOC and one stat call (negligible). The path-validation recipe resolves the architecture-review BLOCKER — a hostile/typoed `EvalSpec` cannot escape spec containment, and the project-root anchor inherits the same home-exclusion guard that already protects `.clauditor/`.

**Validation criteria embedded:** Tests cover (a) skill-dir AGENTS.md wins over project-root AGENTS.md, (b) project-root AGENTS.md found when skill-dir absent, (c) absent both falls through to skill_md, (d) symlink escaping skill dir raises ValueError, (e) absolute path inside AGENTS.md content is fine (the recipe applies to the AGENTS.md path itself, not its content).

#### DEC-010: Refresh `.claude/rules/json-schema-version.md` in-place; no new rule file

**Decision:** Add a short subsection under "Canonical implementation" in `.claude/rules/json-schema-version.md` documenting `context.json` as a new sidecar family. Update the file's "Schema version bumps for #147 / #86" subsections by appending a new "New sidecar family — context.json (#154)" subsection that names: the file, the v1 schema (the dataclass field set), the `MAX_SCHEMA_VERSION["context.json"] = 1` registration, the always-v1 status (no future bumps needed for `reasoning_tokens` / `cost_usd` since they ship as nullable from day one).

**Rationale:** Per `.claude/rules/rule-refresh-vs-delete.md`, the rule's pattern is unchanged ("every persisted JSON declares schema_version") — only the canonical-implementation list grows. Refresh-in-place; the existing rule's framing fully covers the new sidecar.

**Validation criteria embedded:** A test in `tests/test_audit.py` asserts `MAX_SCHEMA_VERSION["context.json"] == 1` and rejects v999. The rule update lands in the same PR so the documented contract matches the shipped code.

#### DEC-011: Audit loader reads `context.json` parallel to records; renders only — no aggregation participation

**Decision:** `context.json` is loaded by a new pure helper `_read_context(skill_dir: Path) -> IterationContext | None` (no `_records_from_context` dispatcher, no `IterationRecord.context` field). The loader maps `iteration_dir → IterationContext | None` parallel to the existing `_records_from_*` reads in `audit.py::load_iterations`. The result attaches to the rendered output only — `render_json` always emits `iterations[*].context: {...} | null`, `render_markdown` and `render_stdout_table` emit the per-iteration block under `--verbose` only.

`aggregate()` and `IterationRecord` are unchanged. Context does not participate in pass-rate grouping or threshold evaluation.

**Rationale:** Context is comparability metadata, not score data. Adding `context: IterationContext | None` to `IterationRecord` would force every aggregate call site to branch on presence; the parallel-read shape keeps aggregation logic clean and matches `badge.py`'s precedent (it reads each sidecar independently, composes at render time).

**Validation criteria embedded:** `aggregate()` tests are unchanged (no new field). New tests in `tests/test_audit.py` cover (a) `_read_context` returns `IterationContext` for a valid sidecar, (b) returns `None` for a missing file (silent skip per the `_read_json` convention), (c) emits stderr warning + returns `None` for a wrong-schema-version file (per `_check_schema_version`), (d) `render_json` always includes `context` (legacy iterations get `null`), (e) `render_markdown` / `render_stdout_table` emit the block only under `--verbose`.

---

## Detailed Breakdown

Story ordering follows the natural data-flow direction: foundation (dataclass + validators) → harness-side data sources → resolver → sidecar writer → readers (audit + badge) → rule refresh → quality gate → patterns. Each story is sized for one Ralph context window.

Project validation command (embedded in every story's acceptance criteria):
```bash
uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing
```
The 80% coverage gate is enforced.

---

### US-001 — `IterationContext` dataclass + `to_json` / `from_dict` + closed-set validators

**Description:** Introduce a new pure-data module `src/clauditor/context.py` with an `IterationContext` dataclass, a `to_json() -> str` method (schema_version first key), a `from_dict(data: dict) -> IterationContext` loader with hard-validation of discriminator literals, and a closed-set sandbox-mode validator. Methodless dataclass per `data-vs-asserter-split.md`.

**Traces to:** DEC-001, DEC-002, DEC-007, DEC-008, plus the security CONCERN around `sandbox_mode` validation (closed-set `{"read-only", "workspace-write", "danger-full-access"}`).

**Acceptance criteria:**
- New file `src/clauditor/context.py` exports `IterationContext` dataclass with fields: `schema_version: int = 1`, `harness: str`, `provider: str | None`, `model_runner: str | None`, `model_grader: str | None`, `system_prompt_source: str`, `sandbox_mode: str | None`, `reasoning_tokens: int | None = None`, `cost_usd: float | None = None`. (Per DEC-007: `model_runner` is nullable so harnesses without a model field — e.g. `claude-code` stream-json — can record `None` rather than fabricating a default.)
- `to_json()` emits `schema_version` as the first key per `.claude/rules/json-schema-version.md`; round-trips losslessly.
- `from_dict(data)` hard-rejects: `harness` not in `{"claude-code", "codex"}`; `provider` not in `{"anthropic", "openai", None}`; `system_prompt_source` not in `{"explicit", "agents_md", "skill_md"}`; `sandbox_mode` not in `{"read-only", "workspace-write", "danger-full-access", None}` per the security review.
- Bool-guard on `reasoning_tokens` (must be `int`, not `bool`) per `.claude/rules/constant-with-type-info.md`.
- `__init__.py` exports `IterationContext` per the project export convention.
- Validation command passes; new tests reach >80% line coverage on `context.py`.

**Done when:** `tests/test_context.py` exists with the test classes below; `from src.clauditor import IterationContext` works; `ruff` clean.

**Files:**
- NEW `src/clauditor/context.py` (~80–120 LOC).
- MODIFY `src/clauditor/__init__.py` (add export).
- NEW `tests/test_context.py` (~150 LOC).

**Depends on:** none.

**TDD:**
- `TestIterationContextSerialization::test_to_json_first_key_is_schema_version`
- `TestIterationContextSerialization::test_to_json_emits_all_fields`
- `TestIterationContextSerialization::test_round_trip_full_payload`
- `TestIterationContextSerialization::test_round_trip_with_nulls` (validate-only iteration shape: `provider=None`, `model_grader=None`, `cost_usd=None`, `reasoning_tokens=None`)
- `TestIterationContextValidation::test_from_dict_rejects_unknown_harness` (with descriptive error message)
- `TestIterationContextValidation::test_from_dict_rejects_unknown_provider`
- `TestIterationContextValidation::test_from_dict_rejects_unknown_system_prompt_source`
- `TestIterationContextValidation::test_from_dict_rejects_unknown_sandbox_mode`
- `TestIterationContextValidation::test_from_dict_rejects_bool_for_reasoning_tokens` (per `constant-with-type-info.md` bool-guard)
- `TestIterationContextValidation::test_from_dict_accepts_known_literals` (parametrized over the valid literal sets)

---

### US-002 — Harness contract: `harness_metadata["model"]` (ClaudeCodeHarness) + sandbox_mode closed-set (CodexHarness)

**Description:** Add `harness_metadata["model"] = effective_model` to `ClaudeCodeHarness.invoke` (parity with CodexHarness). Promote the existing Codex `sandbox_mode` to a module-level `_SANDBOX_MODES: frozenset[str]` constant; validate inside `CodexHarness` before stamping `harness_metadata["sandbox_mode"]`. Establishes the harness contract that DEC-007 depends on.

**Traces to:** DEC-004, DEC-007, plus the security CONCERN on sandbox_mode.

**Acceptance criteria:**
- `ClaudeCodeHarness.invoke` populates `result.harness_metadata["model"]` with `effective_model` (the value used for the `--model` argv flag, or `self.model` when none was passed).
- `CodexHarness` defines `_SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})` at module level; raises `ValueError` if a future code path attempts to stamp an unknown mode (defense-in-depth — today's value is hardcoded, so no runtime change).
- Both harnesses' existing tests continue to pass.
- New tests cover the model-stamp paths (default, override).
- Validation command passes.

**Done when:** `result.harness_metadata["model"]` is asserted in tests for both harnesses; `_SANDBOX_MODES` exists and is referenced.

**Files:**
- MODIFY `src/clauditor/_harnesses/_claude_code.py` (~5 LOC).
- MODIFY `src/clauditor/_harnesses/_codex.py` (~3 LOC for the constant + validation).
- MODIFY `tests/test_runner.py` or `tests/test_harnesses.py` (~50 LOC).

**Depends on:** none.

**TDD:**
- `TestClaudeCodeHarnessHarnessMetadata::test_invoke_populates_model_default` — `ClaudeCodeHarness(model="claude-sonnet-4-6").invoke(...)` → `result.harness_metadata["model"] == "claude-sonnet-4-6"`.
- `TestClaudeCodeHarnessHarnessMetadata::test_invoke_populates_model_override` — `invoke(..., model="claude-opus-4-7")` overrides; `harness_metadata["model"] == "claude-opus-4-7"`.
- `TestCodexHarnessSandboxModes::test_sandbox_modes_constant_matches_known_values`
- `TestCodexHarnessSandboxModes::test_invalid_sandbox_mode_raises` (defense-in-depth — call the validator directly)

---

### US-003 — AGENTS.md resolver in `SkillSpec.run` + `system_prompt_source` stamping

**Description:** Extend `SkillSpec.run`'s prompt-resolution block (lines 204–228) with a two-tier AGENTS.md search (`<skill-dir>/AGENTS.md` first, then `<project-root>/AGENTS.md`). Both reads gated by the `path-validation.md` recipe. Stamp the resolved source label into `harness_metadata["system_prompt_source"]` on the returned `SkillResult`.

**Traces to:** DEC-003, DEC-008, DEC-009. Resolves the security blocker.

**Acceptance criteria:**
- New pure helper (likely `src/clauditor/paths.py::resolve_agents_md(skill_path: Path, project_root: Path) -> Path | None`) returns the validated path or `None`. The helper applies `Path.resolve(strict=True)` and `is_relative_to(anchor)` per `.claude/rules/path-validation.md`; raises `ValueError` on escape attempts.
- `SkillSpec.run`'s prompt-resolution block consults the helper between the explicit-spec and SKILL.md auto-derive branches.
- Three source labels round-trip correctly: `"explicit"` (when `EvalSpec.system_prompt` is set), `"agents_md"` (when AGENTS.md found and validated at either tier), `"skill_md"` (auto-derive fallback).
- `SkillResult.harness_metadata["system_prompt_source"]` is populated on every successful run.
- Validation command passes; >80% coverage on the new resolver helper.

**Done when:** All four AGENTS.md test cases (DEC-009 acceptance set) pass; `harness_metadata["system_prompt_source"]` is asserted in `tests/test_spec.py`.

**Files:**
- MODIFY `src/clauditor/spec.py` (~25 LOC in `SkillSpec.run`).
- MODIFY `src/clauditor/paths.py` (new pure helper, ~30 LOC).
- MODIFY `tests/test_spec.py` (~120 LOC of new test class).
- MODIFY `tests/test_paths.py` (~80 LOC for the resolver-helper tests).

**Depends on:** US-002 (the harness_metadata pattern).

**TDD:**
- `TestResolveAgentsMd::test_skill_dir_wins_when_both_exist`
- `TestResolveAgentsMd::test_falls_back_to_project_root`
- `TestResolveAgentsMd::test_returns_none_when_neither_exists`
- `TestResolveAgentsMd::test_rejects_symlink_escape_from_skill_dir`
- `TestResolveAgentsMd::test_rejects_absolute_path_outside_anchor`
- `TestSkillSpecRunSystemPromptSource::test_explicit_eval_spec_wins_over_agents_md`
- `TestSkillSpecRunSystemPromptSource::test_agents_md_wins_over_skill_md_body`
- `TestSkillSpecRunSystemPromptSource::test_falls_through_to_skill_md_when_agents_md_absent`
- `TestSkillSpecRunSystemPromptSource::test_harness_metadata_carries_source_label`

---

### US-004 — Sidecar writer integration in `cli/grade.py` and `cli/validate.py`

**Description:** Wire `IterationContext` construction and `context.json` write into both iteration-allocating CLI commands. Read fields from `harness_metadata` (model, system_prompt_source, sandbox_mode), the resolved provider (cli-seam value), the primary report's `model` (for `model_grader`), and harness identity from `harness.name`. Write inside the staging block per `.claude/rules/sidecar-during-staging.md`.

**Traces to:** DEC-001, DEC-002, DEC-005 (write side), DEC-007, DEC-008.

**Acceptance criteria:**
- `cli/grade.py::_write_workspace_sidecars` builds an `IterationContext` from in-scope variables and writes `workspace.tmp_path / "context.json"` BEFORE `workspace.finalize()`. For non-grading runs (no L2/L3 reached), `provider`, `model_grader`, and `cost_usd` are stamped `None`.
- `cli/validate.py::cmd_validate` performs the equivalent write inside its staging block (line ~258 region). Always: `provider=None`, `model_grader=None`, `cost_usd=None`, `reasoning_tokens=None`. Stamps `harness`, `model_runner`, `system_prompt_source`, and `sandbox_mode` (when applicable).
- `cost_usd` and `reasoning_tokens` ship as `None` always (placeholders for #169 / #170).
- The write sits inside the existing `try/except → workspace.abort()` envelope per the staging contract.
- CLI integration tests verify `context.json` lands on disk with the correct fields after `cmd_grade` and `cmd_validate`.
- Validation command passes; coverage holds.

**Done when:** `iteration-N/<skill>/context.json` exists after both `cmd_grade` and `cmd_validate` runs in tests; field round-trip matches resolved values.

**Files:**
- MODIFY `src/clauditor/cli/grade.py` (~25 LOC).
- MODIFY `src/clauditor/cli/validate.py` (~20 LOC).
- MODIFY `tests/test_cli.py` (~150 LOC of new test cases).
- MODIFY `tests/test_cli_validate.py` if it exists, otherwise extend `tests/test_cli.py`.

**Depends on:** US-001, US-002, US-003.

**TDD:**
- `TestCmdGradeWritesContextJson::test_anthropic_default_path` (full populated fields)
- `TestCmdGradeWritesContextJson::test_openai_provider_path` (with `--grading-provider openai`)
- `TestCmdGradeWritesContextJson::test_no_l2_l3_branch` (provider/model_grader stay set when grade ran; only validate-only path nulls them)
- `TestCmdValidateWritesContextJson::test_default_path` (provider/model_grader/cost_usd/reasoning_tokens all `null`)
- `TestCmdValidateWritesContextJson::test_codex_harness_carries_sandbox_mode` (under `--harness codex`)
- `TestCmdGradeContextJsonFirstKeyOrder` (assert serialized first key is `schema_version`)

---

### US-005 — Audit `--verbose` reads context + renders per-iteration block; `MAX_SCHEMA_VERSION` registration

**Description:** Register `"context.json": 1` in `audit.py::MAX_SCHEMA_VERSION`. Add a pure helper `_read_context(skill_dir: Path) -> IterationContext | None` that reads + schema-validates the sidecar. Wire it into `load_iterations` parallel to existing `_records_from_*` reads (NOT into `IterationRecord` per DEC-011). Update three render functions: `render_json` always emits `iterations[*].context` (null for legacy); `render_markdown` and `render_stdout_table` emit a per-iteration block under `--verbose`. Add `--verbose` flag to the audit CLI subparser.

**Traces to:** DEC-005, DEC-010 (rule pointer), DEC-011.

**Acceptance criteria:**
- `MAX_SCHEMA_VERSION["context.json"] == 1`; `_is_accepted_version("context.json", 1)` → `True`; `_is_accepted_version("context.json", 999)` → `False`.
- `_read_context` returns `IterationContext` for a valid sidecar, `None` for missing (silent skip per existing `_read_json` convention), `None` plus stderr warning for wrong-schema-version (via `_check_schema_version`).
- `render_json` ALWAYS includes a `context` key on each iteration record (legacy: `null`). No `--verbose` gate on JSON output.
- `render_markdown` / `render_stdout_table` emit the per-iteration context block ONLY under `--verbose` (default behavior unchanged for non-verbose audit users).
- `clauditor audit --verbose` accepts the new flag without argparse errors.
- `IterationRecord` and `aggregate()` are UNCHANGED (verified by existing tests passing without modification).
- Validation command passes.

**Done when:** Verbose audit output shows the per-iteration context block in markdown/stdout/json; non-verbose output unchanged in markdown/stdout but JSON now includes `context: null` for legacy iterations.

**Files:**
- MODIFY `src/clauditor/audit.py` (~80 LOC: helper + render-path additions + map registration).
- MODIFY `src/clauditor/cli/audit.py` (~5 LOC: argparse `--verbose` flag).
- MODIFY `tests/test_audit.py` (~200 LOC).

**Depends on:** US-001, US-004.

**TDD:**
- `TestMaxSchemaVersion::test_context_json_registered_at_v1`
- `TestMaxSchemaVersion::test_context_json_v999_rejected`
- `TestReadContext::test_returns_iteration_context_for_valid_sidecar`
- `TestReadContext::test_returns_none_on_missing_file_silent`
- `TestReadContext::test_returns_none_with_stderr_warning_on_schema_mismatch` (use `capsys`)
- `TestRenderJsonContext::test_always_includes_context_key`
- `TestRenderJsonContext::test_legacy_iteration_emits_null`
- `TestRenderMarkdownVerbose::test_per_iteration_block_under_verbose`
- `TestRenderMarkdownVerbose::test_no_block_without_verbose`
- `TestRenderStdoutTableVerbose` (mirrors markdown shape)
- `TestAggregateUnchanged::test_iteration_record_has_no_context_field` (regression guard for DEC-011)

---

### US-006 — Badge `ClauditorExtension` v1 → v2 bump with optional `context` field

**Description:** Add `context: IterationContext | None = None` field to `ClauditorExtension`. Bump `_CLAUDITOR_EXTENSION_SCHEMA_VERSION` from 1 to 2. `from_dict` (or equivalent loader) defaults missing `context` to `None` for v1 reads. `_extension_to_dict` omits the `context` block when `None` (mirrors existing l1/l3/variance optional-block pattern). Badge writer reads the latest iteration's `context.json` (when present) and stamps it onto the extension. Shields.io `<skill>.json` payload UNCHANGED per `.claude/rules/dual-version-external-schema-embed.md`.

**Traces to:** DEC-006.

**Acceptance criteria:**
- `ClauditorExtension.schema_version` defaults to 2; the constant is updated.
- v1 `<skill>.clauditor.json` files load cleanly with `context = None` (default-on-read precedent).
- v2 files round-trip with full `context` populated.
- `<skill>.json` (shields.io payload) is unchanged byte-for-byte before and after this story (regression assertion).
- Badge writer reads `iteration-N/<skill>/context.json` for the latest iteration N and threads through; absent file → `extension.context = None`.
- Validation command passes.

**Done when:** A `clauditor badge` run after a graded iteration writes the extension with `schema_version: 2` and the populated `context` block; pre-existing v1 fixtures still load.

**Files:**
- MODIFY `src/clauditor/badge.py` (~30 LOC).
- MODIFY `src/clauditor/cli/badge.py` (~10 LOC for the latest-iteration read).
- MODIFY `tests/test_badge.py` (~120 LOC).
- MODIFY `tests/test_cli_badge.py` (~50 LOC).

**Depends on:** US-001, US-004.

**TDD:**
- `TestClauditorExtensionSchemaBump::test_schema_version_is_2`
- `TestClauditorExtensionV1Compat::test_v1_payload_loads_with_context_none`
- `TestClauditorExtensionV2RoundTrip::test_serializes_and_deserializes`
- `TestClauditorExtensionContextOmission::test_extension_dict_omits_context_when_none`
- `TestShieldsPayloadUnchanged::test_shields_json_byte_identical_before_after`
- `TestCmdBadgeReadsLatestContext::test_extension_carries_context_when_present`
- `TestCmdBadgeReadsLatestContext::test_extension_context_null_when_absent`

---

### US-007 — Refresh `.claude/rules/json-schema-version.md` with `context.json` paragraph

**Description:** Add a "New sidecar family — context.json (#154)" subsection under "Canonical implementation" in `.claude/rules/json-schema-version.md`. Document the file, its v1 schema (the dataclass field set with nullability table), the `MAX_SCHEMA_VERSION["context.json"] = 1` registration, and the always-v1 status (no future bumps needed for `reasoning_tokens` / `cost_usd` since they ship nullable from day one). Refresh-in-place per `.claude/rules/rule-refresh-vs-delete.md`.

**Traces to:** DEC-010.

**Acceptance criteria:**
- `.claude/rules/json-schema-version.md` gains a new subsection (~20 lines) under the canonical-implementation list.
- The subsection names: filename, dataclass module, dataclass field list with nullability, `MAX_SCHEMA_VERSION` registration line, the always-v1 contract, the link to follow-up tickets #169 (`cost_usd`) and #170 (`reasoning_tokens`).
- The rule's existing prose, structure, and other sections are byte-unchanged (refresh-in-place).
- Validation command passes (no code change in this story; doc only).

**Done when:** A `git diff` on the rule file shows ONLY the additive subsection.

**Files:**
- MODIFY `.claude/rules/json-schema-version.md` (~20 LOC additive).

**Depends on:** US-001, US-005.

**TDD:** N/A — pure documentation update.

---

### US-008 — Quality Gate (code review × 4 + CodeRabbit)

**Description:** Run code reviewer four times across the full changeset, fixing every real bug found per pass. Run CodeRabbit if available. Project validation must pass after all fixes.

**Traces to:** All implementation decisions; this is the safety net before merge.

**Acceptance criteria:**
- Four passes of the code-reviewer agent (`subagent_type=code-reviewer`) on the full diff vs `dev`. Each pass fixes every real bug; false positives are documented inline.
- One CodeRabbit pass via PR comment if the integration is available.
- All `.claude/rules/` constraints from Discovery are explicitly verified (the validation criteria embedded in DEC-001..DEC-011, especially the security path-validation invariants).
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes the 80% gate.
- Each context-related test class from US-001..US-006 is present and green.

**Done when:** All four review passes complete with no real bugs remaining; CI green; merge-ready.

**Files:** Whatever the review passes touch.

**Depends on:** US-001, US-002, US-003, US-004, US-005, US-006, US-007.

---

### US-009 — Patterns & Memory

**Description:** Update `.claude/rules/`, `docs/`, or memory entries with new patterns or learnings from #154. The most likely candidates: the harness-contract pattern from DEC-007 ("unguarded subscript surfaces missing-key bugs loudly") may deserve a sentence on `.claude/rules/harness-protocol-shape.md`. The two-tier path-validation pattern from DEC-009 (resolver consults two anchors with the same recipe) may warrant a paragraph on `.claude/rules/path-validation.md`. Inspect what was learned during implementation and add only if non-obvious.

**Traces to:** Cross-cutting patterns surfaced during implementation.

**Acceptance criteria:**
- Inspect the implementation diffs for any pattern that recurred or was non-obvious; add to the corresponding rule file (refresh-in-place per `.claude/rules/rule-refresh-vs-delete.md`).
- If no non-obvious pattern emerged, this story closes with a note ("no rule updates needed") rather than fabricating one.
- Memory updates per the `auto memory` shape if any user feedback or session-context lessons emerged.

**Done when:** Either the rule files are updated (with a justification line) OR a one-line "no patterns to add" note is recorded in this plan.

**Files:** Possibly `.claude/rules/harness-protocol-shape.md`, `.claude/rules/path-validation.md`, or memory.

**Depends on:** US-008.

---

### Dependency graph

```text
US-001 ────┬─→ US-003 ──┐
           ├─→ US-004 ──┼─→ US-005 ──┐
US-002 ────┴─→ US-003   │             ├─→ US-008 ──→ US-009
                        ├─→ US-006 ──┤
                        └─→ US-007 ──┘
```

US-001 and US-002 are independent; everything downstream depends on at least one of them.

---

## Beads Manifest

- **Epic:** `clauditor-hqv` — #154: per-iteration context.json sidecar
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/154-context-sidecar`
- **Branch:** `feature/154-context-sidecar`
- **Plan PR:** https://github.com/wjduenow/clauditor/pull/171

| ID | Story | Priority | Depends on |
|---|---|---|---|
| `clauditor-hqv.1` | US-001 — IterationContext dataclass + to_json/from_dict + closed-set validators | P2 | — |
| `clauditor-hqv.2` | US-002 — harness_metadata['model'] in ClaudeCodeHarness + sandbox_mode closed-set in CodexHarness | P2 | — |
| `clauditor-hqv.3` | US-003 — AGENTS.md resolver in SkillSpec.run + system_prompt_source stamping | P2 | hqv.2 |
| `clauditor-hqv.4` | US-004 — sidecar writer in cli/grade.py + cli/validate.py | P2 | hqv.1, hqv.2, hqv.3 |
| `clauditor-hqv.5` | US-005 — audit --verbose reads context + per-iteration block render + MAX_SCHEMA_VERSION registration | P2 | hqv.1, hqv.4 |
| `clauditor-hqv.6` | US-006 — badge ClauditorExtension v1→v2 bump with optional context field | P2 | hqv.1, hqv.4 |
| `clauditor-hqv.7` | US-007 — refresh json-schema-version.md with context.json paragraph | P3 | hqv.1, hqv.5 |
| `clauditor-hqv.8` | Quality Gate — code review x4 + CodeRabbit | P2 | hqv.1..hqv.7 |
| `clauditor-hqv.9` | Patterns & Memory — update conventions and docs | P4 | hqv.8 |

**Initial ready set:** `clauditor-hqv.1` (US-001) + `clauditor-hqv.2` (US-002) — both unblocked, can run in parallel.
