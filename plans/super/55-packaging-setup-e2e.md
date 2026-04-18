# Super Plan: #55 — Investigate E2E test coverage for bundled-skill packaging + setup round-trip

## Meta
- **Ticket:** https://github.com/wjduenow/clauditor/issues/55
- **Branch:** `feature/55-packaging-setup-e2e`
- **Worktree:** `/home/wesd/dev/worktrees/clauditor/feature/55-packaging-setup-e2e`
- **Phase:** `detailing`
- **PR:** _(pending)_
- **Sessions:** 1
- **Last session:** 2026-04-18

> **Scope note:** This ticket is investigation + design. The deliverable
> is THIS plan doc — decisions about test shape, location, invocation,
> runtime, tooling. Implementation of the tests lands in one or more
> follow-up tickets filed at devolve time.

---

## Discovery

### Ticket Summary

**What:** Design an end-to-end test (or small set of tests) that closes
the gap between "wheel builds" and "wheel installs correctly and
`clauditor setup` produces a working skill symlink at the stamped
version." The current test stack stops at inspecting a built wheel as
a ZIP; no test installs the wheel into a venv and runs `clauditor
setup` inside a scratch project.

**Why:** A bug in `build_hooks/stamp_skill_version.py`, the
`importlib.resources` resolution in `cli/setup.py`, or the
`[tool.hatch.build.targets.wheel]` include config would ship silently
— CI's `validate-skill` job only lints frontmatter on the source tree,
and there is no install round-trip anywhere in the pipeline.

**Done when:** A design decision is recorded for test shape, location,
invocation (pytest vs CI-only vs both), venv fixture pattern, scratch
project marker, assertion set, runtime budget, and coverage-gate
interaction. Follow-up implementation tickets are filed; this ticket
closes when the plan is merged.

**Who benefits:** Future packaging changes (build-hook tweaks, wheel
include rules, new bundled skill files, `importlib.resources` seam
moves) — the test catches breakage at PR time instead of at `pip
install clauditor` in the wild.

### Codebase Findings (from Codebase Scout subagent)

**Existing tests:**

- `tests/test_packaging.py` (lines 1–86) — builds wheel via
  `subprocess.run(["uv", "build", "--wheel", ...])` into
  `tmp_path_factory.mktemp("wheel")`, inspects as ZIP. Asserts stamped
  `clauditor-version: "0.1.0"` in wheel (line 74), dev placeholder
  absent (line 79), source-tree retains placeholder (line 83), no
  `__pycache__` (line 65). **Does NOT install, does NOT run `clauditor
  setup`.** Skips if `uv` not on PATH.
- `tests/test_bundled_skill.py` — source-tree SKILL.md invariants
  only. Frontmatter shape, body ≤ 500 lines, sibling eval sidecar,
  stable-id uniqueness across L1/L2/L3. Does NOT touch installed
  artifact.
- `tests/test_setup.py` — 23 tests of `plan_setup()` pure decision
  logic + `find_project_root()` home-exclusion guards (see
  `.claude/rules/project-root-home-exclusion.md`). Scratch filesystems
  via `tmp_path`. **Does NOT exercise `os.symlink()`, real installed
  wheel, or `importlib.resources` resolution.**
- `tests/test_paths.py` — `resolve_clauditor_dir` only, unrelated.
- `tests/conftest.py` — no venv/pip fixtures; `_FakePopen` is a
  subprocess mock for stream tests.

**Production seams the E2E must cover (gap):**

| Seam | File:lines | Why source-tree tests miss it |
|------|------------|-------------------------------|
| Build-hook stamping at `uv build` time | `build_hooks/stamp_skill_version.py:27,104` | Source-tree SKILL.md keeps `"0.0.0-dev"`; only wheel contents have real version. `test_packaging.py` covers this at the ZIP level, but doesn't cover the installed layout. |
| `importlib.resources.files("clauditor") / "skills"` → `Path` via `as_file()` | `cli/setup.py:244–259` | Rejects zip/PEX installs. No unit test installs into a real venv to exercise the happy path. |
| Real `os.symlink()` creation + race-retry | `cli/setup.py:132–142,175–177,267–297` | `plan_setup` is pure; `_dispatch_setup_action` is the I/O side that `test_setup.py` does not touch. |
| Symlink target readable + stamped content | `cli/setup.py:283` | Symlink exists is one thing; reading through it and verifying `clauditor-version` matches `pyproject.toml` is another. |
| `clauditor setup --unlink` reversal | `cli/setup.py` (REMOVE_SYMLINK branch) | Round-trip "install → unlink → gone" only validated via `plan_setup` pure tests. |

**CI (`.github/workflows/ci.yml`):**

- `lint` job: ruff on src/tests.
- `validate-skill` job (lines 21–35): runs
  `scripts/validate_skill_frontmatter.py` on source-tree SKILL.md. No
  wheel build.
- `test` job: `uv sync --dev` + `pytest --cov` on Python 3.11/3.12/3.13.
  **No `uv build`, no `pip install`.** Ticket claim confirmed.

**Runtime cost baseline:** `uv build --wheel` completes in ~374 ms on
this machine (Codebase Scout measurement). Resulting wheel is ~151 KB.
This is fast enough that an in-suite pytest test is feasible without
a `@pytest.mark.slow` gate, though a cold venv create will add
seconds.

**`pyproject.toml` anchors:**
- `[project].version = "0.1.0"` (line 7) — what the stamp must equal.
- `[tool.hatch.build.targets.wheel]` includes `src/clauditor/skills/**/*`.
- `[tool.hatch.build.targets.wheel.hooks.custom]` wires the stamp hook
  to run automatically on every `uv build`.

### Applicable `.claude/rules/` (from Convention Checker subagent)

1. **`project-root-home-exclusion.md`** — load-bearing. The scratch
   project's marker walk must NOT ascend into `$HOME/.claude/`. The
   test fixture must construct the scratch project under `tmp_path`
   and place a `.git/` (or `.claude/`) marker inside it; NEVER rely on
   ascending to the user's home dir. Regression guard:
   `tests/test_setup.py::TestFindProjectRoot::test_find_project_root_skips_claude_at_home`
   already tests the production code; the E2E test must not re-
   introduce the hazard by landing scratch dirs above any `.git`
   marker on the developer's machine.
2. **`subprocess-cwd.md`** — if a new helper wraps `uv build` or
   `pip install` invocations for reuse, use a keyword-only `cwd`
   parameter defaulting to the configured scratch dir. (Existing
   `test_packaging.py` inlines `subprocess.run` without a wrapper;
   follow-up impl may or may not need one.)
3. **`pytester-inprocess-coverage-hazard.md`** — if the design ever
   considers using `pytester` to test fixture wiring, **do NOT**
   combine `runpytest_inprocess` + `--cov=clauditor` + `mock.patch` on
   clauditor modules. The E2E test should NOT use pytester; real
   subprocess invocation avoids the hazard.
4. **`sidecar-during-staging.md`, `json-schema-version.md`,
   `in-memory-dict-loader-path.md`, `pure-compute-vs-io-split.md`** —
   tangentially relevant if the E2E test ends up reading a JSON
   sidecar from the installed skill dir, but most likely the test
   only asserts on SKILL.md text + symlink properties.

Non-applicable but adjacent: `centralized-sdk-call.md` (no LLM),
`pre-llm-contract-hard-validate.md` (no LLM), `bundled-skill-docs-sync.md`
(docs edit, not test), `readme-promotion-recipe.md` (not touching
README).

No rule conflicts detected.

### Proposed Scope

A **pytest-based integration test** in a new file
`tests/test_e2e_setup.py` that:

1. Builds a wheel via `uv build --wheel` into a session-scoped
   tmpdir.
2. Creates a fresh venv and `pip install`s the wheel into it.
3. Constructs a scratch project (`tmp_path/project/` with `.git/`
   marker).
4. Invokes the installed `clauditor setup` from inside the scratch
   project via `subprocess.run`, using the venv's Python.
5. Asserts: exit 0; symlink at `project/.claude/skills/clauditor`
   exists; symlink resolves to a path inside the venv's site-
   packages; reading through the symlink yields a SKILL.md whose
   `clauditor-version` line equals `pyproject.toml`'s
   `[project].version` (not `"0.0.0-dev"`).
6. Additionally asserts `clauditor setup --unlink` exits 0 and the
   symlink is gone.

Mark the test `@pytest.mark.slow` (new marker; reserve for tests
that exceed ~2 s wall time). Default `pytest` invocation runs it;
developers can skip via `pytest -m "not slow"` during inner-loop
work. CI runs it as part of the existing `test` job.

### Open Questions for User

Five scoping questions (details in "Scoping Questions" section
below).

---

## Scoping Questions

**Q1 — Test invocation strategy:** how should the E2E test be run?

- **A.** Pytest in-suite, always runs, marked `@pytest.mark.slow` for
  opt-out via `pytest -m "not slow"`. Single source of truth for both
  local dev and CI. (Recommended given ~500 ms build cost.)
- **B.** Pytest opt-in, marked `@pytest.mark.e2e`, requires `pytest -m
  e2e` to include. CI gets a dedicated e2e matrix job. Keeps the
  default local `pytest` run fast regardless of cost growth.
- **C.** CI-only workflow step: shell script in `.github/workflows/ci.yml`
  that runs `uv build`, creates a venv, installs, invokes `clauditor
  setup`, and greps the output. Not runnable locally via `pytest`.
- **D.** Both A (pytest in-suite) AND C (CI bash step) — belt-and-
  suspenders if the real-user install path diverges from pytest's
  subprocess.

**Q2 — Venv fixture shape:** how many venvs does the test suite
build?

- **A.** Session-scoped fixture: build wheel once, create venv once,
  reuse for all E2E tests. Cheapest total runtime. Risk: tests leak
  state into each other (e.g., one test leaves a symlink that
  another assumes absent).
- **B.** Per-test fresh venv: cleanest isolation, slowest. Each test
  pays the venv create cost (~1–2 s) + install cost.
- **C.** Session-scoped venv, per-test fresh scratch project dir.
  Balance: venv reuse is safe (we don't mutate site-packages), but
  each test gets a clean `tmp_path` for the scratch project +
  symlink assertions.
- **D.** No venv — install with `pip install --target=tmp_path/deps`
  and set `PYTHONPATH` when invoking `clauditor`. Avoids venv
  creation cost but is an unusual install layout that doesn't match
  how real users install.

**Q3 — Scratch project marker:** what does the E2E scratch project
look like?

- **A.** `tmp_path/project/.git/` (empty dir). Matches typical
  project layout; `find_project_root` stops at `.git`.
- **B.** `tmp_path/project/.claude/` (empty dir). Exercises the
  `.claude`-marker path specifically, which is the codepath most at
  risk for the home-exclusion bug.
- **C.** Both, as parametrized test cases (one with `.git`, one with
  `.claude`). Small cost, covers both branches.
- **D.** `.git` only for the happy-path test, plus a separate
  negative test (no marker → exit 2).

**Q4 — Assertion set (MVP):** what must the E2E test verify?

- **A.** Minimal: `clauditor setup` exits 0; symlink exists at
  expected path; symlink target is readable.
- **B.** A + stamped version: SKILL.md read through the symlink has
  `clauditor-version:` matching `pyproject.toml`'s `[project].version`.
- **C.** B + `clauditor setup --unlink` reversal: second invocation
  exits 0, symlink is gone.
- **D.** C + negative path: `clauditor setup` from a dir with no
  marker exits 2 with a helpful message.

**Q5 — Runtime budget + coverage:** how strict is the runtime
constraint and does the E2E test count toward the 80 % coverage
gate?

- **A.** Aim for <3 s total; INCLUDE in 80 % gate (E2E covers the
  `cli/setup.py` I/O branches that pure tests miss — genuine
  coverage).
- **B.** Aim for <3 s total; EXCLUDE from 80 % gate (E2E is
  integration-only; coverage should reflect unit coverage of
  individual modules).
- **C.** Looser budget (up to ~10 s); opt-in via `@pytest.mark.slow`
  so the fast gate is unaffected.
- **D.** Any budget OK; CI-only (Q1=C) so local `pytest` stays
  fast.

---

## Architecture Review

### Scoping picks locked in (user-confirmed)

| Q | Pick | Decision |
|---|------|----------|
| Q1 | **A** | Pytest in-suite, `@pytest.mark.slow` opt-out marker. |
| Q2 | **C** | Session-scoped venv + per-test scratch project. |
| Q3 | **C** | Parametrize both `.git/` and `.claude/` markers. |
| Q4 | **D** | Full assertion stack: exit 0 + symlink + stamped version + `--unlink` + negative no-marker case. |
| Q5 | **B** | <3 s budget, excluded from 80 % coverage gate (subprocess coverage not wired). |

### Runtime measurement (empirical)

| Step | Operation | Time |
|------|-----------|------|
| 1 | `uv build --wheel` clean | 0.472 s |
| 2 | `uv venv` create | 0.007 s |
| 3 | `uv pip install <wheel>` | 0.050 s |
| 4 | `.git/` marker + `clauditor setup` | 0.109 s |
| 5 | Symlink inspect | — |
| 6 | Read stamped version through symlink | — (finds `"0.1.0"` ✓) |
| 7 | `clauditor setup --unlink` | 0.067 s |

**Session setup (steps 1–3, amortized):** 0.529 s
**Per-test cost (steps 4–7):** ~0.176 s
**Projected total for 8 parametrized tests:** 0.529 + 8 × 0.176 ≈ **1.93 s**

Verdict: <3 s budget (Q5=B) holds with comfortable margin.

### Seam-verification (does the design catch the bugs the ticket
cites?)

| Scenario | Catches? | Assertion that fires |
|----------|----------|----------------------|
| Broken `stamp_skill_version.py` (ships `"0.0.0-dev"`) | ✓ catches | Step 6: `assert "0.1.0" in SKILL.md` read through symlink |
| Wrong `importlib.resources.files(...)` target | ✓ catches | `cmd_setup` errors at `isinstance(traversable, Path)` guard, OR symlink resolves to non-existent path |
| `pyproject.toml` drops `skills/**/*` include | ✓ catches | `cmd_setup` fails at the same guard — `files("clauditor")/"skills"/"clauditor"` isn't a materializable `Path` |

**Seam-verification rating: strong (3 / 3).** All three motivating
regressions land on distinct, observable assertions.

### Rated review areas

| Area | Rating | Finding |
|------|--------|---------|
| **Runtime budget** | `pass` | 1.93 s projected ≪ 3 s budget. Empirically measured, not estimated. |
| **Seam coverage** | `pass` | All 3 ticket-cited regression scenarios caught by distinct assertions. |
| **Python exe resolution in venv** | `pass` | Standard `venv/bin/python` vs `venv/Scripts/python.exe` split; one `sysconfig`-style helper covers both. |
| **`importlib.resources.as_file()` happy path** | `pass` | `pip install <wheel>` always unpacks to `site-packages`; `isinstance(traversable, Path)` guard passes for wheel installs. |
| **Permissions / umask** | `pass` | `cli/setup.py` sets `0o755` explicitly; `tmp_path` world-readable on CI; no hazard. |
| **Retry / race hazards** | `pass` | Single-process pytest; `pytest-xdist` workers get separate `tmp_path`. Retry loop's `FileExistsError` branch is untriggerable here — covered only by unit tests. |
| **Windows symlink support** | `concern` | `os.symlink` requires admin/dev-mode on Windows. CI is Linux-only (`ubuntu-latest`, no Windows runner), so not a present blocker. If Windows lands in the matrix later, the test must skip or the production code needs a non-symlink fallback. |
| **Subprocess env isolation** | `concern` | The autouse `_isolate_clauditor_history` fixture in `tests/conftest.py` is an in-process monkeypatch — it does NOT propagate into a `subprocess.run(...)` child. A subprocess-spawned `clauditor setup` can read/write the user's real `$HOME/.claude/` or `$HOME/.clauditor/history.jsonl`. `setup` specifically does not write history, but the `find_project_root` walk is influenced by `$HOME`. Mitigation: pass `env={"HOME": scratch_dir, "PATH": ...}` to `subprocess.run`. |
| **`uv` availability gate** | `concern` | `test_packaging.py` skips when `uv` is absent on PATH. Inside a subprocess-spawned install, a partial failure (`uv build` OK but `uv pip install` fails) would error instead of skip. Mitigation: the session fixture pre-checks `uv --version` once and skips the entire class if missing; inside the test, any subsequent subprocess failure is a real failure, not a skip. |

### Overall rating: `concern`

Two concrete concerns to resolve in refinement (subprocess env isolation + `uv` gate shape) and one future-proofing decision (Windows `skipif`). No blockers for the current Linux-only CI matrix. Runtime + seam-verification both pass empirically.

---

## Refinement Log

### Refinement picks locked in (user-confirmed)

| R | Pick | Topic |
|---|------|-------|
| R1 | **A** | Windows `skipif` added now (not deferred). |
| R2 | **B** | Strict subprocess env whitelist: `PATH`, `HOME=scratch_dir`, `USER`, `LANG`, `UV_CACHE_DIR` only. |
| R3 | **C** | Require `uv` on PATH; tests fail hard if missing (no `pytest.skip`). |
| R4 | **B** | `.git` marker is an empty file (git-worktree style), not a dir. |
| R5 | **A** | Negative no-marker test asserts exit code 2 only; no stderr content. |

### Decisions

**DEC-001 — Test invocation.** Pytest in-suite in a new file
`tests/test_e2e_setup.py`. All tests in the file are marked
`@pytest.mark.slow`. The `slow` marker is registered in
`pyproject.toml`'s `[tool.pytest.ini_options].markers`. Default
`pytest` runs it; developers doing inner-loop TDD opt-out via
`pytest -m "not slow"`. CI runs it as part of the existing `test`
job — no separate matrix entry. _Traces to:_ Q1=A.

**DEC-002 — Venv fixture shape.** Session-scoped fixture builds one
wheel via `uv build --wheel`, creates one venv via `uv venv`,
installs the wheel via `uv pip install`. This fixture yields the
venv's Python executable path and is reused by every E2E test in
the file. Per-test fixture yields a fresh `tmp_path/project/`
scratch directory. The venv is NOT mutated by `clauditor setup` (it
writes symlinks into the scratch project, not site-packages), so
reuse is safe. _Traces to:_ Q2=C.

**DEC-003 — Scratch project marker layout.** Two parametrized
positive-path test cases:
- **Case 1 (`.git` file, git-worktree style):** write an empty file
  at `tmp_path/project/.git` (NOT a dir). This exercises
  `find_project_root`'s "treat file-typed `.git` as a worktree
  marker" branch (`src/clauditor/setup.py:73`).
- **Case 2 (`.claude` dir):** create an empty dir at
  `tmp_path/project/.claude/`. This is the branch most at risk
  from the home-exclusion hazard
  (`.claude/rules/project-root-home-exclusion.md`); even though
  that rule is enforced by the production code + a regression guard
  in `tests/test_setup.py`, a real-subprocess install-then-walk
  exercise reassures us nothing in the install path broke it.

  The `.claude`-as-file variant is deliberately NOT parametrized
  because production code rejects it (`cli/setup.py` treats
  `.claude` only as dir).

_Traces to:_ Q3=C + R4=B.

**DEC-004 — Assertion stack.** Each positive-path test asserts
(in order):
1. `clauditor setup` exits 0.
2. Symlink exists at `project/.claude/skills/clauditor` (use
   `Path.is_symlink()`, not `.exists()` — the latter follows the
   link and would pass even on a broken target).
3. `readlink(symlink).resolve()` lands inside the installed venv's
   `site-packages/clauditor/skills/clauditor/` (use
   `is_relative_to()`).
4. SKILL.md read through the symlink contains a frontmatter line
   matching `clauditor-version: "<VERSION>"` where `<VERSION>` is
   `pyproject.toml`'s `[project].version` and is NOT `0.0.0-dev`.
   Use `tomllib.loads()` on `pyproject.toml` to read the expected
   version at test-time — never hard-code.
5. `clauditor setup --unlink` exits 0 and the symlink is gone
   (`not Path.is_symlink()` AND `not Path.exists()`).

Plus one separate negative test (non-parametrized): `clauditor
setup` from a scratch dir without any marker exits 2. Assert exit
code only. _Traces to:_ Q4=D + R5=A.

**DEC-005 — Runtime + coverage.** <3 s total budget (empirically
validated at 1.93 s for 8 projected tests). The E2E test is
excluded from the 80 % coverage gate via a pytest marker +
`.coveragerc` (or equivalent) entry. Subprocess coverage is NOT
wired at this time; the decision to wire `COVERAGE_PROCESS_START` +
`.coveragerc`'s `[run].concurrency = multiprocessing` can be a
separate ticket if the `cli/setup.py` I/O-branch coverage gap
becomes a concern. _Traces to:_ Q5=B.

**DEC-006 — Windows guard.** Every test in the file is further
guarded by `@pytest.mark.skipif(sys.platform == "win32",
reason="clauditor setup uses os.symlink; Windows requires admin or
developer mode — unsupported until a non-symlink fallback lands.")`.
No-op on the current Linux-only CI matrix; future-proofs against a
Windows runner being added without anyone remembering to fix the
test. _Traces to:_ R1=A.

**DEC-007 — Subprocess environment isolation.** Every
`subprocess.run` call that invokes the installed `clauditor`
spawns the child with an explicit whitelist env dict:

```python
env = {
    "PATH": os.environ["PATH"],
    "HOME": str(tmp_path),        # prevents subprocess from seeing
                                   # the developer's real ~/.claude/
    "USER": os.environ.get("USER", "tester"),
    "LANG": os.environ.get("LANG", "C.UTF-8"),
    "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", "")
                    or str(tmp_path / ".uv-cache"),
}
```

Specifically NOT inherited: `CLAUDITOR_*`, `ANTHROPIC_*`,
`PYTHONPATH`, anything else. `HOME` redirection prevents the
`find_project_root` home-exclusion walk from being contaminated by
the developer's real home. `UV_CACHE_DIR` override keeps the
test's pip install hermetic. _Traces to:_ R2=B.

**DEC-008 — `uv` availability contract.** The session-scoped
fixture calls `uv --version` once at setup time. If `uv` is not on
PATH, the fixture raises (which becomes a test error, not a skip).
Rationale: `uv` is the repo's declared dev-dependency workflow
tool per CLAUDE.md's `uv sync --dev`; a missing `uv` means the
developer's env is broken and should be fixed, not silently
skipped. Differs from the existing `tests/test_packaging.py` skip
pattern — that test predates the CLAUDE.md dev-workflow commitment
and should eventually match this contract (out of scope for #55).
_Traces to:_ R3=C.

**DEC-009 — Negative test assertion depth.** The no-marker test
asserts exit code 2 only. No stderr content assertion. Rationale:
stderr messages are UI; locking tests to specific phrases creates
friction every time the CLI reference is polished (see
`.claude/rules/llm-cli-exit-code-taxonomy.md` — exit codes are the
contract, stderr is a hint). _Traces to:_ R5=A.

### Session notes accumulated during refinement

- R4's pick (empty `.git` *file*) maps to production code's
  git-worktree handling at `src/clauditor/setup.py:73`. Empty file
  works because `find_project_root` checks `(current /
  ".git").exists()` which matches both files and dirs. An empty
  `.git` dir would also work; the file variant is the more
  unusual path and therefore has higher payoff to cover.
- R2 strict env whitelist: verified no clauditor CLI invocations
  require `CLAUDITOR_*` envvars to function (setup reads only
  `cwd` + `importlib.resources`). If a future clauditor command
  starts depending on, say, `CLAUDITOR_PROJECT_DIR`, that dep
  needs an explicit allowlist entry here.
- R3 hard-fail on missing `uv`: intentional divergence from
  `test_packaging.py`'s skip pattern. The existing test was
  written before `uv` became the canonical dev workflow; we do
  NOT touch it in #55. Future ticket may harmonize.

---

## Detailed Breakdown

> **Reminder:** Ticket #55 is investigation + design. The plan doc
> IS the deliverable; the E2E test's implementation is explicitly
> out of scope per the ticket's "Out of scope" section. These
> stories are the minimal set to ship this plan + hand off to
> implementation.

### US-001 — Commit plan doc + publish draft PR

**Description:** Commit `plans/super/55-packaging-setup-e2e.md` to
the `feature/55-packaging-setup-e2e` branch, push to origin, open
a draft PR with a summary of the 9 decisions so it can be
reviewed.

**Traces to:** Ticket ACs #1, #2, #3 (all three are satisfied by
the plan doc).

**Acceptance criteria:**
- [ ] `plans/super/55-packaging-setup-e2e.md` committed with a
      descriptive message.
- [ ] Branch pushed to `origin`.
- [ ] Draft PR opened against `dev` titled `#55: Plan — E2E test
      for bundled-skill packaging + setup round-trip (plan)`.
- [ ] PR body links to issue #55 + summarizes each of DEC-001
      through DEC-009 in one line.
- [ ] Plan doc's Meta section `PR:` field updated with the PR
      URL and re-committed.

**Done when:** Draft PR URL pasted into the plan's `Meta.PR`
field and the plan passes `pytest tests/test_bundled_skill.py` +
`ruff check` (plan doc is prose, but we run the full gate to
confirm no accidental source-tree breakage).

**Files:** `plans/super/55-packaging-setup-e2e.md` (commit).
GitHub (draft PR via `gh pr create --draft`).

**Depends on:** none.

**No TDD.**

---

### US-002 — File follow-up implementation ticket

**Description:** Create a new GitHub issue titled "Implement
tests/test_e2e_setup.py per plan #55". The body summarizes the
test shape (DEC-001 through DEC-009), links back to the plan doc,
and carries an acceptance-criteria checklist the implementing
agent can check off.

**Traces to:** DEC-001 through DEC-009 (complete handoff context
for the follow-up).

**Acceptance criteria:**
- [ ] New GH issue exists; linked via "Relates to #55" in its body.
- [ ] Issue body contains: one-paragraph summary, bulleted list of
      DECs with a 1-sentence description each, link to the plan
      doc, acceptance-criteria checklist derived from DEC-004.
- [ ] Issue NOT closed; assigned to the project's default inbox
      (no assignee).

**Done when:** Issue URL added to the plan doc's "Follow-up"
section (new subsection under Meta) + re-committed.

**Files:** none in-tree. GitHub (new issue via `gh issue create`).

**Depends on:** US-001 (plan doc must be committed + PR-published
first so the new issue can link to a real URL).

**No TDD.**

---

### US-003 — Quality Gate (plan doc review)

**Description:** Run `code-reviewer` subagent on the plan doc for
up to 2 passes (prose quality + completeness vs ticket ACs + accuracy
of decision rationale + adherence to super-plan template). Fewer
passes than the 4 usually prescribed because this is a doc-only
change, not code — rapid convergence is expected. Also trigger
CodeRabbit via the draft PR; address any review comments.

**Traces to:** Quality Gate convention per super-plan template.

**Acceptance criteria:**
- [ ] code-reviewer run #1 addressed (all concerns resolved or
      marked out-of-scope in the plan's Session Notes).
- [ ] code-reviewer run #2 clean (no new concerns) OR concerns
      addressed in a follow-up commit.
- [ ] CodeRabbit feedback on the PR addressed.
- [ ] Plan doc re-reads clean: each DEC traces to a scoping/
      refinement answer; each AC traces to a DEC.

**Done when:** Two consecutive code-reviewer runs report no
outstanding concerns, AND CodeRabbit has no unresolved threads.

**Files:** `plans/super/55-packaging-setup-e2e.md` (polish edits
as needed).

**Depends on:** US-001.

**No TDD.**

---

### US-004 — Patterns & Memory

**Description:** Decide whether the E2E test design in this plan
introduces patterns worth codifying as new `.claude/rules/*.md`
entries BEFORE the implementation ticket lands. Three candidates:

1. **`session-venv-per-test-scratch.md`** — the session-scoped
   venv + per-test fresh scratch-project fixture shape. First use
   in this repo; future packaging/install tests could follow it.
2. **`subprocess-env-whitelist.md`** — the strict env-whitelist
   pattern (DEC-007). First use in this repo; distinct from
   `.claude/rules/subprocess-cwd.md` which only covers the `cwd`
   parameter.
3. **`symlink-test-windows-skipif.md`** — the `skipif(sys.platform
   == "win32")` guard for tests that exercise symlink code paths.
   Probably too narrow to justify its own rule; could be folded
   as a footnote into an existing rule.

**Traces to:** Patterns & Memory convention per super-plan
template.

**Acceptance criteria:**
- [ ] Decision documented in the plan's Session Notes: either
      (a) stub `.claude/rules/*.md` file(s) created (so the
      implementation ticket can cite them as constraints), or (b)
      explicit statement "existing rules
      (`project-root-home-exclusion.md`, `subprocess-cwd.md`,
      `pytester-inprocess-coverage-hazard.md`) cover all design
      constraints; new rules defer to post-implementation when we
      have real code to anchor."
- [ ] If rules written: each has the standard frontmatter +
      canonical-implementation pointer (pointing at the
      implementation ticket's planned file path is acceptable).

**Done when:** Either new `.claude/rules/*.md` file(s) committed
+ plan references them, OR plan's Session Notes explicitly record
"no new rules authored; existing rules cover design."

**Files:** possibly `.claude/rules/*.md` (new) +
`plans/super/55-packaging-setup-e2e.md` (Session Notes update).

**Depends on:** US-003.

**No TDD.**

**Recommended default:** (b) defer. Stub rules without real code
to anchor them tend to drift. Wait for the implementation ticket
to land real patterns before codifying them.

---

## Beads Manifest
_(Phase 7 — epic ID, task IDs, dependencies.)_

---

## Session Notes

### Session 2 — 2026-04-18 (same day)

- User confirmed scoping picks Q1=A, Q2=C, Q3=C, Q4=D, Q5=B.
- Phase 2 Architecture Review: spawned two focused subagents
  (cross-platform + flakiness; runtime + seam verification).
  Empirical runtime total for 8 projected parametrized tests:
  ~1.93 s (well under the 3 s budget). Seam verification strong
  (3/3 motivating regressions caught at distinct assertions).
  Two concerns raised: Windows symlink support (future-proofing
  only, current CI is Linux), subprocess env isolation (autouse
  `_isolate_clauditor_history` fixture does not propagate to
  subprocess children). No blockers.
- User answered refinement R1=A, R2=B, R3=C, R4=B, R5=A.
  Locked in DEC-001 through DEC-009.
- Phase 4 Detailing: four stories drafted (US-001 commit+PR,
  US-002 follow-up ticket, US-003 Quality Gate, US-004 Patterns
  & Memory). Recommended default for US-004 is defer.
- Phase set to `detailing`; awaiting user approval to proceed to
  Publish PR phase.

### Session 1 — 2026-04-18

- Fetched ticket #55 via `gh api`; confirmed investigation-only scope.
- Created worktree `feature/55-packaging-setup-e2e` via `bark`.
- Spawned Codebase Scout (Explore subagent) — mapped existing tests
  (`test_packaging.py`, `test_bundled_skill.py`, `test_setup.py`),
  production seams (`cli/setup.py`, `setup.py`, `stamp_skill_version.py`),
  CI jobs (`lint`, `validate-skill`, `test` — none install). Measured
  `uv build --wheel` cost: ~374 ms.
- Spawned Convention Checker (Explore subagent) — surveyed
  `.claude/rules/*.md`. Load-bearing: `project-root-home-exclusion.md`
  (scratch-project marker placement), `subprocess-cwd.md` (helper
  shape), `pytester-inprocess-coverage-hazard.md` (rules out a
  pytester approach if coupled to `--cov`). No conflicts.
- Drafted proposed scope (pytest in-suite `tests/test_e2e_setup.py`
  marked `@pytest.mark.slow`) and five scoping questions (Q1–Q5).
- Phase set to `discovery`; awaiting user answers.
