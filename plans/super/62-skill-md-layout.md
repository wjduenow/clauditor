# Super Plan: #62 — Support the modern `<name>/SKILL.md` layout in `SkillSpec`

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/62
- **Branch:** `feature/62-skill-md-layout`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/62-skill-md-layout`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/66
- **Sessions:** 1
- **Last session:** 2026-04-20

---

## Discovery

### Ticket summary

**What:** Teach `SkillSpec.__init__` (and `cli/init.py`'s starter-eval
writer) to handle the modern Anthropic skills layout
`.claude/skills/<name>/SKILL.md` alongside the legacy
`.claude/commands/<name>.md` layout. Today, both sites derive
`skill_name` via `skill_path.stem`, which produces the literal string
`"SKILL"` for a modern-layout path — so the CLI invokes `/SKILL`
(unknown command) and the subprocess returns `"Skill failed to run:
None"` after a wasted 37-second round trip.

**Why:** Anthropic's documented shape for skills shared via plugins
and agentskills.io is `<name>/SKILL.md`. Any skill authored against
that spec is currently untestable with clauditor without a manual
symlink workaround. The project's own bundled skill lives at
`src/clauditor/skills/clauditor/SKILL.md` in the modern layout —
meaning we cannot even self-validate.

**Done when:**
- `clauditor validate .claude/skills/<name>/SKILL.md` succeeds on a
  skill that has declared `name:` in its frontmatter (no workaround).
- `clauditor init .claude/skills/<name>/SKILL.md` writes a starter
  eval at the right path with `skill_name = <name>`, not `"SKILL"`.
- `clauditor validate .claude/commands/<name>.md` (legacy layout)
  continues to work byte-identically.
- Coverage stays ≥80%; ruff passes.

### Key findings — codebase scout

#### Bug site 1: `src/clauditor/spec.py`

```python
# src/clauditor/spec.py:30–39
def __init__(
    self,
    skill_path: Path,
    eval_spec: EvalSpec | None = None,
    runner: SkillRunner | None = None,
):
    self.skill_path = skill_path
    self.skill_name = skill_path.stem                              # BUG
    self.eval_spec = eval_spec
    self.runner = runner or SkillRunner(
        project_dir=skill_path.parent.parent.parent                # BUG
    )
```

- **Line 37 (skill_name):** For `/repo/.claude/skills/foo/SKILL.md`
  → `"SKILL"`. For `/repo/.claude/commands/foo.md` → `"foo"`
  (legacy: correct).
- **Line 39 (project_dir):** 3-deep ascent assumes legacy.
  `.claude/commands/foo.md` → `/repo` (correct). For modern
  `.claude/skills/foo/SKILL.md` — one extra dir level —
  `parent.parent.parent` = `/repo/.claude`, which is **not** the
  project root. The runner then launches `claude` with the wrong
  CWD, and relative paths (input_files, output_files) resolve
  against `.claude/` instead of the project.

#### Bug site 2: `src/clauditor/cli/init.py` lines 35–36

```python
starter = {
    "skill_name": skill_path.stem,                            # BUG
    "description": f"Eval spec for /{skill_path.stem}",       # BUG
    ...
}
```

Same pattern — writes `"skill_name": "SKILL"` and
`"description": "Eval spec for /SKILL"` into the starter eval.json.
Note: line 25 (`skill_path.with_suffix(".eval.json")`) produces
`SKILL.eval.json` for the modern layout, which loads fine but has
an ugly on-disk filename.

#### Frontmatter parser already exists and is pure

`src/clauditor/_frontmatter.py::parse_frontmatter(text) -> (dict | None, str)`
is stateless, returns top-level scalars as strings. The bundled
`src/clauditor/skills/clauditor/SKILL.md` declares `name: clauditor`
in its frontmatter — the idiomatic source of truth for modern
skills.

#### Eval-spec auto-discovery

`spec.py:62` uses `skill_path.with_suffix(".eval.json")`:
- Legacy `/foo/my-skill.md` → `/foo/my-skill.eval.json` ✓
- Modern `/foo/SKILL.md` → `/foo/SKILL.eval.json` (works but odd
  filename — coexists with SKILL.md in the same dir)

#### Callers and blast radius

- `src/clauditor/cli/__init__.py` — `_load_spec_or_report` calls
  `from_file`.
- `src/clauditor/cli/compare.py` — two calls.
- `src/clauditor/pytest_plugin.py` — `clauditor_spec` fixture wraps
  `from_file`.
- 20+ test sites in `tests/test_spec.py`, `tests/test_cli.py`,
  `tests/test_pytest_plugin.py`, etc.
- **All test fixtures today use legacy `.md` shape**
  (`tmp_skill_file` writes `tmp_path/<name>.md`). No test
  currently covers the modern `<name>/SKILL.md` layout — that's
  the test gap that let this ship.

#### Setup / project-root walk

`src/clauditor/setup.py::find_project_root` already walks up for a
`.claude/` marker and has the home-exclusion guard
(`.claude/rules/project-root-home-exclusion.md`). Not affected by
this fix — the bug lives in a different code path (spec
construction, not project-root discovery).

### Applicable `.claude/rules/`

| Rule | Applies? | Constraint on this plan |
|---|---|---|
| `pure-compute-vs-io-split.md` | **yes** | Extract `derive_skill_name(skill_path, skill_md_text) -> str` as a pure function — frontmatter-first, stem fallback, zero I/O. Caller does `read_text()`. Testable without `tmp_path` for the logic. |
| `in-memory-dict-loader-path.md` | yes (satisfied) | `parse_frontmatter` is already pure in-memory (no `from_file` wrapper needed). New helper consumes its output. |
| `path-validation.md` | no | Not accepting user path-lists from JSON; only reclassifying already-validated SKILL paths. |
| `json-schema-version.md` | no | No new persisted sidecar — only a loader fix. (`init.py`'s starter already has `schema_version` via `EvalSpec`.) |
| `llm-cli-exit-code-taxonomy.md` | no | Not an LLM command. |
| `bundled-skill-docs-sync.md` | conditional | Only if we edit the bundled SKILL.md workflow. This fix shouldn't require it. |
| `readme-promotion-recipe.md` | conditional | Modern-layout support is worth a line in README + docs if we also let `init` handle it. |
| `project-root-home-exclusion.md` | no | `find_project_root` untouched. |
| `eval-spec-stable-ids.md` | no | No changes to eval schema. |
| `monotonic-time-indirection.md` | no | No timing code. |
| CLAUDE.md | yes | Use `bd`, not TodoWrite. Validation = `uv run ruff check src/ tests/` + `uv run pytest --cov=clauditor --cov-report=term-missing` (80% gate). |

### Proposed scope

A) **Core fix** (must):
   1. New pure helper `derive_skill_name(skill_path, skill_md_text)`
      in `spec.py` (or a new small module if we prefer isolation).
      Frontmatter `name:` wins; fallback is layout-aware
      (modern → `parent.name`, legacy → `stem`).
   2. New pure helper `derive_project_dir(skill_path)` — layout-aware
      ascent (4-deep for modern, 3-deep for legacy). Or route
      through `find_project_root` with skill_path as starting cwd.
   3. Refactor `SkillSpec.__init__` to call both helpers (thin I/O
      wrapper around the pure layer).
   4. Fix `cli/init.py` to use `derive_skill_name` (same skill-path
      input).
B) **Tests** (must):
   - `TestDeriveSkillName` — unit tests for the pure helper (modern,
     legacy, missing frontmatter, malformed frontmatter, name field
     present/absent).
   - `TestDeriveProjectDir` — unit tests for the pure helper.
   - `TestSkillSpecFromFile` — integration tests covering both
     layouts via `tmp_path` fixtures.
   - Extend `tmp_skill_file` fixture (conftest.py) to support
     modern layout, OR add a sibling `tmp_skill_dir` fixture.
   - Regression test asserting the bundled skill itself loads
     cleanly through `SkillSpec.from_file` (validates #7 of the
     scout report).
C) **Docs** (likely):
   - `docs/skill-usage.md` — add a line noting both layouts work.
   - `README.md` — one-sentence mention in the Quick Start or
     CLI reference if the modern layout is the primary audience.

### Open questions for the user

See _Scoping Questions_ below.

---

## Scoping Questions

**Answered 2026-04-20:**
- **Q1 = A** — Frontmatter `name:` first, layout-aware fallback, silent.
- **Q2 = B** — Frontmatter wins on disagreement with a stderr warning
  (so an accidental rename never silently goes unnoticed).
- **Q3 = C** — Try marker-walk via `find_project_root` first, fall
  back to layout-aware count (so `tmp_path` fixtures without a
  `.git`/`.claude` marker still resolve a sensible root).
- **Q4 = A** — Fix `cli/init.py` in the same PR via the shared helper.
- **Q5 = C** — Unit + integration + bundled-skill regression test.

### Q1 — Source of truth for `skill_name`

Which derivation order should the helper use?

- **A.** Frontmatter `name:` first (authoritative if present),
  layout-aware fallback (modern → parent dir name, legacy → stem).
  Silent fallback — no warning when `name:` is absent.
- **B.** Layout-aware only — modern uses `parent.name`, legacy uses
  `stem`. Frontmatter ignored. Simpler, but loses the "frontmatter
  is the canonical identity" property.
- **C.** Frontmatter `name:` required for modern layout; hard-fail
  if missing. Legacy uses `stem`. Strictest, surfaces missing
  frontmatter loudly.
- **D.** Frontmatter-first with a stderr warning when falling back.
  Informative but noisy for well-behaved legacy skills that never
  declare frontmatter.

### Q2 — Disagreement policy when frontmatter `name:` ≠ filesystem-derived

If a SKILL.md sits at `.claude/skills/foo/SKILL.md` but declares
`name: bar` in frontmatter:

- **A.** Frontmatter wins silently.
- **B.** Frontmatter wins with a stderr warning.
- **C.** Hard-fail — refuse to load.
- **D.** Filesystem wins silently (frontmatter is metadata, not
  identity).

### Q3 — `project_dir` derivation

- **A.** Layout-aware ascent: detect modern vs legacy by
  `parent.name == "skills"` (modern) vs `parent.name == "commands"`
  (legacy), then ascend the correct number of levels.
- **B.** Marker-walk via `find_project_root(skill_path.parent)` —
  walks up for `.git` / `.claude`. Most robust (handles future
  layouts). Requires importing from `setup.py`.
- **C.** Both: try marker-walk first, fall back to layout-aware
  count if marker-walk fails (e.g., `tmp_path` fixtures with no
  `.git`/`.claude`).
- **D.** Thread `project_dir` as an explicit `__init__` param with
  a default of `None` → use current logic; callers that know their
  root (CLI, pytest plugin) pass it explicitly. Breaking-change for
  programmatic callers, but most explicit.

### Q4 — Scope: fix `cli/init.py` in the same PR?

- **A.** Yes — route `cli/init.py` through the same
  `derive_skill_name` helper (same bug, same fix).
- **B.** No — split into a follow-up ticket. Keep this PR focused
  on `SkillSpec`.
- **C.** Yes for the name derivation, but leave
  `skill_path.with_suffix(".eval.json")` alone (i.e., accept the
  `SKILL.eval.json` filename for the modern layout).
- **D.** Yes, including a smarter eval.json path — for modern
  layout, write `<name>/SKILL.eval.json`; for legacy, keep
  `<name>.eval.json`.

### Q5 — Test coverage depth

- **A.** Unit tests on the pure helpers only.
- **B.** Unit + integration (SkillSpec.from_file round-trip via
  `tmp_path` for both layouts).
- **C.** B + a regression test asserting the bundled
  `src/clauditor/skills/clauditor/SKILL.md` loads through
  `SkillSpec.from_file` (real file, self-validation).
- **D.** C + end-to-end via `pytester` subprocess mode (avoid the
  `pytester + --cov + mock.patch` hazard from
  `.claude/rules/pytester-inprocess-coverage-hazard.md`).

---

## Architecture Review

### Ratings

| Area | Rating | Note |
|---|---|---|
| Back-compat: direct `SkillSpec(...)` callers | **concern** | `tests/test_quality_grader.py:1902` passes `Path("dummy.md")` (non-existent). Any `read_text()` inside `__init__` would fail. |
| `__init__` vs `from_file` split | **concern** | Resolves Concern 1: frontmatter read lives in `from_file`, passed to `__init__` via optional `skill_name_override=None`. |
| Pytest-plugin impact | pass | `clauditor_spec` always uses `SkillSpec.from_file`. No direct-constructor fixtures. |
| Legacy `name:` collision risk | pass | Scan: no `.claude/commands/*.md` or bundled skill declares a frontmatter `name:` that disagrees with filesystem. Bundled `src/clauditor/skills/clauditor/SKILL.md` declares `name: clauditor` matching its parent dir. |
| Runner CWD change for modern layout | pass | Intended — today `SkillRunner.project_dir` points at `.claude/` for a modern skill, which is the bug. |
| Circular-import risk | pass | `setup.py` imports only stdlib; safe for `spec.py` to import from it. |
| Helper home | **concern** | Recommendation: put pure helpers in `src/clauditor/paths.py` (already home to `resolve_clauditor_dir`). Move `find_project_root` there from `setup.py` with a back-import for compatibility. |
| Frontmatter parser robustness | pass | `parse_frontmatter` raises `ValueError` on malformed input; bounded, single-pass. No DoS vector. |
| Untrusted `name:` injection | **concern** | `propose_eval.py` already defines `_SKILL_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"`. We should reuse it (promote to a shared location, e.g. `paths.py`) and reject frontmatter `name:` values that fail the regex — fall back to layout-derived name with a warning. |
| `name:` used as filesystem segment | pass | Grep shows two uses — `cli/capture.py` (user-provided skill_name, pre-validated) and `pytest_plugin.py` display only. No path-traversal surface. |
| Warning format | **concern** | No canonical format today. Adopt `"clauditor.spec: "` prefix (consistent with existing `runner.py` `"clauditor.runner: "` style). |
| Verbose/quiet interaction | pass | Emit warning unconditionally; it's informational, not debug. |
| Silent fallback on missing `name:` | pass | Q1=A agreed. Forward-compat: a future `--debug` flag can log it without signature changes. |
| I/O error reporting | **concern** (was blocker) | `cli/__init__.py::_load_spec_or_report` catches only `FileNotFoundError`. A new `read_text()` can raise `OSError` (permission denied, I/O error). Expand the except to `(FileNotFoundError, OSError)` with a clear message. |
| Test-class organization | pass | Add `TestDeriveSkillName` + `TestDeriveProjectDir`; extend `TestFromFile` with modern-layout cases. |
| Fixture extension | **concern** | Extend `tmp_skill_file(layout="legacy" \| "modern")` — single factory, both layouts. |
| Integration matrix | pass | 6 cases: modern+match, modern+disagree (warn), modern+missing-name, legacy+match, legacy+missing-name, bundled-skill regression. |
| Bundled-skill regression test | pass | New `TestBundledSkillViaSpec` class in `tests/test_bundled_skill.py` — round-trip through `SkillSpec.from_file`. |
| Reload hazard | pass | `tests/test_spec.py` already reloads `clauditor.spec`. |
| Pytester+cov hazard | pass | No `pytester` + inner `mock.patch` in planned tests. |

### Concerns to resolve in refinement
- **C1.** Keep `SkillSpec(...)` direct constructor safe — move frontmatter read into `from_file`, pass name as optional kwarg.
- **C2.** Pick a single home for the pure helpers (`paths.py` recommended). Decide whether to move `find_project_root` too.
- **C3.** Reuse/promote `_SKILL_NAME_RE` for frontmatter `name:` validation.
- **C4.** Pin the stderr warning format.
- **C5.** Expand `_load_spec_or_report` except clause.
- **C6.** Confirm fixture approach (`layout=` kwarg vs sibling fixture).

---

## Refinement Log

### DEC-001 — Source of truth for `skill_name`: frontmatter-first, layout-aware fallback, silent on missing
Frontmatter `name:` is authoritative when present and valid. When absent, fall back to `skill_path.parent.name` for the modern `<name>/SKILL.md` layout and `skill_path.stem` for the legacy `<name>.md` layout. No warning on missing — keeps legacy skills (which don't declare `name:`) quiet. (Q1=A.)

### DEC-002 — Disagreement policy: frontmatter wins + stderr warning
If frontmatter `name:` disagrees with the filesystem-derived name, use the frontmatter value and emit a stderr warning. Scanning the repo confirmed no current file disagrees, so the warning is future-proofing against accidental rename. (Q2=B.)

### DEC-003 — `project_dir` derivation: `find_project_root` first, layout-aware ascent fallback
Try `find_project_root(skill_path.parent)` (the existing marker-walk with home-exclusion). If it returns `None` (e.g., `tmp_path` with no `.git`/`.claude`), fall back to: 4-deep ascent for modern `<name>/SKILL.md`, 3-deep for legacy `<name>.md`. This preserves byte-identical legacy behavior when markers are absent. (Q3=C.)

### DEC-004 — Fix `cli/init.py` in the same PR
`cli/init.py` has the same `skill_path.stem` bug. Route it through the shared helper so `clauditor init .claude/skills/foo/SKILL.md` writes `"skill_name": "foo"` instead of `"skill_name": "SKILL"`. (Q4=A.)

### DEC-005 — Test depth: unit + integration + bundled-skill regression
Unit tests on the pure helpers, integration via extended `tmp_skill_file` fixture covering both layouts, and a regression test in `tests/test_bundled_skill.py` asserting the bundled `src/clauditor/skills/clauditor/SKILL.md` loads cleanly through `SkillSpec.from_file`. No `pytester`. (Q5=C.)

### DEC-006 — Frontmatter read lives in `from_file`, not `__init__`
`SkillSpec.__init__` gains an optional `skill_name_override: str | None = None` kwarg. When provided (the `from_file` path), it's used directly. When `None` (direct-constructor path used by `tests/test_quality_grader.py:1902` with a non-existent path), `__init__` falls back to layout-aware filesystem derivation with no `read_text()` call. Preserves back-compat for any test or programmatic caller that constructs `SkillSpec` with a non-existent path. (Resolves C1.)

### DEC-007 — Pure helpers live in `src/clauditor/paths.py`; `find_project_root` stays in `setup.py`
`paths.py` already hosts `resolve_clauditor_dir` — it's the natural home for path-classifier helpers. `find_project_root` stays in `setup.py` (where it's co-located with its other callers); `paths.py` and `spec.py` import it from there. Keeps this PR's diff smaller; no module-move cascade. (Resolves C2, R1=B.)

### DEC-008 — Promote `SKILL_NAME_RE` to `paths.py`; lenient fallback when regex fails
Move `_SKILL_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"` from `propose_eval.py` to `paths.py` as `SKILL_NAME_RE`. `propose_eval.py` re-imports it. When the frontmatter `name:` value fails the regex, `derive_skill_name` treats it as absent, falls back to the layout-aware filesystem name, and emits a warning naming the bad value and the chosen fallback. Lenient beats strict: a malformed frontmatter shouldn't make the whole skill uncallable. (Resolves C3, R2=A.)

### DEC-009 — Stderr warning format: `"clauditor.spec: ..."`
Consistent with existing `"clauditor.runner: ..."` prefix convention. Two canonical messages:
- Disagreement: `clauditor.spec: frontmatter name 'bar' overrides filesystem name 'foo' — using 'bar'`
- Regex failure: `clauditor.spec: frontmatter name 'bad;name' is not a valid skill identifier — using 'foo'`

Always emitted; no `--quiet` suppression. (Resolves C4.)

### DEC-010 — `_load_spec_or_report`: branch on exception type
Expand `cli/__init__.py::_load_spec_or_report`'s except clause to `(FileNotFoundError, OSError)`. Keep the existing "not found → suggest `clauditor init`" hint for `FileNotFoundError`. For other `OSError`, emit `ERROR: cannot read {path}: {exc}` (shows "Permission denied", "Input/output error", etc.). Two distinct messages, one except clause. (Resolves C5, R3=B.)

### DEC-011 — Extend `tmp_skill_file` with `layout="legacy" | "modern"` kwarg
Single factory handles both layouts. `layout="legacy"` (default) writes `tmp_path/<name>.md` (byte-identical to today). `layout="modern"` writes `tmp_path/.claude/skills/<name>/SKILL.md`. New tests opt in via the kwarg; all existing tests keep working untouched. Avoids the plugin-fixture-shadowing hazard in `tests/conftest.py`. (Resolves C6.)

### DEC-012 — Helper return type: pure `(str, str | None)` tuple; caller emits
`derive_skill_name(skill_path, skill_md_text) -> tuple[str, str | None]` — returns `(skill_name, warning_message_or_None)`. The helper never touches stderr; the `SkillSpec.from_file` caller emits the warning when the second tuple element is not `None`. Keeps the pure-compute-vs-io-split rule intact: the helper is unit-testable without capturing stderr; integration tests capture stderr to verify emission.

---

## Detailed Breakdown

Natural ordering: shared primitives → pure helpers → integration → adjacent fixes → hardening → regression/docs → quality gate → patterns.

---

### US-001 — Promote `SKILL_NAME_RE` to `paths.py`

**Description.** Move the skill-identifier regex from `src/clauditor/propose_eval.py` to `src/clauditor/paths.py` as the public constant `SKILL_NAME_RE`. Update `propose_eval.py` to import from `paths.py`. Pure refactor, no behavior change — unblocks US-002 from needing its own regex.

**Traces to:** DEC-008.

**Acceptance criteria:**
- `src/clauditor/paths.py` declares `SKILL_NAME_RE: str = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"` at module scope.
- `src/clauditor/propose_eval.py` imports `SKILL_NAME_RE` from `paths.py`; the local `_SKILL_NAME_RE` is removed.
- Every existing test in `tests/test_propose_eval.py` passes byte-identically (same error messages, same behavior).
- `uv run ruff check src/ tests/` passes; `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥80% coverage.

**Done when:** grep finds exactly one definition of the regex (in `paths.py`), `propose_eval.py` imports it, and all existing tests pass.

**Files:**
- `src/clauditor/paths.py` — add `SKILL_NAME_RE`.
- `src/clauditor/propose_eval.py` — replace `_SKILL_NAME_RE` usage with the imported symbol; remove the local.
- `tests/test_paths.py` — one test asserting `SKILL_NAME_RE` matches a known-good identifier and rejects a known-bad one.

**Depends on:** none.

---

### US-002 — Pure skill-identity helpers in `paths.py`

**Description.** Add two pure functions to `src/clauditor/paths.py`:

- `derive_skill_name(skill_path: Path, skill_md_text: str) -> tuple[str, str | None]`
- `derive_project_dir(skill_path: Path) -> Path`

Both are pure — no stderr, no disk side effects. `derive_skill_name` parses frontmatter via `parse_frontmatter`, validates `name:` against `SKILL_NAME_RE`, applies the DEC-001/DEC-002 rules, and returns `(name, warning_or_None)`. `derive_project_dir` wraps `find_project_root` and falls back to layout-aware ascent per DEC-003. TDD: tests first.

**Traces to:** DEC-001, DEC-002, DEC-003, DEC-007, DEC-008, DEC-012.

**Acceptance criteria:**
- `derive_skill_name`:
  - Returns `(frontmatter_name, None)` when `name:` is present, valid per `SKILL_NAME_RE`, and matches the filesystem-derived name.
  - Returns `(frontmatter_name, "clauditor.spec: frontmatter name '<fm>' overrides filesystem name '<fs>' — using '<fm>'")` on disagreement.
  - Returns `(filesystem_name, None)` when `name:` is absent (no frontmatter, or frontmatter has no `name:` key).
  - Returns `(filesystem_name, "clauditor.spec: frontmatter name '<bad>' is not a valid skill identifier — using '<fs>'")` when `name:` fails `SKILL_NAME_RE`.
  - Returns `(filesystem_name, None)` when `parse_frontmatter` raises `ValueError` (malformed frontmatter — treat as absent).
  - Modern layout (`<dir>/SKILL.md`): filesystem name = `skill_path.parent.name`.
  - Legacy layout (`<name>.md` where `<name> != "SKILL"`): filesystem name = `skill_path.stem`.
- `derive_project_dir`:
  - Returns `find_project_root(skill_path.parent)` when it returns a non-None value.
  - Falls back to `skill_path.parent.parent.parent.parent` when layout is modern (`skill_path.name == "SKILL.md"`).
  - Falls back to `skill_path.parent.parent.parent` otherwise (legacy).
- Zero stderr writes inside either function. Zero `read_text` calls — `derive_skill_name` takes text as input.
- Ruff passes; coverage ≥80% on the new code.

**Done when:** `TestDeriveSkillName` (≥7 tests) and `TestDeriveProjectDir` (≥4 tests) pass; every branch of both helpers is hit by at least one test.

**Files:**
- `src/clauditor/paths.py` — add `derive_skill_name`, `derive_project_dir`. Import `find_project_root` from `clauditor.setup` and `parse_frontmatter` from `clauditor._frontmatter`.
- `tests/test_paths.py` — add `TestDeriveSkillName`, `TestDeriveProjectDir`.

**TDD:** write these tests first, in this order:
- `test_frontmatter_name_matches_filesystem` — modern layout, `name: foo`, parent dir `foo`, no warning.
- `test_frontmatter_name_overrides_filesystem_with_warning` — modern layout, `name: bar`, parent dir `foo`, returns `("bar", <warn>)`.
- `test_missing_frontmatter_falls_back_modern` — modern SKILL.md with no frontmatter, returns `("<parent.name>", None)`.
- `test_missing_name_field_falls_back_legacy` — legacy `.md` file with frontmatter but no `name:`, returns `(stem, None)`.
- `test_invalid_regex_falls_back_with_warning` — `name: bad;value`, returns `(filesystem_name, <warn>)`.
- `test_malformed_frontmatter_treated_as_absent` — frontmatter text that raises `ValueError`, returns `(filesystem_name, None)`.
- `test_legacy_without_frontmatter` — plain `.md` file, no frontmatter block at all.
- `test_project_dir_via_find_project_root` — when a `.git` marker is present, uses it.
- `test_project_dir_fallback_modern_ascent` — in a `tmp_path` with no markers, modern path returns 4-deep.
- `test_project_dir_fallback_legacy_ascent` — in a `tmp_path` with no markers, legacy path returns 3-deep.

**Depends on:** US-001.

---

### US-003 — Wire helpers into `SkillSpec`; extend fixture; integration tests

**Description.** Rewire `SkillSpec.from_file` to read the skill file, call `derive_skill_name` and `derive_project_dir`, emit the stderr warning when non-None, and pass `skill_name_override=` to `__init__`. `SkillSpec.__init__` accepts the new optional kwarg, and when it's `None` falls back to a minimal layout-aware name derivation (no file I/O — this path is for direct construction with non-existent paths). Extend `tmp_skill_file` with `layout=` kwarg. Add integration tests.

**Traces to:** DEC-001, DEC-002, DEC-003, DEC-006, DEC-009, DEC-011.

**Acceptance criteria:**
- `SkillSpec.__init__` signature:
  ```python
  def __init__(
      self,
      skill_path: Path,
      eval_spec: EvalSpec | None = None,
      runner: SkillRunner | None = None,
      *,
      skill_name_override: str | None = None,
  ):
  ```
  When `skill_name_override` is provided, use it directly. When `None`, derive name layout-aware without reading the file (modern → `parent.name`, legacy → `stem`), and derive project_dir via `derive_project_dir`.
- `SkillSpec.from_file` reads the file with `skill_path.read_text(encoding="utf-8")`, calls `derive_skill_name`, emits any warning to stderr, and passes the name via `skill_name_override`. Eval auto-discovery logic unchanged.
- `tests/conftest.py::tmp_skill_file` accepts `layout="legacy" | "modern"` (default `"legacy"`). `"modern"` writes `tmp_path/.claude/skills/<name>/SKILL.md` and the sibling eval (if any) at `tmp_path/.claude/skills/<name>/SKILL.eval.json`.
- `TestFromFile` gains ≥5 new tests covering the DEC-005 matrix: modern+match, modern+disagree (captures stderr), modern+missing-name, legacy+match, legacy+missing-name.
- Every existing test in `tests/test_spec.py` passes untouched. `tests/test_quality_grader.py:1902` (direct `SkillSpec(Path("dummy.md"), ...)` construction) still passes.
- Coverage on modified code ≥80%.

**Done when:** `clauditor validate .claude/skills/<name>/SKILL.md` on a manually-constructed modern skill succeeds end-to-end (no `/SKILL` slash command). Legacy validate path byte-identical.

**Files:**
- `src/clauditor/spec.py` — modify `__init__` and `from_file`.
- `tests/conftest.py` — extend `tmp_skill_file`.
- `tests/test_spec.py` — new cases in `TestFromFile`.

**TDD:** tests for the new `TestFromFile` cases + a regression test for the direct-constructor path first. Implementation follows.

**Depends on:** US-002.

---

### US-004 — Fix `cli/init.py` via the shared helper

**Description.** Replace `cli/init.py`'s `skill_path.stem` derivation with a call to `derive_skill_name`. Requires reading the skill file (which the command does not do today). Preserve the existing flag set and output format.

**Traces to:** DEC-001, DEC-004, DEC-008.

**Acceptance criteria:**
- `clauditor init .claude/skills/foo/SKILL.md` writes a starter eval with `"skill_name": "foo"` and `"description": "Eval spec for /foo"`.
- `clauditor init .claude/commands/foo.md` writes `"skill_name": "foo"` (unchanged).
- If the skill file is missing, the command returns the existing error message (exit 1, per its current behavior). If the file is unreadable, emit `ERROR: cannot read {path}: {exc}` and return 1 (consistent with DEC-010 style, at the command level).
- The stderr warning from `derive_skill_name` (disagreement / invalid regex) is emitted to stderr by the command before writing the starter.
- Existing tests in `tests/test_cli.py` for `init` pass; add ≥2 new tests: one for modern layout, one asserting the warning is emitted on a disagreement.

**Done when:** `TestInit` tests pass for both layouts; grep confirms `skill_path.stem` is no longer used in `cli/init.py`.

**Files:**
- `src/clauditor/cli/init.py` — route through `derive_skill_name`.
- `tests/test_cli.py` — add `TestInit` modern-layout cases.

**Depends on:** US-002.

---

### US-005 — Harden `_load_spec_or_report` I/O error handling

**Description.** Expand `cli/__init__.py::_load_spec_or_report`'s except clause to `(FileNotFoundError, OSError)`. Branch on exception type: keep the existing "not found → suggest `clauditor init`" message for `FileNotFoundError`; emit `ERROR: cannot read {path}: {exc}` for other `OSError`.

**Traces to:** DEC-010.

**Acceptance criteria:**
- `FileNotFoundError` still produces the existing message (byte-identical).
- `PermissionError` (subclass of `OSError`) produces `ERROR: cannot read {path}: Permission denied` (or whatever the OS error formats as).
- Other `OSError` subclasses (e.g., `IsADirectoryError`) produce the same format.
- Both branches return the same non-zero exit code that the existing path does.
- Tests: one for `FileNotFoundError` (existing test still passes), one for `PermissionError` via monkey-patching `Path.read_text` to raise, one for `IsADirectoryError` via passing a directory path.

**Done when:** three test cases in `tests/test_cli.py::TestLoadSpecOrReport` pass; existing callers unaffected.

**Files:**
- `src/clauditor/cli/__init__.py` — expand the except clause.
- `tests/test_cli.py` — add tests.

**Depends on:** US-003.

---

### US-006 — Bundled-skill regression test + docs polish

**Description.** Add a regression test asserting `SkillSpec.from_file(src/clauditor/skills/clauditor/SKILL.md)` returns a spec with `skill_name == "clauditor"` and auto-discovers the sibling eval. Add one line to `docs/skill-usage.md` documenting that both layouts are supported.

**Traces to:** DEC-005.

**Acceptance criteria:**
- New class `TestBundledSkillViaSpec` in `tests/test_bundled_skill.py`, one test method: `test_bundled_skill_loads_via_skillspec` — asserts `spec.skill_name == "clauditor"`, `spec.skill_path` resolves to the real bundled SKILL.md, and `spec.eval_spec is not None`.
- `docs/skill-usage.md` mentions both layouts: one sentence under an existing section ("clauditor works with both `.claude/commands/<name>.md` and `.claude/skills/<name>/SKILL.md`.").
- README unchanged (internal fix; no teaser update warranted per DEC-005 scoping).
- Bundled skill `SKILL.md` is NOT modified — the `bundled-skill-docs-sync.md` rule does not trigger.

**Done when:** the new test passes; the docs diff is one sentence.

**Files:**
- `tests/test_bundled_skill.py` — add `TestBundledSkillViaSpec`.
- `docs/skill-usage.md` — add one sentence.

**Depends on:** US-003.

---

### US-007 — Quality Gate — code review x4 + CodeRabbit

**Description.** Run the code-reviewer agent four times across the full changeset, fixing every real bug each pass. Run CodeRabbit review if available on the PR. Ruff + pytest + coverage ≥80% must pass after all fixes.

**Acceptance criteria:**
- Four code-reviewer passes completed; every actionable finding either fixed or explicitly documented as a false positive.
- CodeRabbit (if configured) review triaged the same way.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with coverage ≥80%.
- No new TODO/FIXME comments landed.

**Done when:** all implementation stories are complete and quality gates pass cleanly.

**Depends on:** US-001 through US-006.

---

### US-008 — Patterns & Memory — update conventions and docs (priority 99)

**Description.** If new patterns emerged during this work, record them in `.claude/rules/` or docs. Candidate additions: (a) a rule about reading SKILL.md frontmatter as the identity source, (b) a note on layout-aware ascent patterns, (c) memory updates if new user/feedback memories surfaced.

**Acceptance criteria:**
- Any new `.claude/rules/*.md` file is self-contained with canonical implementation pointers and "when this rule applies / does NOT apply" sections matching existing rules' shape.
- If no new patterns emerged, the story closes as "no-op, verified during Quality Gate" — no file churn.

**Done when:** explicit decision (write rule OR skip) documented in the PR description.

**Depends on:** US-007.

---

### Rules-compliance gate for all stories

Validated against `.claude/rules/` identified in Discovery:

- **`pure-compute-vs-io-split.md`** ✓ — US-002's helpers are pure (take text, return tuples); US-003 is the I/O wrapper.
- **`in-memory-dict-loader-path.md`** ✓ — `parse_frontmatter` already pure in-memory; no split needed.
- **`path-validation.md`** ✓ — not applicable (no user path-lists from JSON; only reclassifying known paths).
- **`json-schema-version.md`** ✓ — no new sidecar.
- **`llm-cli-exit-code-taxonomy.md`** ✓ — not applicable (not LLM commands; `cli/init.py` retains its existing 0/1 taxonomy).
- **`bundled-skill-docs-sync.md`** ✓ — SKILL.md not modified (trigger: workflow edit); rule does not fire.
- **`readme-promotion-recipe.md`** ✓ — no README change (internal fix).
- **`project-root-home-exclusion.md`** ✓ — `find_project_root` logic untouched; exclusion guard inherited as-is.
- **`pytester-inprocess-coverage-hazard.md`** ✓ — no `pytester` in planned tests.
- **CLAUDE.md test conventions** ✓ — class-based tests; `tmp_path`; no fixture-name shadowing; existing `importlib.reload` in `test_spec.py` covers the reload hazard.

---

## Beads Manifest

- **Epic:** `clauditor-600` — #62: modern `<name>/SKILL.md` layout support (P1)
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/62-skill-md-layout`
- **Branch:** `feature/62-skill-md-layout`
- **PR:** https://github.com/wjduenow/clauditor/pull/66

### Tasks (priority P2 unless noted)

| ID | Title | Depends on |
|---|---|---|
| `clauditor-600.1` | US-001 — Promote `SKILL_NAME_RE` to `paths.py` | — |
| `clauditor-600.2` | US-002 — Pure skill-identity helpers in `paths.py` (TDD) | `.1` |
| `clauditor-600.3` | US-003 — Wire helpers into `SkillSpec`; extend `tmp_skill_file`; integration tests | `.2` |
| `clauditor-600.4` | US-004 — Fix `cli/init.py` via shared helper | `.2` |
| `clauditor-600.5` | US-005 — Harden `_load_spec_or_report` I/O error handling | `.3` |
| `clauditor-600.6` | US-006 — Bundled-skill regression test + docs polish | `.3` |
| `clauditor-600.7` | Quality Gate — code review x4 + CodeRabbit | `.1`, `.2`, `.3`, `.4`, `.5`, `.6` |
| `clauditor-600.8` | Patterns & Memory (P4) | `.7` |

### Ready at devolve

`bd ready` shows `clauditor-600.1` (US-001) as the only unblocked implementation task. US-003 and US-004 will unblock after US-002 completes; the two stories can run in parallel against separate worker contexts since neither depends on the other.

---

## Session Notes

### Session 1 — 2026-04-20
- Fetched ticket #62.
- Created worktree `/home/wesd/dev/worktrees/clauditor/62-skill-md-layout` on branch `feature/62-skill-md-layout` from `dev@e1f4e19`.
- Parallel scout + convention-check complete.
- Scope sized: core fix is ~2 pure helpers + `SkillSpec.__init__` +
  `cli/init.py`. Test surface is ~2 new test classes plus one
  bundled-skill regression.
- Awaiting user answers to Q1–Q5 before architecture review.
