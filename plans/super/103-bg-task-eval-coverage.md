# Plan: #103 — Close the remaining background-task evaluation-coverage bullets

## Meta

- **Ticket:** [#103](https://github.com/wjduenow/clauditor/issues/103) — "Support evaluation of skills that use Task(run_in_background=true) for parallel sub-agents"
- **Branch:** `feature/103-bg-task-eval-coverage`
- **Worktree:** `../worktrees/clauditor/103-bg-task-eval-coverage`
- **Phase:** devolved
- **Planner:** super-plan-team (Lead + Ticket Analyst + Codebase Scout)
- **Priority:** P3
- **Beads epic:** `clauditor-c71p`
  - US-001 `clauditor-c71p.1` — live-gated fixture + warning test
  - US-002 `clauditor-c71p.2` — example skill + Recipe A/B worked example
  - US-003 `clauditor-c71p.3` — warning + CLI hint docs link
  - US-004 `clauditor-c71p.4` — Tier 3 revisit-triggers ADR subsection
  - US-005 `clauditor-c71p.5` — related-docs sweep (deps: .1–.4)
  - US-006 `clauditor-c71p.6` — Quality Gate (dep: .5)
  - US-007 `clauditor-c71p.7` — Patterns & Memory (dep: .6)

## TL;DR

Issue #103 is a mostly-shipped umbrella for the "skills using
`Task(run_in_background=true)` cannot be evaluated end-to-end under
`claude -p` print mode" gap. #116 already shipped `--sync-tasks`, the
skill-compatibility matrix, Recipe A/B prose, and the Tier 2 research
ADR. This plan closes the **four remaining open bullets** (three from
the 2026-04-24 status comment + one Tier 1 item the Analyst found was
never checked off):

1. **Worked example** for Recipe A/B — a runnable companion example
   skill (parallel-research fan-out shape) + before/after diffs in
   `docs/skill-usage.md`.
2. **Live-gated known-bad fixture** — a real skill that launches
   `Task(run_in_background=true)`, exercised by a live-gated test that
   asserts the `#97` `background-task:` warning fires end-to-end.
3. **Warning → docs link** (Tier 1 bullet 3, still open) — append the
   `docs/skill-usage.md#skill-compatibility` anchor to the warning
   text.
4. **Tier 3 revisit-tracking** — a structured "revisit triggers"
   subsection in the existing transport-research ADR. **No
   implementation** of the turn-loop emulator (no-go pending upstream).

Tier 3 implementation, the Tier 2 spike, and the already-shipped Tier 1
matrix/prose are **out of scope**.

---

## Phase 1 — Discovery

### What / Why / Who

- **What:** Close the cheap remaining coverage bullets on the #103
  umbrella so the background-task gap has a complete user-facing story
  (worked example), an end-to-end fidelity test (live fixture), an
  actionable warning (docs link), and a documented revisit path
  (Tier 3 tracking).
- **Why:** clauditor's mission is "auditor for Claude Code skills." A
  whole skill category (parallel sub-agent fan-out) can't be evaluated
  at full fidelity. The detect-and-warn (#97) + `--sync-tasks` (#116)
  responses are correct, but the user-facing guidance and the
  end-to-end proof are incomplete.
- **Who:** Skill authors who hit the loud `background-task:` warning and
  need an actionable refactoring path; maintainers tracking when the
  upstream features that unblock Tier 3 land.

### Codebase findings (Scout)

| Topic | Anchor | Note |
|---|---|---|
| Detector (pure) | `src/clauditor/_harnesses/_claude_code.py:199` `_detect_background_task_noncompletion(stream_events, final_text)` | Two-signal: waiting-regex (`:131`) OR `num_turns < launches + 2` |
| Launch counter (pure) | `_claude_code.py:164` `_count_background_task_launches` | Counts `Task` `tool_use` blocks with `input.run_in_background is True` (strict `is True`) |
| Warning body | `_claude_code.py:156-161` `_BACKGROUND_TASK_WARNING` | Ends `"...truncated (heuristic)"` — **no docs link today** |
| Warning prefix | `src/clauditor/runner.py:160` `_BACKGROUND_TASK_WARNING_PREFIX = "background-task:"` | Load-bearing; `succeeded_cleanly` keys on it |
| CLI hint | `src/clauditor/cli/__init__.py:575-586` `_CATEGORY_HINTS["background-task"]` | Static inline, no docs link |
| Suppression | `_claude_code.py:824-834` | Detector skipped when `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` (via `--sync-tasks`) |
| CI logic coverage | `tests/test_runner.py:4300+` `TestBackgroundTaskNoncompletionIntegration` | Mock-stream path already asserts `error_category=="background-task"`, warning prefix, `succeeded_cleanly is False` |
| Live-test precedent | `tests/test_bundled_review_skill.py:276-350` `TestLiveSkillRun` + `_live_run_skip_reason()` | Triple-lock gate, tmp_path symlink, `.git` marker, 360s timeout, `@pytest.mark.live` |
| `live` marker | `pyproject.toml:91-93` | Already registered; skipped unless `CLAUDITOR_RUN_LIVE=1` |
| Fixture tree | `tests/fixtures/review-agentskills-spec/` | README + `captured-output.txt` precedent |
| Example tree | `examples/.claude/skills/find-kid-activities/` | `SKILL.md` + `SKILL.eval.json` + `assets/` — the runnable-companion shape |
| Recipe A/B prose | `docs/skill-usage.md:244-256` | Prose-only, no worked example |
| Worked-example model | `docs/skill-usage.md:258-332` (AskUserQuestion recipe) | narrative + eval.json + SKILL.md diff + "things to copy" |
| Tier 3 ADR | `docs/adr/transport-research-103.md` | No-go documented; revisit line at `:98` is prose, not structured |

### Rule constraints (Scout — validation checklist for Phase 4)

- **`internal-skill-live-test-tmp-symlink.md`** (governs US-001): tmp_path
  `.claude/skills/<name>` symlink (not copy), `.git/` marker dir,
  `project_dir=tmp_path/project` (never `cwd`), 360s timeout, triple-lock
  gate via a `_live_run_skip_reason()`-style helper, **and** the
  silent-failure guard. NOTE the inversion below (DEC-003).
- **`bundled-skill-docs-sync.md`**: editing `docs/skill-usage.md` worked
  examples is a ref-doc addition, NOT a `/clauditor` SKILL.md workflow
  edit — the README/SKILL.md sync triangle does **not** fire. The new
  example skill is **unbundled**, so the rule does not apply to it.
- **`readme-promotion-recipe.md`**: `skill-usage.md` is already promoted;
  adding worked examples is ref-doc expansion, not a root-README
  promotion. No teaser-budget gate. (If the README's Skill Compatibility
  teaser needs a one-line pointer, respect D2-lean shape.)
- **`pure-compute-vs-io-split.md`**: the detector is already split pure;
  the live fixture is the I/O layer. No new pure helper needed.
- **`json-schema-version.md`** / others: not triggered (no new sidecar,
  no schema change).
- **Coverage gate (CLAUDE.md, 80%)**: live tests are skipped by default,
  so they neither add nor subtract coverage. The detector logic stays
  covered by the existing mock-stream tests.
- **Memory `feedback_review_agentskills_spec_internal.md`**: the known-bad
  fixture is **test-only** (lives under `tests/fixtures/`, never
  packaged, never in `clauditor setup` or any "list skills" surface).

---

## Phase 2 — Architecture Review

This change is docs + a test fixture + a warning-string edit + an ADR
section. No auth surface, no input boundaries, no data model, no API.
Security review: **pass** (no new trust boundary; the fixture skill is
test-only and the live test spends tokens only under explicit opt-in).
Architecture review focuses on **rule conformance** for the live fixture
and docs sync:

- **Live fixture (US-001):** must follow `internal-skill-live-test-tmp-symlink.md`
  verbatim. Rating: **pass with one design note** — the assertion target
  inverts the precedent (assert the *warning*, not `result.succeeded`),
  see DEC-003.
- **Worked example (US-002):** unbundled example skill + ref-doc edit;
  no triangle. Rating: **pass**.
- **Warning link (US-003):** one constant edit + test substring. The
  warning prefix `background-task:` is load-bearing for
  `succeeded_cleanly` — the edit must **append** to the body, never
  touch the prefix. Rating: **pass with guard** (DEC-005).
- **Tier 3 tracking (US-004):** ADR refresh-in-place per
  `rule-refresh-vs-delete.md` spirit. Rating: **pass**.

---

## Phase 3 — Refinement (Decisions)

- **DEC-001 — Scope = four bullets.** Worked example, live-gated fixture,
  warning→docs link, Tier 3 ADR tracking. Tier 3 implementation, the
  Tier 2 spike, and shipped Tier 1 items are out of scope. *(User, Phase 1
  scope question.)*
- **DEC-002 — Worked example uses a runnable companion skill.** A real
  skill under `examples/.claude/skills/<name>/` (parallel-research
  fan-out shape, mirroring how the AskUserQuestion recipe references
  `find-kid-activities`), plus inline before/after diffs in
  `docs/skill-usage.md`. *(User decision.)*
- **DEC-003 — Live fixture asserts the WARNING, not success.** The
  known-bad skill is *expected* to truncate and warn. The live test
  asserts `result.error_category == "background-task"`, a
  `background-task:`-prefixed entry in `result.warnings`, and
  `result.succeeded_cleanly is False`. It must **not** assert
  `result.succeeded`. This inverts the `review-agentskills-spec` live
  test's `assert result.succeeded` precondition — but the
  silent-failure guard from the rule is still honored differently: the
  test must distinguish "warning fired (good)" from "Unknown command /
  empty output (test misconfigured)" by also asserting non-empty
  `stream_events` and that the launch was actually detected.
- **DEC-004 — Live fixture must NOT pass `--sync-tasks`.** The whole
  point is to exercise the un-suppressed detector, so the test must not
  set `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`. A sibling assertion can
  optionally prove the *suppression* path (warning absent when the env
  var IS set) but that is covered by existing CI mock tests — keep the
  live test single-purpose.
- **DEC-005 — Warning link is additive, anchor is `#skill-compatibility`.**
  Append `" See docs/skill-usage.md#skill-compatibility for refactoring
  recipes."` to `_BACKGROUND_TASK_WARNING`. Use the **actual shipped
  anchor** `#skill-compatibility` (not the issue's hypothetical
  `#background-task-compatibility`). The `background-task:` prefix is
  untouched. Mirror the link in the CLI `_CATEGORY_HINTS["background-task"]`
  entry for consistency. Update the existing CI substring tests
  (`tests/test_cli.py:8048`, `tests/test_runner.py:2178`) to assert the
  new anchor substring.
- **DEC-006 — Tier 3 tracking = ADR subsection.** Add a structured
  "## Tier 3 revisit triggers" subsection to
  `docs/adr/transport-research-103.md` with a per-upstream-issue table
  (issue # → what landing it unblocks → the concrete clauditor change it
  enables → checkbox). No new GitHub issue, no code. *(User decision.)*
- **DEC-007 — Live-test fragility is accepted and documented.** A real
  `claude -p` run may not deterministically exhibit the bad pattern
  (Claude could behave differently run-to-run). The mock-stream CI tests
  remain the source of truth for detector LOGIC; the live test is an
  opt-in end-to-end fidelity check. The fixture SKILL.md is written to
  make the bad pattern as deterministic as possible (explicitly instruct
  launching `Task(run_in_background=true)` then immediately summarizing
  without polling). The test's failure message dumps `warnings`,
  `error_category`, and `output[:500]` for diagnosability.

### Open ambiguities — resolved

- Worked example runnable? → **Yes** (DEC-002).
- Fixture internal-only? → **Yes, test-only** under `tests/fixtures/`,
  excluded from `clauditor setup` (no rule/setup change needed since it
  never lives in `src/clauditor/skills/`).
- Tier 3 tracking location? → **ADR subsection** (DEC-006).
- Warning-link in scope? → **Yes** (DEC-005).

---

## Phase 4 — Stories

> Architecture ordering: independent string edit first, then the two
> additive content stories, then the ADR, then quality gate, then
> patterns. US-001/US-002/US-003/US-004 are mutually independent and may
> be implemented in any order; the Quality Gate depends on all four.

### US-001 — Live-gated known-bad fixture + warning test

**Goal:** Prove end-to-end that a real skill launching
`Task(run_in_background=true)` under `claude -p` produces the `#97`
`background-task:` warning.

**Files:**
- `tests/fixtures/background-task-fanout/SKILL.md` (new) — a minimal
  skill whose instructions explicitly launch 2-3
  `Task(run_in_background=true)` sub-agents (e.g. a trivial parallel
  "research" fan-out) and then summarize immediately without polling.
  Keep it cheap (no real WebFetch needed — sub-agents can do a trivial
  bounded task) to bound token spend and runtime.
- `tests/fixtures/background-task-fanout/README.md` (new) — provenance +
  "this fixture is intentionally broken" note + refresh guidance,
  mirroring `tests/fixtures/review-agentskills-spec/README.md`.
- `tests/test_background_task_fixture.py` (new) — `TestLiveSkillRun`-shape
  class:
  - Reuse/replicate `_live_run_skip_reason()` triple-lock (consider
    extracting the helper to a shared test util if duplication is
    objectionable; otherwise copy per existing precedent).
  - `@pytest.mark.live`.
  - tmp_path `.claude/skills/background-task-fanout` symlink to the
    fixture dir, `.git/` marker, `project_dir=tmp_path/project`, 360s
    timeout.
  - Run **without** `--sync-tasks` (DEC-004).
  - Assert (DEC-003): `result.error_category == "background-task"`;
    `any(w.startswith("background-task:") for w in result.warnings)`;
    `result.succeeded_cleanly is False`; `result.stream_events` non-empty
    (silent-failure guard). Failure message dumps `warnings`,
    `error_category`, `output[:500]`.

**Rules:** `internal-skill-live-test-tmp-symlink.md` (all constraints),
test-only fixture (memory).

**Traces:** Tier 1 bullet 4 / closing checklist #2. DEC-003, DEC-004,
DEC-007.

**TDD note:** the detector logic is already unit-tested
(`test_runner.py:4145+`); this story adds the end-to-end live path only.
No new pure logic to TDD.

### US-002 — Runnable companion example skill + Recipe A/B worked example

**Goal:** Give skill authors a concrete, runnable before/after for the
two refactoring recipes.

**Files:**
- `examples/.claude/skills/parallel-research/SKILL.md` (new) — the
  **refactored-good** version (Recipe A: sequential `Task` calls, no
  `run_in_background`), a parallel-research fan-out shape echoing the
  motivating `find-restaurants --depth deep` (editorial / authoritative /
  verification lanes). Runnable under clauditor.
- `examples/.claude/skills/parallel-research/SKILL.eval.json` (new) —
  eval spec with assertions (mirror `find-kid-activities/SKILL.eval.json`
  shape: `skill_name`, `description`, `user_prompt`, `test_args`,
  `assertions`). Include a `not_contains` assertion on
  `"run_in_background"` to lock in the refactor.
- `examples/.claude/skills/parallel-research/assets/sample-input.txt`
  (new, if the eval needs an input file) — optional.
- `docs/skill-usage.md` (edit, ~line 256, under "Refactoring recipes") —
  add a worked-example block matching the AskUserQuestion-recipe pattern
  (`:258-332`):
  1. Short narrative of the parallel-fanout failure mode.
  2. **Before** SKILL.md `diff` showing the
     `Task(..., run_in_background=true)` fan-out.
  3. **Recipe A** `diff` → drop `run_in_background` (sequential, isolation
     preserved, longer latency) — link to the runnable
     `examples/.claude/skills/parallel-research/` skill.
  4. **Recipe B** `diff` → replace `Task` sub-agents with parallel
     `WebSearch`/`WebFetch` `tool_use` blocks in the parent (shorter
     latency, loses isolation).
  5. "Things to copy" numbered list.

**Rules:** `bundled-skill-docs-sync.md` does NOT fire (no `/clauditor`
SKILL.md edit; unbundled example skill). `readme-promotion-recipe.md`:
optionally add a one-line pointer from the README Skill Compatibility
teaser, D2-lean.

**Traces:** Tier 1 bullet 2 / closing checklist #1. DEC-002.

### US-003 — Warning message links to docs

**Goal:** Make the `#97` warning actionable by pointing at the
refactoring recipes (closes the never-checked-off Tier 1 bullet 3).

**Files:**
- `src/clauditor/_harnesses/_claude_code.py:156-161` — append
  `" See docs/skill-usage.md#skill-compatibility for refactoring
  recipes."` to `_BACKGROUND_TASK_WARNING`. **Do not touch the
  `background-task:` prefix** (DEC-005).
- `src/clauditor/cli/__init__.py:575-586` — mirror the docs pointer in
  `_CATEGORY_HINTS["background-task"]`.
- `tests/test_runner.py:2178` and `tests/test_cli.py:8048` — update the
  existing substring assertions to also assert the
  `#skill-compatibility` (or `docs/skill-usage.md`) substring.

**Rules:** prefix is load-bearing for `succeeded_cleanly`
(`runner.py:143`) — additive edit only.

**Traces:** Tier 1 bullet 3. DEC-005.

**TDD note:** substring assertion is the test; edit the constant to make
it pass.

### US-004 — Tier 3 revisit-triggers ADR subsection

**Goal:** Record the concrete conditions under which the no-go on the
in-clauditor turn-loop emulator should be revisited.

**Files:**
- `docs/adr/transport-research-103.md` (edit, near `:98`) — add a
  "## Tier 3 revisit triggers" subsection: a table with one row per
  upstream dependency —
  `anthropics/claude-code#52856` (headless `claude status --json`),
  `#28221` (PostTask hook), `#48657` (fire hooks on bg-task completion),
  `#52917` (clauditor-filed gap report), `#50572` (referenced, bg shells
  reaped on turn end) — each row: issue # → what landing unblocks → the
  concrete clauditor change it enables → an unchecked box for the
  maintainer to tick when status changes.

**Rules:** `rule-refresh-vs-delete.md` spirit — refresh in place; keep
the existing no-go rationale byte-stable, add the structured triggers.

**Traces:** Tier 3 / closing checklist #3. DEC-006.

### US-005 — Related-docs sweep (depends on US-001..US-004)

**Goal:** After the four implementation stories land, sweep every doc
surface that references the background-task gap so nothing is left
stating the pre-implementation story. This is the catch-all for doc
drift that the per-story edits (US-002 `skill-usage.md`, US-003 warning,
US-004 ADR) don't already cover.

**Files / surfaces to audit and update where needed:**
- `README.md` — the Skill Compatibility section + transport-architecture
  limitations entry. Add a pointer to the new worked example if the
  teaser warrants it (D2-lean per `readme-promotion-recipe.md`).
- `docs/skill-usage.md` — verify the new worked example is cross-linked
  from the compatibility matrix row and the `--sync-tasks` section reads
  coherently alongside it.
- `docs/cli-reference.md` / `docs/eval-spec-reference.md` — confirm the
  `--sync-tasks` / `sync_tasks` references still align with the warning's
  new docs link (US-003) and the worked example.
- `CHANGELOG` (if present) — add entries for the new example skill, the
  live fixture, and the warning-link change.
- **GitHub issue #103** — tick the now-closed checklist boxes in the
  2026-04-24 status comment (worked example, known-bad fixture, warning
  link); leave the Tier 3 revisit box and the umbrella open.
- Grep for stale references to the pre-link warning text or the
  hypothetical `#background-task-compatibility` anchor anywhere in
  `docs/`, `README.md`, and `src/` comments; fix to `#skill-compatibility`.

**Rules:** `bundled-skill-docs-sync.md` (only fires if a `/clauditor`
SKILL.md workflow edit slipped in — it should not have);
`readme-promotion-recipe.md` (teaser budget for any README pointer).

**Traces:** Coherence follow-up across DEC-002/003/005/006. Closes the
issue-checklist housekeeping.

**Note:** keep this story scoped to **doc/reference coherence** — it must
not introduce new code or behavior. If the sweep surfaces a missing doc
surface that needs net-new content, flag it rather than silently
expanding scope (per `.claude/rules/plan-contradiction-stop.md`).

### US-006 — Quality Gate (second-to-last; depends on US-001..US-005)

- Run the code reviewer 4 times across the full changeset; fix every real
  finding each pass.
- Run CodeRabbit review if available.
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` — 80% gate
  holds (live test skipped by default; verify it skips cleanly with
  `CLAUDITOR_RUN_LIVE` unset and is collectable).
- Optionally run the live test once with `CLAUDITOR_RUN_LIVE=1` +
  `ANTHROPIC_API_KEY` + `claude` on PATH to confirm the warning fires
  end-to-end; record the run time + result in the story notes (per the
  `internal-skill-live-test` rule's validation-note convention).
- Validate every story against the Phase 1 rule checklist.

### US-007 — Patterns & Memory (last; depends on US-006)

- If the live-test assertion inversion (assert-warning-not-success)
  recurs as a pattern, consider a short note or a refresh to
  `internal-skill-live-test-tmp-symlink.md` documenting the
  "known-bad fixture asserts the warning" variant (DEC-003).
- Update any memory entries if a durable insight emerged (e.g. the
  test-only-fixture vs bundled-skill distinction for deliberately-broken
  skills).
- No new rule expected unless the reviewer surfaces one.

---

## Acceptance criteria (this plan)

- [ ] `docs/skill-usage.md` Recipe A/B section has a worked example with
      before/after diffs referencing a runnable
      `examples/.claude/skills/parallel-research/` skill. (US-002)
- [ ] A live-gated test asserts a real `Task(run_in_background=true)`
      skill produces the `background-task:` warning end-to-end; skips
      cleanly without `CLAUDITOR_RUN_LIVE=1`. (US-001)
- [ ] The `#97` warning text + CLI hint link to
      `docs/skill-usage.md#skill-compatibility`; CI substring tests
      updated. (US-003)
- [ ] `docs/adr/transport-research-103.md` has a structured Tier 3
      revisit-triggers subsection. (US-004)
- [ ] Related-docs sweep done; no surface left stating the
      pre-implementation story; issue-#103 checklist boxes ticked. (US-005)
- [ ] Quality gate passes; 80% coverage holds. (US-006)

## Out of scope

- Tier 3 implementation (in-clauditor turn-loop emulator) — no-go pending
  upstream.
- Tier 2 research spike — already shipped (`transport-research-103.md`).
- Already-shipped Tier 1 matrix/prose and `--sync-tasks` (#116).
- Closing the #103 umbrella — it stays open for the async-fidelity gap.
