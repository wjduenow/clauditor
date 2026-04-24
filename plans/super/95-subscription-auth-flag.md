# 95 — Subscription auth end-to-end: combined flag

## Meta

- **Ticket:** [#95](https://github.com/wjduenow/clauditor/issues/95) — "Add combined flag for subscription auth end-to-end"
- **Branch:** `feature/95-subscription-auth-flag`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/95-subscription-auth-flag`
- **Phase:** complete (PR [#96](https://github.com/wjduenow/clauditor/pull/96) — all 7 stories closed; Quality Gate 4/4 clean)
- **Sessions:** 1 (2026-04-23)
- **Base:** dev @ eb1a75d

## Summary

Running `clauditor grade` end-to-end on Claude Max/Pro subscription (no API-key spend) today requires two flags:

- `--transport cli` — routes the **grader** call through `claude -p`
- `--no-api-key` — strips `ANTHROPIC_API_KEY` from the **skill subprocess** env

If a user passes only `--transport cli` while `ANTHROPIC_API_KEY` is exported, the skill subprocess prefers the API key and hits 429 before the grader ever runs. The ticket proposes coupling the two flags under a single mental model ("don't spend API credits"). User preference: **Option B** — `--transport cli` implicitly sets `--no-api-key` with a one-time stderr notice.

## Discovery

### Ticket

- **Options on the table:**
  - **A** — new `--subscription-auth` flag that implies both
  - **B** — `--transport cli` implicitly sets `--no-api-key` (+ stderr notice)
  - **C** — docs + 429 hint only
- **User preference:** B (single mental model, backward-compat via `--transport api`, notice preserves discoverability)

### Key codebase findings

**Transport resolution seam** — `_resolve_grader_transport(args, eval_spec)` at `src/clauditor/cli/__init__.py:58-88`. Implements four-layer precedence (CLI flag > `CLAUDITOR_TRANSPORT` env > `EvalSpec.transport` > `"auto"`). Called by all six LLM-mediated commands.

**Six `--transport` commands:**
| Command | CLI file | Runs skill subprocess? |
|---------|----------|------------------------|
| `grade` | `cli/grade.py` | **Yes** — the problem shows up here |
| `extract` | `cli/extract.py` | No (extracts from existing output) |
| `propose-eval` | `cli/propose_eval.py` | No (generates spec from SKILL.md) |
| `suggest` | `cli/suggest.py` | No (LLM edit proposer) |
| `triggers` | `cli/triggers.py` | No (trigger precision judge) |
| `compare --blind` | `cli/compare.py` | No (uses provided output strings) |

Only `grade` both runs the skill subprocess AND uses the grader transport — which is the exact failure surface described in the ticket.

**`--no-api-key` plumbing (today):**
- Flag lives on: `grade`, `capture`, `run`, `validate` (the four commands that spawn a skill subprocess).
- Converts to `env_override = env_without_api_key()` (`src/clauditor/runner.py:33-46`).
- Flows `SkillSpec.run(env_override=…)` → `SkillRunner.run(env=…)` → `Popen(env=…)`.
- NOT on the other five LLM-mediated commands (they have no skill subprocess).

**One-time announcement pattern** — `src/clauditor/_anthropic.py:620-629`. Module-level `_announced_cli_transport = False` flag; `_CLI_AUTO_ANNOUNCEMENT` text "clauditor: using Claude CLI transport (subscription auth); pass --transport api to opt out". Fires once per process only on auto→cli resolution (explicit `--transport cli` is silent to avoid surprise).

**Auth guard** — `check_any_auth_available(cmd_name)` at `_anthropic.py:257-295`. Passes when either `ANTHROPIC_API_KEY` is set OR `shutil.which("claude")` is available. Transport selection is independent of the guard today.

### Relevant rules (from `.claude/rules/`, 31 total)

- `spec-cli-precedence.md` — four-layer precedence rule. Implicit flag coupling must live at the resolver seam, not be scattered across call sites. **Likely needs a new sub-section** documenting "implicit coupling at the resolution seam."
- `centralized-sdk-call.md` — multi-transport routing section covers the one-time auto→cli announcement. **Extend** to cover explicit-CLI→no-api-key implicit coupling announcement.
- `precall-env-validation.md` — `check_anthropic_auth` guard. Not directly affected, but any skip-when-cli logic would need a rule note.
- `llm-cli-exit-code-taxonomy.md` — exit codes unchanged by this feature.
- `readme-promotion-recipe.md` — doc teasers reference `--transport` and `--no-api-key`.
- `bundled-skill-docs-sync.md` — `/clauditor` SKILL.md / `docs/skill-usage.md` / README triangle, if any mentions these flags.

**No `workflow-project.md`** found.

### Scope (proposed)

1. Modify `_resolve_grader_transport` (or a sibling) to return an implicit-no-api-key signal alongside the resolved transport, **or** expose a distinct helper `should_strip_api_key_for_skill_subprocess(resolved_transport)`.
2. Route that signal into the env_override computation at the one command where it matters (`grade`) so `--transport cli` → `env_override = env_without_api_key()` when `ANTHROPIC_API_KEY` was set.
3. Add a new one-time stderr notice on the implicit coupling, gated on "we actually stripped a key that was present."
4. Provide an escape hatch for the rare user who wants `--transport cli` AND the key retained.
5. Update docs (`docs/cli-reference.md`, `README.md` authentication section) and the two rules most affected.

### Scoping questions

**Q1 — Which commands does the implicit coupling apply to?**
- **A)** Only `grade` — the sole command where the bug manifests; the other five LLM-mediated commands have no skill subprocess, so implying `--no-api-key` there is a no-op.
- **B)** `grade` + any future commands that spawn a skill subprocess *and* also take `--transport` (e.g. if `triggers` or `compare --blind` ever gain a skill-run mode). Centralize the coupling inside `_resolve_grader_transport` so it travels for free.
- **C)** All six LLM-mediated commands "uniformly" as the ticket says, even though it's a no-op for five of them (makes the mental model simpler, consistent stderr notice).
- **D)** Expand `--no-api-key` to all four subprocess-spawning commands (`grade`, `capture`, `run`, `validate`) and couple on all of them — `--transport cli` is on `grade` only, so this affects only `grade` today, but future-proofs.

**Q2 — Which transport-resolution paths trigger the implicit coupling?**
- **A)** Only explicit `--transport cli` CLI flag — most predictable, user-intent-explicit.
- **B)** Explicit CLI flag + `CLAUDITOR_TRANSPORT=cli` env var (both operator-intentional).
- **C)** Also `EvalSpec.transport = "cli"` (skill author's declared preference).
- **D)** All four layers, including `auto` when it resolves to CLI (widest net; most beginner-friendly).

**Q3 — When should the one-time stderr notice fire?**
- **A)** Every time `--transport cli` is used, regardless of whether a key was actually set/stripped (predictable).
- **B)** Only when `ANTHROPIC_API_KEY` was set and got stripped (otherwise it's a no-op no-notice situation; keeps silent logs clean).
- **C)** Never emit — the behavior change is documented in `--help`; stderr noise is churn.
- **D)** Emit + support `CLAUDITOR_QUIET=1` or similar suppression env var.

**Q4 — Escape hatch for "I want `--transport cli` AND my API key passed to the subprocess":**
- **A)** No escape hatch — if you want the API key passed, use `--transport api`. Simpler.
- **B)** Explicit `--keep-api-key` flag on `grade` (inverse of `--no-api-key`, takes precedence over the implicit coupling).
- **C)** `CLAUDITOR_KEEP_API_KEY=1` env var (belt-and-suspenders, matches other env-based overrides).
- **D)** Preserve today's "explicit `--no-api-key=false`" semantics somehow (unclear today — argparse doesn't support `--no-flag=false` natively).

**Q5 — Notice wording — which shape fits better?**
- **A)** `clauditor: --transport cli implies --no-api-key for the skill subprocess (ANTHROPIC_API_KEY was stripped); pass --transport api to keep the API key`
- **B)** `clauditor: stripping ANTHROPIC_API_KEY from skill subprocess env (--transport cli implies subscription-auth end-to-end)`
- **C)** Something short like `clauditor: --transport cli → --no-api-key (subscription auth end-to-end)`
- **D)** Write new text in Phase 3 after the other answers are known.

## Architecture Review

| Area | Rating | Summary |
|------|--------|---------|
| Security | **pass** | `env_without_api_key()` strips both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`; notice text is a static constant (no key value interpolation); `Popen(env=…)` replacement is sound (non-auth vars preserved); no persistent leak surface (not written to sidecars). |
| API Design | **concern** | Three clarifications needed: (a) how `grade.py` tells "explicit `--transport cli`" from "`CLAUDITOR_TRANSPORT=cli`" from "spec/auto" — does `_resolve_grader_transport` need to return a `(transport, source_layer)` tuple, or does the call site re-check sources; (b) interaction between explicit `--no-api-key` and implicit coupling (do both fire the notice); (c) where the notice emits from. |
| Observability | **pass** | Second module-level flag (parallel to `_announced_cli_transport`) is the proven pattern; stderr does not pollute `--json` stdout (confirmed at `grade.py:1119-1151`); no sidecar schema change needed. |
| Testing | **concern** | Overlaps API-Design concerns (a/b/c above). Plus: test-file placement — new `tests/test_cli_grade_transport_coupling.py` vs folding into existing `tests/test_cli.py`. Autouse fixture to reset the new flag needs a home. Notice wording currently names only `ANTHROPIC_API_KEY` but `env_without_api_key` also strips `ANTHROPIC_AUTH_TOKEN` — drift hazard. |

Performance and Data Model reviews skipped (no surface).

### Notable cross-review findings

- **Notice wording vs. what actually gets stripped.** DEC-005's notice says `"(ANTHROPIC_API_KEY was stripped)"`. `env_without_api_key` strips **both** `ANTHROPIC_API_KEY` AND `ANTHROPIC_AUTH_TOKEN`. If a user has only `ANTHROPIC_AUTH_TOKEN` set, the notice text is misleading. Options: (a) adjust wording to name whichever was actually stripped, (b) keep the DEC-005 text but document it as a stand-in label for "API auth env", (c) name both vars explicitly.
- **Backward-compatibility.** Existing CI that passes `clauditor grade --transport cli` with `ANTHROPIC_API_KEY` set will silently lose the key after this lands. In practice: users already experiencing the 429 have already added `--no-api-key` (or they're not using CLI at all), so this is strictly-better for anyone who hit the bug. But a `--help` + release-notes mention is worth the 2 lines.


## Refinement Log

### Decisions

- **DEC-001 — Scope: implicit coupling applies to `grade` only** *(Q1 → A)*.
  The bug only manifests in `grade` (only command that both spawns a skill
  subprocess and uses the grader transport). Implicit-strip logic is a
  property of skill-subprocess-spawning commands, not of the grader
  resolver; keeping it at the `grade.py` call site avoids scope creep on
  `_resolve_grader_transport`. Revisit if `compare --blind` or `triggers`
  ever gains a skill-run mode.

- **DEC-002 — Trigger layers: explicit `--transport cli` flag + `CLAUDITOR_TRANSPORT=cli` env var** *(Q2 → B)*.
  Both are operator-intent signals (matches the "operator > author > default"
  direction in `spec-cli-precedence.md`). `EvalSpec.transport = "cli"` is
  author-intent and does NOT know the user's env, so the coupling does not
  fire on spec-only CLI selection. Auto→CLI resolution does NOT fire the
  coupling — stripping keys on any system with `claude` on PATH would
  surprise users who maintain an API key for production purposes.

- **DEC-003 — Notice gating: fires only when `ANTHROPIC_API_KEY` was set and got stripped** *(Q3 → B)*.
  If the user has no API key in env, the coupling is a no-op and there is
  nothing to announce. Only print to stderr when the strip actually
  happened. One-time per process, parallel to the existing
  `_announced_cli_transport` pattern in `_anthropic.py`.

- **DEC-004 — No escape hatch for "`--transport cli` + keep API key"** *(Q4 → A)*.
  The combination makes no sense: if the API key remains in the subprocess
  env, `claude -p` inside the subprocess prefers it (per DEC-001 of #86)
  and re-hits the 429 the coupling exists to prevent. Users who want the
  API key used end-to-end should pass `--transport api`. If a real use
  case surfaces later, add a `--keep-api-key` flag or
  `CLAUDITOR_KEEP_API_KEY=1` env var then — YAGNI for now.

- **DEC-005 — Notice wording: long explanatory form matching existing auto→CLI announcement** *(Q5 → A)*.
  Mirrors the style of `_CLI_AUTO_ANNOUNCEMENT` in `_anthropic.py`:
  explain WHY + point at the escape hatch (`--transport api`). Terser
  wordings assume the reader already knows what `--no-api-key` does.
  **Superseded for exact wording by DEC-011** — see below (names both
  `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` statically).

- **DEC-006 — Source-detection via sibling helper `should_strip_api_key_for_skill_subprocess(args)`** *(R1 → C)*.
  `_resolve_grader_transport` stays pure (transport-only). A new sibling
  helper co-located with it reads `args.transport == "cli"` and
  `os.environ.get("CLAUDITOR_TRANSPORT", "").strip() == "cli"`, returning
  a bool. `EvalSpec.transport` and auto resolution explicitly do NOT
  trigger. Pure function; trivially unit-testable; reusable if
  `capture`/`run`/`validate` ever gain `--transport`.

- **DEC-007 — Explicit `--no-api-key` does NOT fire the new notice** *(R2 → A)*.
  The notice exists to surface the *implicit* coupling to surprised
  users. If the user typed `--no-api-key` themselves, there is nothing
  to surprise them with. Notice fires only when: (a) the implicit path
  triggered the strip AND (b) `args.no_api_key` was False AND (c) at
  least one of `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` was
  actually present and got stripped. Keeps signal-to-noise high.

- **DEC-008 — Notice emitted from the `cli/grade.py` env-override call site** *(R3 → C)*.
  Co-located with the decision. `env_without_api_key()` stays pure (no
  stderr side effect, preserving the pure-helper contract established
  by `.claude/rules/pure-compute-vs-io-split.md`). The module-level
  flag in `_anthropic.py` (DEC-009) is imported and flipped from the
  call site.

- **DEC-009 — Module-level flag lives in `_anthropic.py`** *(R4 → A)*.
  Second flag (`_announced_implicit_no_api_key: bool = False`) alongside
  the existing `_announced_cli_transport`. Announcement flags form an
  emerging family and should live in one module. A public helper
  `announce_implicit_no_api_key()` in `_anthropic.py` does the
  print-and-flip; `cli/grade.py` imports and calls it.

- **DEC-010 — Backward-compat: `--help` text + release notes, no opt-out env var** *(R6 → A)*.
  The behavior is strictly-better for anyone who would have hit the
  429. `--help` on `--transport` gains one line describing the
  coupling. Release notes get two lines. No `CLAUDITOR_NO_IMPLICIT_STRIP=1`
  escape hatch — adds a codepath for a case that is self-inflicted
  (user explicitly asked for two conflicting things). YAGNI.

- **DEC-011 — Notice names both auth env vars statically, supersedes DEC-005 wording** *(R5 → C)*.
  Final wording:
  `clauditor: --transport cli stripped ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN from the skill subprocess env (subscription auth end-to-end); pass --transport api to keep the keys.`
  Named both vars (not dynamic) so the text is a constant, users
  grep-searching for either name find the notice, and the wording
  never drifts from what `env_without_api_key` actually strips.
  Committed as the `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT` constant in
  `src/clauditor/_anthropic.py`.

## Detailed Breakdown

Validation command (run after each story): `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing`. 80% coverage gate enforced.

---

### US-001 — Pure helper `should_strip_api_key_for_skill_subprocess`

**Description.** Add a pure sibling helper next to `_resolve_grader_transport` in `src/clauditor/cli/__init__.py` that returns a bool: True iff transport-cli was selected via explicit `--transport cli` flag OR `CLAUDITOR_TRANSPORT=cli` env var. Used by the `grade` command to decide whether to imply `--no-api-key`.

**Traces to:** DEC-001, DEC-002, DEC-006.

**Files:**
- `src/clauditor/cli/__init__.py` — add `should_strip_api_key_for_skill_subprocess(args: argparse.Namespace) -> bool`. Reads `getattr(args, "transport", None)` and `os.environ.get("CLAUDITOR_TRANSPORT", "")`. Returns True only when either equals `"cli"` after `.strip()` (same whitespace-normalization discipline the existing resolver uses per `.claude/rules/spec-cli-precedence.md`).
- `tests/test_cli.py` (or a new `tests/test_cli_helpers.py` if one already exists) — add `TestShouldStripApiKeyForSkillSubprocess`.

**TDD:**
1. `args.transport == "cli"` + no env var → True
2. No `args.transport` + `CLAUDITOR_TRANSPORT=cli` → True
3. `args.transport == "cli"` + `CLAUDITOR_TRANSPORT=api` → True (CLI flag wins, matches existing precedence)
4. `args.transport == "api"` + no env var → False
5. `args.transport is None` + no env var → False
6. `args.transport == "auto"` + `claude` on PATH (auto → cli) → **False** (auto does NOT trigger per DEC-002)
7. `CLAUDITOR_TRANSPORT="  cli  "` (whitespace) → True (strips before compare)
8. `CLAUDITOR_TRANSPORT=""` (empty) → False
9. `args` without `transport` attribute at all → False (no AttributeError)

**Done when:** The new helper is unit-tested against all nine cases; ruff + pytest pass; coverage on the new function is 100%.

**Depends on:** none.

---

### US-002 — Announcement flag, constant, and `announce_implicit_no_api_key` helper in `_anthropic.py`

**Description.** Add the module-level one-time flag and the notice constant, plus a public helper `announce_implicit_no_api_key()` that prints to stderr and flips the flag. Parallel to the existing `_announced_cli_transport` pattern.

**Traces to:** DEC-003, DEC-005 (superseded), DEC-009, DEC-011.

**Files:**
- `src/clauditor/_anthropic.py` — add:
  - `_announced_implicit_no_api_key: bool = False` (module-level)
  - `_IMPLICIT_NO_API_KEY_ANNOUNCEMENT: Final[str] = ( "clauditor: --transport cli stripped ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN from the skill subprocess env (subscription auth end-to-end); pass --transport api to keep the keys." )` (constant, per DEC-011)
  - `def announce_implicit_no_api_key() -> None:` — checks the flag; on first call prints to stderr and flips.
- `tests/test_anthropic.py` — add `TestAnnounceImplicitNoApiKey` with an autouse `monkeypatch.setattr(..., False)` fixture parallel to the existing `_reset_announcement_flag` pattern.

**TDD:**
1. First call emits the constant text to stderr.
2. Second call in same process emits nothing.
3. The constant string contains both `"ANTHROPIC_API_KEY"` and `"ANTHROPIC_AUTH_TOKEN"` substrings (prose-presence assertions, not byte-identical — so minor copy edits don't churn tests).
4. The constant string contains `"--transport api"` (escape-hatch anchor).
5. Flag reset via monkeypatch makes the notice fire again (sanity check the test-isolation pattern).

**Done when:** Three assertions land and pass; autouse fixture added; ruff + pytest pass.

**Depends on:** none.

---

### US-003 — Wire implicit coupling into `cmd_grade` env-override computation

**Description.** Extend the `env_override` computation in `cli/grade.py` so that the implicit-coupling helper can trigger a strip even when `args.no_api_key` is False. Emit the notice only when the implicit path fired AND a key was actually present.

**Traces to:** DEC-001, DEC-003, DEC-006, DEC-007, DEC-008, DEC-009.

**Files:**
- `src/clauditor/cli/grade.py`:
  - Import `should_strip_api_key_for_skill_subprocess` from `clauditor.cli`.
  - Import `announce_implicit_no_api_key` from `clauditor._anthropic`.
  - Replace the current `env_override = env_without_api_key() if getattr(args, "no_api_key", False) else None` block with:
    - `explicit_strip = getattr(args, "no_api_key", False)`
    - `implicit_strip = (not explicit_strip) and should_strip_api_key_for_skill_subprocess(args)`
    - `key_was_present = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))`
    - `env_override = env_without_api_key() if (explicit_strip or implicit_strip) else None`
    - `if implicit_strip and key_was_present: announce_implicit_no_api_key()`
  - Extend the `--transport` help text to note the coupling (DEC-010).
- `tests/test_cli_grade.py` (or extend existing `tests/test_cli.py::TestCmdGrade`) — add all the cases enumerated in the Testing review.

**TDD:**
**Positive (implicit coupling fires, notice emits):**
1. `--transport cli` + `ANTHROPIC_API_KEY` set → env stripped + notice emitted.
2. `--transport cli` + `ANTHROPIC_AUTH_TOKEN` set (no API_KEY) → env stripped + notice emitted.
3. `CLAUDITOR_TRANSPORT=cli` + `ANTHROPIC_API_KEY` set → env stripped + notice emitted.

**Positive (implicit coupling fires, NO notice):**
4. `--transport cli` + no key env vars → env strip is a no-op, notice NOT emitted (DEC-003 gating).

**Positive (explicit strip, NO new notice — DEC-007):**
5. `--no-api-key` + no `--transport` → env stripped + the implicit notice NOT emitted (stays exit-status-same as today).
6. `--no-api-key` + `--transport cli` → env stripped + the implicit notice NOT emitted (explicit wins, DEC-007).

**Negative (coupling does NOT fire):**
7. `--transport api` + key → env NOT stripped.
8. `--transport auto` resolving to cli + key → env NOT stripped (DEC-002).
9. `EvalSpec.transport = "cli"` + key, no CLI flag / env var → env NOT stripped (DEC-002).

**Edge / once-per-process:**
10. Two successive `cmd_grade` calls in the same pytest session (implicit path both times) → notice emits once, not twice.

**Done when:** All ten cases pass; ruff + pytest + coverage gate green; `--help` text on `--transport` mentions the coupling.

**Depends on:** US-001, US-002.

---

### US-004 — Documentation sync: cli-reference, README, SKILL.md triangle

**Description.** Update user-facing docs to describe the implicit coupling. Per `.claude/rules/bundled-skill-docs-sync.md`, if the `/clauditor` SKILL.md workflow references these flags, the triangle must stay in sync.

**Traces to:** DEC-010.

**Files:**
- `docs/cli-reference.md`:
  - `## grade` subsection — in the `--transport` flag row, add a sentence: "On `grade`, `--transport cli` (explicit flag or `CLAUDITOR_TRANSPORT=cli`) implies `--no-api-key` so the skill subprocess and grader both use subscription auth end-to-end."
  - `--no-api-key` row — add a note: "Also implied by `--transport cli` on `grade`."
- `README.md`:
  - Authentication section — append a two-line recipe: `clauditor grade <skill> --transport cli` is the subscription-auth one-liner; expand the existing note to mention the coupling.
- `src/clauditor/skills/clauditor/SKILL.md` — scan for references to `--transport` or `--no-api-key`. If absent (likely), no change. If present, mirror the one-line update per `bundled-skill-docs-sync.md`.
- `docs/skill-usage.md` — scan for narrative references to the two flags. If absent, no change.

**TDD:** prose-presence assertions in `tests/test_bundled_skill.py` (per `bundled-skill-docs-sync.md`) only if SKILL.md is updated. Otherwise: no new tests (docs edits don't need unit tests; ruff + link-check if available is enough).

**Done when:** All user-facing docs mention the coupling; triangle regression assertion lands iff SKILL.md was touched; ruff + pytest pass.

**Depends on:** US-003.

---

### US-005 — Rule updates: `spec-cli-precedence.md` + `centralized-sdk-call.md`

**Description.** Extend the two rules most directly affected. Per `.claude/rules/rule-refresh-vs-delete.md`, these are refreshes, not new rules — the existing patterns still apply, we are adding a sub-section describing the implicit-coupling extension.

**Traces to:** DEC-002, DEC-006, DEC-007, DEC-009, DEC-011.

**Files:**
- `.claude/rules/spec-cli-precedence.md`:
  - Add a new sub-section under "Canonical implementations" titled "Implicit coupling at the operator-intent layers":
    - Names the pattern: a CLI flag (or its env-var sibling) implicitly setting a *related* runner-config flag.
    - Canonical anchor: `should_strip_api_key_for_skill_subprocess` in `cli/__init__.py` (US-001).
    - Invariant: the implicit coupling fires only on *operator-intent* precedence layers (CLI flag + env var), never on author-intent (`EvalSpec.*`) or auto-resolution. DEC-002 traces here.
    - Call-site contract: explicit user flag always wins; implicit fires only when explicit is absent. DEC-007 traces here.
- `.claude/rules/centralized-sdk-call.md`:
  - Extend the "Multi-transport routing (CLI + SDK, #86)" section with a short paragraph describing implicit-coupling announcements as an emerging family (currently two flags — `_announced_cli_transport`, `_announced_implicit_no_api_key`). Canonical anchor: US-002's helper in `_anthropic.py`.
  - Rule text stays at "emits a one-time stderr announcement on first X per process" shape — just adds the second case.

**TDD:** No unit tests for rule markdown. Prose-presence grep in CI would be overkill for rule docs.

**Done when:** Both rules name the new canonical implementation; traces to DEC-### numbers from this plan land in the "Canonical implementation" sections; ruff + pytest pass (no code changes).

**Depends on:** US-003 (the canonical anchors must exist before the rule references them).

---

### US-006 — Quality Gate

**Description.** Four-pass code review (code-reviewer agent) + CodeRabbit review (if available), fixing every real finding each pass. Per project convention, this is the second-to-last story.

**Traces to:** all DECs.

**Files:** none directly; fixes whatever the reviewers find.

**Done when:**
- Four code-reviewer passes over the full changeset, with fixes each pass until a clean pass.
- CodeRabbit review if available (branch pushed, PR draft, review fetched via `gh pr review`).
- `uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing` green with ≥80% coverage.
- Bundled skill gates (`tests/test_bundled_skill.py`, `tests/test_bundled_review_skill.py`) still green.

**Depends on:** US-001 through US-005.

---

### US-007 — Patterns & Memory

**Description.** Capture any *new* patterns the implementation surfaced that are not already covered by existing rules. Update auto-memory with anything notable for future sessions. This is always the last story per the super-plan convention.

**Traces to:** all DECs (observationally).

**Files:**
- `.claude/rules/*.md` — only if a genuinely NEW pattern emerged (e.g., "implicit coupling between CLI flags" as a new rule, rather than a sub-section of `spec-cli-precedence.md`). Likely no new rule needed since US-005 already refreshes the two most-relevant ones; this story confirms.
- `~/.claude/projects/-home-wesd-Projects-clauditor/memory/*.md` — add memories only if something surprising or non-obvious emerged that helps future sessions.
- Optional: `docs/cli-reference.md` cross-reference updates if any doc drift surfaces during review.

**Done when:** A conscious decision has been made on whether any new rule or memory file is warranted; if yes, it exists and is indexed in `MEMORY.md`; if no, that decision is recorded in the commit message. Quality Gate (US-006) still green.

**Depends on:** US-006.

---

### Rule compliance check

Applied to this breakdown, each applicable rule from Phase 1:

- `spec-cli-precedence.md` → US-001 honors the operator-intent direction (DEC-002 + DEC-006); US-005 refreshes the rule.
- `centralized-sdk-call.md` → US-002 keeps the announcement pattern co-located in `_anthropic.py`; US-005 extends the rule.
- `pure-compute-vs-io-split.md` → US-001 is pure; US-002's helper is the thin I/O wrapper; US-003's call site composes them.
- `precall-env-validation.md` → not affected (the existing `check_any_auth_available` still fires when neither auth path is available).
- `llm-cli-exit-code-taxonomy.md` → not affected (no new failure categories).
- `readme-promotion-recipe.md` → US-004 touches README's authentication section — must respect teaser budget.
- `bundled-skill-docs-sync.md` → US-004 includes a scan of SKILL.md + skill-usage.md; triangle invariant preserved.
- `rule-refresh-vs-delete.md` → US-005 is a refresh of two existing rules, not new rules; pattern + anchors stay intact.

## Beads Manifest

- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/95-subscription-auth-flag`
- **Branch:** `feature/95-subscription-auth-flag` (PR #96)

| ID | Story | Depends on | Status |
|----|-------|------------|--------|
| `clauditor-zo9` | Epic — #95 Subscription auth combined flag | — | closed |
| `clauditor-ops` | US-001 — Pure helper `should_strip_api_key_for_skill_subprocess` | — | closed |
| `clauditor-aoo` | US-002 — Announcement flag + constant + helper in `_anthropic.py` | — | closed |
| `clauditor-e9h` | US-003 — Wire implicit coupling into `cmd_grade` | US-001, US-002 | closed |
| `clauditor-1dl` | US-004 — Docs sync (cli-reference + README + SKILL.md triangle) | US-003 | closed |
| `clauditor-865` | US-005 — Rule refresh (spec-cli-precedence + centralized-sdk-call) | US-003 | closed |
| `clauditor-2w4` | Quality Gate — code-reviewer x4 + CodeRabbit | US-001..US-005 | closed |
| `clauditor-60r` | Patterns & Memory | Quality Gate | closed |


## Session Notes

**2026-04-23 (session 1):**
- Read ticket #95 + parallel research via codebase-scout and convention-checker subagents.
- Key finding: ticket says "all six LLM-mediated commands uniformly" but the bug only manifests in `grade` (the sole command that both spawns a skill subprocess AND uses the grader transport). Surfacing this in scoping Q1 — user should decide whether to keep the "uniform" framing or scope to the actual failure surface.
- Second finding: the one-time `_announced_cli_transport` pattern in `_anthropic.py` today fires **only** on auto→cli resolution, not on explicit `--transport cli` (by design — explicit intent shouldn't surprise). New implicit-coupling notice is a separate concern and needs its own gating decision (Q3).

**2026-04-23 (session 2 — ralph execution + quality gate):**
- All 5 implementation stories landed (commits `0d5d03e`, `bc9e94f`, `b989673`, `abdd575`, `a6c6be4`). Worker for US-003 proactively caught and patched the `--baseline` arm's parallel `env_override` computation (the plan had specified only the primary arm — good defensive work).
- Quality Gate pass 1 (code-reviewer): flagged missing baseline-arm test — fixed with `test_baseline_arm_mirrors_implicit_strip_without_double_announce` (commit `36d1300`).
- Quality Gate pass 2: rule-text accuracy in `centralized-sdk-call.md` (the #86 `_CLI_AUTO_ANNOUNCEMENT` is a plain `str` inlined at the call site, not a `Final[str]` with a public emitter — rule rewrite distinguishes the inlined #86 shape from the public-helper #95 shape); cli-reference wording clarified (the strip is unconditional on the implicit path; the notice is gated on key presence). Commit `7f5f457`.
- Quality Gate passes 3 + 4: 0 blockers, 0 concerns, only skip/follow-up nits. No fixes required.
- CodeRabbit not configured on this repo; skipped.
- Patterns & Memory decision: no new rule or memory warranted. The implicit-coupling pattern is appropriately a sub-section of `spec-cli-precedence.md`, not a standalone rule (it's an extension of the four-layer precedence concept). The announcement-family pattern is appropriately a sub-section of `centralized-sdk-call.md` (announcements live on the centralized SDK seam). All named symbols verified present via grep.
- Residual follow-up (non-blocking, not filed): Pass 3 nit 4 suggests extracting a `_compute_skill_env_override(args)` helper to deduplicate the primary/baseline coupling decision. Worth a small follow-up bead if the pattern accretes more logic (e.g. the hypothetical `DEC-004 escape hatch`); skip until that happens.
- Final state: 2456 tests passing, 98.33% coverage, ruff clean, 7 beads closed.
