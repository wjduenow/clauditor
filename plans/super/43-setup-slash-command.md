# Super Plan: #43 — `clauditor setup` slash command installer

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/43
- **Branch:** `feature/43-setup-slash-command` *(to be created)*
- **Worktree:** `worktrees/clauditor/feature/43-setup-slash-command` *(to be created)*
- **Phase:** `detailing`
- **PR:** _pending_
- **Sessions:** 1
- **Last session:** 2026-04-16

---

## Discovery

### Ticket Summary

**What:** Add a `clauditor setup` CLI subcommand that installs a bundled
skill file from the installed package into the project's
`.claude/commands/clauditor.md` via a symlink. `clauditor setup --unlink`
removes the symlink. The shipped skill file exposes clauditor's
capture → validate → grade workflow as a Claude Code slash command.

**Why:** After `pip install clauditor`, the CLI works but there is no
Claude Code integration — users must remember CLI commands. A `/clauditor`
slash command makes the workflow discoverable from inside Claude Code.

**User addendum (this session):** Shipping this feature requires using
clauditor itself to test the bundled skill — dogfood the grader against
the artifact we're about to ship. The new skill must have an eval spec
and a passing gate before the feature is considered done.

**Done when:**
- `clauditor setup` creates `.claude/commands/clauditor.md` symlink to the
  bundled `skill/clauditor.md` inside the installed package
- `clauditor setup --unlink` removes the symlink cleanly
- The bundled skill file is correctly packaged into the wheel
- A clauditor eval run (L1 and/or L3) passes against the bundled skill and
  is wired into the shipping gate
- `pip install clauditor && clauditor setup` works from a clean venv

### Codebase Findings

**CLI subcommand pattern** (`src/clauditor/cli.py`):
- Subparsers defined at `cli.py:2225-2230` (`add_subparsers(dest="command", required=True)`)
- Existing subparser templates:
  - `validate` — `cli.py:2233-2257` (positional args + flags)
  - `run` — `cli.py:2259-2264` (simpler shape)
  - `doctor` — `cli.py:2620-2626` (no args)
- Dispatch is an elif chain at `cli.py:2644-2668`
- Each `cmd_*(args: argparse.Namespace) -> int` convention; `cmd_doctor`
  (`cli.py:1680-1760`) is the closest shape reference — read-only, returns 0

**Bundled non-Python resources — NONE TODAY.**
- `src/clauditor/` is Python-only (grep confirmed)
- `pyproject.toml:50-51` wheel target: `packages = ["src/clauditor"]` only;
  no `include-package-data`, no `[tool.hatch.build.targets.wheel.shared-data]`
- No `MANIFEST.in`
- We must add packaging config **and** runtime lookup via
  `importlib.resources.files("clauditor") / "skill" / "clauditor.md"`

**Existing `.claude/commands/`** — 9 synced `*.md` skills already present
(`chunk.md`, `code-review.md`, `closeout.md`, etc.). Each has:
- A `<!-- Synced from glaude. Do not edit in project repos. -->` comment
- `# /<name> — Title` H1
- `## Usage`, `## What This Does`, `## Steps` with numbered subsections
- The bundled `clauditor.md` we ship must follow the same structural
  convention so it renders correctly as a slash command

**Symlink prior art — almost none.** Single reference at `cli.py:1759`
(`origin.is_symlink()`) in `cmd_doctor`. No `symlink_to()`/`os.symlink()`
calls anywhere. Build from scratch.

**CLI test patterns** (`tests/test_cli.py`):
- `TestCmdValidate::test_validate_with_output_file` (line 52-116): invoke via
  `main([...])`, mock `SkillSpec.from_file`, assert return code
- `TestCmdRun::test_run_happy_path` (line 142-157): mock `SkillRunner`,
  `capsys.readouterr().out`
- `TestCmdGrade::test_grade_with_output` (line 199-219): `tmp_path` +
  `monkeypatch.chdir()` for filesystem-rooted tests

**Eval spec shape** (`src/clauditor/schemas.py:121-359`, fixture at
`examples/.claude/commands/example-skill.eval.json`):
- JSON, sibling `{skill_name}.eval.json` next to the skill `.md`
- Keys: `skill_name`, `description`, `test_args`, `user_prompt`,
  `input_files`, `assertions` (L1), `sections` (L2), `grading_criteria`
  (L3), `grading_model`, `trigger_tests`, `variance`
- Skills are referenced **by name**, not path — `SkillRunner.run()` takes
  `skill_name` and prompts Claude with `/<name>` (`runner.py:136-139`),
  which Claude resolves against `.claude/commands/<name>.md` at runtime.
  This means: once `clauditor setup` has installed the symlink, an eval
  spec can reference the skill by the plain name `clauditor` with no
  special handling.

**pytest plugin** (`src/clauditor/pytest_plugin.py`):
- `clauditor_grader` fixture at `pytest_plugin.py:139-160` — takes a skill
  path + optional eval path, returns a `GradingReport`. L3 grading is
  gated behind the `--clauditor-grade` pytest flag (line 76-82) to avoid
  accidental API charges.
- `clauditor_runner` (line 86) — direct skill invocation, no grading
- `clauditor_spec` (line 96) — run the skill without L3 grading

**Cost of a full L3 grading run:** ~1000 input + ~500 output tokens on
Sonnet per grading pass. `clauditor validate` (L1 only) is free and
sub-second — that's the right shape for a PR-gate, with L3 reserved for
pre-release or manual runs.

### Applicable `.claude/rules/` (Convention Checker)

- **`path-validation.md`** — RELEVANT. `.claude/commands/clauditor.md`
  destination path and the bundled source path both need validation: no
  absolute escape, resolved target must land inside the expected dir,
  correct file type.
- **`pure-compute-vs-io-split.md`** — RELEVANT. Split "resolve source
  path, resolve destination path, decide action (create/skip/error)"
  (pure) from "actually create the symlink" (I/O). One caller today, but
  the pure `plan_setup()` return becomes trivially unit-testable.
- **`subprocess-cwd.md`** — N/A (no subprocess in `setup`).
- **`eval-spec-stable-ids.md`** — RELEVANT for the dogfood eval spec;
  every assertion/criterion must carry a stable `id`.
- **`llm-judge-prompt-injection.md`** — N/A unless we add a new judge.
- **`json-schema-version.md`** — N/A (no new JSON artifact written by
  `setup`; the eval spec we author follows existing shape).
- **`sidecar-during-staging.md`** — N/A (not a grading command).
- **`stream-json-schema.md`**, **`non-mutating-scrub.md`**,
  **`monotonic-time-indirection.md`** — N/A.

### Proposed Scope

1. Add `src/clauditor/skill/clauditor.md` — bundled slash-command skill
   that describes the capture → validate → grade workflow.
2. Wire packaging so `clauditor.md` is included in the wheel.
3. Add `clauditor setup` and `clauditor setup --unlink` subcommands.
4. Add `src/clauditor/skill/clauditor.eval.json` — eval spec for the
   bundled skill (shipped next to it).
5. Add a dogfood test (mechanism TBD in scoping Q3).
6. Unit tests for the new CLI subcommand and the pure resolver.
7. Documentation updates (README install section).

### Scoping Questions

**Q1 — Bundled file layout.** Where does the shipped skill live inside
the package?
- **A.** `src/clauditor/skill/clauditor.md` (singular dir, exactly as the
  ticket describes) ← default
- **B.** `src/clauditor/skills/clauditor.md` (plural, anticipating growth
  if we ship more skills later)
- **C.** `src/clauditor/_bundled/clauditor.md` (underscore prefix signals
  "resource dir, not a Python subpackage")

**Q2 — Installation mechanism.**
- **A.** Symlink only (matches ticket) ← simplest
- **B.** Hard copy only — immune to pip cache clears; risks drifting from
  the installed package's version
- **C.** Symlink by default, `--copy` flag for environments where
  symlinks are problematic (Windows, certain CI containers)

**Q3 — Dogfood test approach** (user's shipping requirement).
- **A.** **L1 only via pytest** — add a test using `clauditor_runner` +
  `clauditor_spec` fixtures that invokes `/clauditor` and asserts L1
  criteria. Free, fast, runs on every PR.
- **B.** **L3 via pytest behind `--clauditor-grade`** — full rubric
  grading; manual / pre-release only, not on every PR.
- **C.** **Both** — L1 on every PR (free), L3 gated behind the existing
  `--clauditor-grade` flag for pre-release runs.
- **D.** **CLI shell-out in a release script** — `make ship` or
  documented pre-release checklist invokes
  `uv run clauditor grade <path>`, not in pytest.

**Q4 — Eval spec location.** The bundled skill needs an eval spec. Where
does it live?
- **A.** `src/clauditor/skill/clauditor.eval.json` — shipped with the
  package alongside `clauditor.md`; users who install clauditor get it
  for free and can point their own `clauditor grade` runs at it
- **B.** `tests/fixtures/clauditor.eval.json` — dev-only, not shipped in
  the wheel
- **C.** Both — ship a lean user-facing spec, keep a heavier dev spec
  for CI

**Q5 — `setup` collision semantics.** When `.claude/commands/clauditor.md`
already exists:
- **A.** No-op if it's our correct symlink; **error** if it's anything
  else (requires `--force` to overwrite) ← safest
- **B.** No-op if it's our correct symlink; **silently overwrite** if
  it's an old symlink to a different target; error only if it's a
  regular file
- **C.** Always overwrite without prompt (simplest, riskiest)

**Q6 — Shipping gate wiring.** Where does the "dogfood must pass before
shipping" requirement actually get enforced?
- **A.** A GitHub Actions workflow step that runs `uv run pytest
  tests/test_bundled_skill.py` on every PR (free if L1-only)
- **B.** A `Makefile` target (`make dogfood`) documented in
  `CONTRIBUTING.md` / release checklist — developer discipline, not
  CI-enforced
- **C.** Both — CI for fast L1, human-run Makefile target for L3 before
  tagging a release
- **D.** Defer — ship the feature, file a follow-up ticket for the CI
  wiring; just require the dogfood test exists and passes locally at
  merge time

---

## Architecture Review

### Resolved Scoping Choices (from Discovery)

- **Q1 → B:** Bundled file lives at `src/clauditor/skills/clauditor.md`
  (plural, anticipates growth).
- **Q2 → A:** Symlink only.
- **Q3 → C:** Dogfood L1 on every PR + L3 gated behind
  `--clauditor-grade` for pre-release.
- **Q4 → A:** Eval spec shipped alongside at
  `src/clauditor/skills/clauditor.eval.json`.
- **Q5 → A:** No-op if correct symlink; hard error otherwise (`--force`
  overrides).
- **Q6 → C:** Both — CI L1 + human-run L3 pre-release documented.

### Review Summary

| Area | Verdict | Notes |
|---|---|---|
| Security — symlink TOCTOU | concern | Atomic create-or-fail + `--unlink` verifies target is ours |
| Security — `--unlink` on non-symlink | concern | Must error, not delete |
| Security — CWD detection | concern | Require project-root marker before creating `.claude/commands/` |
| Security — `--force` destructiveness | pass | Matches project convention (`cmd_init`) |
| Security — symlink target escape | pass | `importlib.resources` is namespace-bound |
| Packaging — hatchling `.md` inclusion | concern | Needs explicit `include = ["src/clauditor/skills/*"]` |
| Packaging — editable install | pass | Resolves to source tree correctly |
| Packaging — `importlib.resources` API | pass | Py3.11+, modern `files()` API |
| **Packaging — wheel contents test** | **BLOCKER** | No test validates wheel actually contains `skills/clauditor.md` |
| Packaging — stale symlink after uninstall | concern | Extend `cmd_doctor` with a stale-symlink check |
| Packaging — upgrade workflow | pass | Symlink rebinding is stateless |
| CLI — subcommand naming (`setup` / `--unlink` / `--force`) | pass | Aligns with `cmd_init` precedent |
| CLI — output format | concern | Adopt `cmd_init` single-line confirmation style |
| CLI — exit codes | pass | 1 = business error, 2 = validation/package broken |
| CLI — help text richness | concern | `--unlink` / `--force` need multi-line help explaining collision |
| Testing — pure resolver shape | pass | `plan_setup(cwd, pkg_root, *, force, unlink) -> SetupAction` enumerated 8 edge cases |
| Testing — dogfood mechanics feasible | pass | `clauditor_spec("clauditor")` works against installed symlink |
| Testing — pytester-coverage hazard | pass | Not applicable (no `mock.patch` inside pytester) |
| **Testing — "L1 is free" assumption** | **BLOCKER** | `clauditor_spec` invokes real `claude -p` subprocess — not a mock. L1 per-PR is NOT free |
| Observability — `cmd_doctor` extension | pass | Easy add to existing checks list |
| Observability — stale-symlink user message | concern | Wording spec'd during refinement |

### Blocker Details

**BLOCKER-1 — Wheel contents validation missing.**
No CI test or grep match validates that the built wheel actually contains
`clauditor/skills/clauditor.md` + `clauditor.eval.json`. Without this,
a packaging config mistake (forgotten `include` directive, typo in path)
only surfaces on user reports after release. Fix: add a pytest test that
builds a wheel and inspects its contents via `zipfile`.

**BLOCKER-2 — L1 per-PR dogfood is NOT free.**
The original scoping assumed L1 assertion-only pytest runs were free,
but `clauditor_spec` (`pytest_plugin.py:96-135`, specifically line
invoking `spec.run()`) shells out to the real `claude -p` subprocess on
every test. That's a live Claude API call per PR — not free, not fast,
and noisy in token budget. Three options to resolve:
- **B2-A.** Accept the cost (~50-100 tokens/run, a few seconds). Simple,
  honest, but makes CI flaky if Claude is down.
- **B2-B.** Add a "canned-output" fixture mode — run the skill once
  locally, snapshot stdout to a fixture file, test assertions against
  the snapshot in CI. Removes the live API dependency but tests
  assertion logic not the skill-under-load.
- **B2-C.** Defer dogfood L1 from CI to pre-release checklist only.
  CI runs pure unit tests (pure resolver, symlink I/O with
  `tmp_path`, packaging wheel-contents test). L1 + L3 dogfood both
  become pre-release human-run steps.

---

## Refinement Log

### Key Finding — Agent Skills spec reshapes the ticket

Both Claude Code docs and agentskills.io confirm: **custom commands have
been merged into skills.** A file at `.claude/commands/<name>.md` and a
skill at `.claude/skills/<name>/SKILL.md` both create `/<name>`. Skills
are now a *directory* containing a required `SKILL.md` entrypoint plus
optional `scripts/` / `references/` / `assets/` subdirs. This
supersedes the ticket's premise (install `.claude/commands/clauditor.md`
as a single file). The ticket body needs an amendment — see DEC-017.

Core agentskills.io required frontmatter: `name` (lowercase a-z + digits
+ hyphens, ≤64 chars, must match parent dir name), `description` (≤1024
chars). Optional: `license`, `compatibility`, `metadata`,
`allowed-tools` (experimental). Claude Code layers on extensions:
`when_to_use`, `argument-hint`, `disable-model-invocation`,
`user-invocable`, `model`, `effort`, `context`, `agent`, `hooks`,
`paths`, `shell`, `$ARGUMENTS`, `${CLAUDE_SESSION_ID}`,
`${CLAUDE_SKILL_DIR}`, inline `` !`cmd` `` execution.

### Resolved Scoping (full ledger)

| Q | Choice | Intent |
|---|---|---|
| Q1 | B | Plural `src/clauditor/skills/` top-level |
| Q2 | A | Symlink only |
| Q3 | C → revised by B2-C | Original "L1 per PR + L3 pre-release" → all dogfood deferred to pre-release (see DEC-007) |
| Q4 | A → revised | Eval spec bundled inside the skill dir under `assets/` (see DEC-002) |
| Q5 | A | Strict collision; `--force` overrides |
| Q6 | C → revised by B2-C | CI runs unit + wheel + skills-ref only; dogfood moves to pre-release |
| Q7 | A | Install target `.claude/skills/clauditor/` |
| Q8 | A | Single whole-directory symlink |
| Q9 | C | Don't print restart-required warning; document in README |
| Q10 | C | Hybrid frontmatter: core + Claude Code extensions |
| Q11 | A | `metadata.clauditor-version` stamped at wheel-build time |
| Q12 | A | `skills-ref validate` in CI |
| B1 | Accept | Add wheel-contents pytest |
| B2 | C | Dogfood not in CI — pre-release checklist only |

### Decisions

**DEC-001 — Install location: `.claude/skills/<name>/SKILL.md`.**
Bundled skill installs to `<cwd>/.claude/skills/clauditor/` (directory
symlink), not the legacy `.claude/commands/clauditor.md`.
*Rationale:* Claude Code docs mark skills as the recommended path;
directory layout unlocks supporting files. agentskills.io spec mandates
`SKILL.md` at the skill-dir root with frontmatter `name` matching the
parent directory name.

**DEC-002 — Bundled source layout.**
```
src/clauditor/skills/clauditor/
├── SKILL.md
└── assets/
    └── clauditor.eval.json
```
*Rationale:* Spec recommends `assets/` for static data files.
`clauditor.eval.json` is neither code (`scripts/`) nor docs
(`references/`); `assets/` is the correct slot.

**DEC-003 — Installation mechanism: single directory symlink.**
`clauditor setup` creates one symlink: `<cwd>/.claude/skills/clauditor`
→ `<importlib.resources path>/clauditor/skills/clauditor/`.
*Rationale:* Covers `SKILL.md` + `assets/` + future supporting files in
one operation. pip upgrade updates the target contents automatically;
the symlink itself never needs re-creation.

**DEC-004 — Frontmatter: hybrid core + Claude Code extensions.**
Bundled `SKILL.md` frontmatter:
```yaml
---
name: clauditor
description: Run the clauditor capture/validate/grade workflow against a skill. Use when evaluating a Claude Code skill's output against an eval spec, or when the user asks to validate / grade / audit a skill.
compatibility: Requires clauditor installed via pip/uv
metadata:
  clauditor-version: "0.0.0-dev"   # substituted at wheel build (DEC-005)
argument-hint: "[skill-path]"       # Claude Code extension
disable-model-invocation: true      # Claude Code extension
allowed-tools: Bash(uv run clauditor *)  # both core + Claude Code
---
```
*Rationale:* Unknown YAML fields are ignored by non-Claude-Code agents,
so portability is preserved. `disable-model-invocation: true` is
load-bearing — clauditor writes sidecars and spawns subprocesses; we
don't want Claude speculatively invoking it mid-conversation.

**DEC-005 — Self-versioning via hatch build hook.**
A hatch build hook reads `pyproject.toml`'s `[project] version` and
substitutes `metadata.clauditor-version` in the bundled `SKILL.md` at
wheel build. Source-tree `SKILL.md` ships with `"0.0.0-dev"` as a
placeholder so dev workflow stays simple.
*Rationale:* Enables `cmd_doctor` drift detection (DEC-013). The YAML
`metadata` slot is the spec's official extension point for client-
specific fields.

**DEC-006 — CI validation: wheel-contents test + `skills-ref validate`.**
Two new CI steps:
- `tests/test_packaging.py` — builds wheel via `hatchling build`,
  opens with `zipfile`, asserts `clauditor/skills/clauditor/SKILL.md`
  and `clauditor/skills/clauditor/assets/clauditor.eval.json` are both
  present.
- `skills-ref validate src/clauditor/skills/clauditor` — optional
  belt-and-suspenders frontmatter/naming check via the
  agentskills.io reference validator. If the validator is unavailable
  at CI install time, fall back to an inline YAML parse of the
  required fields.

*Rationale:* Catches forgotten hatchling `include` directive and
frontmatter drift before release.

**DEC-007 — Dogfood testing deferred to pre-release checklist.**
CI runs only: unit tests + `test_packaging.py` + skills-ref validate.
The `clauditor_spec` fixture shells out to live `claude -p`
(`pytest_plugin.py:96-135`), so per-PR dogfood would burn tokens and
break on unrelated Claude-infra flakes. Instead, a new `CONTRIBUTING.md`
(or `docs/release.md`) section adds two explicit pre-tag gates:
- L1: `uv run clauditor validate src/clauditor/skills/clauditor/SKILL.md`
- L3: `uv run clauditor grade src/clauditor/skills/clauditor/SKILL.md`
  (with `--eval` pointing at `assets/clauditor.eval.json`)

*Rationale:* Shipping a broken bundled skill is the user-visible
failure mode; the pre-release gate catches it where the cost is
amortized over one release, not every PR. CI stays hermetic/free/fast.

**DEC-008 — Collision handling: strict mode + `--force`.**
`.claude/skills/clauditor` exists:
- our correct symlink → no-op + success line, exit 0
- anything else → exit 1, stderr hint `use --force to overwrite`

With `--force`: remove existing entry (file, dir, or wrong-target
symlink), create fresh symlink.
*Rationale:* Mirrors `cmd_init` convention; protects user-authored
skills from silent overwrite.

**DEC-009 — `--unlink` safety: verify-ours-before-remove.**
`clauditor setup --unlink`:
- nothing at path → no-op info line, exit 0
- our symlink (resolves into installed clauditor package root) →
  remove, exit 0
- regular file/dir → refuse, exit 1, `"not a symlink; refusing to
  unlink"`
- symlink to somewhere else → refuse, exit 1, `"symlink target
  doesn't match installed clauditor; refusing"`

*Rationale:* destructive operations default safe. Matches the security-
review concern about `--unlink` deleting user files.

**DEC-010 — Symlink creation: atomic create-or-fail (no check-then-create).**
`os.symlink(src, dst)` is called directly. On `FileExistsError`,
*then* inspect what's there; no prior existence check.
*Rationale:* Closes TOCTOU surfaced in security review. POSIX-atomic
at the syscall layer.

**DEC-011 — CWD detection: require project-root marker.**
`clauditor setup` walks up from `cwd` looking for `.git/` or an
existing `.claude/`. If neither found within the search bound, refuse
with exit 2 and `"no project root found; run from a project
directory or pass --project-dir"` (flag added for explicit override).
*Rationale:* Prevents accidental `~/.claude/skills/` creation when the
user runs `clauditor setup` from `$HOME`. Mirrors the `paths.py`
`resolve_clauditor_dir` pattern.

**DEC-012 — Directory creation mode: explicit `0o755`.**
`Path.mkdir(mode=0o755, parents=True, exist_ok=True)` for both
`.claude/` and `.claude/skills/` creation.
*Rationale:* Umask-independent — a user with `umask 0o000` doesn't end
up with world-writable config dirs.

**DEC-013 — `cmd_doctor` stale-symlink check.**
New entry in the `cmd_doctor` (cli.py:1680) checks list inspects
`.claude/skills/clauditor`:
- absent → info `"clauditor skill not installed; run 'clauditor setup'"`
- correct symlink → ok `"symlink → <target>"`
- stale (broken) symlink → warn `"stale symlink; 'clauditor setup --force' to fix"`
- wrong-target symlink → warn `"symlink target doesn't match installed package"`
- regular file/dir → warn `"not a symlink; unmanaged by clauditor"`

*Rationale:* pip uninstall/upgrade surfaces dangling symlinks;
doctor is the right diagnostic channel.

**DEC-014 — Pure resolver `plan_setup(...)`.**
```python
def plan_setup(
    cwd: Path,
    pkg_skill_root: Path,
    *,
    force: bool,
    unlink: bool,
) -> SetupAction: ...

class SetupAction(Enum):
    CREATE_SYMLINK = ...
    NOOP_ALREADY_INSTALLED = ...
    REPLACE_WITH_FORCE = ...
    REFUSE_EXISTING_FILE = ...
    REFUSE_EXISTING_DIR = ...
    REFUSE_WRONG_SYMLINK = ...
    REMOVE_SYMLINK = ...
    NOOP_NOTHING_TO_UNLINK = ...
    REFUSE_UNLINK_NON_SYMLINK = ...
    REFUSE_UNLINK_WRONG_TARGET = ...
```
Each enum member maps to a message + exit code at the `cmd_setup`
call site. Unit tests cover each branch without `tmp_path`.
*Rationale:* `.claude/rules/pure-compute-vs-io-split.md` compliance.

**DEC-015 — Help text.**
Subparser `help=` / `description=` on `setup` explains collision
semantics for `--unlink` and `--force` inline. Matches `cmd_grade`
help-text richness, not `cmd_init`'s sparseness.

**DEC-016 — Output format.**
Single-line stdout confirmation, `cmd_init` style:
- Create: `"Installed /clauditor: .claude/skills/clauditor -> <target>"`
- No-op: `"/clauditor already installed (no changes)"`
- Unlink: `"Removed .claude/skills/clauditor"`
- Errors: `ERROR:` prefix on stderr per `cli.py:1788` convention.

**DEC-017 — Amend issue #43 body.**
Post a comment on GH issue #43 amending the install target from
`.claude/commands/clauditor.md` to `.claude/skills/clauditor/SKILL.md`
and appending the user's dogfood-pre-release requirement. Draft after
plan approval, before devolve-to-beads.

---

## Detailed Breakdown

Natural ordering: packaging foundation → bundled content → build hook →
pure resolver → CLI glue → doctor integration → CI validator → docs →
ticket amendment → quality gate → patterns & memory.

**Validation command** for every story's acceptance: `uv run ruff check
src/ tests/ && uv run pytest --cov=clauditor --cov-report=term-missing`
(80% coverage gate enforced).

---

### US-001 — Packaging foundation: hatch include + wheel-contents test

**Description:** Add an empty `src/clauditor/skills/` subpackage and
wire hatchling to include non-`.py` files under it in the built wheel.
Add a pytest that builds the wheel and asserts expected files are
present. This unblocks every subsequent story that ships a bundled
resource.

**Traces to:** DEC-002, DEC-006 (wheel-contents portion)

**Acceptance criteria:**
- `pyproject.toml` declares an explicit hatchling include for
  `src/clauditor/skills/**/*` (markdown, JSON, any extension).
- `src/clauditor/skills/__init__.py` exists (empty — ensures the
  subpackage is importable, makes `importlib.resources.files("clauditor.skills")` work).
- `tests/test_packaging.py` builds a wheel via `hatchling build` or
  `python -m build --wheel`, opens it with `zipfile.ZipFile`, and
  asserts `clauditor/skills/__init__.py` is present plus any
  sentinel `.md` file (use a fixture `.md` file if no real skill
  file exists yet — it can be deleted/replaced in US-002).
- Test runs in CI under `uv run pytest` without any extra deps
  beyond `[project.optional-dependencies].dev`.
- Validation command passes.

**Done when:** CI runs `test_packaging.py` green and the wheel
contains the expected files.

**Files:**
- `pyproject.toml` — add `[tool.hatch.build.targets.wheel]` include
  directive
- `src/clauditor/skills/__init__.py` — new, empty
- `tests/test_packaging.py` — new
- `src/clauditor/skills/.sentinel.md` — temporary fixture file so the
  test has something to assert on; US-002 replaces it

**Depends on:** none

**TDD:**
- `test_wheel_contains_skills_subpackage` — build wheel, assert
  `clauditor/skills/__init__.py` in members
- `test_wheel_contains_bundled_markdown` — build wheel, assert the
  sentinel `.md` is included
- `test_wheel_excludes_pycache` — regression guard that the include
  pattern doesn't accidentally ship `__pycache__/`

---

### US-002 — Bundled skill content: `SKILL.md` + `clauditor.eval.json`

**Description:** Author the bundled slash-command skill file and its
eval spec. Frontmatter follows the agentskills.io core spec with
Claude Code extensions layered in per DEC-004. Body is a concise
playbook for the capture/validate/grade workflow. Remove the
sentinel from US-001.

**Traces to:** DEC-002, DEC-004

**Acceptance criteria:**
- `src/clauditor/skills/clauditor/SKILL.md` exists with the
  frontmatter shown in DEC-004 (placeholder version `"0.0.0-dev"`
  — US-003 replaces at build time).
- `src/clauditor/skills/clauditor/assets/clauditor.eval.json` exists
  following `examples/.claude/commands/example-skill.eval.json`
  shape with `assertions` (L1), at least one `grading_criteria`
  entry (L3). All `id` fields present and unique per
  `.claude/rules/eval-spec-stable-ids.md`.
- Frontmatter `name` value equals the parent directory name
  (`clauditor`) per agentskills.io spec.
- Body is ≤500 lines, ≤5000 tokens (tracked via word count heuristic
  in the test).
- `tests/test_bundled_skill.py` parses the frontmatter, asserts
  required fields, naming constraints (regex
  `^[a-z0-9]+(-[a-z0-9]+)*$`), and length bounds.
- Sentinel file from US-001 is deleted; `test_packaging.py` updated
  to assert real paths.
- Validation command passes.

**Done when:** Frontmatter validates, eval spec loads via
`EvalSpec.from_file()` without error, wheel test still green.

**Files:**
- `src/clauditor/skills/clauditor/SKILL.md` — new
- `src/clauditor/skills/clauditor/assets/clauditor.eval.json` — new
- `tests/test_bundled_skill.py` — new
- `tests/test_packaging.py` — update assertions
- `src/clauditor/skills/.sentinel.md` — delete

**Depends on:** US-001

**TDD:**
- `test_skill_md_has_required_frontmatter`
- `test_skill_md_name_matches_directory`
- `test_skill_md_name_conforms_to_spec_regex`
- `test_skill_md_description_under_1024_chars`
- `test_eval_spec_loads_via_from_file`
- `test_eval_spec_all_ids_unique`

---

### US-003 — Hatch build hook: stamp `metadata.clauditor-version`

**Description:** Add a hatchling custom build hook that reads the
`[project] version` from `pyproject.toml` and substitutes it into the
bundled `SKILL.md` frontmatter's `metadata.clauditor-version` field
at wheel build time. Source-tree file keeps `"0.0.0-dev"` for dev
workflow.

**Traces to:** DEC-005

**Acceptance criteria:**
- Hatchling build-hook plugin configured in `pyproject.toml` under
  `[tool.hatch.build.targets.wheel.hooks.custom]`.
- Hook script at `build_hooks/stamp_skill_version.py` reads
  `pyproject.toml` version, rewrites the `metadata.clauditor-version`
  line in the bundled `SKILL.md` using a minimal-risk regex (no
  full YAML re-serialization to avoid accidental formatting churn).
- Source `SKILL.md` still has `"0.0.0-dev"` after build (hook only
  mutates the staged wheel copy).
- `tests/test_packaging.py` extracts `SKILL.md` from the built wheel
  and asserts `metadata.clauditor-version` equals the project's
  declared version.
- Validation command passes.

**Done when:** Build-hook test passes; source-tree `SKILL.md` is
unmodified after `python -m build`.

**Files:**
- `pyproject.toml` — add `[tool.hatch.build.targets.wheel.hooks.custom]`
- `build_hooks/__init__.py` — new, empty
- `build_hooks/stamp_skill_version.py` — new
- `tests/test_packaging.py` — add the version-stamp assertion

**Depends on:** US-002

**TDD:**
- `test_wheel_skill_md_has_stamped_version` — extract SKILL.md from
  built wheel, regex the `clauditor-version` line, assert it matches
  `pyproject.toml`'s `[project] version`
- `test_source_skill_md_remains_dev_placeholder` — after build, the
  source-tree file still says `"0.0.0-dev"`

---

### US-004 — Pure resolver: `plan_setup(...)` + `SetupAction` enum

**Description:** Implement the pure decision function that, given a
cwd, an installed-package skill root, and `force`/`unlink` flags,
returns a `SetupAction` enum member describing what the I/O layer
should do. Per `.claude/rules/pure-compute-vs-io-split.md`, no file
I/O here — caller handles the side effect. Ten enum branches cover
every edge case from architecture review.

**Traces to:** DEC-008, DEC-009, DEC-010, DEC-011, DEC-014

**Acceptance criteria:**
- New module `src/clauditor/setup.py` exports:
  - `class SetupAction(Enum)` with members per DEC-014
  - `def plan_setup(cwd: Path, pkg_skill_root: Path, *, force: bool, unlink: bool) -> SetupAction`
  - `def find_project_root(cwd: Path) -> Path | None` (walks up for
    `.git` or `.claude`)
- Function is pure: no side effects, no writes, no network. Accepts
  `pathlib.Path` instances for both args, returns the enum.
- **Project-root resolution**: if `find_project_root(cwd)` returns
  `None`, raise `ValueError("no project root found; run from a
  project directory")`. Caller maps to exit 2.
- **Symlink semantics**: correct-target detection uses
  `Path.resolve()` on the symlink and compares with
  `Path.is_relative_to(pkg_skill_root)`.
- `tests/test_setup.py` covers all 10 enum branches with `tmp_path`
  fixtures (scratch files/dirs/symlinks), plus:
  - `cwd` with `.git` marker → resolves root correctly
  - `cwd` with `.claude` marker → resolves root correctly
  - neither → raises `ValueError`
  - walks up multiple levels correctly
- Validation command passes. Coverage on `setup.py` ≥ 95%.

**Done when:** All 10 enum branches have dedicated tests and
`plan_setup` is the only decision maker for US-005's `cmd_setup`.

**Files:**
- `src/clauditor/setup.py` — new
- `tests/test_setup.py` — new

**Depends on:** none (can run in parallel with US-001/002/003)

**TDD:** One test per `SetupAction` enum member — named
`test_plan_setup_returns_<member_name_lower>` — plus 3 project-root
tests. 13 tests total.

---

### US-005 — CLI glue: `clauditor setup` subcommand

**Description:** Wire the `setup` subparser and `cmd_setup(args) ->
int` handler in `cli.py`. Dispatches on the `SetupAction` enum from
US-004, performs the actual symlink creation/removal atomically per
DEC-010, handles directory creation with explicit mode 0o755,
prints per DEC-016, exits per DEC-008/DEC-009 semantics.

**Traces to:** DEC-001, DEC-003, DEC-012, DEC-015, DEC-016

**Acceptance criteria:**
- New `cmd_setup(args)` in `src/clauditor/cli.py` returning `int`.
- New subparser registered in the subparsers block, with rich
  multi-line help per DEC-015 and `argparse` flags:
  - `--unlink` (store_true)
  - `--force` (store_true)
  - `--project-dir PATH` (override project-root detection from DEC-011)
- I/O side: `os.symlink(target, dst)` is attempted directly; on
  `FileExistsError`, re-inspect and map to correct `SetupAction`.
  Directory creation uses `Path.mkdir(mode=0o755, parents=True,
  exist_ok=True)`.
- Installed-package skill root resolved via
  `importlib.resources.files("clauditor") / "skills" / "clauditor"`,
  converted to a `Path` via `with as_file(...)` guard for
  zipped-wheel safety (fallback path for the unlikely pyc-zipped
  install case — just `Path(str(traversable))`).
- Stdout/stderr/exit code per DEC-008/DEC-009/DEC-016.
- `tests/test_cli.py` adds a `TestCmdSetup` class mirroring other
  subcommand tests — `tmp_path` + `monkeypatch.chdir()`, invoke via
  `main(["setup", ...])`, assert `capsys.readouterr()`. Covers:
  - create when absent
  - no-op when correct
  - refuse when existing file (exit 1)
  - refuse when wrong-target symlink (exit 1)
  - `--force` replace
  - `--unlink` removes our symlink
  - `--unlink` refuses non-symlink (exit 1)
  - `--unlink` on absent (exit 0, info)
  - project-root detection failure (exit 2)
  - `--project-dir` override
- Validation command passes.

**Done when:** `uv run clauditor setup` end-to-end works in a test
project; all CLI tests green.

**Files:**
- `src/clauditor/cli.py` — add `cmd_setup` + subparser + dispatch elif
- `tests/test_cli.py` — add `TestCmdSetup` class

**Depends on:** US-002 (bundled content exists to symlink at),
US-004 (pure resolver)

**TDD:** 10 CLI tests mirroring the `SetupAction` branches + 1
override test.

---

### US-006 — `cmd_doctor` extension: stale-symlink check

**Description:** Add a single check entry to `cmd_doctor` (cli.py:1680)
that inspects `.claude/skills/clauditor` and reports 5 distinct states
per DEC-013.

**Traces to:** DEC-013

**Acceptance criteria:**
- New helper `_check_clauditor_skill_symlink(project_root: Path,
  pkg_skill_root: Path) -> tuple[str, str, str]` returning
  `(check_name, status, detail)` tuple matching the existing
  `checks` list shape in `cmd_doctor`.
- Status values: `ok`, `warn`, `info` (existing codebase convention;
  grep for `"warn"` in `cmd_doctor` to confirm).
- 5 branches per DEC-013: absent, correct, stale, wrong-target,
  non-symlink.
- `tests/test_cli.py::TestCmdDoctor` adds 5 tests (one per branch)
  with `tmp_path` fixtures.
- Validation command passes.

**Done when:** `uv run clauditor doctor` surfaces the new check in
every matching state.

**Files:**
- `src/clauditor/cli.py` — extend `cmd_doctor`
- `tests/test_cli.py` — add doctor-check tests

**Depends on:** US-005

**TDD:** 5 tests (one per DEC-013 branch).

---

### US-007 — CI: `skills-ref validate` step

**Description:** Add a CI step that runs `skills-ref validate
src/clauditor/skills/clauditor` to catch frontmatter drift. Fallback
to an inline YAML parse if the validator isn't available in CI.

**Traces to:** DEC-006 (validator portion)

**Acceptance criteria:**
- `.github/workflows/<ci>.yml` (or equivalent existing workflow
  file — check repo layout) adds a step invoking `skills-ref
  validate src/clauditor/skills/clauditor`. If `skills-ref` is
  unavailable via pip, the step uses a minimal Python fallback that
  parses the frontmatter and asserts the agentskills.io core
  constraints (`name` pattern, length; `description` length).
- Step exits non-zero on frontmatter issues.
- Validation command passes.

**Done when:** CI workflow passes on a valid skill, fails if
frontmatter is corrupted deliberately.

**Files:**
- `.github/workflows/*.yml` — new step (check existing workflow
  names; do NOT add a new workflow file if an existing `ci.yml`
  fits)
- `scripts/validate_skill_frontmatter.py` — new (fallback
  implementation)

**Depends on:** US-002

**TDD:** N/A — configuration task. Verify by temporarily breaking
frontmatter on a feature branch and confirming CI red.

---

### US-008 — Docs: pre-release checklist + README install section

**Description:** Document the pre-release dogfood gate (DEC-007) in
`CONTRIBUTING.md` (or create `docs/release.md`). Add a README
section describing `clauditor setup` for end users, including the
Q9=C note that creating `.claude/skills/` for the first time
requires a Claude Code restart.

**Traces to:** DEC-007, Q9 (C — document in README)

**Acceptance criteria:**
- `README.md` gains a `## Installing the /clauditor slash command`
  section with:
  - `uv run clauditor setup` example
  - Expected output line
  - `--unlink` / `--force` descriptions
  - One-line note: "If `.claude/skills/` didn't exist before, restart
    Claude Code once so it picks up the new directory."
- `CONTRIBUTING.md` (or `docs/release.md`) gains a `## Pre-release
  dogfood` section enumerating the two required gates:
  - `uv run clauditor validate src/clauditor/skills/clauditor/SKILL.md`
  - `uv run clauditor grade src/clauditor/skills/clauditor/SKILL.md --eval src/clauditor/skills/clauditor/assets/clauditor.eval.json`
- Both must pass before tagging a release.
- Validation command passes.

**Done when:** Docs landed; a fresh reader can install the slash
command and find the dogfood gate.

**Files:**
- `README.md` — new section
- `CONTRIBUTING.md` — new section (create if missing)

**Depends on:** US-005

**TDD:** N/A — docs.

---

### US-009 — Amend issue #43 on GitHub

**Description:** Post a comment on GH #43 amending the ticket's
install-target language (commands/.md → skills/SKILL.md) and
appending the dogfood pre-release gate requirement. Close out the
original ticket body's "Proposed Design" section by cross-linking
to the plan doc at `plans/super/43-setup-slash-command.md`.

**Traces to:** DEC-017, original user request

**Acceptance criteria:**
- Comment posted on #43 via `gh issue comment 43 --body "..."`
  containing:
  - Amendment note: install path is `.claude/skills/clauditor/SKILL.md`
    per agentskills.io + Claude Code skills docs
  - New requirement: clauditor must be used to dogfood the bundled
    skill in the pre-release checklist (L1 + L3)
  - Link to the plan doc on the `dev` branch
- Optionally update the issue body via `gh issue edit` if the team
  prefers in-place amendments over appended comments — confirm with
  the user at execution time.

**Done when:** Comment visible on issue #43.

**Files:** none (GitHub-only)

**Depends on:** none (can run any time after approval; recommended
just before or during devolve)

**TDD:** N/A.

---

### US-010 — Quality Gate

**Description:** Run code-reviewer agent 4 times across the full
changeset, fixing every real bug surfaced each pass. Run CodeRabbit
if available. Rerun the full validation gate and confirm all tests +
coverage pass.

**Traces to:** all implementation DECs (coverage of the entire
changeset)

**Acceptance criteria:**
- Code reviewer agent invoked 4 times; each pass's findings either
  fixed or documented as false positives with rationale.
- CodeRabbit review (if available on the PR) addressed via
  `pr-reviewer` agent.
- `uv run ruff check src/ tests/` — clean.
- `uv run pytest --cov=clauditor --cov-report=term-missing` — all
  tests pass, coverage ≥80% on the new modules.
- Wheel-contents test green.
- `skills-ref validate` green.
- Manual spot-check of `uv run clauditor setup` end-to-end in a
  fresh temp project.

**Done when:** All 4 reviewer passes are clean + validation gate
green.

**Files:** whatever reviewer/CodeRabbit findings touch

**Depends on:** US-001 through US-008

---

### US-011 — Patterns & Memory

**Description:** Distill reusable patterns from this feature into
`.claude/rules/` and update `docs/` / beads memory as appropriate.

**Traces to:** this ticket's novel patterns

**Acceptance criteria:**
- Evaluate whether any of these are new-rule-worthy:
  - Hatchling custom-build-hook pattern for substituting version
    fields into bundled non-Python resources (candidate new rule
    if we expect future resources to need similar stamping)
  - `importlib.resources.files()` + `as_file` guard pattern for
    resolving bundled artifacts to symlink targets (candidate if
    we ship more bundled files)
  - `find_project_root` helper + marker search (probably belongs
    in `paths.py`, not a rule — mention in that module's docstring)
  - Pure-compute-vs-io-split applied to a Pathlib-only function
    that raises on precondition failure (already covered by the
    existing rule; augment with a second-anchor reference)
- Update `.claude/rules/pure-compute-vs-io-split.md` with a new
  anchor pointing to `src/clauditor/setup.py::plan_setup` as the
  fourth canonical example.
- Run `bd remember` for any ephemeral insight not worth a rule file.
- Validation command passes.

**Done when:** Rule updates merged; any transient insights
captured in beads memory.

**Files:**
- `.claude/rules/pure-compute-vs-io-split.md` — add fourth anchor
- Potentially a new rule file for the build-hook pattern if the
  reviewer decides it's load-bearing
- `bd remember` calls as needed

**Depends on:** US-010

---

### Dependency graph

```
US-001 ─┬─► US-002 ──► US-003 ──┐
        │                        │
        └──────► US-007 ◄────────┤
                                 │
US-004 ───────────────► US-005 ──┼─► US-006 ──┐
                                 │            │
                         US-008 ─┤            │
                                 ├─► US-010 ─► US-011
                         US-009 ─┘
```

US-004 and US-009 are fully independent of the others and can start
immediately. US-001 unlocks US-002/US-003/US-007 in sequence.
US-005 needs both US-002 (target exists) and US-004 (resolver).
US-006 and US-008 need US-005.

---

## Beads Manifest

_(Pending — populated in Phase 7.)_

---

## Session Notes

### Session 1 — 2026-04-16

**Discovery complete.** Ticket fetched, codebase scouted across CLI
subcommand patterns, packaging conventions, existing eval-spec shape,
and pytest-plugin fixtures. Zero bundled non-Python resources exist
today — this feature introduces both the packaging pattern AND its
first consumer. Added user's dogfood-testing requirement as explicit
scope.

**Architecture review complete.** Three parallel reviews (security,
packaging, CLI/testing/observability) surfaced two blockers and
several concerns. User answered Q1–Q6.

**Spec fetch complete.** User requested fetching the Claude Code slash
commands docs and agentskills.io specification. Discovery **reshaped**
the ticket: custom commands have merged into skills; correct install
path is `.claude/skills/<name>/SKILL.md` (directory), not
`.claude/commands/<name>.md` (file). Core agentskills.io spec
enumerated, Claude Code extensions identified. Added Q7–Q12.

**Refinement complete.** User resolved Q7–Q12 + both blockers. 17
decisions recorded. Next: move to Detailing (generate stories).
