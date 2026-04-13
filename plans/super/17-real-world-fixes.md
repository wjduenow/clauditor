# Super Plan: #17 — Real-world P0/P1 fixes from my_claude_agent eval run

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/17
- **Branch:** `feature/17-real-world-fixes`
- **Worktree:** `/home/wesd/Projects/worktrees/clauditor/17-real-world-fixes`
- **Phase:** `devolved`
- **PR:** https://github.com/wjduenow/clauditor/pull/19
- **Sessions:** 1
- **Last session:** 2026-04-12

---

## Discovery

### Ticket Summary

**What:** Six independent fixes surfaced by running clauditor against real `/find-restaurants` output in `my_claude_agent`. Source observations: `docs/temp/real-world-test-observations.md`.

**Why:** These are blockers/traps that would bite anyone adopting clauditor as "the way" to test Claude Code skills. Fix them before wider promotion.

**Acceptance criteria (from ticket):**
- [ ] `domain` format added; `url` docs warn about bare-domain extraction
- [ ] `clauditor capture <skill>` CLI works end-to-end
- [ ] `TierRequirement.max_entries` field added and enforced
- [ ] `AssertionSet.grouped_summary()` collapses repeated failures
- [ ] `pytest.mark.network` registered in pytest plugin
- [ ] `clauditor doctor` command surfaces install/version issues
- [ ] All existing tests still pass

### Codebase Findings

**Fix 1 — `domain` format** → `src/clauditor/formats.py`
- `FORMAT_REGISTRY` built via `_def()` helper at lines 24–36; existing `url` entry at 56–59.
- `FormatDef` already supports separate `pattern` / `extract_pattern` — no schema change needed.

**Fix 2 — `clauditor capture` CLI** → `src/clauditor/cli.py` + `runner.py`
- `cli.py` uses argparse subparsers (lines 457–579), dispatch via `cmd_*()` functions.
- `runner.py` lines 100–124 already shells out to `self.claude_bin -p <prompt>` — reusable.
- Captured-output convention: `tests/eval/captured/<skill>.txt`.

**Fix 3 — `TierRequirement.max_entries`** → `schemas.py` + `grader.py`
- `TierRequirement` at schemas.py:24–30; `min_entries: int = 0` on line 29.
- `grade_extraction()` min_entries check at grader.py:93–104; mirror it for max.
- JSON round-trip: schemas.py:111, 197.

**Fix 4 — `AssertionSet.grouped_summary()`** → `assertions.py`
- `AssertionSet` at lines 30–56; existing `summary()` is flat.
- Names are structured: `section:{Section}/{tier}[{i}].{field}:{suffix}` (suffix=`format`, `pattern`, or empty for presence).

**Fix 5 — Register pytest markers** → `src/clauditor/pytest_plugin.py`
- `pytest_configure` already exists (lines 59–64) and registers `clauditor_grade`. Add `network` and `slow` with one line each.

**Fix 6 — `clauditor doctor`** → `cli.py`
- New subparser + `cmd_doctor()`. Checks: Python ≥ 3.11 (pyproject.toml:11), `anthropic` SDK importable, `claude` CLI on PATH, pytest plugin entry_point registered, editable-install health.

### Conventions (from CLAUDE.md)
- Test file per source module (`test_<module>.py`).
- 80% coverage gate enforced.
- Don't shadow plugin fixture names `clauditor_runner/spec/grader/triggers`.
- Beads for task tracking; no TodoWrite/markdown TODOs.
- No `.claude/rules/*.md` files and no `workflow-project.md` — general CLAUDE.md rules apply.

### Proposed Scope

All six fixes land in one plan. They're independent and small; bundling avoids thrash. Ordering follows dependency (format → schema → assertions/grader → cli → pytest plugin → doctor), and the Quality Gate + Patterns & Memory stories close it out.

### Scoping Questions

**Q1 — `capture` CLI: output path convention**
Where should `clauditor capture <skill>` write by default?

- **(A)** Always `tests/eval/captured/<skill>.txt` relative to cwd (matches existing convention; fails if run outside a repo with that layout).
- **(B)** Configurable via `--out <path>` with default `tests/eval/captured/<skill>.txt`.
- **(C)** (B) plus auto-version option `--versioned` → `tests/eval/captured/<skill>-YYYY-MM-DD.txt`.
- **(D)** (B) plus a config key in `clauditor.toml`/pyproject for per-project override.

**Q2 — `capture` CLI: skill args**
How should args be passed to `claude -p "/skill ..."`?

- **(A)** `clauditor capture find-restaurants -- "near San Jose"` — everything after `--` is the skill prompt suffix.
- **(B)** `clauditor capture find-restaurants --args "near San Jose"` — explicit flag.
- **(C)** Positional: `clauditor capture find-restaurants "near San Jose"`.

**Q3 — `max_entries` semantics when violated**
When extraction returns more entries than `max_entries`, what happens?

- **(A)** Hard fail — emit a `count_max` assertion failure; keep all entries for downstream field checks (so users see both count failure AND field failures).
- **(B)** Hard fail and truncate — only grade the first N entries for field checks.
- **(C)** Soft signal — emit a `count_max` assertion with `passed=False` but grade all entries (same as A, but explicit framing as "precision signal, not blocker"). Identical behavior to A; just naming.

**Q4 — `grouped_summary()` — what gets grouped**
Which grouping key makes failures most readable?

- **(A)** Group by `{field}:{suffix}` across all entries in a section/tier → "6/6 Restaurants/default[*].website:format failed".
- **(B)** Group by exact name prefix up to `[` → one group per section+tier, list fields inside.
- **(C)** Two-level: section/tier, then field+suffix.
- **(D)** (A) is the default; `summary()` stays flat; `grouped_summary()` is an opt-in method.

**Q5 — `doctor` checks scope**
Which checks does v1 run?

- **(A)** Minimal: Python version, `anthropic` importable, `claude` CLI on PATH.
- **(B)** (A) + pytest plugin entry_point registered + editable-install detection (is `src/clauditor` writable from the venv site-packages?).
- **(C)** (B) + warn if `pyproject.toml` in cwd has `path = "../clauditor"` without `editable = true`.

**Q6 — Should we *also* ship observation #7 docs (pattern vs format)?**
The ticket lists 6 items but observation #7 (FieldRequirement pattern vs format confusion) is a docs-only fix that naturally fits alongside the domain-format work.

- **(A)** No — stay strictly in ticket scope.
- **(B)** Yes — add a short README section + `clauditor formats list` command.
- **(C)** Yes — README only, skip the CLI subcommand.

---

## Decisions (from scoping)

- **DEC-001 — `capture` output path:** `--out` flag overriding default `tests/eval/captured/<skill>.txt`, plus `--versioned` flag producing `<skill>-YYYY-MM-DD.txt`. *(Q1=C)*
- **DEC-002 — `capture` args passing:** Trailing-`--` form: `clauditor capture find-restaurants -- "near San Jose"`. Everything after `--` is appended to the skill prompt. *(Q2=A)*
- **DEC-003 — `max_entries` behavior:** Hard-fail with a `count_max` assertion; field-level checks still run over all extracted entries so users see both the count failure and the per-entry failures. *(Q3=A)*
- **DEC-004 — `grouped_summary()` shape:** Flat `summary()` stays as-is; `grouped_summary()` is a new opt-in method that groups by `{field}:{suffix}` across all entries in a section/tier. *(Q4=D)*
- **DEC-005 — `doctor` v1 scope:** Python version + `anthropic` importable + `claude` CLI on PATH + pytest plugin entry_point registered + editable-install detection. *(Q5=B)*
- **DEC-006 — Deprecate `FieldRequirement.pattern`:** Keep `format` only. Custom regex capability is preserved via inline regex on `format` (registry-lookup-then-regex-fallback) — details in refinement. *(Q6 user choice)*

## Architecture Review

Single-pass review — each fix is small, independent, and touches well-scoped internals. No auth, no data migration, no external API surface beyond `claude -p` subprocess (already used).

| Area | Rating | Notes |
|---|---|---|
| **Security** | pass | `capture` shells out to the already-trusted `claude` binary via existing `runner.py` path. Skill-args go through argv (not shell), so no injection. `doctor` is read-only. |
| **Performance** | pass | All fixes are O(N) over assertions or entries. `grouped_summary()` is a single extra pass over `AssertionSet.results`. |
| **Data Model** | concern | `TierRequirement.max_entries` is backward compatible (default `None`). **However** removing `FieldRequirement.pattern` is a breaking change to spec JSON — any existing `*.eval.json` files with `"pattern": ...` will fail to load unless we auto-migrate or reject with a clear error. See DEC-006 refinement. |
| **API Design** | concern | `capture` CLI surface: `-- "args"` vs `--out` vs `--versioned` is a moderately wide surface. Plan carefully; document in `--help`. `doctor` exit codes matter — should `doctor` exit non-zero on failures so CI can gate on it? Flag for refinement. |
| **Observability** | pass | `doctor` is itself the observability improvement. `grouped_summary()` improves failure legibility. No new logging needed. |
| **Testing** | concern | `capture` subcommand is inherently hard to unit-test (shells out). Plan must specify: mock `runner.run()` in unit tests; a single end-to-end smoke test behind `@pytest.mark.network` can optionally exercise the real path. Coverage gate (80%) is tight — `doctor` branches (Python version check, editable-install detection) must all have test coverage. |
| **Migration / Breaking changes** | **blocker** | DEC-006 (remove `pattern`) breaks: schemas.py:203, grader.py:140–162, `__init__.py` export, 7+ test usages in test_schemas.py/test_grader.py, possibly the `find-restaurants.eval.json` spec in my_claude_agent. Must decide in refinement: hard-remove with migration script, or soft-deprecate with warning. |

### Blockers to resolve in refinement
1. **DEC-006 implementation shape** — hard remove vs soft deprecate vs extend `format` to accept inline regex.

### Concerns to address in refinement
1. `doctor` exit code behavior (CI-gating semantics).
2. Test strategy for `capture` (unit mock + optional integration).
3. `capture` `--versioned` and `--out` interaction (error if both?).

## Refinement Log

- **DEC-007 — `pattern` removal shape (R2):** Delete `FieldRequirement.pattern` entirely. Extend `FieldRequirement.format` to do registry-lookup first, fall back to compiling the string as a regex if no registry match. On regex compile failure, raise at spec-load time with a clear error message. Rationale: preserves custom-regex capability, collapses API to one field, matches CLAUDE.md "no backwards-compat shims." Grep confirmed **no `.eval.json` files currently use `"pattern"`** — migration is tests-only.
- **DEC-008 — `doctor` exit code (C1=a):** Always exit 0; `doctor` is report-only. CI gating is not a v1 requirement.
- **DEC-009 — `capture` test strategy (C2):** Unit tests mock `runner.run()`; one optional integration test behind `@pytest.mark.network` may exercise the real `claude -p` path but is not required for 80% coverage gate.
- **DEC-010 — `capture --out` + `--versioned` interaction (C3=c):** `--versioned` appends `-YYYY-MM-DD` to the stem of whatever `--out` resolves to. Works for both default path and custom `--out`.
- **DEC-011 — Registry-vs-regex disambiguation (implied by DEC-007):** Registry lookup happens first. A registry key is literal string equality — no regex chars allowed in registry names, so there's no ambiguity with regex patterns. If the string is neither a registered format nor a valid regex, raise `ValueError` at `FieldRequirement` construction time.
- **DEC-012 — `grouped_summary()` key for presence checks:** Names without a `:suffix` (e.g. `section:Restaurants/default[0].name`) group under the synthetic suffix `presence`. Grouping key: `(field_name, suffix_or_presence)`.
- **DEC-013 — `capture` skill name normalization:** Accept both `find-restaurants` and `/find-restaurants`; strip a single leading `/` before building the prompt.
- **DEC-014 — `doctor` output format:** Human-readable table with `[ok]` / `[warn]` / `[fail]` prefixes. No `--json` flag in v1.

## Detailed Breakdown

Stories are ordered by dependency. Each is sized for a single Ralph context window. Every story's acceptance criteria includes `uv run pytest --cov=clauditor --cov-report=term-missing` passing with the 80% gate intact (per CLAUDE.md).

---

### US-001 — Add `domain` format to registry

**Description:** Add a new `domain` entry to `FORMAT_REGISTRY` in `formats.py` that matches bare domains like `paesanosj.com` — the common output when LLMs extract display text from markdown links.

**Traces to:** Ticket Fix #1, DEC-011

**Files:**
- `src/clauditor/formats.py` — add entry via `_def()` helper after the existing `url` entry
- `tests/test_formats.py` — add matching/non-matching cases

**TDD:**
- `domain` matches: `paesanosj.com`, `sub.example.co.uk`, `a-b.io`
- `domain` rejects: `https://paesanosj.com`, `paesanosj`, `.com`, `example..com`, `example.com/path`
- `FORMAT_REGISTRY["domain"]` exists and has `.pattern` compiled

**Acceptance criteria:**
- New format registered, existing format tests still pass
- `format="domain"` usable in `FieldRequirement` at spec-load time
- `uv run ruff check src/ tests/` clean
- `uv run pytest --cov=clauditor` passes with coverage ≥ 80%

**Done when:** A spec with `{"format": "domain"}` loads and grades bare-domain values as passing.

**Depends on:** none

---

### US-002 — Deprecate `FieldRequirement.pattern`; extend `format` to accept inline regex

**Description:** Remove the `pattern` field from `FieldRequirement`. Extend `format` to support two modes: (a) registry lookup if the string matches a registered format name, (b) fall back to compiling the string as a regex. Invalid values raise `ValueError` at construction. Migrate all internal callers and tests.

**Traces to:** Ticket observation #7, DEC-006, DEC-007, DEC-011

**Files:**
- `src/clauditor/schemas.py` — remove `pattern` field from `FieldRequirement` (line ~19); remove `pattern` from `to_dict()` (line ~203); add construction-time validation that `format` is either a registry key or a compilable regex, raising `ValueError` otherwise
- `src/clauditor/grader.py` — remove the `field_req.pattern` branch (lines 140–162); leave the `format` branch (line 180) intact but extend it to handle the inline-regex case (where `format` was a regex string not a registry key)
- `src/clauditor/__init__.py` — remove `pattern` from exports if present
- `tests/test_schemas.py` — migrate the 3 `pattern=` usages (lines 118, 128, 441) to `format=` with either a registered name or an inline regex
- `tests/test_grader.py` — migrate the 4 `pattern=` usages (lines 623, 789, 856, 905) to `format=`
- `tests/conftest.py` — audit and migrate any `pattern=` usages
- `README.md` — add a short "Field validation" section explaining that `format` accepts either a registered name or an inline regex, with a decision-tree example

**TDD:**
- `FieldRequirement(name="x", format="phone_us")` — registry hit, works as before
- `FieldRequirement(name="x", format=r"\d{3}-\d{4}")` — inline regex, compiles and validates values
- `FieldRequirement(name="x", format="[invalid")` — raises `ValueError` at construction with a clear message
- Spec JSON with `"pattern": "..."` → loading raises `ValueError` or `TypeError` (no silent drop)
- Existing tests migrated to `format=` still exercise the same validation outcomes

**Acceptance criteria:**
- No reference to `FieldRequirement.pattern` anywhere in `src/` or `tests/`
- `uv run pytest --cov=clauditor` passes with coverage ≥ 80%
- `uv run ruff check src/ tests/` clean
- README section added

**Done when:** Grep for `FieldRequirement.*pattern` and `field_req\.pattern` returns zero matches in source and tests.

**Depends on:** US-001 (domain format exists so tests can use it as a registry example)

---

### US-003 — Add `TierRequirement.max_entries` with `count_max` enforcement

**Description:** Add an optional `max_entries: int | None = None` field to `TierRequirement`. In `grade_extraction()`, emit a new `count_max` assertion when extraction returns more entries than `max_entries`. Field-level checks still run over all extracted entries (DEC-003). Round-trip the field through JSON serialization.

**Traces to:** Ticket Fix #3, DEC-003

**Files:**
- `src/clauditor/schemas.py` — add `max_entries: int | None = None` to `TierRequirement` (~line 29); include in `to_dict()` (~line 197) only when non-None; parse from JSON in `from_file()` (~line 111)
- `src/clauditor/grader.py` — after the existing `min_entries` check (lines 93–104), add a mirrored `max_entries` check emitting an assertion named `section:{section_name}:count_max/{tier.label}`; only emit when `tier.max_entries is not None`
- `tests/test_schemas.py` — round-trip test: spec with `max_entries` loads, serializes, reloads
- `tests/test_grader.py` — grade case where extraction returns more than `max_entries` → assertion fails; case where equal → passes; case where `max_entries=None` → no assertion emitted; case where extraction exceeds max AND has field failures → both appear in the result set

**TDD:**
- `TierRequirement(name="default", max_entries=3)` serializes and deserializes
- Extract 6 entries when `max_entries=3` → one `count_max` failure, field checks still run for all 6
- Extract 2 entries when `max_entries=3` → no `count_max` failure
- `max_entries=None` (default) → no `count_max` assertion ever emitted

**Acceptance criteria:**
- Backward compatible: existing specs without `max_entries` behave identically
- Coverage ≥ 80%; ruff clean

**Done when:** A spec with `max_entries: 3` against a 6-entry extraction produces both the `count_max` failure and any field-level failures from the 6 entries.

**Depends on:** none (independent of US-001/US-002 beyond sharing schemas.py)

---

### US-004 — Add `AssertionSet.grouped_summary()` with field+suffix grouping

**Description:** Add a new `grouped_summary()` method to `AssertionSet` that collapses repeated failures by `(field_name, suffix_or_presence)`. Flat `summary()` stays unchanged (DEC-004). Parse assertion names of the form `section:{Section}/{tier}[{i}].{field}[:{suffix}]`.

**Traces to:** Ticket Fix #4, DEC-004, DEC-012

**Files:**
- `src/clauditor/assertions.py` — add `grouped_summary(self) -> list[str]` to `AssertionSet` (~line 50). Parse name structure with a regex, group by `(field, suffix_or_"presence")`, emit one line per group: `"{N}/{M} {section}/{tier}[*].{field}{:suffix} failed: {first_evidence}"`. Names that don't match the structured form fall through to individual entries.
- `tests/test_assertions.py` — cases: all-same-field-all-fail → one line; mixed passes/fails → only failing groups shown; unparseable names → passthrough; empty set → empty list

**TDD:**
- 6 results all failing `section:Restaurants/default[*].website:format` → one grouped line `"6/6 Restaurants/default[*].website:format failed"`
- 3 presence-check failures (`section:Restaurants/default[0].name` etc.) → one grouped line with synthetic `presence` suffix
- Mixed failing fields → one line per `(field, suffix)` group
- A `has_urls` top-level failure (non-structured name) → passthrough as individual entry

**Acceptance criteria:**
- `summary()` output unchanged (regression test)
- New method documented in docstring
- Coverage ≥ 80%; ruff clean

**Done when:** Calling `grouped_summary()` on the real-world-test AssertionSet from observation #2 produces 1 line instead of 24+.

**Depends on:** none

---

### US-005 — Register `network` and `slow` pytest markers

**Description:** Extend the existing `pytest_configure` hook in `pytest_plugin.py` to register `network` and `slow` markers so downstream projects don't see `PytestUnknownMarkWarning`.

**Traces to:** Ticket Fix #5

**Files:**
- `src/clauditor/pytest_plugin.py` — in the existing `pytest_configure()` (lines 59–64), add two `config.addinivalue_line("markers", ...)` calls for `network` and `slow` with the descriptions from the ticket
- `tests/test_pytest_plugin.py` — add a test that uses a pytester/subprocess fixture to assert the markers are registered after `pytest_configure` runs (or a simpler unit test that calls `pytest_configure` with a mock config and asserts the `addinivalue_line` calls)

**TDD:**
- After `pytest_configure(mock_config)`, `mock_config.addinivalue_line` was called with `("markers", "network: ...")` and `("markers", "slow: ...")` in addition to the existing `clauditor_grade` registration

**Acceptance criteria:**
- Running `uv run pytest -m "not network"` on a test file that imports clauditor does not emit `PytestUnknownMarkWarning` for `network` or `slow`
- Coverage ≥ 80%; ruff clean

**Done when:** The my_claude_agent test file using `@pytest.mark.slow` no longer triggers `PytestUnknownMarkWarning`.

**Depends on:** none

---

### US-006 — Add `clauditor capture <skill>` CLI subcommand

**Description:** New subparser `capture` that shells out to `claude -p "/<skill> <args>"` via the existing `runner.py` path, writes stdout to `tests/eval/captured/<skill>.txt` by default, and supports `--out <path>` override plus `--versioned` date-stamping.

**Traces to:** Ticket Fix #2, DEC-001, DEC-002, DEC-009, DEC-010, DEC-013

**Files:**
- `src/clauditor/cli.py` — add new subparser `capture` with positional `skill_name`, `--out`, `--versioned`, and trailing `--` passthrough for skill args; add `cmd_capture()` function that constructs the prompt `/{skill_name} {args}`, invokes `runner.run()` or equivalent subprocess call, resolves the output path (with `--versioned` appending `-YYYY-MM-DD` to the stem), creates parent directory, writes stdout
- `tests/test_cli.py` — unit tests mocking `runner.run()` / `subprocess.run`:
  - default path: writes to `tests/eval/captured/find-restaurants.txt`
  - `--out custom.txt`: writes to `custom.txt`
  - `--versioned`: filename has `-YYYY-MM-DD` suffix
  - `--out custom.txt --versioned`: writes to `custom-YYYY-MM-DD.txt`
  - skill name `/find-restaurants` normalizes to `find-restaurants`
  - skill name `find-restaurants` without leading slash still works
  - non-zero runner exit → CLI exits non-zero with error message

**TDD:**
- All 6 cases above before implementation

**Acceptance criteria:**
- `clauditor capture --help` shows the new subcommand with all flags documented
- Parent directory created if missing
- No network calls in unit tests (all mocked); coverage ≥ 80%; ruff clean

**Done when:** `clauditor capture find-restaurants -- "near San Jose"` (with mocked subprocess) writes the expected content to the expected path.

**Depends on:** none

---

### US-007 — Add `clauditor doctor` CLI subcommand

**Description:** New subparser `doctor` that runs read-only checks and prints a human-readable report. v1 scope (DEC-005): Python version ≥ 3.11, `anthropic` SDK importable, `claude` CLI on PATH, pytest plugin entry_point registered under `pytest11`, editable-install detection (is the installed `clauditor` package path writable / does it point at a source checkout?). Always exits 0 (DEC-008).

**Traces to:** Ticket Fix #6, DEC-005, DEC-008, DEC-014

**Files:**
- `src/clauditor/cli.py` — add `doctor` subparser and `cmd_doctor()` that runs each check, collects `(name, status, message)` tuples where status is `ok`/`warn`/`fail`, prints as aligned table with `[ok]`/`[warn]`/`[fail]` prefixes, always returns exit code 0
- `tests/test_cli.py` — for each check, unit test both the passing and failing branch by patching `sys.version_info`, `importlib.util.find_spec`, `shutil.which`, `importlib.metadata.entry_points`, and `importlib.util.find_spec("clauditor").origin`

**TDD:**
- Python 3.10 → `[fail]` for python version
- Python 3.11 → `[ok]`
- `anthropic` importable → `[ok]`; not importable → `[warn]` (it's an optional extra)
- `shutil.which("claude")` returns path → `[ok]`; returns None → `[fail]`
- `pytest11` entry_points contains `clauditor` → `[ok]`; missing → `[fail]`
- Installed path under site-packages with no source link → `[warn]` (not editable); path points at source tree → `[ok]`
- Exit code is 0 even when all checks fail

**Acceptance criteria:**
- All 6 check branches covered by unit tests
- Always exit 0
- Coverage ≥ 80%; ruff clean

**Done when:** `clauditor doctor` on a healthy dev venv prints 5 `[ok]` lines and exits 0.

**Depends on:** none

---

### US-008 — Quality Gate (code review × 4 + CodeRabbit)

**Description:** Run the `code-review` skill four times across the full changeset; fix all real bugs found each pass. Run CodeRabbit if available. Re-run project validation (`uv run ruff check`, `uv run pytest --cov`) after fixes. This story depends on **all implementation stories** being complete.

**Traces to:** All DEC-001 → DEC-014

**Acceptance criteria:**
- 4 code-review passes complete
- All real bugs found in reviews are fixed (non-real / stylistic "concerns" may be deferred with a written justification)
- `uv run ruff check src/ tests/` clean
- `uv run pytest --cov=clauditor --cov-report=term-missing` passes with ≥ 80% coverage
- All ticket acceptance-criteria checkboxes are satisfied

**Done when:** Four consecutive review passes produce no new real bugs.

**Depends on:** US-001, US-002, US-003, US-004, US-005, US-006, US-007

---

### US-009 — Patterns & Memory

**Description:** Update project conventions and memory with patterns learned in this plan. Specifically: (a) document the `format` field's registry-lookup-then-regex-fallback semantics in README and/or a future `.claude/rules/` file; (b) if `.claude/rules/` doesn't yet exist, propose creating one entry capturing "field validation uses `format` only; no `pattern` field"; (c) `bd remember` any non-obvious patterns (e.g. the naming scheme for `count_max` vs `count` assertions, the `grouped_summary` grouping key convention).

**Traces to:** DEC-007, DEC-012, DEC-014

**Acceptance criteria:**
- README updated with `format` decision tree
- At least one `bd remember` entry capturing a non-obvious insight

**Done when:** A fresh agent opening this repo would not re-ask any of the design questions resolved in DEC-006 through DEC-014.

**Depends on:** US-008

---

## Beads Manifest (see section above)

## Beads Manifest (see section above)
