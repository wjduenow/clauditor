# 152 — Multi-harness sidecar: add harness field to L1/L2/L3 sidecars

## Meta

- **Ticket:** https://github.com/wjduenow/clauditor/issues/152
- **Branch:** `feature/152-sidecar-harness-field`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/152-sidecar-harness-field`
- **Phase:** published
- **PR:** https://github.com/wjduenow/clauditor/pull/167
- **Sessions:** 1 (2026-05-03)
- **Depends on:** #147 (CLOSED — provider_source field, MAX_SCHEMA_VERSION map), #151 (CLOSED — EvalSpec.harness + four-layer precedence + construct_harness)
- **Blocks:** #153 (cross-{harness,provider} grouping refusal in audit/trend/compare)
- **Related rule:** `.claude/rules/json-schema-version.md` — explicitly names #152 as the assertions.json harness-axis bump

## Discovery

### Ticket summary

Mirror of #147 on the harness axis. Sidecars must record which harness produced
the output so audit/trend doesn't average across non-comparable runs.

In scope:
- `assertions.json` schema_version 1 → 2, add top-level `harness: str = "claude-code"`
- `extraction.json` schema_version 3 → 4, add `harness` field (sibling to existing `provider_source`)
- `grading.json` schema_version 3 → 4, add `harness` field (sibling to `provider_source`)
- Audit loader accepts all prior versions; defaults missing `harness` to `"claude-code"` for legacy reads
- `clauditor audit` groups by `(harness, provider, layer, id)` (was 3-tuple, becomes 4-tuple)
- L1 audit table gets a `harness` column where it doesn't have a `provider` one
- Update `.claude/rules/json-schema-version.md` "Schema version bumps" section

Out of scope (per ticket):
- Sandbox-mode / system-prompt-source / reasoning-token capture (#154)
- Cross-{harness,provider} averaging refusal in trend/compare (#153)
- Harness-axis bump on `history.jsonl` — ticket is silent; flagged as scoping question

### Codebase landscape (post-#147, post-#151)

**Sidecar writers:**
- `src/clauditor/quality_grader.py::GradingReport` (lines 75–104) — dataclass at v3 with `provider_source: str = "anthropic"`. `to_json` at lines 129–168 stamps `schema_version: 3` first; `from_json` at 171–209 defaults missing `provider_source` to `"anthropic"` for v1/v2 reads.
- `src/clauditor/grader.py::ExtractionReport` (lines 86–145) — same shape, v3, `provider_source` field; `to_json` at 153–185, `from_json` at 188–227.
- `src/clauditor/cli/grade.py::_write_assertions_sidecar` (lines 876–941) — assertions.json is **inline-built** (no dataclass), currently emits v1 with shape `{"schema_version": 1, "skill", "iteration", "runs": [...]}`.

**Audit loader (`src/clauditor/audit.py`):**
- `MAX_SCHEMA_VERSION` map (lines 52–56): `{"assertions.json": 1, "extraction.json": 3, "grading.json": 3}`. Per-#147 DEC-008 a future bump is "one-number-per-file edit."
- `_is_accepted_version` (lines 59–87) and `_check_schema_version` (107–129) enforce the range check + stderr warn-and-skip.
- `IterationRecord` (lines 157–177) has `provider: str = "anthropic"` placeholder (added #147 line 176). No `harness` field.
- `AuditAggregate` (lines 180–198) carries `provider: str = "anthropic"` (line 198). No `harness` field.
- `aggregate()` grouping (lines 445–509) uses `(provider, layer, id)` 3-tuple key (line 463).
- `_records_from_assertions` (242–276) — sets `provider="anthropic"` placeholder per #147 DEC-002.
- `_records_from_extraction` (279–313) — reads `provider_source` via `_provider_or_default(data.get("provider_source"))` (line 297).
- `_records_from_grading` (316–353) — same provider read pattern (line 333).

**History (`src/clauditor/history.py`):**
- `SCHEMA_VERSION = 2`, `_ACCEPTED_SCHEMA_VERSIONS = frozenset({1, 2})` (post-#147).
- `append_record` (lines 127–197) takes mandatory `provider: str` keyword; rejects `"auto"`. No `harness` field today.
- v1 reads default missing `provider` to `"anthropic"`.

**Harness propagation (post-#151):**
- `SkillSpec.run(harness_name_override: str | None = None, ...)` materializes a fresh `SkillRunner` via `construct_harness()` (spec.py 246–262) when override is set; reuses runner otherwise.
- `SkillResult` (runner.py 54–101) has **no `harness` field today**. Comment at line 69 explicitly warns: "runtime-only — do not serialize to sidecars without bumping schema_version".
- CLI grade.py resolves harness via `_resolve_harness(args, spec.eval_spec)` (lines 384/414) per #151 four-layer precedence; threads to `spec.run(..., harness_name_override=harness_name)` (lines 644/677).
- The harness identity at sidecar-emit time is currently only on the active runner's `runner.harness.name`; not surfaced on `SkillResult`.

**Test bumps required:**
- `tests/test_audit.py:93,115,129,264` — assertions.json v1 fixtures → v2 (or assert defaulted-on-read).
- `tests/test_cli_auth_guard.py:181`, `tests/test_cli_lint.py:504` — v1 assertions samples.
- `tests/test_grader.py:1648,1819` — extraction/grading v3 → v4.
- `tests/test_grader.py:1670,1840,1859` — legacy v1/v2 read tests stay; add v3 legacy default-on-read tests.
- ~10 sites total across audit + grader + cli tests.

### Convention-rule constraints (from `.claude/rules/`)

Directly applicable:
1. **`json-schema-version.md`** — anchor rule. Use `MAX_SCHEMA_VERSION` map (DEC-008 pattern); writers stamp `schema_version` as first key; loaders default missing field on legacy reads. Rule explicitly names #152 as the assertions.json harness-axis bump.
2. **`pure-compute-vs-io-split.md`** — sidecar `to_json` stays on dataclass; audit grouping stays pure (no I/O in `aggregate`).
3. **`back-compat-shim-discipline.md`** — Pattern 2 (class identity) — no new shim added in #152, but if any field is moved across modules, add identity test.
4. **`sidecar-during-staging.md`** — assertions.json / extraction.json / grading.json all written inside `workspace.tmp_path` before `workspace.finalize()`. Already enforced; #152 must not break.
5. **`spec-cli-precedence.md`** — Four-layer precedence — harness (#151) is the resolution path; #152 only consumes the resolved harness name; no new precedence layer.
6. **`harness-protocol-shape.md`** — The string written into sidecars is `Harness.name` (ClassVar). Use the canonical attribute, not a synthesized string.
7. **`centralized-sdk-call.md`** + **`multi-provider-dispatch.md`** — harness ≠ provider per #151 DEC-010. Sidecar grouping by `(harness, provider, layer, id)` is two orthogonal dimensions; do not merge.

Read but not applicable:
- `constant-with-type-info.md` (no new KeySpec mixed-type field)
- `eval-spec-stable-ids.md` (sidecar metadata, not per-entry id)
- `pre-llm-contract-hard-validate.md` (no LLM output)
- `permissive-parser-strict-validator.md` (sidecars are clauditor-authored)
- `data-vs-asserter-split.md` (no new helper class)

No `workflow-project.md` exists.

### #147 canonical pattern to mirror

#147's structural moves that #152 will replay:
- **DEC-001 (field naming)** — `provider_source` was the on-disk byte-identical field name. For #152 the field is just `harness` (no `_source` suffix; harness identity is materialized by #151, not a "source" choice the user makes mid-call).
- **DEC-002 (L1 placeholder)** — #147 left L1 at v1 with `IterationRecord.provider="anthropic"` placeholder. #152 inverts this: assertions.json bumps to v2 because L1 *can* be honestly stamped (the harness ran the skill that produced the output being asserted on). After #152, the L1 placeholder for `provider` remains (no LLM call) but `harness` is real.
- **DEC-008 (MAX_SCHEMA_VERSION map)** — already in place; #152 is a one-number-per-file edit.
- **DEC-012 (history bump)** — #147 bumped history v1→v2 alongside sidecars; #152 should consider the parallel v2→v3 bump (open question DEC-S3 below).
- **US-001 / US-003 shape** — sidecar writer + reader bump per file, then `IterationRecord` / `AuditAggregate` field, then grouping-key widen, then renderer column, then tests.

## Scoping questions for the user

DEC-S1 — **How should the harness name reach sidecar emitters?**
- **(A)** Add a `harness: str` field to `SkillResult` dataclass (runtime-only mirror of how provider_source lives on the orchestrator return). Sidecar emitters read `result.harness`. Cleanest seam; one place to populate; matches the existing pattern of "everything the sidecar needs lives on the result the orchestrator returns."
- **(B)** Thread the resolved `harness_name` separately into each sidecar write call. Avoids touching `SkillResult` (which has the "do not serialize without bumping schema_version" comment), but spreads the concern across multiple call sites.
- **(C)** Read `runner.harness.name` from the active runner inside the writers. Tightest coupling to runner; brittle if the runner is reused across calls.

DEC-S2 — **`assertions.json`: top-level harness or per-run within `runs[]`?**
- **(A)** Top-level field, mirroring `extraction.json` / `grading.json`'s `provider_source` placement. Simplest; one harness per command invocation today (no per-rep harness override).
- **(B)** Per-run inside each entry of `runs[]`. Forward-compat for #155 (pytest fixtures parametrize harness across runs in one command), but premature today.

DEC-S3 — **Bump `history.jsonl` v2 → v3 in this ticket, or defer?**
- **(A)** Bump in #152: add `harness` field, mandatory keyword on `append_record`, default missing → `"claude-code"` for legacy reads. Keeps sidecar + history axes in lockstep (#147 did both).
- **(B)** Defer: ticket scope is silent on history; only #153 (which depends on #152) needs `trend` to detect mixed harness from history.
- Recommendation: (A) — locks the axes together and avoids a tiny follow-up later.

DEC-S4 — **`IterationRecord.harness` and `AuditAggregate.harness` default value?**
- **(A)** `harness: str = "claude-code"` default — mirrors `provider: str = "anthropic"` from #147 and matches the legacy-read default.
- **(B)** Required (no default) — forces every record-builder site to pass it explicitly.
- Recommendation: (A) for consistency with #147; constructors at the record-builder sites still pass it explicitly.

DEC-S5 — **L1 audit-table column treatment.**
- Ticket says: "L1 audit gets a `harness` column where it didn't have a `provider` one." Confirming the renderer model:
  - L1 row gets a real `harness` column (sourced from assertions.json v2)
  - L1 row gets either no `provider` cell or a placeholder ("—" / "—") — which?
- **(A)** Show "—" or empty in the provider column for L1 rows (visually grouped, but null-call-out).
- **(B)** Hide the provider column entirely if the iteration set has only L1 records.
- Recommendation: (A) — matches markdown-table semantics where columns are stable across rows.

DEC-S6 — **Pytest plugin / fixture surface.** The pytest fixtures (`clauditor_grader`, `clauditor_blind_compare`) build `GradingReport`s in tests. Do those fixtures need to populate `harness` on the report (and threaded through), or can tests construct the field directly when needed?
- **(A)** Fixtures populate `harness` from `EvalSpec.harness` resolution (mirror of how `provider` flows through). Fixture callers don't have to think about it.
- **(B)** Add `harness` to fixture factory kwargs; default to `"claude-code"`; require explicit pass when testing non-default harness.
- Recommendation: (A) — closes the loop with how the production CLI seam threads it.

## Discovery answers (confirmed 2026-05-03)

DEC-S1=A — `harness: str` on `SkillResult`. DEC-S2=A — top-level on assertions.json. DEC-S3=A — bump history.jsonl v2→v3 in this ticket. DEC-S4=A — defaults `"claude-code"`. DEC-S5=A — L1 provider cell shows "—" placeholder. DEC-S6=A — fixtures auto-populate `harness` from `EvalSpec.harness` resolution.

## Architecture review

| Area                        | Rating  | Headline finding |
|-----------------------------|---------|------------------|
| Schema migration safety     | PASS    | `MAX_SCHEMA_VERSION` map + `_is_accepted_version` already absorb per-file bumps; legacy default-on-read mirrors #147 exactly. |
| Aggregation grouping widen  | CONCERN | 5 call sites consume the 3-tuple key (`apply_thresholds`, three renderers, `AuditAggregate` ctor); all reachable, mechanical. |
| `SkillResult.harness` field | PASS    | Runtime-only; `runner.harness.name` available at construction in `SkillRunner.run`. The "do not serialize without bumping" comment refers to observation fields, not adopted-for-sidecar fields. |
| `history.jsonl` v3 bump     | PASS    | Three `append_record` call sites identified; mandatory-keyword pattern mirrors #147's provider rollout. |
| L1 placeholder inversion    | PASS    | `provider="anthropic"` placeholder remains correct (no LLM call); only `harness` moves from placeholder to real-from-v2-sidecar. |
| `_write_assertions_sidecar` | PASS    | Inline writer at `cli/grade.py:876-941`; called from `_grade_primary_arm` at line 789 with `harness_name` already in scope at line 414. |
| Existing test coverage map  | PASS    | 11 schema-literal sites + 35 history sites + 16 cli sites + 8 ctor sites + 5 grouping-tuple sites = ~75 total mechanical updates. No blind spots. |
| New test cases needed       | PASS    | Six suites well-scoped: round-trip v4, legacy default-on-read, grouping splits on harness, renderer columns, history v3, fixture auto-population. |
| Coverage gate (80%)         | PASS    | New branches mirror provider's #147 branches; existing test discipline covers them. |
| Pytester coverage hazard    | PASS    | No `runpytest_inprocess + mock.patch + cov` combos planned; tests are pure round-trips. |
| Audit renderers             | PASS    | Three renderers (stdout/markdown/JSON) take a HARNESS column cleanly; column order open as DEC-A1 below. |
| Audit JSON output schema    | CONCERN | `render_json` returns its own `schema_version: 2` (post-#147); needs v3 bump for harness. Open as DEC-A2 below. |
| Other audit consumers       | PASS    | `clauditor badge` reads sidecars directly (per-iteration singleton; unaffected). `compare`/`trend` out of scope per ticket but cleanly compatible. |
| Stderr observability        | PASS    | `_check_schema_version` warning text is parameterized by `MAX_SCHEMA_VERSION`; future-proof. |
| Documentation               | CONCERN | `.claude/rules/json-schema-version.md` needs a "Schema version bumps for #152" section. `docs/cli-reference.md:473` already namedrops #152 — needs a sentence about the column. Open as DEC-A3 below. |
| Dual-version external-schema rule | PASS | Doesn't apply — sidecars are clauditor-internal; badge pair already correctly split. |

**Master rating: PASS** (no blockers). Three concerns landed as refinement questions DEC-A1, DEC-A2, DEC-A3 below.

## Refinement questions (architecture concerns)

**DEC-A1 — Audit-table column order.** Where does `HARNESS` slot in the renderer's column sequence?
- (A) `HARNESS | PROVIDER | LAYER | ID | ...` — harness leftmost; matches the grouping-key tuple order `(harness, provider, layer, id)` declared in the ticket.
- (B) `PROVIDER | HARNESS | LAYER | ID | ...` — harness between provider and layer; preserves existing `PROVIDER` lead column from #147.
- Recommendation: (A) — keys-tuple order matches reading order; "harness" is the higher-level dispatch axis (it determines which provider's data even exists). Also makes `audit_json` entries' field order intuitive.

**DEC-A2 — Audit JSON output (`render_json`) schema bump.** The audit's own JSON output (not sidecars) is currently `schema_version: 2`. With `harness` becoming a field on every entry:
- (A) Bump to `schema_version: 3`. Add to top-level `harnesses_seen[]` array (sibling of existing `providers_seen[]`). Each `assertions[]` entry gains `harness` field.
- (B) Stay at v2 with optional `harness` key. Backward-permissive but semantically dishonest (the field is mandatory, not optional, in v3 sidecars).
- Recommendation: (A) — explicit bump, sibling top-level array, mandatory per entry. Mirrors #147's audit JSON v1→v2 bump exactly.

**DEC-A3 — Documentation footprint.** Which docs absolutely must update in this ticket vs. follow-on?
- (A) Mandatory in #152: `.claude/rules/json-schema-version.md` "Schema version bumps for #152" section; `docs/cli-reference.md:473` audit description sentence.
- (B) Also include: README.md "Three Layers of Validation" mention if any; `docs/audit-reference.md` if exists (scout: doesn't exist).
- Recommendation: (A) — strict footprint matching the rule's anchor pattern; no speculative doc work.

## Architecture answers (confirmed 2026-05-03)

DEC-A1=A — HARNESS leftmost column. DEC-A2=A — `render_json` bumps to v3 with `harnesses_seen[]`. DEC-A3=A — strict doc footprint.

## Refinement log — formal decisions

**DEC-001 — Field name on disk: `harness` (no `_source` suffix).**
*Rationale:* #147 used `provider_source` because the operator chose between API/CLI/auto transports per call. `harness` identity is materialized once by #151's resolver; the field records what ran the skill, not a "source" choice. Keeps the field name visually distinct from `provider_source`.

**DEC-002 — `harness` reaches sidecar emitters via `SkillResult.harness: str` (DEC-S1=A).**
*Rationale:* Mirrors how `provider_source` flows on grader returns. `SkillRunner._invoke` reads `self.harness.name` (the `Harness.name` ClassVar per `harness-protocol-shape.md`) at SkillResult construction. The "runtime-only — do not serialize" comment in `runner.py:69` governs observation fields like `api_key_source`/`harness_metadata`; `harness` is now an adopted-for-sidecar field, deliberately serialized.

**DEC-003 — `assertions.json` carries `harness` as a top-level field (DEC-S2=A), v1 → v2.**
*Rationale:* One harness per command invocation today. Matches `provider_source` placement in `extraction.json` / `grading.json`. Per-run harness within `runs[]` is premature — when #155 lands per-rep harness parametrization, the field can be additionally placed inside `runs[]` entries without removing the top-level (or migrated cleanly to v3 then).

**DEC-004 — `extraction.json` v3 → v4 and `grading.json` v3 → v4 add `harness` sibling to `provider_source`.**
*Rationale:* Mirrors #147 DEC-001 placement. Loaders default missing `harness` to `"claude-code"` for legacy v1/v2/v3 reads; `provider_source` defaults to `"anthropic"` for v1/v2 reads (preserved).

**DEC-005 — `history.jsonl` v2 → v3 in this ticket (DEC-S3=A).** Adds top-level `harness` per record; `harness=` becomes mandatory keyword on `append_record`. Legacy v1/v2 reads default missing `harness` to `"claude-code"`.
*Rationale:* Locks the harness axis in lockstep with sidecar bumps; mirrors #147's parallel history v1→v2 bump (DEC-012 of #147). Avoids a tiny follow-up immediately before #153 needs `trend` to refuse mixed-harness history.

**DEC-006 — `IterationRecord.harness: str = "claude-code"` and `AuditAggregate.harness: str = "claude-code"` (DEC-S4=A).**
*Rationale:* Mirrors `provider: str = "anthropic"` from #147. Default keeps legacy-default-on-read semantics consistent. Production record-builder sites still pass it explicitly (per #147 precedent).

**DEC-007 — Audit grouping key widens from `(provider, layer, id)` to `(harness, provider, layer, id)` 4-tuple.**
*Rationale:* Honest grouping per ticket. Same `id` under different harnesses produces two distinct `AuditAggregate` buckets; this is the load-bearing test. Five downstream consumers (apply_thresholds + three renderers + the dataclass field add) are all reachable in one synchronized PR.

**DEC-008 — L1 audit row shows `harness` real, `provider` "—" placeholder (DEC-S5=A).**
*Rationale:* L1 makes no LLM call — provider is genuinely "no value." The em-dash placeholder cell preserves the column structure in markdown/stdout tables (rendering invariant: every column appears on every row regardless of layer). For audit JSON output (post-DEC-A2 v3), L1 entries emit `"provider": "anthropic"` (the placeholder per #147 DEC-002 stays unchanged) AND `"harness": <real>` from the v2 sidecar.

**DEC-009 — Audit-table column order: `HARNESS | PROVIDER | LAYER | ID | ...` (DEC-A1=A).**
*Rationale:* Matches the grouping-key tuple order. Harness is the higher-level dispatch axis (it determines which provider's data even exists for a given run).

**DEC-010 — Audit JSON output (`render_json`) bumps `schema_version: 2` → `3` (DEC-A2=A).** Adds top-level `harnesses_seen[]` array (sibling to existing `providers_seen[]`). Each `assertions[]` entry gains a `"harness": str` field.
*Rationale:* Audit's own JSON is a separate schema from the sidecars it aggregates. Mirrors the #147 audit JSON v1→v2 bump exactly.

**DEC-011 — Pytest fixtures auto-populate `harness` from `EvalSpec.harness` resolution (DEC-S6=A).**
*Rationale:* `clauditor_grader` and `clauditor_blind_compare` fixtures resolve provider via `_resolve_fixture_provider` (post-#162); harness mirrors the same shape via the resolver from #151. Fixture callers don't have to think about it.

**DEC-012 — Strict documentation footprint (DEC-A3=A).** Two doc updates only:
- `.claude/rules/json-schema-version.md` — new section "Schema version bumps for #152" (parallel to existing "#86" and "#147" sections); documents per-file bumps and the `harnesses_seen[]` audit JSON addition.
- `docs/cli-reference.md` — one sentence on `audit` describing the new HARNESS column and the v3 audit JSON output (line ~473 already namedrops #152, expand it).

No README change; `docs/audit-reference.md` does not exist.

**DEC-013 — Schema-bump isolation: extract `assertions.json` writer into a tiny dataclass (DEC-internal).**
*Rationale (architectural pre-flight):* `assertions.json` is currently inline-built in `_write_assertions_sidecar` (cli/grade.py:876–941). Bumping it to v2 via inline edits is fine for one bump, but the field will be re-bumped in #154 (sandbox-mode/system-prompt-source) and possibly again. Per `pure-compute-vs-io-split.md`'s "json-schema-version anchor", the writers that own `schema_version` should be a `to_json` method on a dataclass. **Decision: DEFER the dataclass extraction; bump v1→v2 in-place as inline JSON.** Rationale to defer: in-place is one PR; extraction is a separate refactor that doesn't change wire behavior, and the inline shape is small enough to bump again next time. Track as a follow-up issue if #154 also touches it.

**DEC-014 — `BlindReport` is OUT of scope for #152.**
*Rationale:* `compare --blind` takes two pre-captured outputs as inputs (no skill execution at judge time → no harness axis). The harness that produced each captured output is recorded in *that capture's own sidecars*, not on the blind-judge result. Separately, `BlindReport.to_json` at `quality_grader.py:270` is still at `schema_version: 1` and deliberately does not serialize `provider_source` (line 260 comment); that is a #147 follow-up gap — also out of scope here.

### Schema-version table (target state after #152)

| Artifact | Current (dev) | After #152 | Touched by #152 |
|---|---|---|---|
| `assertions.json` | v1 | **v2** | yes — adds top-level `harness` |
| `extraction.json` | v3 | **v4** | yes — adds `harness` sibling to `provider_source` |
| `grading.json` | v3 | **v4** | yes — adds `harness` sibling to `provider_source` |
| `history.jsonl` | v2 | **v3** | yes — adds top-level `harness`; mandatory `harness=` keyword on `append_record` |
| audit `render_json` output | v2 | **v3** | yes — adds top-level `harnesses_seen[]` + per-entry `harness` |
| `BlindReport` JSON | v1 | (stays v1) | NO — no harness axis at blind-judge time |

`MAX_SCHEMA_VERSION` map post-#152: `{"assertions.json": 2, "extraction.json": 4, "grading.json": 4}`.

`history.py` post-#152: `SCHEMA_VERSION = 3`, `_ACCEPTED_SCHEMA_VERSIONS = frozenset({1, 2, 3})`.

## Open questions (none)

All blockers and concerns resolved.

## Detailed breakdown — stories

Validation command per `CLAUDE.md`: `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing`. Every story's "Acceptance Criteria" includes a clean run of this command.

---

### US-001 — `SkillResult.harness: str` field + populate at runner construction

**Description.** Add a `harness: str` field to `SkillResult` and populate it from the active runner's `harness.name` ClassVar at the point `SkillResult` is constructed in `SkillRunner._invoke` / `SkillRunner.run`. This is the foundation: every sidecar emitter downstream reads `result.harness` to get the value to stamp.

**Traces to:** DEC-002.

**Files:**
- `src/clauditor/runner.py` — add `harness: str` field to `SkillResult` dataclass (lines 54–101); populate in `SkillRunner._invoke` (or wherever `SkillResult(...)` is constructed) by reading `self.harness.name` per `harness-protocol-shape.md`.
- Update the "runtime-only — do not serialize" comment at line 69 to clarify that `harness` is an adopted-for-sidecar field, distinct from observation-only fields (`api_key_source`, `harness_metadata`).

**TDD:**
- `tests/test_runner.py::TestSkillResult::test_harness_field_present_with_default` — direct dataclass construction; default `"claude-code"`.
- `tests/test_runner.py::TestSkillRunner::test_invoke_populates_harness_from_active_harness` — uses `MockHarness` (name="mock") via `SkillRunner(harness=MockHarness(...))`; `SkillResult.harness == "mock"`.
- `tests/test_runner.py::TestSkillRunner::test_invoke_populates_harness_claude_code_default` — default `ClaudeCodeHarness` produces `result.harness == "claude-code"`.

**Acceptance criteria:**
- `SkillResult.harness: str` exists with default `"claude-code"`.
- After any `SkillRunner.run`/`_invoke` call, `result.harness == runner.harness.name`.
- `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing` passes (≥80% coverage).

**Done when:** All three TDD tests pass; existing tests still pass (no `SkillResult(...)` direct constructions break — default kwarg keeps them green).

**Depends on:** none (foundation).

---

### US-002 — `assertions.json` v1 → v2 with top-level `harness` field

**Description.** Bump the inline-built `assertions.json` writer to `schema_version: 2`, adding a top-level `harness` field. Thread the value from the `SkillResult.harness` field (US-001) at the call site.

**Traces to:** DEC-003, DEC-006 (sidecar wire-format), DEC-013.

**Files:**
- `src/clauditor/cli/grade.py::_write_assertions_sidecar` (lines 876–941) — accept new `harness: str` parameter; stamp `"schema_version": 2` (line 930) and add top-level `"harness": harness` key (canonical key order: `schema_version`, `harness`, then existing fields).
- Call site at `cli/grade.py:789` (inside `_grade_primary_arm`) — pass `harness=result.harness` (or equivalent, post-US-001).
- `src/clauditor/audit.py::MAX_SCHEMA_VERSION` — bump `"assertions.json"` from `1` to `2` (lines 52–56). Loader behavior is forward-compat; no other audit changes here (US-005 owns the record-builder default-on-read).

**TDD:**
- `tests/test_cli_grade.py::TestWriteAssertionsSidecar::test_emits_schema_version_2` — stamps v2 first key.
- `tests/test_cli_grade.py::TestWriteAssertionsSidecar::test_includes_harness_field` — top-level `harness` from caller value.
- `tests/test_cli_grade.py::TestWriteAssertionsSidecar::test_harness_passed_through_unchanged` — caller passes `"codex"`, payload stamps `"codex"`.

**Acceptance criteria:**
- `assertions.json` payload starts with `{"schema_version": 2, "harness": "<name>", ...}`.
- `MAX_SCHEMA_VERSION["assertions.json"] == 2`.
- All `tests/test_audit.py` v1 fixture sites updated (4 sites: `93,115,129,264`) — either bumped to v2-with-harness or kept as v1 to assert legacy default-on-read (split decision in test).
- `tests/test_cli_auth_guard.py:181` and `tests/test_cli_lint.py:504` — bumped to v2 OR refactored to test legacy default explicitly.
- Validation command passes.

**Done when:** All three TDD tests pass; `_grade_primary_arm` integration test still emits assertions.json successfully end-to-end.

**Depends on:** US-001.

---

### US-003 — `ExtractionReport` v3 → v4 with `harness` field

**Description.** Add `harness: str = "claude-code"` field to `ExtractionReport`; bump `to_json` to v4; extend `from_json` to accept v1/v2/v3/v4 with default-on-read for missing `harness`. Orchestrator-side: `extract_and_grade` / `extract_and_report` pass `harness=skill_result.harness` to the report constructor.

**Traces to:** DEC-004, DEC-006.

**Files:**
- `src/clauditor/grader.py::ExtractionReport` (lines 86–145) — add `harness: str = "claude-code"` field after `provider_source`.
- `ExtractionReport.to_json` (lines 153–185) — emit `"schema_version": 4` (was 3) and `"harness": self.harness` field (place after `provider_source` to mirror provider sibling).
- `ExtractionReport.from_json` (lines 188–227) — add v4 branch reading `harness`; v1/v2/v3 reads default missing `harness` to `"claude-code"`.
- `src/clauditor/audit.py::MAX_SCHEMA_VERSION` — bump `"extraction.json"` from `3` to `4`.
- `extract_and_grade` / `extract_and_report` orchestrators in `grader.py` — pass `harness=skill_result.harness` to `ExtractionReport(...)` construction.

**TDD:**
- `tests/test_grader.py::TestExtractionReport::test_to_json_emits_v4_with_harness`.
- `tests/test_grader.py::TestExtractionReport::test_from_json_v4_round_trip`.
- `tests/test_grader.py::TestExtractionReport::test_from_json_v3_defaults_harness_to_claude_code`.
- `tests/test_grader.py::TestExtractionReport::test_from_json_v1_v2_legacy_still_load`.

**Acceptance criteria:**
- `ExtractionReport.to_json()` first key `schema_version: 4`; includes `"harness": ...`.
- v3 sidecar (no harness) round-trips through `from_json` with `report.harness == "claude-code"`.
- `MAX_SCHEMA_VERSION["extraction.json"] == 4`.
- Existing v3 round-trip tests at `tests/test_grader.py:1648,1819` updated to v4 or split into legacy reads.
- Validation command passes.

**Done when:** All four TDD tests pass; orchestrator tests for `extract_and_*` produce a report with non-empty `harness`.

**Depends on:** US-001.

---

### US-004 — `GradingReport` v3 → v4 with `harness` field

**Description.** Same shape as US-003, mirrored on `GradingReport`.

**Traces to:** DEC-004, DEC-006.

**Files:**
- `src/clauditor/quality_grader.py::GradingReport` (lines 75–104) — add `harness: str = "claude-code"`.
- `GradingReport.to_json` (lines 129–168) — emit `"schema_version": 4` and `"harness"` field after `provider_source`.
- `GradingReport.from_json` (lines 171–209) — v4 branch + v1/v2/v3 default-on-read for missing `harness`.
- `src/clauditor/audit.py::MAX_SCHEMA_VERSION` — bump `"grading.json"` from `3` to `4`.
- `grade_quality` orchestrator in `quality_grader.py` — pass `harness=skill_result.harness` to `GradingReport(...)`.

**TDD:**
- `tests/test_quality_grader.py::TestGradingReport::test_to_json_emits_v4_with_harness`.
- `tests/test_quality_grader.py::TestGradingReport::test_from_json_v4_round_trip`.
- `tests/test_quality_grader.py::TestGradingReport::test_from_json_v3_defaults_harness_to_claude_code`.
- `tests/test_quality_grader.py::TestGradingReport::test_from_json_v1_v2_legacy_still_load`.

**Acceptance criteria:**
- `GradingReport.to_json()` first key `schema_version: 4`; includes `"harness"`.
- v3 sidecar round-trips with default `harness="claude-code"`.
- `MAX_SCHEMA_VERSION["grading.json"] == 4`.
- Existing v3 tests at `tests/test_quality_grader.py:360,404,483` updated.
- Validation command passes.

**Done when:** All four TDD tests pass; `grade_quality` orchestrator tests produce a report with non-empty `harness`.

**Depends on:** US-001.

---

### US-005 — Audit `IterationRecord` + `AuditAggregate` `harness` field; grouping key widened to 4-tuple

**Description.** Add `harness: str = "claude-code"` to `IterationRecord` and `AuditAggregate`. Widen `aggregate()`'s grouping key from `(provider, layer, id)` to `(harness, provider, layer, id)`. Update `_records_from_assertions` (read v2 sidecar's harness; default "claude-code" for v1), `_records_from_extraction` and `_records_from_grading` (read v4 sidecars' harness; default for v1/v2/v3). Update `apply_thresholds` to consume the 4-tuple key.

**Traces to:** DEC-006, DEC-007, DEC-008.

**Files:**
- `src/clauditor/audit.py::IterationRecord` (lines 157–177) — add `harness: str = "claude-code"`.
- `src/clauditor/audit.py::AuditAggregate` (lines 180–198) — add `harness: str = "claude-code"`.
- `_records_from_assertions` (lines 242–276) — read `data.get("harness", "claude-code")`; pass into `IterationRecord(harness=...)`.
- `_records_from_extraction` (lines 279–313) — read `data.get("harness", "claude-code")`.
- `_records_from_grading` (lines 316–353) — read `data.get("harness", "claude-code")`.
- `aggregate()` (lines 445–509) — change grouping key from 3-tuple to 4-tuple `(harness, provider, layer, id)` (line 463).
- `apply_thresholds` and any other consumer of the aggregate dict's keys — adapt to 4-tuple (~5 sites total per architecture review).

**TDD:**
- `tests/test_audit.py::TestAggregate::test_grouping_splits_on_harness` — same `(provider, layer, id)` under different harnesses produces TWO distinct `AuditAggregate` buckets (load-bearing).
- `tests/test_audit.py::TestRecordsFromAssertions::test_reads_harness_from_v2_sidecar`.
- `tests/test_audit.py::TestRecordsFromAssertions::test_defaults_harness_for_v1_legacy`.
- `tests/test_audit.py::TestRecordsFromExtraction::test_reads_harness_from_v4_sidecar` + legacy default sibling.
- `tests/test_audit.py::TestRecordsFromGrading::test_reads_harness_from_v4_sidecar` + legacy default sibling.
- `tests/test_audit.py::TestApplyThresholds::test_handles_four_tuple_keys` — function still works after key widening.

**Acceptance criteria:**
- `IterationRecord.harness` and `AuditAggregate.harness` exist with default `"claude-code"`.
- Aggregate grouping key is 4-tuple `(harness, provider, layer, id)`.
- Records from v1/v2/v3 legacy sidecars default `harness="claude-code"`.
- All existing 3-tuple-iteration tests in `test_audit.py` (~5 sites near lines 313–335) updated to 4-tuple.
- Validation command passes.

**Done when:** Load-bearing grouping-split test passes; all `_records_from_*` default-on-read tests pass.

**Depends on:** US-002, US-003, US-004.

---

### US-006 — Audit renderers: HARNESS column + audit JSON v3 with `harnesses_seen[]`

**Description.** Update `render_stdout_table`, `render_markdown`, and `render_json` to surface the harness dimension. Column order leftmost: `HARNESS | PROVIDER | LAYER | ID | ...` (DEC-009). For L1 rows, the provider cell renders as `"—"` em-dash placeholder (DEC-008). The audit's own JSON output schema bumps from `2` to `3` and gains a top-level `harnesses_seen[]` array sibling to the existing `providers_seen[]` (DEC-010).

**Traces to:** DEC-008, DEC-009, DEC-010.

**Files:**
- `src/clauditor/audit.py::render_stdout_table` (lines 670–689) — add `HARNESS` column leftmost; render L1 provider as `"—"`.
- `src/clauditor/audit.py::render_markdown` (lines 692–773) — add `harness` column to per-layer detail tables; same `"—"` for L1 provider.
- `src/clauditor/audit.py::render_json` (line 814) — bump `schema_version` from `2` to `3`; add top-level `harnesses_seen: list[str]` (sorted, deduped); each `assertions[]` entry gains `"harness": str` field.

**TDD:**
- `tests/test_audit.py::TestRenderStdoutTable::test_includes_harness_column_leftmost`.
- `tests/test_audit.py::TestRenderStdoutTable::test_l1_row_shows_em_dash_for_provider`.
- `tests/test_audit.py::TestRenderMarkdown::test_per_layer_table_includes_harness_column`.
- `tests/test_audit.py::TestRenderJson::test_schema_version_3`.
- `tests/test_audit.py::TestRenderJson::test_includes_harnesses_seen_array`.
- `tests/test_audit.py::TestRenderJson::test_per_entry_harness_field`.

**Acceptance criteria:**
- All three renderers emit a HARNESS column / field.
- `render_json()` first key is `schema_version: 3`; top-level `harnesses_seen` populated.
- L1 stdout/markdown rows show `"—"` (em-dash) in provider column.
- Validation command passes.

**Done when:** All six TDD tests pass; existing renderer tests updated to handle the new column.

**Depends on:** US-005.

---

### US-007 — `history.jsonl` v2 → v3: `harness` field; mandatory `harness=` keyword on `append_record`

**Description.** Bump `history.py::SCHEMA_VERSION` to `3`; widen `_ACCEPTED_SCHEMA_VERSIONS` to `{1, 2, 3}`. Add `harness: str` as a mandatory keyword-only parameter on `append_record` (mirrors the post-#147 `provider=` mandatory-keyword pattern). Update production call sites in `cli/grade.py` and `cli/extract.py` (and any others discovered) to pass the resolved harness name. `read_records` defaults missing `harness` to `"claude-code"` for v1/v2 reads.

**Traces to:** DEC-005.

**Files:**
- `src/clauditor/history.py::SCHEMA_VERSION` — `2` → `3`.
- `src/clauditor/history.py::_ACCEPTED_SCHEMA_VERSIONS` — `frozenset({1, 2})` → `frozenset({1, 2, 3})`.
- `src/clauditor/history.py::append_record` (lines 127–197) — add mandatory keyword-only `harness: str`; reject blank/whitespace-only; reject `"auto"` (mirroring provider validation); add `"harness": harness` to the record template (lines 181–192).
- `src/clauditor/history.py::read_records` — when reading v1/v2 records, default missing `harness` to `"claude-code"`.
- Production call sites — every `append_record(...)` in `src/` gains `harness=harness_name`. Per architecture review there are 3 production sites; update each.

**TDD:**
- `tests/test_history.py::TestAppendRecord::test_v3_writes_harness_field`.
- `tests/test_history.py::TestAppendRecord::test_harness_keyword_required` — missing `harness=` raises `TypeError`.
- `tests/test_history.py::TestAppendRecord::test_harness_blank_rejected`.
- `tests/test_history.py::TestAppendRecord::test_harness_auto_rejected`.
- `tests/test_history.py::TestReadRecords::test_v2_legacy_defaults_harness_to_claude_code`.
- `tests/test_history.py::TestReadRecords::test_v3_round_trip`.

**Acceptance criteria:**
- `SCHEMA_VERSION == 3`; `_ACCEPTED_SCHEMA_VERSIONS == {1, 2, 3}`.
- v3 record contains `"harness": <name>`.
- v1/v2 reads default `harness="claude-code"` per record.
- All ~51 test-side `append_record` call sites compile (mostly auto-resolve via `harness=` default in fixtures, or explicit pass at integration sites).
- Validation command passes.

**Done when:** All six TDD tests pass; integration tests for `cli grade` / `cli extract` still write history end-to-end.

**Depends on:** US-001 (call sites need a resolved harness name; #151 already provides resolution at the CLI seam).

---

### US-008 — Pytest fixtures auto-populate `harness`

**Description.** The grader-related fixtures (`clauditor_grader`, `clauditor_triggers`) auto-populate `harness` from `EvalSpec.harness` resolution, mirroring the post-#162 provider flow. `clauditor_blind_compare` is unaffected (no harness axis at blind-judge time per DEC-014).

**Traces to:** DEC-011.

**Files:**
- `src/clauditor/pytest_plugin.py::clauditor_grader` factory — resolve harness from `eval_spec.harness` (use the same resolver path the CLI uses); thread into `spec.run(harness_name_override=...)` and into the resulting `GradingReport`.
- `src/clauditor/pytest_plugin.py::clauditor_triggers` factory — same.
- `clauditor_blind_compare` — verify no change needed.

**TDD:**
- `tests/test_pytest_plugin.py::TestClauditorGrader::test_uses_harness_from_eval_spec`.
- `tests/test_pytest_plugin.py::TestClauditorGrader::test_defaults_harness_to_claude_code_when_eval_spec_unset`.
- `tests/test_pytest_plugin.py::TestClauditorTriggers::test_uses_harness_from_eval_spec`.

**Acceptance criteria:**
- Fixture-produced `GradingReport.harness` reflects the spec's resolved harness.
- No fixture-caller code changes required for default `"claude-code"` behavior.
- Validation command passes.

**Done when:** All three TDD tests pass.

**Depends on:** US-001, US-004.

---

### US-009 — Documentation: rule + cli-reference updates

**Description.** Add a "Schema version bumps for #152" section to `.claude/rules/json-schema-version.md` documenting the per-file bumps (assertions 1→2, extraction 3→4, grading 3→4, history 2→3, audit `render_json` 2→3) and the `harnesses_seen[]` audit-output addition. Update the existing namedrop of #152 in `docs/cli-reference.md:473` to a sentence describing the HARNESS column and the v3 audit JSON shape.

**Traces to:** DEC-012.

**Files:**
- `.claude/rules/json-schema-version.md` — append new "Schema version bumps for #152" section after the existing #147 section. Document each of the five bumps + the `MAX_SCHEMA_VERSION` map updates + L1 placeholder inversion (harness real, provider "—") + audit JSON v3 shape.
- `docs/cli-reference.md` — at line ~473 (the audit subsection), expand the namedrop to a sentence: "L1 audit rows now show a real HARNESS value (sourced from `assertions.json` v2) alongside the existing PROVIDER column, which renders as '—' for L1 rows since L1 makes no LLM call."

**TDD:** Not applicable (text-only).

**Acceptance criteria:**
- New rule section exists with all five bumps documented; cross-links to canonical implementations.
- `cli-reference.md` audit section reads cleanly post-update.
- `tests/test_bundled_skill.py` doesn't regress (the bundled skill SKILL.md doesn't reference these docs directly).
- Validation command passes (doc edits are markdown; ruff/pytest still green).

**Done when:** Both files updated; no markdown formatting regressions.

**Depends on:** US-001 through US-008 (doc reflects the implemented behavior).

---

### Quality Gate

**Description.** Run code-reviewer four times across the full changeset (each pass fixes any real bugs found); run CodeRabbit if available. The full validation command must pass after all fixes.

**Files:** Any file flagged by review; tests.

**Acceptance criteria:**
- Four code-reviewer passes complete with no remaining real bugs.
- CodeRabbit pass complete with no remaining real bugs.
- `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing` passes ≥80% coverage.
- All changes committed.

**Done when:** All gates green; no outstanding review findings.

**Depends on:** US-001 through US-009.

---

### Patterns & Memory

**Description.** Update `.claude/rules/` and memory with patterns learned. Likely candidates:
- `json-schema-version.md` — already updated by US-009; verify the rule's "Canonical implementation" section names the new code anchors (US-002–US-007).
- Consider whether the "harness adopted-for-sidecar field on `SkillResult`" pattern deserves a note (small enough to fold into `harness-protocol-shape.md` rather than a new rule).
- Memory: any session-level insights worth persisting (probably none — this is a structural mirror of #147).

**Files:** `.claude/rules/*.md`, `~/.claude/projects/.../memory/*.md` if applicable.

**Acceptance criteria:**
- Rules updated to reflect new canonical implementation anchors for #152.
- No new rule files created unless a genuinely new pattern emerged.
- All changes committed.

**Done when:** Rules accurately point at #152's code; rule-refresh discipline (per `.claude/rules/rule-refresh-vs-delete.md`) honored.

**Depends on:** Quality Gate.
