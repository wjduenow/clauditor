# 134 — Bundled `/clauditor` skill cleanup

## Meta

- **Ticket:** [#134](https://github.com/wjduenow/clauditor/issues/134) — "Bundled /clauditor skill ships maintainer-only artifacts and references repo-only docs"
- **Branch:** `feature/134-bundled-skill-fixes`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/134-bundled-skill-fixes`
- **Phase:** devolved (PR [#136](https://github.com/wjduenow/clauditor/pull/136); epic `clauditor-0jy`)
- **Sessions:** 1 (2026-04-25)
- **Base:** dev @ a325f9d

## Summary

A user installed clauditor 0.1.0 via `uv` into a fresh project and asked Claude Code to review the bundled `/clauditor` skill. Four real issues surfaced; the skill ships maintainer-only artifacts that don't work in user installs and references repo-only doc paths.

The four sub-pieces have different blast radii — packaging vs prose vs frontmatter — and one of them (the bundled eval) is intentionally maintainer-only by an existing decision (DEC-007 of `plans/super/43-setup-slash-command.md`). The plan needs to reconcile that prior intent with the user-visible friction.

## Discovery

### Ticket

Four sub-pieces (full text in #134):

1. **Bundled `assets/clauditor.eval.json` is inert in user installs.** Auto-discovery uses `skill_path.with_suffix(".eval.json")` (sibling lookup), so `assets/<name>.eval.json` is never found. The spec's `test_args` references `.claude/commands/chunk.md` which is repo-only.
2. **SKILL.md links to `docs/cli-reference.md#propose-eval` and `docs/cli-reference.md`** — paths that DO ship under the wheel today (`docs/` is included in the sdist, but the wheel package only contains `src/clauditor/`; `docs/` is a repo-root concern, not user-install). Need to verify whether the doc paths actually resolve from the install perspective.
3. **SKILL.md never mentions `clauditor lint` or `clauditor doctor`** — both exist and are diagnostic-relevant.
4. **`allowed-tools` redundancy** — `Bash(clauditor *)` subsumes the two narrower entries.

### Key codebase findings (Codebase Scout)

**Packaging (`pyproject.toml:51-68`):**
- Wheel `include = ["src/clauditor/skills/**/*"]` ships `assets/clauditor.eval.json` to every install.
- sdist also includes the asset.
- `clauditor setup` (`cli/setup.py:132-142`) creates a SYMLINK at `~/.claude/skills/clauditor` → installed package's bundled skill root. Whatever ships under `src/clauditor/skills/clauditor/` is reachable from the symlink.

**Existing consumers of the bundled eval (4):**
- `tests/test_bundled_skill.py:31, 254-267` — `TestBundledEvalSpec` loads it via `EvalSpec.from_file`.
- `tests/test_packaging.py` — asserts the path is in the wheel.
- `CONTRIBUTING.md` — references `--eval src/clauditor/skills/clauditor/assets/clauditor.eval.json` for maintainer runs.
- The bundled spec's own `description` (line 3) marks it "maintainer-only pre-release dogfood gate (DEC-007). Not runnable from a user's project."

**DEC-007 origin (`plans/super/43-setup-slash-command.md:383-396`):**
The bundled eval is **intentionally maintainer-only** — it is the pre-release dogfood gate run by maintainers before tagging:
- L1: `uv run clauditor validate src/clauditor/skills/clauditor/SKILL.md`
- L3: `uv run clauditor grade ... --eval assets/clauditor.eval.json`

The dogfood runs were deferred to pre-release (not per-PR) because they shell out to live `claude -p` and would burn tokens / flake on infra. So DEC-007 is the reason the eval exists at all — it isn't "deadweight that crept in," it is a deliberate maintainer-only artifact whose **shipping-to-users** is the problem.

**SKILL.md regression test surface (`tests/test_bundled_skill.py`):**
- `BODY_MAX_LINES = 500` enforced (line 45).
- Asserts `"propose-eval" in body` (line 208) and `"clauditor suggest" in body` (line 222).
- Frontmatter checks: `disable-model-invocation: true`, `name`, `description ≤1024 chars`. **No** structural assertion on `allowed-tools` content beyond presence.

**lint / doctor command surface:**
- `clauditor lint` (`cli/lint.py`) — agentskills.io conformance check on SKILL.md. Validates frontmatter, naming, structure. Exit 0/1/2.
- `clauditor doctor` (`cli/doctor.py`) — environment diagnostics: Python version, anthropic SDK, `claude` CLI on PATH, API key presence, install mode, plugin registration, `~/.claude/skills/clauditor` symlink health. Always exits 0.

**`bundled-skill-docs-sync.md` triangle:**
- SKILL.md `## Workflow` (canonical) ↔ `docs/skill-usage.md` "What Claude does" numbered list (lines 30-60) ↔ `README.md` `## Using /clauditor in Claude Code` (lines 100+).
- Sync triggers on workflow-section changes (step count/order/title/branching).
- Pure-prose edits inside a single step do NOT trigger sync.
- Frontmatter edits (other than `allowed-tools` semantics) do NOT trigger sync.

### Relevant rules (from Convention Checker, 31 total scanned)

**APPLIES:**

- **`bundled-skill-docs-sync.md`** — applies to #3 if a workflow-section step is added (e.g. a new "Diagnostics" step). Does NOT apply if `lint`/`doctor` mentions land only inside the existing "Common errors" subsection. Also applies to #2 if doc-ref edits change command-name framing.
- **`json-schema-version.md`** — applies to #1 ONLY if we choose to add a portable `SKILL.eval.json` (new persisted JSON file). The bundled eval already has the EvalSpec shape; this rule is about new files we'd create.
- **`eval-spec-stable-ids.md`** — applies to #1 if a new portable `SKILL.eval.json` is added: every assertion / criterion needs `id`, uniqueness-within-skill enforced at load.
- **`path-validation.md`** — adjacent to #1 if a portable spec uses `input_files` or path fields.
- **`readme-promotion-recipe.md`** — applies to #2 if a doc-ref edit cascades into the README teaser; H2 anchor preservation is load-bearing.
- **`skill-identity-from-frontmatter.md`** — adjacent: any frontmatter edit (#4) must preserve the `name:` semantics for identity resolution. Trim of `allowed-tools` does not affect this.

**ADJACENT but not tripped:**

- `internal-skill-live-test-tmp-symlink.md` — bundled `/clauditor` is user-facing (already installed by `clauditor setup`), not maintainer-only-skill.
- `rule-refresh-vs-delete.md` — applies if a rule's canonical anchor moves; none of the planned changes move rule-anchored files in load-bearing ways.

**N/A (16 rules):** centralized-sdk-call, data-vs-asserter-split, in-memory-dict-loader-path, llm-judge-prompt-injection, mock-side-effect-for-distinct-calls, monotonic-time-indirection, non-mutating-scrub, permissive-parser-strict-validator, plan-contradiction-stop, positional-id-zip-validation, pre-llm-contract-hard-validate, pure-compute-vs-io-split, pytester-inprocess-coverage-hazard, spec-cli-precedence, stream-json-schema, subprocess-cwd, llm-cli-exit-code-taxonomy, precall-env-validation, sidecar-during-staging, dual-version-external-schema-embed, project-root-home-exclusion, constant-with-type-info, monotonic-time-indirection, per-type-drift-hints.

### `workflow-project.md`

Does not exist in `.claude/`. No project-specific scoping/review/chunking layer to consult beyond `.claude/rules/`.

### Scoping questions

The four sub-pieces are independent enough that each can take its own decision; bundling them under one PR is fine. The hard scoping question is on #1 — the bundled eval — where DEC-007 (intentionally maintainer-only) collides with the user-visible friction.

#### Q1 — How to handle the bundled `assets/clauditor.eval.json` (the intent-vs-shipping tension)

DEC-007 says the eval is the maintainer pre-release dogfood gate; the user-visible friction is that it ships to every install in a non-runnable shape.

- **A** — **Stop shipping it to the wheel; keep it in the source repo.** Update `pyproject.toml` `include` glob to exclude `**/assets/`. Maintainer pre-release gate still runs in the source repo (where the file lives). Breaks `tests/test_packaging.py` (must update assertion to verify ABSENCE), removes the maintainer reference target from CONTRIBUTING.md (must rewrite to point at the source-repo path), and the `tests/test_bundled_skill.py::TestBundledEvalSpec` load test still passes (file still exists in repo, test runs from repo).
- **B** — **Move under an `_internal/` (or `.maintainer/`) prefix and exclude that prefix from the wheel.** Same effect as A, but the rename signals maintainer-only intent at the directory level. Two-line `pyproject.toml` change + file move. Slightly cleaner than A because the exclusion glob is tied to a deliberate-looking directory name, not to the substring "assets".
- **C** — **Replace with a portable sibling `SKILL.eval.json`** that targets a non-skill-specific input (e.g. omits `test_args`, uses a generic prompt). Users get an auto-discoverable example; maintainers lose the dogfood gate (or keep both — sibling + assets). Highest engineering, biggest user-facing payoff, but introduces a second file to maintain and may water down the dogfood scope.
- **D** — **Keep as-is; just add a SKILL.md prose note marking the bundled eval maintainer-only.** Lowest engineering, but ships dead weight and relies on users reading the note before they try the eval.

Recommendation: **A** or **B** — both honor DEC-007's "maintainer-only" intent by removing the file from the user-visible install surface entirely. B is slightly nicer (named directory) at the cost of a file move + CONTRIBUTING.md rewrite. C is overscoped for this issue (could be a separate ticket). D is the path of least resistance but accepts permanent friction.

#### Q2 — How to handle `docs/cli-reference.md` references in SKILL.md

- **A** — **Replace with stable GitHub URLs** (e.g. `https://github.com/wjduenow/clauditor/blob/v0.1.x/docs/cli-reference.md#propose-eval`). External, stable, doesn't bloat SKILL.md. Risk: link rot if docs reorganize; needs version-pinning convention.
- **B** — **Inline the relevant flag info** into SKILL.md. Self-contained, no external dependency. Risk: SKILL.md grows toward the 500-line cap; flag info drifts from `docs/cli-reference.md`.
- **C** — **Drop the references entirely.** SKILL.md already shows the most common invocation; users who need flag detail can run `clauditor <cmd> --help`. Smallest edit; trades discoverability for terseness.

Recommendation: **C** for the propose-eval link (the prose already covers the common path; `--help` carries the rest); **A or C** for the suggest link. Inline expansion (B) is overkill and bloats SKILL.md.

#### Q3 — Where to place the lint / doctor mention

- **A** — **In the existing "Common errors" subsection only.** Two-line additions ("If `lint` reports issues..." / "If something seems off, run `clauditor doctor`"). Does NOT trigger `bundled-skill-docs-sync.md` (no workflow-section change).
- **B** — **New "Diagnostics" subsection at the end of SKILL.md.** Cleaner organization; scales if more diagnostic commands appear later. Borderline on the sync rule — depends on whether the new subsection is workflow-adjacent or purely reference. Likely still does NOT trigger sync (rule is specific to the `## Workflow` section).
- **C** — **Add a workflow step (e.g. "If errors appear, run `clauditor doctor`")**. DOES trigger the three-file sync rule (workflow step count changes from 7 to 8). Highest blast radius.

Recommendation: **A**. Smallest scope, no sync cascade, achieves the discoverability goal.

#### Q4 — Bundling strategy

- **A** — **One PR for all four sub-pieces.** Simpler to review; one merge.
- **B** — **Two PRs: packaging (#1) + prose/frontmatter (#2,#3,#4).** Packaging change has tests to update; prose changes are mechanical. Lower risk per PR; reviewer can merge prose immediately while packaging gets discussion.
- **C** — **Four PRs, one per sub-piece.** Maximum granularity; thrash on overhead.

Recommendation: **A** if Q1 lands at A or B (mechanical packaging change); **B** if Q1 lands at C (a portable spec is a real engineering effort that deserves its own review surface).

#### Q5 — Target version

`__version__` is currently `0.1.1.dev0`. This work lands in **0.1.1**. Confirm or override.

## Architecture Review

| Area | Rating | Finding |
|------|--------|---------|
| Packaging change (hatchling exclude) | PASS | `exclude = ["src/clauditor/skills/**/assets/**"]` is the correct hatchling syntax; exclude wins over include. Only one `assets/` dir in the repo, so the glob is tight. |
| Runtime symlink behavior | PASS | `clauditor setup` creates a symlink only; no code reads `<install>/assets/` at runtime. EvalSpec.from_file is always called with explicit paths from CLI args, never inferred from the package. |
| `test_packaging.py` | **CONCERN (must fix)** | Line 56-62 currently asserts the bundled eval IS in the wheel. Must flip to assert ABSENCE (and confirm SKILL.md stays). |
| `test_bundled_skill.py::TestBundledEvalSpec` | PASS | Loads from a source-repo path (`SKILL_DIR / "assets" / ...`), not an install path. Tests run from the source checkout, so the file is still there. |
| `CONTRIBUTING.md` | PASS | Maintainer dogfood gate already references the source-repo path; runs from source; no rewrite needed. |
| URL stability for SKILL.md doc refs | PASS | Repo convention is `blob/dev`-pinned URLs (README.md uses this). The `## propose-eval` H2 anchor exists today (`docs/cli-reference.md:153`). |
| `bundled-skill-docs-sync.md` cascade | PASS | Q3=A places lint/doctor mention inside "Common errors" only — does NOT touch `## Workflow` — so the three-file sync rule is not triggered. Q2 prose changes to non-workflow sections, also no trigger. Q4 frontmatter trim does not touch workflow. |
| Body line cap (`test_skill_md_body_under_500_lines`) | PASS | Current body is well under 500. Adding two short lines for lint/doctor + URL replacements is net-neutral or smaller. |

No blockers. The CONCERN on `test_packaging.py` is a known/expected change captured as US-001 below.

## Refinement Log

### DEC-001 — Exclude `**/assets/**` from the built wheel

Add `exclude = ["src/clauditor/skills/**/assets/**"]` to `[tool.hatch.build.targets.wheel]` in `pyproject.toml`. Mirror in the sdist target if needed. The maintainer-only `assets/clauditor.eval.json` stays in the source tree (where the maintainer dogfood gate per DEC-007 of `plans/super/43-setup-slash-command.md` runs); it stops landing on user installs.

*Rationale:* Q1=A. Honors DEC-007's "intentionally maintainer-only" intent by removing the artifact from the user-visible install surface, without losing the dogfood capability.

### DEC-002 — Flip `test_packaging.py` from presence-assertion to absence-assertion

Replace the `test_wheel_contains_bundled_eval_json` assertion (currently asserts the path IS in `wheel_namelist`) with a regression guard that:
1. Asserts no path under `assets/` ships in the wheel.
2. Asserts `SKILL.md` itself still ships (positive control — confirms the wheel still has the bundled skill).

*Rationale:* The current test is a regression guard against accidentally dropping the bundled skill; post-change, it must guard against accidentally re-shipping the maintainer artifact AND against accidentally dropping SKILL.md. Both invariants matter.

### DEC-003 — Replace `docs/cli-reference.md` references with `blob/dev`-pinned GitHub URLs

Lines 67 and 109 of SKILL.md become:
- `https://github.com/wjduenow/clauditor/blob/dev/docs/cli-reference.md#propose-eval`
- `https://github.com/wjduenow/clauditor/blob/dev/docs/cli-reference.md`

*Rationale:* Q2=A. Repo convention is `blob/dev`-pinned (README.md established the pattern). `dev` is the integration branch, so the link tracks current state; tag-pinning would rot fast and tag-by-version would mislead users on a 0.1.x release reading docs about 0.2.x features.

### DEC-004 — `lint` / `doctor` mentions land in "Common errors" only; no workflow step

Add two short bullets to the "Common errors" subsection:
- One pointing operators at `clauditor lint <skill>` for spec-conformance issues.
- One pointing at `clauditor doctor` for environment / install diagnostics.

Do NOT add a new workflow step or new top-level subsection.

*Rationale:* Q3=A. Avoids triggering the `bundled-skill-docs-sync.md` three-file cascade (rule is scoped to `## Workflow` step changes). Achieves the discoverability goal at minimum surface area.

### DEC-005 — Trim `allowed-tools` to non-redundant entries

Drop `Bash(clauditor propose-eval *)` and `Bash(clauditor suggest *)`; keep `Bash(clauditor *), Bash(uv run clauditor *)`.

*Rationale:* Q4=A scope; the wider entry already grants the narrower behaviors. No regression test today asserts on `allowed-tools` content beyond presence, so this is safe.

### DEC-006 — One PR; target 0.1.1

All four sub-pieces ship in a single PR against `dev`. `__version__` already on `0.1.1.dev0`; the next release tag will be `0.1.1`.

*Rationale:* Q4=A, Q5=confirm. The four sub-pieces are small and topically coherent ("clean up the bundled skill"); splitting would be churn.

### DEC-007 — Maintainer dogfood gate stays in the source repo, unchanged

DEC-007 of `plans/super/43-setup-slash-command.md` (the original "bundled eval is the pre-release dogfood gate") is preserved as-is. The maintainer still runs:

```bash
uv run clauditor grade src/clauditor/skills/clauditor/SKILL.md \
  --eval src/clauditor/skills/clauditor/assets/clauditor.eval.json
```

from the source checkout. Only the wheel's contents change; CONTRIBUTING.md needs no edit.

*Rationale:* The maintainer dogfood gate is orthogonal to user-install hygiene. Don't change two things at once.

### DEC-008 — Add a regression guard pinning the wheel-exclusion

A new `test_packaging.py` test that asserts `assets/clauditor.eval.json` is NOT in the wheel (in addition to DEC-002's "no `assets/` paths" assertion) — load-bearing because the file is the original surface that motivated this issue, and a future contributor adding a new `assets/` file should hit a clear test failure pointing at this specific decision.

*Rationale:* Cheap insurance against the exact regression this PR is fixing.

## Detailed Breakdown

Story ordering follows: packaging change → tests → frontmatter/prose → quality gate → patterns. Each story is sized to one Ralph context window.

### US-001 — Exclude `assets/` from the wheel; update packaging tests

**Description:** Add the hatchling exclude glob and update `test_packaging.py` to enforce the new contract (assets/ absent, SKILL.md present).

**Traces to:** DEC-001, DEC-002, DEC-008.

**Acceptance criteria:**
- `pyproject.toml` `[tool.hatch.build.targets.wheel]` declares `exclude = ["src/clauditor/skills/**/assets/**"]` (and the sdist target if applicable — verify with `uv build`).
- `tests/test_packaging.py::test_wheel_contains_bundled_eval_json` is renamed/rewritten to assert that `assets/clauditor.eval.json` (and any other `assets/` path) is NOT in the wheel namelist.
- A positive-control assertion confirms `clauditor/skills/clauditor/SKILL.md` IS in the wheel namelist.
- `uv run pytest tests/test_packaging.py` passes.
- `uv build` succeeds; the resulting wheel contains `SKILL.md` but not `assets/`.

**Done when:** Both the wheel-build and the packaging tests pass on the new contract.

**Files:**
- `pyproject.toml` — add `exclude` line.
- `tests/test_packaging.py` — flip assertion + add positive-control assertion.

**Depends on:** none.

**TDD:**
- Write the new (failing) assertions first; confirm they fail against the current `pyproject.toml`.
- Add the `exclude` glob; confirm the new assertions pass.
- Verify `tests/test_bundled_skill.py::TestBundledEvalSpec` (which loads the source-repo path) still passes — this is the regression guard that the source-tree file stays.

### US-002 — Replace `docs/cli-reference.md` refs in SKILL.md with `blob/dev`-pinned GitHub URLs

**Description:** Edit the two SKILL.md references (lines 67 and 109) to point at stable GitHub URLs.

**Traces to:** DEC-003.

**Acceptance criteria:**
- SKILL.md line ~67: `docs/cli-reference.md#propose-eval` → `https://github.com/wjduenow/clauditor/blob/dev/docs/cli-reference.md#propose-eval`.
- SKILL.md line ~109: `docs/cli-reference.md` → `https://github.com/wjduenow/clauditor/blob/dev/docs/cli-reference.md`.
- `tests/test_bundled_skill.py::test_skill_md_body_under_500_lines` still passes (no risk — change is net-neutral on line count).
- The two regression presence-assertions (`"propose-eval"`, `"clauditor suggest"`) still pass.

**Done when:** The two URL replacements are in SKILL.md; existing regression tests pass.

**Files:**
- `src/clauditor/skills/clauditor/SKILL.md` — two substring replacements.

**Depends on:** none.

### US-003 — Add `clauditor lint` / `clauditor doctor` mentions to SKILL.md "Common errors"

**Description:** Add two short bullets to the existing "Common errors" subsection — one for `lint`, one for `doctor`. Do NOT modify the `## Workflow` section.

**Traces to:** DEC-004.

**Acceptance criteria:**
- The "Common errors" subsection has a bullet referencing `clauditor lint <skill-path>` for spec-conformance issues (with one-line context: it validates SKILL.md against the agentskills.io spec).
- The "Common errors" subsection has a bullet referencing `clauditor doctor` for environment/install diagnostics (with one-line context: it inspects Python, SDK, `claude` CLI, API key, install mode).
- The `## Workflow` section is byte-identical to before this story.
- New regression assertions in `tests/test_bundled_skill.py` for prose presence: `"clauditor lint"` and `"clauditor doctor"` (per the `bundled-skill-docs-sync.md` rule's "load-bearing string regression assertion" pattern, even though the rule's three-file cascade is not triggered).
- `tests/test_bundled_skill.py::test_skill_md_body_under_500_lines` still passes.

**Done when:** The two bullets land in the right subsection; the new prose-presence assertions land; all bundled-skill tests pass.

**Files:**
- `src/clauditor/skills/clauditor/SKILL.md` — additions to the "Common errors" subsection.
- `tests/test_bundled_skill.py` — two new prose-presence assertions following the existing `test_body_mentions_propose_eval` shape.

**Depends on:** none. Independent of US-002.

**TDD:**
- Write the failing prose-presence assertions first.
- Add the SKILL.md bullets; confirm the new assertions pass and the line-cap test still passes.

### US-004 — Trim redundant `allowed-tools` entries in SKILL.md frontmatter

**Description:** Drop `Bash(clauditor propose-eval *)` and `Bash(clauditor suggest *)` from the `allowed-tools` line; keep `Bash(clauditor *)` (which subsumes them) and `Bash(uv run clauditor *)`.

**Traces to:** DEC-005.

**Acceptance criteria:**
- `allowed-tools:` reads exactly: `Bash(clauditor *), Bash(uv run clauditor *)`.
- The `disable-model-invocation: true` and `name:` invariants are unchanged.
- `tests/test_bundled_skill.py` frontmatter assertions all pass.
- `clauditor lint src/clauditor/skills/clauditor/SKILL.md` returns exit 0 (sanity check on the conformance layer's view of the new frontmatter).

**Done when:** Frontmatter is trimmed and lint passes.

**Files:**
- `src/clauditor/skills/clauditor/SKILL.md` — single-line frontmatter edit.

**Depends on:** none.

### US-005 — Quality Gate

**Description:** Run code reviewer 4× across the full changeset, fixing real bugs each pass. Run CodeRabbit if available. Run the project validation suite.

**Acceptance criteria:**
- 4 passes of the code-reviewer agent over the diff vs `dev`. All real findings fixed; false positives documented in the plan's session notes.
- `uv run ruff check src/ tests/` clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes; coverage gate (80%) holds.
- `uv build` succeeds and produces a wheel with `SKILL.md` but no `assets/`.
- Manual smoke: install the built wheel into a scratch venv; confirm `clauditor setup` creates a working symlink and the `assets/` directory does not appear in the install path.

**Done when:** All four review passes are clean and full validation passes.

**Files:** none (review-only; fixes land in the same files as US-001..US-004).

**Depends on:** US-001, US-002, US-003, US-004.

### US-006 — Patterns & Memory

**Description:** Update `.claude/rules/`, `docs/`, or memory based on patterns surfaced during this work.

**Acceptance criteria:**
- Evaluate whether a new rule is warranted around "ship maintainer-only artifacts to source-tree only, exclude from wheel" — there's no current rule on packaging hygiene; this work is the first instance. If a second instance surfaces, codify; for now, document the pattern in this plan's `## Session Notes` and reference from CONTRIBUTING.md if useful.
- Evaluate whether `bundled-skill-docs-sync.md` needs a clarification on the "load-bearing prose assertion" pattern (the rule mentions it but US-003 is a fresh anchor for the in-Common-errors variant). If yes, refresh the rule's "Canonical implementation" section.
- No memory updates expected unless a user-preference signal emerged.

**Done when:** Either a rule update lands or session notes capture the deliberate "no rule yet" call.

**Files (potentially):**
- `.claude/rules/bundled-skill-docs-sync.md` — only if the rule's anchor list needs an additive note.
- `plans/super/134-bundled-skill-fixes.md` — session notes addendum.

**Depends on:** US-005.

## Beads Manifest

- **Epic:** `clauditor-0jy` — #134: bundled /clauditor skill cleanup
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/134-bundled-skill-fixes`
- **Branch:** `feature/134-bundled-skill-fixes`
- **Plan PR:** [#136](https://github.com/wjduenow/clauditor/pull/136)

| Task | ID | Depends on | Status |
|------|-----|------------|--------|
| US-001 — exclude `assets/` from wheel + flip `test_packaging.py` | `clauditor-i2x` | none | open (ready) |
| US-002 — SKILL.md doc-refs → `blob/dev` URLs | `clauditor-n48` | none | open (ready) |
| US-003 — lint/doctor mentions in Common errors + assertions | `clauditor-gkd` | none | open (ready) |
| US-004 — trim redundant `allowed-tools` entries | `clauditor-i5g` | none | open (ready) |
| US-005 — Quality Gate (4× review + ruff + pytest + wheel smoke) | `clauditor-bqf` | US-001..US-004 | open (blocked) |
| US-006 — Patterns & Memory | `clauditor-9n8` | US-005 | open (blocked) |

All six tasks are linked under the epic via `parent-child`. US-005 has four `blocks` deps (one per implementation story); US-006 has one `blocks` dep on US-005.

## Session Notes

### 2026-04-25 — Session 1 (discovery)

- Worktree created, branch `feature/134-bundled-skill-fixes`.
- Codebase scout + convention checker run in parallel; 31 rules audited.
- Surfaced the DEC-007 origin of the bundled eval — it is intentionally maintainer-only, not deadweight. Reframes Q1 from "fix the eval" to "stop shipping the maintainer artifact to users while preserving the maintainer dogfood gate."
- Five scoping questions presented; user answered A/A/A/A/confirm.
- Phase 2 architecture review: 8 areas, 7 PASS + 1 CONCERN (test_packaging.py — captured as US-001).
- Phase 3 refinement: 8 decisions logged (DEC-001 through DEC-008).
- Phase 4 detailing: 6 stories generated (4 implementation + Quality Gate + Patterns & Memory).
- Phase 5 publish: plan committed, branch pushed, draft PR #136 opened.
- Phase 6 approved: user signed off on the plan.
- Phase 7 devolve: epic `clauditor-0jy` + 6 tasks created in beads with full dep graph; four implementation tasks ready in parallel.

## Patterns & Memory decision (US-006 / clauditor-9n8, 2026-04-25)

Two evaluations under the bead's acceptance criteria. Both default
to "no infrastructure change"; rationale captured here so a future
contributor can audit the call.

### Decision A — No new packaging-hygiene rule (yet)

**Question:** Should `.claude/rules/maintainer-only-artifact-wheel-exclusion.md`
(or similar) codify the "ship maintainer-only artifacts to source-tree
only, exclude from wheel" pattern?

**Decision:** No rule yet. Document the second-instance trigger here.

**Reasoning:**

- The repo has *two* loosely-related instances of "keep maintainer-
  only artifacts out of the user-visible wheel surface":
  - **#75** (`plans/super/75-move-review-skill.md`) — moved
    `review-agentskills-spec` from `src/clauditor/skills/` to
    `.claude/skills/` so the `src/`-anchored include glob no longer
    reached it. Solved at the **layout** level (relocate the file
    out of the glob's reach). DEC-005 of #75 added an empirical
    `uv build` + grep gate in Quality Gate.
  - **#134** (this plan, DEC-001/DEC-002/DEC-008) — added an
    explicit `exclude = ["src/clauditor/skills/**/assets/**"]`
    glob to keep `assets/clauditor.eval.json` (the maintainer
    dogfood eval per DEC-007 of `plans/super/43-setup-slash-command.md`)
    in-tree but out of the wheel. Solved at the **exclude-glob**
    level. DEC-002 + DEC-008 added two regression-guard tests
    pinning the exclusion (`test_wheel_excludes_assets_dir` broad
    + `test_wheel_excludes_bundled_eval_json` narrow).
- The two instances share the *concern* ("don't ship maintainer
  artifacts to users") but differ in *mechanism* (move out of glob
  vs add explicit exclude). A unified rule would have to either
  (a) describe both mechanisms generically, watering down the
  prescriptive value, or (b) pick one mechanism, leaving the other
  uncovered.
- The current safety net is structural and durable without a rule:
  - `tests/test_packaging.py::test_wheel_contains_bundled_skill_md`
    is the positive-control regression guard.
  - `tests/test_packaging.py::test_wheel_excludes_assets_dir` +
    `test_wheel_excludes_bundled_eval_json` are the absence guards
    for the #134 surface.
  - The #75 work captured the empirical wheel-shipping check in its
    Quality Gate decision (DEC-005 of #75).
- Bead clauditor-9n8 instructions explicitly bias toward less
  infrastructure: "New rules are load-bearing and hard to remove;
  only add one if the pattern is durable across multiple future
  instances."

**Second-instance trigger** (when to revisit): if a *third* instance
lands — e.g. excluding test fixtures, build helper scripts, or a
sibling `.maintainer/` subtree from the wheel — codify at that
point. The rule should describe both mechanisms (layout + exclude
glob), name the regression-guard pair (positive + absence
assertions in `tests/test_packaging.py`), and reference both #75
DEC-005 and #134 DEC-002/DEC-008 as canonical anchors.

### Decision B — No refresh of `.claude/rules/bundled-skill-docs-sync.md`

**Question:** Does US-003's two new prose-presence assertions
(`test_body_mentions_lint`, `test_body_mentions_doctor`) — landing
inside the "Common errors" subsection rather than the `## Workflow`
section — warrant a refresh of the rule's Canonical implementation
section?

**Decision:** No refresh. Note retained here.

**Reasoning:**

- The rule's step 5 already prescribes the prose-presence
  assertion pattern: "for any load-bearing string the new workflow
  introduces (e.g. `propose-eval`, `capture`, a new command name).
  One `assert "<string>" in SKILL_MD.read_text()` per branch."
- US-003's two new assertions follow that pattern verbatim — same
  test-class location (`TestSkillMdBody`), same shape
  (`assert "<string>" in body`), same one-line discipline. They
  are *applications* of the existing pattern, not new pattern
  shape.
- Per `.claude/rules/rule-refresh-vs-delete.md`'s Q1 ("Is the
  pattern still load-bearing?"): yes, but the rule's prose already
  covers it. Per the rule's broader guidance: "Refresh is cheap;
  replacement is not" — but a refresh *only* to add two more
  bullets to the canonical-anchor list would be ceremonial, not
  load-bearing.
- The rule's framing is intentionally about the **three-file
  workflow-cascade triangle** (SKILL.md ↔ docs/skill-usage.md ↔
  README.md). DEC-004 of this plan deliberately landed the
  lint/doctor mentions in "Common errors" *to avoid* triggering
  the cascade; inflating the rule's canonical-implementation
  section with non-cascade anchors would blur its primary message.
- Subtle clarification considered and rejected: rewording step 5
  from "any load-bearing string the new workflow introduces" to
  "any load-bearing string the bundled skill introduces" would
  generalize. But the existing wording is already broad enough
  (any reader who hits a lint/doctor-style addition can apply it
  by analogy), and the workflow-framing of the rule is the
  load-bearing context — broadening it risks signal loss.

**Second-instance trigger** (when to revisit): if a *third*
instance lands where the prose-presence assertion pattern is
applied to a non-workflow, non-Common-errors location (e.g. a new
top-level subsection, a frontmatter-derived string), and the
existing rule's framing reads as confusingly workflow-scoped, then
a small refresh to step 5 + the canonical-implementation list is
warranted. Until then, the existing wording carries.

### Memory

No user-preference signals emerged during this work. No memory
updates.
