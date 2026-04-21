# 75 — Move review-agentskills-spec to `.claude/skills/` (maintainer-only)

## Meta

- **Ticket:** [#75](https://github.com/wjduenow/clauditor/issues/75)
- **Phase:** devolved
- **Sessions:** 1
- **Last session:** 2026-04-21
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/75-move-review-skill`
- **Branch:** `feature/75-move-review-skill`
- **Base:** `dev`

## Ticket summary

`review-agentskills-spec` is a maintainer-only skill (it audits the
upstream agentskills.io spec against clauditor's implementation). It
currently lives at `src/clauditor/skills/review-agentskills-spec/`,
which has two wrong-shape consequences:

1. **It ships in the installed wheel.** `pyproject.toml:52` includes
   `src/clauditor/skills/**/*`, so every `pip install clauditor` pulls
   the maintainer-only skill into `site-packages`. It's excluded from
   `clauditor setup` (which hardcodes `skills/clauditor/` only), but
   it's still bundled in the wheel.
2. **The live test has a workaround.**
   `tests/test_bundled_review_skill.py::TestLiveSkillRun` builds a
   `tmp_path` symlink to `.claude/skills/<name>/` because the skill
   is packaged but not auto-installed.

Moving the skill to `.claude/skills/review-agentskills-spec/` at repo
root: (a) stops shipping in the wheel, (b) makes Claude Code's
auto-discovery find it natively when working in this repo, and (c)
leaves `src/clauditor/skills/` holding only the `clauditor/` skill
that's *actually* bundled for users.

## Discovery

### Codebase scout findings (confirmed via grep)

All references to the skill's current on-disk path:

| File | Lines | What |
| --- | --- | --- |
| `src/clauditor/skills/review-agentskills-spec/SKILL.md` | (file) | **Move target — git mv** |
| `src/clauditor/skills/review-agentskills-spec/assets/review-agentskills-spec.eval.json` | (file) | **Move target — git mv** |
| `tests/test_bundled_review_skill.py` | 44, 51 | `SKILL_DIR` constant hardcodes `src/clauditor/skills/...`; `skill_root = SKILL_MD.parent` at 324 derives from it — auto-updates |
| `tests/test_bundled_review_skill.py` | 316-318 | Comments about "internal-only" — rephrase to match new framing |
| `examples/review-agentskills-spec.md` | 5 | "bundled `review-agentskills-spec` skill" wording — no longer bundled |
| `examples/review-agentskills-spec.md` | 18-19 | Files table — two path rows |
| `examples/review-agentskills-spec.md` | 95 | `clauditor capture` command path (see DEC-004 — this line has a pre-existing bug) |
| `tests/fixtures/review-agentskills-spec/README.md` | 26 | Same `clauditor capture` command bug |
| `.claude/rules/internal-skill-live-test-tmp-symlink.md` | 130, 133, 143 | Rule references this skill as its canonical anchor (see DEC-003) |
| `~/.claude/.../memory/feedback_review_agentskills_spec_internal.md` | (path reference line) | Update exclusion framing to match new location |

### Packaging / build seams (verified)

- **`build_hooks/stamp_skill_version.py`** hardcodes
  `Path("src/clauditor/skills/clauditor/SKILL.md")` at line 25 — only
  touches the user-facing `clauditor/` skill. **No change needed.**
- **`pyproject.toml:52`** — `include = ["src/clauditor/skills/**/*"]`
  is anchored to `src/`. After the move, zero files from
  `.claude/skills/review-agentskills-spec/` will be shipped. **No
  change needed** — the wheel shrinks naturally.
- **`importlib.resources`** — the package's setup plumbing uses
  `files("clauditor") / "skills" / "clauditor"` (only the bundled
  skill); `review-agentskills-spec` was never resolved via
  `importlib`. **No runtime seam affected.**

### `clauditor capture` wiring (pre-existing bug confirmed)

`src/clauditor/cli/capture.py:22-24` declares the `skill` argument as
a **skill name** (with optional leading `/`), not a path. `src/clauditor/cli/capture.py:79`
strips the leading slash and passes the name to `SkillRunner.run()`.

Both docs currently say:

```bash
uv run clauditor capture src/clauditor/skills/review-agentskills-spec/SKILL.md
```

Which is **wrong regardless of the move** — `capture` would treat the
path as a skill name, which contains `/` and would fail. The correct
shape is:

```bash
uv run clauditor capture review-agentskills-spec
```

This is a shipped-with-#72 documentation bug. See DEC-004 for
disposition.

### Rule applicability (Convention Checker)

| Rule | Applies? | Constraint |
| --- | --- | --- |
| `skill-identity-from-frontmatter.md` | Yes | Parent dir after move is still `review-agentskills-spec`, matches frontmatter `name:` — invariant preserved. No rule edits. |
| `internal-skill-live-test-tmp-symlink.md` | Yes — and needs a refresh | See DEC-003 |
| `bundled-skill-docs-sync.md` | No | Rule's "When does NOT apply" explicitly scopes to `skills/clauditor/` only. |
| `readme-promotion-recipe.md` | No | Doc stays in `examples/`, not promoted to `docs/`. |

### `.claude/` directory state

`.claude/skills/` does NOT exist in the repo today. The move creates
it fresh — no conflict.

### Tests NOT affected by the move

- `tests/fixtures/review-agentskills-spec/captured-output.txt` — the
  captured replay fixture lives under `tests/fixtures/`, not under
  the skill dir. Path stays.
- `build_hooks/stamp_skill_version.py` — only touches `clauditor/`.
- `pyproject.toml` — glob is `src/`-anchored.

## Scoping questions

### Q1 — Rule disposition for `.claude/rules/internal-skill-live-test-tmp-symlink.md`

After the move, the tmp_path+symlink pattern is **still needed** for
live tests (the rule itself argues against reusing `Path.cwd()` as
`project_dir` because the subprocess inherits the real `.claude/`
tree — that argument is independent of where the skill's source
lives). But the rule's framing — "bundled but intentionally excluded
from `clauditor setup`" — no longer describes the skill's status.

Options:

- **A. Refresh** — update the rule's example path strings from
  `src/clauditor/skills/<name>/` to `.claude/skills/<name>/`, adjust
  the framing from "bundled-but-not-setup-installed" to
  "maintainer-only, lives in `.claude/skills/` rather than being
  `setup`-installed". Keeps the canonical anchor alive.
- **B. Rescope to "live-test isolation"** — generalize the rule to
  be about test isolation for any live-runner test (not specifically
  maintainer-only skills). Broader and more refactor-heavy.
- **C. Delete** — not recommended; the pattern still has
  load-bearing value and one live anchor.

**Recommend A.**

### Q2 — Fix the pre-existing `capture` command doc bug in this PR?

Two docs say `clauditor capture <path>` when the command takes a
skill name. We're editing those exact lines anyway for the move.

Options:

- **A. Fix in this PR** — one-line correction per doc; delta is
  visible as part of the same changeset.
- **B. Separate follow-up issue** — scope discipline.

**Recommend A.**

### Q3 — Should `examples/review-agentskills-spec.md` also move, or stay in `examples/`?

The doc teaches the "captured replay + gated live run" pattern using
this skill as the example. Location options:

- **A. Stay in `examples/`** — no move. Just update the path
  references inside.
- **B. Move to `docs/testing-bundled-skills.md`** — promote if we
  think the pattern has wider reuse value.

**Recommend A** unless there's a concrete second consumer.

### Q4 — Scope guard: anything NOT to touch?

Confirm NO edits to:
- `src/clauditor/cli/setup.py` (the `clauditor setup` skill list stays
  `clauditor/` only).
- `build_hooks/stamp_skill_version.py`.
- `pyproject.toml`.
- The skill's own `SKILL.md` / `eval.json` content (only the file
  location changes, not the contents).
- `tests/fixtures/review-agentskills-spec/captured-output.txt`.

## Architecture review

Scoped review for a file-move refactor — most traditional axes (security,
performance, data model, API, observability) are n/a and rated `pass`.
Material axes below.

| Area | Rating | Finding |
| --- | --- | --- |
| Packaging / Build | pass | `pyproject.toml:52` glob is `src/`-anchored → wheel shrinks naturally after move; `build_hooks/stamp_skill_version.py:25` hardcodes `clauditor/` only → untouched. |
| Testing strategy | concern | 3-layer test suite still works after path updates; BUT the "wheel no longer ships the skill" claim needs an empirical gate (DEC-005). |
| Test isolation | pass | Live-run symlink pattern preserved; `skill_root = SKILL_MD.parent` auto-follows the `SKILL_DIR` constant (DEC-007). |
| Migration / back-compat | pass | Internal-only skill, no external consumers. `git mv` preserves history (DEC-001). |
| Rule integrity | concern | `.claude/rules/internal-skill-live-test-tmp-symlink.md` needs framing + paths refresh (DEC-002); treated as a first-class story (DEC-006). |

No blockers. Both concerns carried forward into Phase 3 decisions and Phase 4 stories.

## Refinement log

### Decisions

**DEC-001 — Use `git mv` (not copy + delete).**
Preserves git history / `git log --follow` on the moved files. The
`.claude/skills/` parent dir does not exist yet; create it first with
`mkdir -p`. The operation sequence:

```bash
mkdir -p .claude/skills
git mv src/clauditor/skills/review-agentskills-spec .claude/skills/review-agentskills-spec
```

No separate cleanup of `src/clauditor/skills/review-agentskills-spec`
needed — `git mv` handles both sides.

**DEC-002 — Refresh (not delete) `.claude/rules/internal-skill-live-test-tmp-symlink.md`.**
Scoped in Q1A. The tmp_path+symlink pattern is still load-bearing
for live-runner tests — the rule's own reasoning ("Never reuse
`Path.cwd()` as `project_dir` — the `claude` CLI inherits the cwd's
`.claude/` tree") applies independently of where the skill's source
lives. What changes is the framing: "bundled but intentionally
excluded from `clauditor setup`" becomes "maintainer-only, lives
under `.claude/skills/` rather than being `setup`-installed — still
not discoverable from an arbitrary `tmp_path` project dir". Example
paths inside the rule also update from `src/clauditor/skills/<name>/`
to `.claude/skills/<name>/` at the repo root.

**DEC-003 — Fix the pre-existing `clauditor capture` doc bug in this PR.**
Scoped in Q2A. Current docs claim
`uv run clauditor capture src/clauditor/skills/<name>/SKILL.md`;
`capture.py:22-24` takes a skill **name**, not a path. The correct
shape is `uv run clauditor capture review-agentskills-spec`. Bug
shipped with #72; the lines carrying it are already being edited for
the move, so fixing in-place is cheap and co-located. No separate
issue.

**DEC-004 — `examples/review-agentskills-spec.md` stays in `examples/`.**
Scoped in Q3A. No concrete second consumer of the "captured replay +
gated live run" pattern outside this skill yet; promoting to
`docs/testing-bundled-skills.md` would create a doc without a stable
audience. Re-evaluate if a second skill adopts the same 3-layer test
shape.

**DEC-005 — Empirical wheel-verification in Quality Gate.**
Phase 2 testing-strategy concern. The claim that
`.claude/skills/review-agentskills-spec/` files will NOT appear in
the built wheel is a reasoning-only assertion today; Quality Gate
adds an empirical check:

```bash
uv build
unzip -l dist/*.whl | grep -i review-agentskills-spec  # must exit 1
# AND confirm the clauditor/ skill DID ship:
unzip -l dist/*.whl | grep -F clauditor/SKILL.md       # must exit 0
```

If either check fails, the packaging assumption is wrong and the
plan's #1 motivation hasn't been met.

**DEC-006 — Rule refresh is its own story, not bundled with path updates.**
Phase 2 rule-integrity concern. Per DEC-002, the rule needs more
than a sed-style path swap — the "Why this rule applies" and "What
NOT to do" framing needs rewording so future readers understand the
pattern still holds after the move. Carve it into its own story
(US-003) with prose-review acceptance criteria, separate from the
mechanical path updates in US-002.

**DEC-007 — Live-test symlink pattern preserved unchanged.**
Phase 2 test-isolation finding. `tests/test_bundled_review_skill.py`
at line 324 uses `skill_root = SKILL_MD.parent` — the symlink target
is derived from `SKILL_DIR`. Updating `SKILL_DIR` to point at
`.claude/skills/<name>/` transparently redirects the symlink's
source. No separate rewrite of the symlink setup logic; it "just
works" after the constant update. Comments around lines 316-318 that
reference "internal-only" / "bundled" get a light rewording to match
the new framing.

**DEC-008 — Memory file update is a path-line edit only.**
`~/.claude/.../memory/feedback_review_agentskills_spec_internal.md`
documents the "internal-only" invariant. Line 24 has a path
reference (`src/clauditor/skills/review-agentskills-spec/`); update
that line and briefly note that the skill now lives in
`.claude/skills/` rather than being bundled. Do NOT rewrite the
"don't add it to `clauditor setup`" part of the memory — that
guidance still applies (setup continues to install only
`clauditor/`).

**DEC-009 — No `clauditor setup` code changes.**
`setup.py` hardcodes the `clauditor/` skill target at line 146 and
never attempted to install `review-agentskills-spec`. The move does
not alter `setup` behavior. Confirmed in Q4.

**DEC-010 — Scope guard: hands-off list.**
Confirmed in Q4. No edits to:
- `src/clauditor/cli/setup.py`
- `build_hooks/stamp_skill_version.py`
- `pyproject.toml`
- The skill's own `SKILL.md` / `eval.json` content (location only)
- `tests/fixtures/review-agentskills-spec/captured-output.txt`

### Session notes

- **Pre-existing bug surfaced during research** (DEC-003): both docs
  have `clauditor capture <path>` when CLI takes `<name>`. Shipped
  with #72. Fixing in-place since the line is being edited anyway,
  per scope-discipline tradeoff captured in Q2.
- **Rule survives the move** (DEC-002 + DEC-006): early thinking
  leaned toward deletion, but Phase 2 test-isolation review
  established the pattern's load-bearing reason is independent of
  the skill's bundling status. Refresh + keep.

## Detailed breakdown

### US-001 — Move skill files and update all references

**Description.** Execute the atomic skill move in a single commit: `git mv`
the two files from `src/clauditor/skills/review-agentskills-spec/` to
`.claude/skills/review-agentskills-spec/`, then update every reference in
the tree (test constants, example doc, fixture README, memory file)
including the pre-existing `clauditor capture` doc bug. The branch must be
green at this story's boundary — this is why the move and the reference
updates are bundled into one story rather than two (a lone `git mv` would
leave tests failing until paths update).

**Traces to:** DEC-001, DEC-003, DEC-007, DEC-008.

**Files.**

- **Move (git mv):**
  - `src/clauditor/skills/review-agentskills-spec/SKILL.md` →
    `.claude/skills/review-agentskills-spec/SKILL.md`
  - `src/clauditor/skills/review-agentskills-spec/assets/review-agentskills-spec.eval.json` →
    `.claude/skills/review-agentskills-spec/assets/review-agentskills-spec.eval.json`

- **Update path references:**
  - `tests/test_bundled_review_skill.py:39-45` — update `SKILL_DIR`
    constant to `.../repo_root/.claude/skills/review-agentskills-spec`.
    `SKILL_MD`, `EVAL_JSON`, and the live-run `skill_root` (line 324)
    auto-follow via `SKILL_DIR / ...` and `SKILL_MD.parent` (DEC-007).
  - `tests/test_bundled_review_skill.py:316-318` — reword comments so
    "internal-only" no longer implies "bundled in the wheel"; mention
    the repo-root `.claude/skills/` location.
  - `examples/review-agentskills-spec.md:5` — drop "bundled" wording;
    describe the skill as a maintainer tool living under `.claude/skills/`.
  - `examples/review-agentskills-spec.md:18-19` — update the two path
    rows in the Files table.
  - `examples/review-agentskills-spec.md:95-96` — fix the capture
    command per DEC-003: `uv run clauditor capture review-agentskills-spec`
    (was: `... src/clauditor/skills/.../SKILL.md` — wrong arg shape AND
    wrong path).
  - `tests/fixtures/review-agentskills-spec/README.md:26` — same DEC-003
    fix.
  - `~/.claude/projects/-home-wesd-Projects-clauditor/memory/feedback_review_agentskills_spec_internal.md`
    — update the path reference per DEC-008; do NOT rewrite the
    "no `clauditor setup` install" guidance (still applies).

**Depends on:** none (first story).

**Acceptance criteria.**

- `rg 'src/clauditor/skills/review-agentskills-spec'` in the repo
  returns **zero** hits.
- `ls .claude/skills/review-agentskills-spec/SKILL.md` and
  `ls .claude/skills/review-agentskills-spec/assets/review-agentskills-spec.eval.json`
  both succeed.
- `ls src/clauditor/skills/` shows only `clauditor/`.
- `git log --follow .claude/skills/review-agentskills-spec/SKILL.md`
  shows the pre-move history (validates `git mv` was used, DEC-001).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes
  with ≥80 % coverage. The `TestLiveSkillRun` class **skips** (no
  `CLAUDITOR_RUN_LIVE=1`), which is expected default CI behavior.
- `uv run pytest tests/test_bundled_review_skill.py -v` shows all
  non-live tests passing (3-layer contract + replay assertions).
- The `capture` command references in both updated docs now read
  `uv run clauditor capture review-agentskills-spec` (no leading `/`
  or `src/` path).

**Done when:** all acceptance criteria pass and the branch contains
a single atomic commit with a message like
`#75: Move review-agentskills-spec to .claude/skills/`.

**TDD:** not applicable — no new business logic; existing tests
validate correctness once constants are updated.

---

### US-002 — Refresh `internal-skill-live-test-tmp-symlink.md` rule

**Description.** Rewrite the rule's framing and example path strings so
a future reader understands the pattern still applies after the move —
the tmp_path+symlink shape is about test isolation (not about
bundling), and the skill living under `.claude/skills/` rather than
`src/clauditor/skills/` does not change the "Never reuse `Path.cwd()`
as `project_dir`" argument. The rule's canonical anchor
(`test_bundled_review_skill.py::TestLiveSkillRun::test_live_run_passes_l1_assertions`)
stays alive; only the surrounding prose shifts.

**Traces to:** DEC-002, DEC-006.

**Files.**

- `.claude/rules/internal-skill-live-test-tmp-symlink.md` — single
  file. Sections to revise:
  - Opening paragraph: replace "bundled skill intentionally excluded
    from `clauditor setup`" with "maintainer-only skill that lives
    under `.claude/skills/` at the repo root (not under
    `src/clauditor/skills/` in the package, and not installed by
    `clauditor setup`)".
  - "The trap" code example: keep the example shape but update any
    path strings to reflect the new location (`.claude/skills/`
    rather than `src/clauditor/skills/`).
  - "The pattern" code example: update `skill_root` comment to
    `.claude/skills/<name>/` (repo root) rather than
    `src/clauditor/skills/<name>/` (package-internal).
  - "Why this shape" — the "Respects the internal-only invariant"
    bullet: reword so the invariant being protected is "source lives
    at repo-root `.claude/skills/`, not shipped in the wheel" rather
    than "excluded from `clauditor setup` despite being bundled".
  - "Canonical implementation" section: keep the
    `TestLiveSkillRun::test_live_run_passes_l1_assertions` anchor;
    keep the 2026-04-20 validation note (factual history); update
    the failure-mode "Unknown command: /review-agentskills-spec"
    example verbatim (still accurate — this failure mode is what
    the pattern prevents).
  - "When this rule applies" / "When this rule does NOT apply" —
    reword "bundled skill filtered out of `clauditor setup`" to
    "maintainer-only skill living at repo-root `.claude/skills/`".

**Depends on:** US-001 (paths being referenced must match the new
location).

**Acceptance criteria.**

- Rule file reads coherently end-to-end — no dangling "bundled"
  references that contradict the new framing.
- `rg 'src/clauditor/skills/review-agentskills-spec'
  .claude/rules/internal-skill-live-test-tmp-symlink.md` returns
  zero hits.
- "The pattern" code example uses `.claude/skills/` for `skill_root`.
- Canonical-implementation section still points at the live-run test
  in `test_bundled_review_skill.py`, and the 2026-04-20 validation
  note is preserved.
- `uv run pytest` still passes (no test references this file, but
  sanity check).

**Done when:** rule refresh committed; spot-check by reading the
rule cold (no context from this session) and confirming the
rationale makes sense for the new skill location.

**TDD:** not applicable — prose-only edit to a rule doc.

---

### US-003 — Quality Gate

**Description.** Standard quality gate: 4 passes of the code-review
agent across the full changeset, CodeRabbit review if the PR is open,
fix all real issues each pass. **Plus** the empirical wheel-shipping
verification per DEC-005 — this is the one place where the plan's
core motivation ("stop shipping this skill to end users") is
concretely checked.

**Traces to:** DEC-005, plus standard quality conventions.

**Files.** None modified directly; any fixes from review passes land
on the existing changeset.

**Depends on:** US-001, US-002 (needs the full implementation in
place).

**Acceptance criteria.**

- **DEC-005 empirical wheel verification.** Run from the worktree:
  ```bash
  uv build
  # Must return EXIT 1 (zero hits) — the skill is NO LONGER in the wheel:
  unzip -l dist/*.whl | grep -i 'review-agentskills-spec' && exit 1 || true
  # Must return EXIT 0 — the clauditor/ skill DID still ship:
  unzip -l dist/*.whl | grep -F 'clauditor/SKILL.md'
  ```
  Both checks must behave as specified. If the first check finds any
  hits, the plan's #1 motivation has NOT been met — investigate
  before merging.
- **Code review — 4 passes.** Spawn the `code-reviewer` agent
  (via `Agent` tool, `subagent_type=code-reviewer`) four times,
  fixing real issues each pass. False positives may be left
  unaddressed but must be noted.
- **CodeRabbit review.** Once the draft PR exists, address any
  CodeRabbit findings that are real (false positives noted).
- **Ruff + pytest green.**
  - `uv run ruff check src/ tests/` passes.
  - `uv run pytest --cov=clauditor --cov-report=term-missing`
    passes at the 80 % coverage gate.
- **Manual live-test smoke.** With `CLAUDITOR_RUN_LIVE=1
  ANTHROPIC_API_KEY=... claude` on PATH, run
  `uv run pytest -m live tests/test_bundled_review_skill.py -v` from
  the worktree. Expected: live test passes, confirming the symlink
  pattern works from the new `.claude/skills/` source location. Per
  the triple-lock gate this test stays skipped in default CI.
- **Project validation final pass.** After all fixes, re-run
  `uv run pytest` one more time to confirm green.

**Done when:** all 5 criteria (wheel verification, 4 code-review
passes + fixes, CodeRabbit pass, ruff/pytest green, manual live-test
smoke) complete successfully.

**TDD:** N/A — Quality Gate is verification, not new code.

---

### US-004 — Patterns & Memory

**Description.** Capture anything learned from this refactor as a
durable pattern. Scope is likely small — this was a
mechanical refactor with a well-scoped rule refresh — but the "when
to refresh vs delete a rule whose canonical anchor's context shifts"
judgment from DEC-002 + DEC-006 is worth recording if it will
generalize to future plan work.

**Traces to:** closing-ceremony convention (always last story).

**Files.**

- `.claude/rules/` — add a new rule only if the "refresh vs delete"
  judgment deserves a durable anchor (likely yes; see below).
- Memory — update `feedback_review_agentskills_spec_internal.md`
  if US-001's edit was only a path swap and additional framing
  (e.g. "skill no longer ships in wheel — verified by DEC-005
  wheel check") is worth capturing.

**Candidate pattern to codify.** DEC-002's "the rule's canonical
anchor stays alive; only surrounding prose shifts" judgment
generalizes: when a refactor changes the *context* a rule describes
but not the *pattern* the rule codifies, refresh framing and keep
the rule. Deletion is only correct when the pattern itself is
obsolete. Consider a short rule
`.claude/rules/rule-refresh-vs-delete.md` or a paragraph in an
existing meta-rule doc.

**Depends on:** US-003 (Quality Gate).

**Acceptance criteria.**

- Memory file reflects the new location AND the new fact
  ("wheel-shipping verified absent per DEC-005"), if that fact adds
  load-bearing value for future sessions.
- If a new rule is added, it follows the same shape as existing
  `.claude/rules/*.md` files (pattern, why-this-shape,
  what-NOT-to-do, canonical implementation, when-applies /
  when-doesn't).
- Plan doc has its "Session notes" section updated with any
  outcomes from the refactor that the next plan author should
  know about.

**Done when:** memory + rules reflect anything learned; plan doc's
Meta phase advances past `devolved`.

**TDD:** N/A.

## Beads manifest

- **Epic:** `clauditor-7qn` — `#75: Move review-agentskills-spec to
  .claude/skills/ (epic)`
- **Tasks:**
  - `clauditor-7qn.1` — US-001 — Move skill + update all references
    (ready; no blockers)
  - `clauditor-7qn.2` — US-002 — Refresh
    internal-skill-live-test-tmp-symlink rule (blocked on
    `clauditor-7qn.1`)
  - `clauditor-7qn.3` — US-003 — Quality Gate (code review x4 +
    CodeRabbit + wheel verification + live smoke; blocked on
    `clauditor-7qn.1` and `clauditor-7qn.2`)
  - `clauditor-7qn.4` — US-004 — Patterns & Memory (blocked on
    `clauditor-7qn.3`)
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/75-move-review-skill`
- **Branch:** `feature/75-move-review-skill`
- **Plan PR:** https://github.com/wjduenow/clauditor/pull/76
