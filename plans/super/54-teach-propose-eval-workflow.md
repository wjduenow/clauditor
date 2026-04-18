# Super Plan: #54 — Teach `/clauditor` skill the `propose-eval` workflow

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/54
- **Branch:** `feature/54-teach-propose-eval`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/54-teach-propose-eval`
- **Phase:** `published`
- **PR:** https://github.com/wjduenow/clauditor/pull/56
- **Sessions:** 1
- **Last session:** 2026-04-17

---

## Discovery

### Ticket Summary

**What:** Update the bundled `/clauditor` slash command
(`src/clauditor/skills/clauditor/SKILL.md`) so Claude Code offers
`clauditor propose-eval` as the default next step when a skill has no
sibling `eval.json`, instead of stopping at Step 2 of the workflow.

**Why:** Epic #52 shipped `propose-eval` (PR #53), an LLM-assisted
bootstrap that generates a full 3-layer EvalSpec. The bundled skill
still dead-ends in that case — the capability exists but the primary
entry point (the `/clauditor` skill) does not know about it.

**Done when:** `/clauditor` run against a skill without an `eval.json`
walks the user through dry-run → review → write via
`clauditor propose-eval`, then hands off to the existing
validate/grade flow. `tests/test_bundled_skill.py` still passes.

**Who benefits:** Users running `/clauditor` in Claude Code on new
skills (common onboarding path) + any agent-loaded use of the skill
that would otherwise stop at "ask the user whether to point at
`--eval` or stop."

### Codebase Findings

- **Edit site:** `src/clauditor/skills/clauditor/SKILL.md`, lines
  35–39 (`## Workflow` Step 2). Skill is 77 lines; the body size cap
  enforced by `tests/test_bundled_skill.py` is 500 lines — plenty of
  headroom.
- **Frontmatter:** lines 1–10. `allowed-tools` is a single string:
  `Bash(clauditor *), Bash(uv run clauditor *)`. The existing
  `Bash(clauditor *)` wildcard already matches
  `clauditor propose-eval` — AC #2's literal addition may be
  redundant (see DEC-001 below).
- **Three-layer model section** already exists at lines 18–27 — the
  new step can reference it rather than duplicate it.
- **`propose-eval` CLI:** `src/clauditor/cli/propose_eval.py` with
  flags `--dry-run`, `--from-capture`, `--from-iteration`, `--force`,
  `--model`, `--json`, `-v/--verbose`, `--project-dir`. Exit codes
  0/1/2/3 per the 4-exit-code taxonomy. Docs live at
  `docs/cli-reference.md#propose-eval` (lines 33–88).
- **Setup / install flow:** `clauditor setup` creates a **symlink**
  at `.claude/skills/clauditor` → `importlib.resources`-located
  bundled skill dir. In editable installs (`uv sync --dev`), the
  symlink targets in-tree source, so edits to SKILL.md are
  hot-reloaded by Claude Code on the next skill load. Published
  wheels include the skill as package data via the
  `[tool.hatch.build.targets.wheel]` include for
  `src/clauditor/skills/**/*`, stamped at build time by
  `build_hooks/stamp_skill_version.py`.
- **Tests that exercise SKILL.md:** `tests/test_bundled_skill.py` —
  validates frontmatter (name pattern, description length, body
  under 500 lines, required `disable-model-invocation: true`), loads
  the sibling `assets/clauditor.eval.json`, and checks per-layer id
  uniqueness. The skill's prose body is not asserted on.
- **DEC-001 (plan #52):** Capture discovery order is
  `tests/eval/captured/<skill>.txt` → `.clauditor/captures/<skill>.txt`
  (then iteration fallback). Missing both → exit 2 suggesting
  `clauditor capture`.
- **DEC-006 (plan #52):** 4-exit-code taxonomy; `--dry-run` prints
  the prompt and exits 0 with no Anthropic spend — the "cost-free
  preview" the ticket cites.

### Applicable `.claude/rules/`

- **`readme-promotion-recipe.md`** — the rule governs README/docs
  structure. It does NOT apply to the SKILL.md body (SKILL.md is
  package data read by Claude Code, not user-facing reference docs),
  but its D2 teaser philosophy ("one sentence + small code block +
  link") maps directly onto the style we want for the new step:
  reference `docs/cli-reference.md#propose-eval` for flag detail
  instead of duplicating it inside SKILL.md (satisfies AC #3).
- All other rules — N/A. This ticket is a prose edit to a bundled
  markdown file. No schema changes, no LLM wiring, no subprocess
  work, no new Python code paths.

### Ambiguities → resolved via scoping questions

See the 5 questions below (DEC-001 through DEC-005 once answered).

---

## Architecture Review

Parallel targeted review (testing + content-consistency; the other baseline areas
are trivially `pass` for a prose-only edit).

| Area | Rating | Finding |
|---|---|---|
| Security | pass | No new code paths; no new inputs. `Bash(clauditor propose-eval *)` narrows nothing the existing `Bash(clauditor *)` wildcard didn't already permit. |
| Performance | pass | N/A — markdown edit. |
| Data Model | pass | No schema changes. |
| API Design | pass | N/A — doc-only. |
| Observability | pass | N/A. |
| **Testing Strategy** | concern | No existing test or CI job performs `uv build` → install → `clauditor setup` end-to-end. `test_packaging.py` inspects wheel contents as a ZIP; `test_bundled_skill.py` reads source; `test_setup.py` is pure decision logic. **Follow-up filed as #55.** For #54, AC #4 verification runs as a Quality Gate manual command sequence (DEC-004, DEC-008). |
| **Content Consistency** | concern | `docs/skill-usage.md` (4-step `/clauditor` workflow description, lines 30–38) and `README.md` "Using /clauditor in Claude Code" summary (lines 60–68) must update in lockstep with SKILL.md to avoid step-count drift. `assets/clauditor.eval.json` rubric (the maintainer dogfood gate, DEC-007 of #52) stays satisfied by design because the new Step 3 still transitions into the existing L1/L3 flow and still mentions assertions + concrete commands. |

No blockers. Concerns resolved as decisions in the Refinement Log.

### Follow-up issue filed

- **#55** — Investigate end-to-end test coverage for bundled skill packaging + setup round-trip. Carries the testing-gap investigation out of #54's scope so #54 itself can ship as a pure prose-edit PR.

---

## Refinement Log

### Decisions

**DEC-001 — `allowed-tools` entry (Q1=A).**
Add explicit `Bash(clauditor propose-eval *)` alongside the existing `Bash(clauditor *)` wildcard. The wildcard already covers it, but the literal entry satisfies AC #2 byte-for-byte and serves as in-place documentation of which subcommands the skill expects to run.

**DEC-002 — Workflow structure (Q2=B).**
Insert a new Step 3 "Bootstrap eval spec if missing" between the current Step 2 (locate) and current Step 3 (Run L1). Renumber existing 3 → 4, 4 → 5, 5 → 6. The `## Workflow` section grows from 5 steps to 6.

**DEC-003 — Prose density for the new branch (Q3=B).**
Medium density: show a short bash block (~6–10 lines) of the `propose-eval --dry-run` → review → `propose-eval` sequence. Flag detail (full table of `--from-capture`, `--from-iteration`, `--force`, etc.) stays in `docs/cli-reference.md#propose-eval`; SKILL.md links there instead of duplicating.

**DEC-004 — Wheel round-trip verification (Q4=B).**
AC #4 verification is the full sequence: `uv build` → scratch `uv venv` → `pip install dist/clauditor-*.whl` → scratch project with `.git/` marker → `clauditor setup` → confirm the installed `.claude/skills/clauditor/SKILL.md` contains the new `propose-eval` content and a stamped `clauditor-version` matching `pyproject.toml`'s `[project].version`. Runs once during Quality Gate; output pasted into PR description. Turning this into a real test is #55's job.

**DEC-005 — Capture-discovery fallback phrasing (Q5=B).**
Two-branch wording with a concrete example: mention both that `propose-eval` auto-discovers a captured run if one exists at `tests/eval/captured/<skill>.txt` or `.clauditor/captures/<skill>.txt`, AND give a concrete "if no capture is available, run `clauditor capture <skill>` first for a higher-quality proposal" pointer. Cites DEC-001 of plan #52 for the discovery order.

**DEC-006 — Docs-sync scope (R1=A).**
Bundle `docs/skill-usage.md` and `README.md` updates into the same PR as the SKILL.md edit. Step-count drift between the three files is the risk this decision eliminates.

**DEC-007 — Regression assertion (R2=A).**
Extend `tests/test_bundled_skill.py` with a tiny prose-presence assertion: the skill body contains the string `"propose-eval"`. Cheap insurance against someone reverting the Step 3 insertion without noticing. Stronger structural assertions (e.g. "Workflow section has exactly 6 steps") rejected as brittle for prose-edit velocity.

**DEC-008 — Manual verification artifact (R3=A).**
The wheel round-trip output is pasted into the PR description as evidence. Do NOT commit a `scripts/verify_bundled_skill.sh` — that's over-scoping for #54. Runnable test coverage is #55's deliverable.

**DEC-009 — Phrasing constraints.**
The new SKILL.md prose mirrors `docs/cli-reference.md#propose-eval` terminology: use "cost-free preview" for `--dry-run`, "LLM-assisted bootstrap" for the command's purpose, "sibling `<skill>.eval.json`" for the output location. Reference the anchor, don't duplicate the flag table.

**DEC-010 — Circular-eval safety.**
`assets/clauditor.eval.json` grades the `/clauditor` skill itself. The new prose must continue to satisfy:
- `"mentions-assertions"` (L1): body mentions "assertion" — preserved by Steps 4–6 (unchanged) still describing L1.
- `"identifies-layer-correctly"` (L3): output distinguishes L1 assertions from L3 rubric. The new Step 3 is a *bridge* into the existing layer flow, not a replacement.
- `"provides-concrete-guidance"` (L3): concrete `clauditor` commands remain throughout.
No rubric update needed; the design preserves all three criteria.

### Session Notes

- 2026-04-17 session 1: discovery + architecture review + refinement complete. Primary finding from parallel review: testing-gap for the wheel round-trip is real but out of scope for a prose-edit ticket; filed as #55 and scoped away via DEC-004 / DEC-008.
- `Bash(clauditor *)` wildcard observation: initially flagged AC #2 as potentially redundant, but user chose DEC-001=A to satisfy the AC verbatim anyway. No harm — one line of documentation value.

---

## Detailed Breakdown

Project validation command (appended to every story's acceptance criteria):
`uv run ruff check src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing`

### US-001 — Rewrite SKILL.md Workflow with the propose-eval bootstrap step

**Description:** Insert a new Step 3 "Bootstrap eval spec if missing" into
`src/clauditor/skills/clauditor/SKILL.md`'s `## Workflow` section and
renumber subsequent steps. Add the explicit `Bash(clauditor propose-eval *)`
entry to `allowed-tools`. Add one short tagline under `## Workflow`
explaining when `propose-eval` is the right entry point.

**Traces to:** DEC-001, DEC-002, DEC-003, DEC-005, DEC-009, DEC-010. AC #1, #2, #3, #5.

**Acceptance Criteria:**
- SKILL.md's `## Workflow` section has 6 steps (was 5); new Step 3 is titled "Bootstrap eval spec if missing".
- Step 3 contains a bash block showing `clauditor propose-eval <skill.md> --dry-run` → review → `clauditor propose-eval <skill.md>`.
- Step 3 includes a two-branch capture-discovery note: auto-discovery paths AND a concrete `clauditor capture` pointer when no capture exists.
- Step 3 links to `docs/cli-reference.md#propose-eval` for flag details (no flag table duplicated inline).
- Frontmatter `allowed-tools` line contains `Bash(clauditor propose-eval *)` alongside the existing wildcard.
- One added line under `## Workflow` header states when `propose-eval` is the right entry point vs starting from an existing `eval.json`.
- Prose mirrors cli-reference terminology: "cost-free preview", "LLM-assisted bootstrap", "sibling `<skill>.eval.json`".
- Existing Steps 4/5/6 (formerly 3/4/5) still describe L1 validation, L3 grading, and reporting — no semantic change to those steps.
- SKILL.md body stays under 500 lines (`tests/test_bundled_skill.py` gate).
- Project validation passes.

**Done when:** `head -60 src/clauditor/skills/clauditor/SKILL.md` shows the new Step 3 and the updated `allowed-tools` line, and `uv run pytest tests/test_bundled_skill.py` is green.

**Files:**
- `src/clauditor/skills/clauditor/SKILL.md` — edit frontmatter (`allowed-tools`) + body (`## Workflow`, insert Step 3, renumber).

**Depends on:** none.

---

### US-002 — Sync docs/skill-usage.md and README.md to the 6-step workflow

**Description:** Parallel prose updates to keep user-facing docs consistent
with SKILL.md. `docs/skill-usage.md` gets the step-count bump from 4 to 5
(the README/skill-usage summary collapses L2 into the "locate" step, so its
count is independent of SKILL.md's internal 6). `README.md`'s "Using
/clauditor in Claude Code" section gains a one-sentence mention of the
propose-eval fallback.

**Traces to:** DEC-006. Mitigates the Content Consistency concern.

**Acceptance Criteria:**
- `docs/skill-usage.md` numbered list reflects the new bootstrap step in the right position (between locate and run-L1).
- `docs/skill-usage.md` terminology mirrors SKILL.md ("cost-free preview", "propose-eval").
- `README.md` "Using /clauditor in Claude Code" summary mentions the propose-eval fallback in one sentence; total section stays within the D2 lean teaser budget (no new code block, no flag duplication).
- No renames or anchor changes — all existing heading text preserved byte-identical per `.claude/rules/readme-promotion-recipe.md`.
- Project validation passes.

**Done when:** `git diff docs/skill-usage.md README.md` shows the workflow-step sync and nothing more.

**Files:**
- `docs/skill-usage.md` — bullet list update.
- `README.md` — one-sentence summary addition under the `## Using /clauditor in Claude Code` heading.

**Depends on:** US-001 (wording must match finalized SKILL.md).

---

### US-003 — Regression assertion in tests/test_bundled_skill.py

**Description:** Add a one-line prose-presence assertion that the SKILL.md
body contains the string `"propose-eval"`. Purpose: catch accidental revert
of US-001.

**Traces to:** DEC-007. Regression guard only — real packaging coverage is #55's job.

**TDD:** write the assertion first against current HEAD (it fails — source doesn't have "propose-eval" yet) → merge US-001 → assertion passes. If TDD order is awkward because US-001 is done first, the assertion still lands as a locking test.

**Acceptance Criteria:**
- A new test case in `tests/test_bundled_skill.py` asserts `"propose-eval" in SKILL_MD.read_text()`.
- Test class placement follows existing pattern (class-based, one logical group).
- Test is additive; no existing assertion is weakened or removed.
- Project validation passes; coverage gate (80%) holds.

**Done when:** `uv run pytest tests/test_bundled_skill.py -k propose_eval` runs and passes.

**Files:**
- `tests/test_bundled_skill.py` — add one test method (e.g. `test_body_mentions_propose_eval`).

**Depends on:** US-001 (test asserts on US-001's prose).

---

### Quality Gate — code review x4 + CodeRabbit + wheel round-trip verification

**Description:** Run the code reviewer agent 4 times across the full
changeset (SKILL.md + docs/skill-usage.md + README.md + tests/test_bundled_skill.py),
fixing real bugs each pass. Run CodeRabbit review. Execute the wheel
round-trip verification sequence (DEC-004) and paste the output into the
PR description.

**Traces to:** DEC-004, DEC-008, DEC-010. AC #4.

**Acceptance Criteria:**
- 4 code-reviewer passes complete; any real-bug findings fixed and re-reviewed.
- CodeRabbit review complete; findings addressed or documented as false positives.
- Wheel round-trip sequence runs cleanly:
  ```bash
  uv build --wheel
  uv venv /tmp/.venv-54
  /tmp/.venv-54/bin/pip install dist/clauditor-*.whl
  mkdir -p /tmp/project-54 && cd /tmp/project-54 && git init
  /tmp/.venv-54/bin/clauditor setup
  grep -c "propose-eval" .claude/skills/clauditor/SKILL.md  # expect >= 1
  grep "clauditor-version:" .claude/skills/clauditor/SKILL.md  # expect stamped, not "0.0.0-dev"
  ```
- Output of the above pasted into the PR description as evidence.
- Circular-eval spot check: read the new SKILL.md prose and confirm it still satisfies `assets/clauditor.eval.json`'s criteria (`mentions-assertions`, `identifies-layer-correctly`, `provides-concrete-guidance`).
- `scripts/validate_skill_frontmatter.py src/clauditor/skills/clauditor` exits 0.
- Project validation passes.

**Done when:** PR description contains the round-trip output block; all reviewer passes green; all tests green.

**Files:** none (verification only; fixes land in the touched files from US-001/US-002/US-003).

**Depends on:** US-001, US-002, US-003.

---

### Patterns & Memory — codify the bundled-skill docs-sync pattern

**Description:** Capture any reusable pattern learned from this ticket. The
strongest candidate: when editing the bundled `/clauditor` SKILL.md's
workflow, always check `docs/skill-usage.md` and `README.md`'s matching
summary for step-count drift. Minor — record as a concise rule if it
pulls its weight; otherwise just update memory.

**Traces to:** DEC-006. Follows `.claude/rules/` writing conventions.

**Acceptance Criteria:**
- Evaluate whether a new rule file in `.claude/rules/` is warranted. If yes, write it in the existing rule-file style (pattern / why / canonical implementation / when applies / when does not). If no, record the insight in memory instead.
- If a rule is added, `MEMORY.md` index is not touched (rules live in `.claude/rules/`, not auto-memory).
- No code changes; no test changes.

**Done when:** either a rule file exists at `.claude/rules/<name>.md` referencing this ticket's anchors, or a memory note records the pattern, or a decision to skip is documented in the plan's Session Notes.

**Files (if rule warranted):**
- `.claude/rules/bundled-skill-docs-sync.md` (or similar).

**Depends on:** Quality Gate.

---

### Rules compliance gate

- `.claude/rules/readme-promotion-recipe.md`: US-002 preserves existing heading text byte-identical; no anchor renames.
- `.claude/rules/project-root-home-exclusion.md`: QG scratch project lives under `/tmp/project-54` with a `.git/` marker, outside `$HOME`. No collision risk.
- All other rules: N/A (prose edit; no Python changes).

---

## Beads Manifest

_Pending devolve phase._
